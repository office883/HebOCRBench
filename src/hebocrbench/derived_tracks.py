"""Derive four printed-document roots from one frozen Modern-Hebrew page root."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import random
import shutil
from typing import Mapping, Sequence

from PIL import Image, ImageEnhance, ImageFilter

DEFAULT_ROBUSTNESS_VARIANTS = (
    "blur",
    "jpeg",
    "low_contrast",
    "speckle",
    "downsample",
    "uneven_illumination",
)


class DerivedTrackError(ValueError):
    """A printed-document track cannot be derived without breaking lineage."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        mask = Image.new("L", rgb.size)
        mask.putdata(
            [
                max(
                    80,
                    min(
                        255,
                        int(235 - 85 * (x / rgb.width) + 30 * (y / rgb.height)),
                    ),
                )
                for y in range(rgb.height)
                for x in range(rgb.width)
            ]
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
                        "degradation_seed": seed,
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
) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    gold = output / "gold.jsonl"
    gold.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    images = []
    for path in sorted((output / "images").rglob("*")):
        if path.is_file():
            images.append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "sha256": _sha(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    fingerprint = hashlib.sha256(
        gold.read_bytes()
        + json.dumps(images, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "schema_version": "1.0",
        "track_id": track_id,
        "source_root": source_root.name,
        "record_count": len(records),
        "document_count": len({str(record.get("document_id")) for record in records}),
        "split_counts": dict(
            sorted(Counter(str(record.get("split")) for record in records).items())
        ),
        "dataset_fingerprint": fingerprint,
        "gold_sha256": _sha(gold),
        "images": images,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def build_canonical_track_roots(
    source_root: str | Path,
    output_root: str | Path,
    *,
    robustness_variants: Sequence[str] = DEFAULT_ROBUSTNESS_VARIANTS,
) -> dict[str, dict[str, object]]:
    source = Path(source_root).resolve()
    output = Path(output_root).resolve()
    records = _load(source)
    output.mkdir(parents=True, exist_ok=True)
    roots = {
        "modern-page-ocr-v1": _page_records(
            source, output / "modern-page-ocr-v1", records, tables_only=False
        ),
        "modern-line-recognition-v1": _line_records(
            source, output / "modern-line-recognition-v1", records
        ),
        "modern-tables-v1": _page_records(
            source, output / "modern-tables-v1", records, tables_only=True
        ),
        "modern-robustness-v1": _robust_records(
            source,
            output / "modern-robustness-v1",
            records,
            robustness_variants,
        ),
    }
    return {
        track_id: _write_root(output / track_id, track_id, items, source)
        for track_id, items in roots.items()
    }
