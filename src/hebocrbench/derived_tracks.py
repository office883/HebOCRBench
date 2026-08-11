"""Derive three specialized roots from one frozen Modern-Hebrew page root."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import shutil
import tempfile
from typing import Mapping, Sequence

from PIL import Image, ImageChops, ImageEnhance, ImageFilter

from .corpus_stats import compute_corpus_stats
from .dataset_audit import audit_dataset
from .io import sha256_file, write_json, write_jsonl
from .validator import validate_gold_records

DEFAULT_ROBUSTNESS_VARIANTS = (
    "clean",
    "blur",
    "jpeg",
    "low_contrast",
    "speckle",
    "downsample",
    "uneven_illumination",
)

_ROBUSTNESS_LEVELS = {
    "clean": "control",
    "blur": "medium",
    "jpeg": "strong",
    "low_contrast": "strong",
    "speckle": "medium",
    "downsample": "2x",
    "uneven_illumination": "medium",
}


class DerivedTrackError(ValueError):
    """A printed-document track cannot be derived without breaking lineage."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_object(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise DerivedTrackError(f"missing release file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DerivedTrackError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DerivedTrackError(f"JSON object required: {path}")
    return value


def _file_inventory(root: Path, *, exclude: set[str] | None = None) -> list[dict[str, object]]:
    excluded = exclude or set()
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
        if path.relative_to(root).as_posix() not in excluded
    ]


def _source_identity(source_root: Path) -> dict[str, object]:
    manifest_path = source_root / "manifest.json"
    manifest = _read_object(manifest_path)
    lock = _read_object(source_root / "dataset.lock.json")
    frozen = _read_object(source_root / "FROZEN.json")
    fingerprint = str(manifest.get("dataset_fingerprint", ""))
    if not fingerprint or any(
        value.get("dataset_fingerprint") != fingerprint for value in (lock, frozen)
    ):
        raise DerivedTrackError("source manifest, lock and freeze fingerprint disagree")
    if frozen.get("manifest_sha256") != _sha(manifest_path):
        raise DerivedTrackError("source FROZEN.json does not bind manifest.json")
    required = (
        "attribution.jsonl",
        "citations.bib",
        "licenses",
        "source_reports",
    )
    missing = [name for name in required if not (source_root / name).exists()]
    if missing:
        raise DerivedTrackError("source release metadata is incomplete: " + ", ".join(missing))
    return {"manifest": manifest, "lock": lock, "fingerprint": fingerprint}


def _copy_release_metadata(source: Path, output: Path) -> None:
    for filename in ("attribution.jsonl", "citations.bib"):
        shutil.copyfile(source / filename, output / filename)
    for dirname in ("licenses", "source_reports"):
        shutil.copytree(source / dirname, output / dirname)


def _load(root: Path) -> list[dict[str, object]]:
    path = root / "gold.jsonl"
    if not path.is_file():
        raise DerivedTrackError(f"missing gold.jsonl: {root}")
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise DerivedTrackError(f"gold line {number} is not an object")
        records.append(value)
    if not records:
        raise DerivedTrackError("source page root is empty")
    return records


def _locked_evaluation_records(
    records: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Project every selected source page into the release's evaluation split.

    The parent corpus uses train/dev/test labels to audit grouping while it is
    assembled.  The public benchmark does not distribute a training partition:
    every selected page is held behind the participant pack and evaluated.  We
    preserve the parent label as provenance instead of carrying it into the
    official track roots, where repeated document boilerplate would otherwise
    create real cross-split leakage for line crops.
    """

    projected: list[dict[str, object]] = []
    for source in records:
        record = deepcopy(source)
        metadata = dict(record.get("metadata") or {})
        metadata.update(
            {
                "source_corpus_split": str(record.get("split", "")),
                "benchmark_holdout": True,
                "benchmark_split_policy": "locked_all_test_v1",
            }
        )
        record["metadata"] = metadata
        record["split"] = "test"
        projected.append(record)
    return projected


def _source_image(root: Path, record: Mapping[str, object]) -> Path:
    image = record.get("image")
    if not isinstance(image, Mapping) or not isinstance(image.get("path"), str):
        raise DerivedTrackError("record has no image path")
    path = (root / str(image["path"])).resolve()
    if root.resolve() != path and root.resolve() not in path.parents:
        raise DerivedTrackError("image path escapes source root")
    if not path.is_file():
        raise DerivedTrackError(f"missing image: {path}")
    expected = str(image.get("sha256", ""))
    actual = _sha(path)
    if expected and expected != actual:
        raise DerivedTrackError(f"image SHA-256 mismatch: {path}")
    return path


def _copy_image(source: Path, output: Path, relative: Path) -> dict[str, object]:
    destination = output / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and _sha(destination) != _sha(source):
        raise DerivedTrackError(f"image destination collision: {relative}")
    if not destination.exists():
        shutil.copyfile(source, destination)
    with Image.open(destination) as image:
        width, height = image.size
    return {
        "path": relative.as_posix(),
        "width": width,
        "height": height,
        "rotation_degrees": 0,
        "sha256": _sha(destination),
    }


def _shift(points: object, left: int, top: int) -> list[list[float]]:
    if not isinstance(points, list):
        return []
    return [
        [float(point[0]) - left, float(point[1]) - top]
        for point in points
        if isinstance(point, list) and len(point) >= 2
    ]


def _line_records(
    root: Path, output: Path, records: Sequence[dict[str, object]]
) -> list[dict[str, object]]:
    result = []
    for page in records:
        source = _source_image(root, page)
        with Image.open(source) as original:
            image = original.convert("RGB")
            for region in page.get("regions", []):
                if not isinstance(region, Mapping):
                    continue
                for line in region.get("lines", []):
                    if not isinstance(line, Mapping):
                        continue
                    polygon = line.get("polygon")
                    if not isinstance(polygon, list) or len(polygon) < 3:
                        continue
                    xs = [float(point[0]) for point in polygon]
                    ys = [float(point[1]) for point in polygon]
                    left = max(0, math.floor(min(xs)) - 2)
                    top = max(0, math.floor(min(ys)) - 2)
                    right = min(image.width, math.ceil(max(xs)) + 2)
                    bottom = min(image.height, math.ceil(max(ys)) + 2)
                    if right <= left or bottom <= top:
                        raise DerivedTrackError("line crop has zero area")
                    line_id = str(line.get("line_id", "line"))
                    page_id = f"{page['page_id']}::line::{line_id}"
                    relative = (
                        Path("images")
                        / "lines"
                        / f"{hashlib.sha256(page_id.encode()).hexdigest()[:24]}.png"
                    )
                    destination = output / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    image.crop((left, top, right, bottom)).save(
                        destination, format="PNG", optimize=False
                    )
                    shifted = deepcopy(dict(line))
                    shifted["polygon"] = _shift(line.get("polygon"), left, top)
                    shifted["baseline"] = _shift(line.get("baseline"), left, top)
                    record = deepcopy(page)
                    record["page_id"] = page_id
                    record["track"] = "modern_line_recognition"
                    record["image"] = {
                        "path": relative.as_posix(),
                        "width": right - left,
                        "height": bottom - top,
                        "rotation_degrees": 0,
                        "sha256": _sha(destination),
                    }
                    record["regions"] = [
                        {
                            "region_id": "line-region",
                            "type": "text_line",
                            "polygon": [
                                [0, 0],
                                [right - left, 0],
                                [right - left, bottom - top],
                                [0, bottom - top],
                            ],
                            "base_direction": shifted.get("base_direction", "rtl"),
                            "reading_index": 0,
                            "lines": [shifted],
                        }
                    ]
                    record["reading_order"] = {"edges": []}
                    record["tables"] = []
                    record["form_fields"] = []
                    metadata = dict(record.get("metadata") or {})
                    metadata.update(
                        {
                            "parent_page_id": page["page_id"],
                            "parent_line_id": line_id,
                            "derivation": "line_crop_v1",
                        }
                    )
                    record["metadata"] = metadata
                    result.append(record)
    if not result:
        raise DerivedTrackError("source root contains no usable lines")
    return result


def _page_records(
    root: Path,
    output: Path,
    records: Sequence[dict[str, object]],
    *,
    tables_only: bool,
) -> list[dict[str, object]]:
    result = []
    for page in records:
        if tables_only and not page.get("tables"):
            continue
        record = deepcopy(page)
        record["track"] = "modern_tables" if tables_only else "modern_page_ocr"
        source = _source_image(root, page)
        relative = Path("images") / ("tables" if tables_only else "pages") / source.name
        record["image"] = _copy_image(source, output, relative)
        result.append(record)
    if tables_only and not result:
        raise DerivedTrackError("source root contains no table pages")
    return result


def _degrade(image: Image.Image, variant: str, seed: int) -> Image.Image:
    rgb = image.convert("RGB")
    rng = random.Random(seed)
    if variant == "clean":
        return rgb.copy()
    if variant == "blur":
        return rgb.filter(ImageFilter.GaussianBlur(radius=1.25))
    if variant == "jpeg":
        stream = io.BytesIO()
        rgb.save(stream, format="JPEG", quality=42, optimize=False)
        stream.seek(0)
        return Image.open(stream).convert("RGB")
    if variant == "low_contrast":
        return ImageEnhance.Contrast(rgb).enhance(0.48)
    if variant == "downsample":
        small = rgb.resize(
            (max(1, rgb.width // 2), max(1, rgb.height // 2)),
            Image.Resampling.BILINEAR,
        )
        return small.resize(rgb.size, Image.Resampling.BICUBIC)
    if variant == "speckle":
        pixels = rgb.load()
        for _ in range(max(1, rgb.width * rgb.height // 350)):
            x = rng.randrange(rgb.width)
            y = rng.randrange(rgb.height)
            pixels[x, y] = (0, 0, 0) if rng.random() < 0.55 else (255, 255, 255)
        return rgb
    if variant == "uneven_illumination":
        # Build separable one-dimensional gradients and let Pillow expand and
        # combine them in C.  The previous per-pixel Python loop made a 700-page
        # release unnecessarily take hours while producing the same fixed field.
        horizontal = Image.new("L", (rgb.width, 1))
        horizontal.putdata([int(235 - 85 * (x / max(1, rgb.width))) for x in range(rgb.width)])
        vertical = Image.new("L", (1, rgb.height))
        vertical.putdata([int(30 * (y / max(1, rgb.height))) for y in range(rgb.height)])
        mask = ImageChops.add(
            horizontal.resize(rgb.size),
            vertical.resize(rgb.size),
        )
        return Image.composite(rgb, Image.new("RGB", rgb.size, (235, 235, 235)), mask)
    raise DerivedTrackError(f"unknown robustness variant: {variant}")


def _robust_records(
    root: Path,
    output: Path,
    records: Sequence[dict[str, object]],
    variants: Sequence[str],
) -> list[dict[str, object]]:
    result = []
    for page in records:
        source = _source_image(root, page)
        source_sha = _sha(source)
        with Image.open(source) as original:
            for variant in variants:
                page_id = f"{page['page_id']}::degradation::{variant}"
                seed = int(
                    hashlib.sha256((source_sha + "\0" + variant).encode()).hexdigest()[:16],
                    16,
                )
                derived = _degrade(original, variant, seed)
                relative = (
                    Path("images")
                    / "robustness"
                    / variant
                    / f"{hashlib.sha256(page_id.encode()).hexdigest()[:24]}.png"
                )
                destination = output / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                derived.save(destination, format="PNG", optimize=False)
                record = deepcopy(page)
                record["page_id"] = page_id
                record["track"] = "modern_robustness"
                record["image"] = {
                    "path": relative.as_posix(),
                    "width": derived.width,
                    "height": derived.height,
                    "rotation_degrees": 0,
                    "sha256": _sha(destination),
                }
                metadata = dict(record.get("metadata") or {})
                metadata.update(
                    {
                        "parent_page_id": page["page_id"],
                        "parent_image_sha256": source_sha,
                        "degradation_variant": variant,
                        "degradation_family": variant,
                        "degradation_level": _ROBUSTNESS_LEVELS.get(variant, "canonical"),
                        "degradation_seed": seed,
                        "degradation_is_control": variant == "clean",
                        "derivation": "paired_robustness_v1",
                    }
                )
                record["metadata"] = metadata
                result.append(record)
    return result


def _write_root(
    output: Path,
    track_id: str,
    records: Sequence[dict[str, object]],
    source_root: Path,
    *,
    derivation: Mapping[str, object],
) -> dict[str, object]:
    identity = _source_identity(source_root)
    parent_manifest = identity["manifest"]
    parent_lock = identity["lock"]
    assert isinstance(parent_manifest, Mapping)
    assert isinstance(parent_lock, Mapping)
    output.mkdir(parents=True, exist_ok=True)
    gold = output / "gold.jsonl"
    write_jsonl(gold, records)
    validation = validate_gold_records(records, dataset_root=output)
    if not validation.is_valid:
        raise DerivedTrackError(
            "derived records failed validation: "
            + "; ".join(f"{issue.code}: {issue.message}" for issue in validation.errors[:10])
        )
    audit = audit_dataset(records, output)
    if not audit.is_valid:
        raise DerivedTrackError(
            "derived records failed leakage/integrity audit: "
            + "; ".join(f"{issue.code}: {issue.message}" for issue in audit.errors[:10])
        )
    stats = compute_corpus_stats(records)
    write_json(output / "stats.json", stats)
    write_json(output / "audit.json", audit.to_dict())
    _copy_release_metadata(source_root, output)

    source_ids = [str(value) for value in parent_manifest.get("source_ids", [])]
    accepted = [str(value) for value in parent_manifest.get("accepted_source_ids", [])]
    source_verification = dict(parent_manifest.get("source_verification") or {})
    image_files = _file_inventory(output / "images")
    fingerprint_basis = {
        "benchmark": parent_manifest.get("benchmark"),
        "benchmark_version": parent_manifest.get("benchmark_version"),
        "schema_version": "1.0",
        "registry_version": parent_lock.get("registry_version"),
        "registry_fingerprint": parent_manifest.get("registry_fingerprint"),
        "profile": parent_manifest.get("profile"),
        "source_ids": source_ids,
        "accepted_source_ids": accepted,
        "source_verification": source_verification,
        "records_sha256": sha256_file(gold),
        "stats_sha256": sha256_file(output / "stats.json"),
        "image_files": image_files,
        "track_id": track_id,
        "profile_scope": "track-component",
        "parent_dataset_fingerprint": identity["fingerprint"],
        "derivation": dict(derivation),
    }
    fingerprint = _canonical_hash(fingerprint_basis)
    lock = {
        **fingerprint_basis,
        "dataset_fingerprint": fingerprint,
        "source_licenses": dict(parent_lock.get("source_licenses") or {}),
    }
    write_json(output / "dataset.lock.json", lock)
    files = _file_inventory(output, exclude={"manifest.json"})
    manifest = {
        "schema_version": "1.0",
        "benchmark": parent_manifest.get("benchmark"),
        "benchmark_version": parent_manifest.get("benchmark_version"),
        "profile": parent_manifest.get("profile"),
        "profile_scope": "track-component",
        "track_id": track_id,
        "parent_dataset_fingerprint": identity["fingerprint"],
        "derivation": dict(derivation),
        "dataset_fingerprint": fingerprint,
        "registry_fingerprint": parent_manifest.get("registry_fingerprint"),
        "page_count": len(records),
        "source_ids": source_ids,
        "accepted_source_ids": accepted,
        "source_verification": source_verification,
        "license_tiers": list(parent_manifest.get("license_tiers", [])),
        "stats": stats,
        "audit": {
            "is_valid": audit.is_valid,
            "errors": len(audit.errors),
            "warnings": len(audit.warnings),
        },
        "files": files,
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def build_canonical_track_roots(
    source_root: str | Path,
    output_root: str | Path,
    *,
    robustness_variants: Sequence[str] = DEFAULT_ROBUSTNESS_VARIANTS,
    overwrite: bool = False,
) -> dict[str, dict[str, object]]:
    source = Path(source_root).resolve()
    output = Path(output_root).resolve()
    records = _load(source)
    evaluation_records = _locked_evaluation_records(records)
    source_identity = _source_identity(source)
    source_manifest = source_identity["manifest"]
    assert isinstance(source_manifest, Mapping)
    if source_manifest.get("track_id") != "modern-page-ocr-v1":
        raise DerivedTrackError("source root must be bound to track_id modern-page-ocr-v1")
    if output.exists() and not overwrite:
        raise DerivedTrackError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.derive-", dir=output.parent))
    try:
        roots = {
            "modern-page-ocr-v1": _page_records(
                source,
                temporary / "modern-page-ocr-v1",
                evaluation_records,
                tables_only=False,
            ),
            "modern-line-recognition-v1": _line_records(
                source, temporary / "modern-line-recognition-v1", evaluation_records
            ),
            "modern-tables-v1": _page_records(
                source,
                temporary / "modern-tables-v1",
                evaluation_records,
                tables_only=True,
            ),
            "modern-robustness-v1": _robust_records(
                source,
                temporary / "modern-robustness-v1",
                evaluation_records,
                robustness_variants,
            ),
        }
        methods: dict[str, dict[str, object]] = {
            "modern-page-ocr-v1": {
                "method": "evaluation_holdout_projection_v1",
                "split_policy": "locked_all_test_v1",
            },
            "modern-line-recognition-v1": {"method": "line_crop_v1", "padding_px": 2},
            "modern-tables-v1": {"method": "table_page_filter_v1"},
            "modern-robustness-v1": {
                "method": "paired_robustness_v1",
                "variants": list(robustness_variants),
                "levels": {
                    variant: _ROBUSTNESS_LEVELS.get(variant, "canonical")
                    for variant in robustness_variants
                },
            },
        }
        manifests = {
            track_id: _write_root(
                temporary / track_id,
                track_id,
                items,
                source,
                derivation=methods[track_id],
            )
            for track_id, items in roots.items()
        }
        if output.exists():
            shutil.rmtree(output)
        os.replace(temporary, output)
        return manifests
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
