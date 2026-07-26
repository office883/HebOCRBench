"""Build and verify leakage-resistant participant and organizer release packs.

The participant pack intentionally contains public train/dev gold and two test
input views:

* ``blind-input.jsonl`` exposes only page IDs and image descriptors.
* ``oracle-layout-input.jsonl`` exposes region/line geometry but no text.

The organizer pack contains the held-out gold, the private identifier map and
the HMAC key used to make test identifiers opaque.  The two packs are bound by
content inventories and SHA-256 fingerprints; neither relies on filenames or
filesystem mtimes for identity.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Iterable, Mapping, Sequence

from .io import load_jsonl, sha256_file, write_json, write_jsonl


PUBLIC_TEST_ID = re.compile(r"^hebocr-v1-test-[0-9a-f]{20}$")
PUBLIC_DOCUMENT_ID = re.compile(r"^hebocr-v1-doc-[0-9a-f]{20}$")
TEXT_KEYS = frozenset({"text", "page_text", "value"})
TEXT_FILE_SUFFIXES = frozenset({".bib", ".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"})


class PackVerificationError(ValueError):
    """A release pack is incomplete, altered or leaks held-out gold."""


@dataclass(frozen=True, slots=True)
class PackBuildResult:
    participant_root: Path
    organizer_root: Path
    test_pages: int
    participant_fingerprint: str
    organizer_fingerprint: str


@dataclass(frozen=True, slots=True)
class PackVerificationReport:
    valid: bool
    test_pages: int
    participant_fingerprint: str
    organizer_fingerprint: str
    checks: Mapping[str, bool]


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _inventory(root: Path, *, excluded: Iterable[str] = ()) -> list[dict[str, object]]:
    skip = set(excluded)
    result: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in skip:
            continue
        result.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return result


def _pack_fingerprint(
    *, role: str, dataset_fingerprint: str, files: Sequence[Mapping[str, object]]
) -> str:
    return _canonical_hash(
        {
            "schema_version": "1.0",
            "role": role,
            "dataset_fingerprint": dataset_fingerprint,
            "files": list(files),
        }
    )


def _opaque_identifier(
    key: bytes,
    *,
    namespace: str,
    dataset_fingerprint: str,
    value: str,
) -> str:
    digest = hmac.new(
        key,
        f"HebOCRBench/1.0/{namespace}\0{dataset_fingerprint}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]
    prefix = "test" if namespace == "page" else "doc"
    return f"hebocr-v1-{prefix}-{digest}"


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != sha256_file(source):
            raise ValueError(f"release-pack destination collision: {destination}")
        return
    shutil.copyfile(source, destination)


def _copy_public_metadata(build_root: Path, participant: Path) -> None:
    for relative in ("attribution.jsonl", "citations.bib"):
        source = build_root / relative
        if source.is_file():
            _copy_file(source, participant / relative)
    license_root = build_root / "licenses"
    if license_root.is_dir():
        shutil.copytree(license_root, participant / "licenses")


def _copy_track_configs(track_config_root: Path, participant: Path) -> list[str]:
    configs = sorted(path for path in track_config_root.glob("*.yaml") if path.is_file())
    if not configs:
        raise ValueError(f"track config root has no YAML configurations: {track_config_root}")
    destination = participant / "tracks"
    destination.mkdir(parents=True, exist_ok=True)
    for config in configs:
        _copy_file(config, destination / config.name)
    lock = track_config_root / "tracks.lock.json"
    if lock.is_file():
        _copy_file(lock, destination / lock.name)
    return [path.name for path in configs]


def _copy_schemas(schema_root: Path, participant: Path) -> list[str]:
    schemas = sorted(path for path in schema_root.glob("*.json") if path.is_file())
    if not schemas:
        raise ValueError(f"schema root has no JSON schemas: {schema_root}")
    destination = participant / "schemas"
    destination.mkdir(parents=True, exist_ok=True)
    for schema in schemas:
        _copy_file(schema, destination / schema.name)
    return [path.name for path in schemas]


def _oracle_layout_view(
    record: Mapping[str, object], public_page_id: str, image: Mapping[str, object]
) -> dict[str, object]:
    regions: list[dict[str, object]] = []
    for raw_region in record.get("regions", []):  # type: ignore[assignment]
        if not isinstance(raw_region, Mapping):
            continue
        region = {
            key: deepcopy(raw_region[key])
            for key in (
                "region_id",
                "type",
                "polygon",
                "base_direction",
                "language",
                "reading_index",
            )
            if key in raw_region
        }
        lines: list[dict[str, object]] = []
        for raw_line in raw_region.get("lines", []):  # type: ignore[assignment]
            if not isinstance(raw_line, Mapping):
                continue
            lines.append(
                {
                    key: deepcopy(raw_line[key])
                    for key in (
                        "line_id",
                        "polygon",
                        "baseline",
                        "base_direction",
                        "language",
                        "reading_index",
                        "tags",
                    )
                    if key in raw_line
                }
            )
        region["lines"] = lines
        regions.append(region)
    return {
        "schema_version": "1.0",
        "page_id": public_page_id,
        "track": str(record.get("track", "page_ocr_blind")),
        "image": dict(image),
        "regions": regions,
        "reading_order": deepcopy(record.get("reading_order", {"edges": []})),
    }


def _blind_view(
    record: Mapping[str, object], public_page_id: str, image: Mapping[str, object]
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "page_id": public_page_id,
        "track": str(record.get("track", "page_ocr_blind")),
        "image": dict(image),
    }


def _contains_text_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in TEXT_KEYS:
                return True
            if _contains_text_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_text_key(item) for item in value)
    return False


def _count_by(records: Sequence[Mapping[str, object]], key: str) -> dict[str, int]:
    values = Counter(str(record.get(key, "unknown")) for record in records)
    return dict(sorted(values.items()))


def _copy_public_split(
    build_root: Path,
    participant: Path,
    split: str,
    records: Sequence[dict[str, object]],
) -> None:
    if not records:
        return
    for record in records:
        image = record.get("image")
        if not isinstance(image, Mapping):
            raise ValueError(f"{record.get('page_id')}: image descriptor is missing")
        relative = Path(str(image.get("path", "")))
        source = build_root / relative
        if not source.is_file():
            raise ValueError(f"{record.get('page_id')}: image is missing: {relative}")
        if image.get("sha256") and sha256_file(source) != str(image["sha256"]):
            raise ValueError(f"{record.get('page_id')}: image SHA-256 mismatch")
        _copy_file(source, participant / relative)
    write_jsonl(participant / split / "gold.jsonl", records)


def _write_pack_lock(
    root: Path,
    *,
    role: str,
    dataset_fingerprint: str,
    lock_name: str,
) -> str:
    files = _inventory(root, excluded={lock_name})
    fingerprint = _pack_fingerprint(role=role, dataset_fingerprint=dataset_fingerprint, files=files)
    write_json(
        root / lock_name,
        {
            "schema_version": "1.0",
            "role": role,
            "dataset_fingerprint": dataset_fingerprint,
            "pack_fingerprint": fingerprint,
            "files": files,
        },
    )
    return fingerprint


def build_benchmark_packs(
    build_root: str | Path,
    output_root: str | Path,
    *,
    track_config_root: str | Path,
    id_key: bytes,
    schema_root: str | Path | None = None,
    public_gold_splits: Sequence[str] = ("train", "dev", "diagnostic"),
    held_out_split: str = "test",
    overwrite: bool = False,
) -> PackBuildResult:
    """Create deterministic public/private packs from one certified-style corpus build."""

    if len(id_key) < 32:
        raise ValueError("test identifier key must contain at least 32 bytes")
    build = Path(build_root).resolve()
    output = Path(output_root).resolve()
    manifest_path = build / "manifest.json"
    gold_path = build / "gold.jsonl"
    if not manifest_path.is_file() or not gold_path.is_file():
        raise ValueError("build root must contain manifest.json and gold.jsonl")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise ValueError("build manifest must contain a JSON object")
    dataset_fingerprint = str(manifest.get("dataset_fingerprint", ""))
    if len(dataset_fingerprint) != 64:
        raise ValueError("build manifest has no valid dataset fingerprint")
    records = [dict(record) for record in load_jsonl(gold_path)]
    if not records:
        raise ValueError("build contains no gold records")

    public_splits = set(public_gold_splits)
    allowed_splits = public_splits | {held_out_split}
    unknown_splits = sorted({str(record.get("split", "")) for record in records} - allowed_splits)
    if unknown_splits:
        raise ValueError("records use unassigned release splits: " + ", ".join(unknown_splits))
    test_records = sorted(
        (record for record in records if record.get("split") == held_out_split),
        key=lambda record: str(record["page_id"]),
    )
    if not test_records:
        raise ValueError("build has no held-out test split")

    split_by_document: defaultdict[str, set[str]] = defaultdict(set)
    for record in records:
        split_by_document[str(record["document_id"])].add(str(record["split"]))
    leaked_documents = sorted(
        document
        for document, splits in split_by_document.items()
        if held_out_split in splits and bool(splits & public_splits)
    )
    if leaked_documents:
        raise ValueError(
            "held-out test documents also occur in public gold: " + ", ".join(leaked_documents[:20])
        )

    if output.exists():
        if not overwrite:
            raise ValueError(f"pack output already exists: {output}")
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.packs-", dir=output.parent))
    participant = temporary / "participant"
    organizer = temporary / "organizer"
    participant.mkdir(parents=True)
    organizer.mkdir(parents=True)

    try:
        for split in sorted(public_splits):
            split_records = sorted(
                (record for record in records if record.get("split") == split),
                key=lambda record: str(record["page_id"]),
            )
            _copy_public_split(build, participant, split, split_records)

        page_map: dict[str, str] = {}
        document_map: dict[str, str] = {}
        blind: list[dict[str, object]] = []
        oracle: list[dict[str, object]] = []
        organizer_gold: list[dict[str, object]] = []
        id_map: list[dict[str, object]] = []
        submissions: list[dict[str, object]] = []

        for record in test_records:
            original_page = str(record["page_id"])
            original_document = str(record["document_id"])
            public_page = page_map.setdefault(
                original_page,
                _opaque_identifier(
                    id_key,
                    namespace="page",
                    dataset_fingerprint=dataset_fingerprint,
                    value=original_page,
                ),
            )
            public_document = document_map.setdefault(
                original_document,
                _opaque_identifier(
                    id_key,
                    namespace="document",
                    dataset_fingerprint=dataset_fingerprint,
                    value=original_document,
                ),
            )
            raw_image = record.get("image")
            if not isinstance(raw_image, Mapping):
                raise ValueError(f"{original_page}: image descriptor is missing")
            source_image = build / str(raw_image.get("path", ""))
            if not source_image.is_file():
                raise ValueError(f"{original_page}: image is missing")
            actual_sha = sha256_file(source_image)
            if raw_image.get("sha256") and actual_sha != str(raw_image["sha256"]):
                raise ValueError(f"{original_page}: image SHA-256 mismatch")
            suffix = source_image.suffix.lower() or ".img"
            relative_image = Path("test") / "images" / f"{public_page}{suffix}"
            _copy_file(source_image, participant / relative_image)
            public_image = dict(raw_image)
            public_image["path"] = relative_image.as_posix()
            public_image["sha256"] = actual_sha

            blind.append(_blind_view(record, public_page, public_image))
            oracle.append(_oracle_layout_view(record, public_page, public_image))
            gold_record = deepcopy(record)
            gold_record["page_id"] = public_page
            gold_record["document_id"] = public_document
            gold_record["image"] = public_image
            organizer_gold.append(gold_record)
            metadata = record.get("metadata")
            source_id = metadata.get("source_id") if isinstance(metadata, Mapping) else None
            id_map.append(
                {
                    "schema_version": "1.0",
                    "public_page_id": public_page,
                    "public_document_id": public_document,
                    "original_page_id": original_page,
                    "original_document_id": original_document,
                    "source_id": source_id,
                }
            )
            submissions.append(
                {
                    "schema_version": "1.0",
                    "page_id": public_page,
                    "regions": [],
                    "tables": [],
                    "form_fields": [],
                }
            )

        write_jsonl(participant / "test" / "blind-input.jsonl", blind)
        write_jsonl(participant / "test" / "oracle-layout-input.jsonl", oracle)
        write_jsonl(participant / "sample-submission.jsonl", submissions)
        write_jsonl(organizer / "test" / "gold.jsonl", organizer_gold)
        write_jsonl(organizer / "test" / "id-map.jsonl", id_map)
        (organizer / "test" / "ID-KEY.bin").write_bytes(id_key)

        _copy_public_metadata(build, participant)
        track_files = _copy_track_configs(Path(track_config_root), participant)
        schemas = _copy_schemas(
            Path(schema_root)
            if schema_root is not None
            else Path(__file__).resolve().parent / "schemas",
            participant,
        )
        participant_readme = (
            "# HebOCRBench 1.0 Participant Pack\n\n"
            "Train/dev gold is public. Test gold and the original source identifiers are not included.\n"
            "Use `test/blind-input.jsonl` for end-to-end OCR and `test/oracle-layout-input.jsonl` "
            "only for the recognition-oracle track.\n"
        )
        (participant / "README.md").write_text(participant_readme, encoding="utf-8")

        split_counts = Counter(str(record["split"]) for record in records)
        public_records = [record for record in records if str(record["split"]) in public_splits]
        public_content_files = _inventory(
            participant,
            excluded={"PACK-MANIFEST.json", "PACK-LOCK.json"},
        )
        participant_content_fingerprint = _canonical_hash(public_content_files)
        write_json(
            participant / "PACK-MANIFEST.json",
            {
                "schema_version": "1.0",
                "benchmark": str(manifest.get("benchmark", "HebOCRBench")),
                "benchmark_version": str(manifest.get("benchmark_version", "1.0.0")),
                "role": "participant",
                "profile": manifest.get("profile"),
                "dataset_fingerprint": dataset_fingerprint,
                "content_fingerprint": participant_content_fingerprint,
                "split_counts": dict(sorted(split_counts.items())),
                "public_gold_splits": sorted(public_splits),
                "held_out_split": held_out_split,
                "test_page_count": len(test_records),
                "public_track_counts": _count_by(public_records, "track"),
                "test_track_counts": _count_by(test_records, "track"),
                "source_ids": list(manifest.get("source_ids", [])),
                "track_configs": track_files,
                "schemas": schemas,
                "test_identifier_scheme": "HMAC-SHA256-20hex-v1",
                "contains_test_gold": False,
            },
        )
        participant_fingerprint = _write_pack_lock(
            participant,
            role="participant",
            dataset_fingerprint=dataset_fingerprint,
            lock_name="PACK-LOCK.json",
        )

        key_fingerprint = hashlib.sha256(id_key).hexdigest()
        write_json(
            organizer / "ORGANIZER-MANIFEST.json",
            {
                "schema_version": "1.0",
                "benchmark": str(manifest.get("benchmark", "HebOCRBench")),
                "benchmark_version": str(manifest.get("benchmark_version", "1.0.0")),
                "role": "organizer",
                "profile": manifest.get("profile"),
                "dataset_fingerprint": dataset_fingerprint,
                "participant_pack_fingerprint": participant_fingerprint,
                "test_page_count": len(test_records),
                "test_gold_sha256": sha256_file(organizer / "test" / "gold.jsonl"),
                "id_map_sha256": sha256_file(organizer / "test" / "id-map.jsonl"),
                "identifier_key_sha256": key_fingerprint,
                "contains_private_test_gold": True,
            },
        )
        (organizer / "README.md").write_text(
            "# HebOCRBench 1.0 Organizer Pack\n\n"
            "Keep this directory private. It contains held-out gold, the original-ID map and the HMAC key.\n",
            encoding="utf-8",
        )
        organizer_fingerprint = _write_pack_lock(
            organizer,
            role="organizer",
            dataset_fingerprint=dataset_fingerprint,
            lock_name="ORGANIZER-LOCK.json",
        )

        report = verify_benchmark_packs(participant, organizer)
        if not report.valid:
            raise PackVerificationError("newly built packs failed verification")
        os.replace(temporary, output)
        return PackBuildResult(
            participant_root=output / "participant",
            organizer_root=output / "organizer",
            test_pages=len(test_records),
            participant_fingerprint=participant_fingerprint,
            organizer_fingerprint=organizer_fingerprint,
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _load_object(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackVerificationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise PackVerificationError(f"{path} must contain a JSON object")
    return value


def _verify_lock(root: Path, lock_name: str, role: str) -> tuple[str, str]:
    lock = _load_object(root / lock_name)
    dataset_fingerprint = str(lock.get("dataset_fingerprint", ""))
    files = lock.get("files")
    if lock.get("role") != role or not isinstance(files, list):
        raise PackVerificationError(f"invalid {role} pack lock")
    actual = _inventory(root, excluded={lock_name})
    if files != actual:
        raise PackVerificationError(f"{role} file inventory mismatch")
    expected = _pack_fingerprint(
        role=role,
        dataset_fingerprint=dataset_fingerprint,
        files=actual,
    )
    if lock.get("pack_fingerprint") != expected:
        raise PackVerificationError(f"{role} pack fingerprint mismatch")
    return expected, dataset_fingerprint


def _participant_text(root: Path) -> str:
    chunks: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.suffix.lower() in TEXT_FILE_SUFFIXES:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def verify_benchmark_packs(
    participant_root: str | Path,
    organizer_root: str | Path,
) -> PackVerificationReport:
    """Verify semantic secrecy first, then verify every byte against both locks."""

    participant = Path(participant_root)
    organizer = Path(organizer_root)
    blind = load_jsonl(participant / "test" / "blind-input.jsonl")
    oracle = load_jsonl(participant / "test" / "oracle-layout-input.jsonl")
    gold = load_jsonl(organizer / "test" / "gold.jsonl")
    mapping = load_jsonl(organizer / "test" / "id-map.jsonl")
    if not blind:
        raise PackVerificationError("participant pack has no held-out test inputs")
    if any(_contains_text_key(item) for item in blind + oracle):
        raise PackVerificationError("test text leakage detected in participant inputs")
    if any("regions" in item or "page_text" in item for item in blind):
        raise PackVerificationError("blind test input contains oracle or text fields")

    blind_ids = [str(item.get("page_id", "")) for item in blind]
    oracle_ids = [str(item.get("page_id", "")) for item in oracle]
    gold_ids = [str(item.get("page_id", "")) for item in gold]
    map_ids = [str(item.get("public_page_id", "")) for item in mapping]
    if len(set(blind_ids)) != len(blind_ids):
        raise PackVerificationError("duplicate public test page identifiers")
    if not all(PUBLIC_TEST_ID.fullmatch(page_id) for page_id in blind_ids):
        raise PackVerificationError("test page identifiers are not opaque v1 identifiers")
    if not (blind_ids == oracle_ids == gold_ids == map_ids):
        raise PackVerificationError("participant, organizer and identifier-map page order differ")
    if any(not PUBLIC_DOCUMENT_ID.fullmatch(str(item.get("document_id", ""))) for item in gold):
        raise PackVerificationError("organizer gold contains non-opaque test document identifiers")
    if any(item.get("split") != "test" for item in gold):
        raise PackVerificationError("organizer gold contains a non-test record")

    blind_images = {str(item["page_id"]): item.get("image") for item in blind}
    for record in gold:
        page_id = str(record["page_id"])
        if record.get("image") != blind_images.get(page_id):
            raise PackVerificationError(f"test image descriptor differs for {page_id}")
    for item in blind:
        image = item.get("image")
        if not isinstance(image, Mapping):
            raise PackVerificationError(f"{item.get('page_id')}: missing test image descriptor")
        relative = str(image.get("path", ""))
        if not relative.startswith("test/images/"):
            raise PackVerificationError(f"{item.get('page_id')}: non-anonymous test image path")
        path = participant / relative
        if not path.is_file() or sha256_file(path) != str(image.get("sha256", "")):
            raise PackVerificationError(f"{item.get('page_id')}: test image is missing or altered")

    participant_text = _participant_text(participant)
    for item in mapping:
        for key in ("original_page_id", "original_document_id"):
            original = str(item.get(key, ""))
            if original and original in participant_text:
                raise PackVerificationError(
                    f"original test identifier leaked into participant pack: {key}"
                )

    participant_fingerprint, participant_dataset = _verify_lock(
        participant, "PACK-LOCK.json", "participant"
    )
    organizer_manifest = _load_object(organizer / "ORGANIZER-MANIFEST.json")
    if organizer_manifest.get("participant_pack_fingerprint") != participant_fingerprint:
        raise PackVerificationError("organizer manifest is not bound to this participant pack")
    organizer_fingerprint, organizer_dataset = _verify_lock(
        organizer, "ORGANIZER-LOCK.json", "organizer"
    )
    if participant_dataset != organizer_dataset:
        raise PackVerificationError("participant and organizer packs bind different datasets")
    id_key = organizer / "test" / "ID-KEY.bin"
    if not id_key.is_file() or hashlib.sha256(id_key.read_bytes()).hexdigest() != str(
        organizer_manifest.get("identifier_key_sha256", "")
    ):
        raise PackVerificationError("organizer identifier key is missing or altered")

    checks = {
        "no_test_text_leakage": True,
        "opaque_test_identifiers": True,
        "test_images_verified": True,
        "participant_inventory": True,
        "organizer_inventory": True,
        "pack_binding": True,
        "identifier_key": True,
    }
    return PackVerificationReport(
        valid=True,
        test_pages=len(blind),
        participant_fingerprint=participant_fingerprint,
        organizer_fingerprint=organizer_fingerprint,
        checks=checks,
    )
