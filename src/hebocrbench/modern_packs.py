"""Leakage-resistant participant packs for the locked Modern Hebrew suite.

The suite pack deliberately keeps the canonical gold files in their certified
track roots.  Participant page, document and oracle-layout identifiers are
HMAC-derived aliases.  The private organizer map translates submitted
predictions back to the original page (and, when present, layout) identifiers
so the unmodified, suite-locked ``gold.jsonl`` remains the scoring authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .io import load_jsonl, sha256_file, write_json, write_jsonl
from .modern_suite import (
    DEFAULT_HEADLINE_TRACKS,
    ModernSuiteSpec,
    coerce_modern_suite_lock,
    load_modern_suite_lock,
)


_OPAQUE_ID = re.compile(r"^hbo-v1-[pdrltf]-[0-9a-f]{24}$")
_IMAGE_SHARD = re.compile(r"^[0-9a-f]{2}$")
_URL = re.compile(r"(?:https?|ftp)://|www\.", re.IGNORECASE)
_GOLD_TEXT_KEYS = frozenset({"text", "page_text", "label_text", "value_text"})
_ORACLE_TRACKS = frozenset({"modern-bidi-v1", "modern-line-recognition-v1"})
_ID_KINDS = {
    "page": "p",
    "document": "d",
    "region": "r",
    "line": "l",
    "table": "t",
    "field": "f",
}
_IMAGE_SHARD_SCHEME = "sha256-opaque-page-id-prefix-2hex-v1"
_MAX_FILES_PER_IMAGE_DIRECTORY = 10_000


class ModernPackError(ValueError):
    """A Modern Hebrew suite pack is incomplete, altered or leaks gold."""


@dataclass(frozen=True, slots=True)
class ModernPackBuildResult:
    participant_root: Path
    organizer_root: Path
    suite_fingerprint: str
    page_count: int
    participant_fingerprint: str
    organizer_fingerprint: str


@dataclass(frozen=True, slots=True)
class ModernPackVerificationReport:
    valid: bool
    suite_fingerprint: str
    page_count: int
    participant_fingerprint: str
    organizer_fingerprint: str
    checks: Mapping[str, bool]


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModernPackError(f"cannot read {label} at {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ModernPackError(f"{label} must contain a JSON object")
    return value


def _suite(value: str | Path | ModernSuiteSpec | Mapping[str, Any]) -> ModernSuiteSpec:
    if isinstance(value, (str, Path)):
        return load_modern_suite_lock(value)
    return coerce_modern_suite_lock(value)


def _safe_relative(value: object, *, label: str) -> Path:
    path = Path(str(value))
    if not str(value) or path.is_absolute() or ".." in path.parts:
        raise ModernPackError(f"{label} is not a safe relative path: {value!r}")
    return path


def _inventory(root: Path, *, exclude: Iterable[str] = ()) -> list[dict[str, object]]:
    skipped = set(exclude)
    result: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ModernPackError(f"pack contains a symbolic link: {path.relative_to(root)}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in skipped:
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
    *, role: str, suite_fingerprint: str, files: Sequence[Mapping[str, object]]
) -> str:
    return _canonical_hash(
        {
            "schema_version": "1.0",
            "role": role,
            "suite_fingerprint": suite_fingerprint,
            "files": list(files),
        }
    )


def _write_lock(root: Path, *, role: str, suite_fingerprint: str, name: str) -> str:
    files = _inventory(root, exclude={name})
    fingerprint = _pack_fingerprint(role=role, suite_fingerprint=suite_fingerprint, files=files)
    write_json(
        root / name,
        {
            "schema_version": "1.0",
            "role": role,
            "suite_fingerprint": suite_fingerprint,
            "pack_fingerprint": fingerprint,
            "files": files,
        },
    )
    return fingerprint


def _verify_lock(root: Path, *, role: str, name: str) -> tuple[str, str]:
    lock = _read_object(root / name, f"{role} pack lock")
    files = lock.get("files")
    if lock.get("role") != role or not isinstance(files, list):
        raise ModernPackError(f"invalid {role} pack lock")
    actual = _inventory(root, exclude={name})
    if files != actual:
        raise ModernPackError(f"{role} pack file inventory mismatch")
    suite_fingerprint = str(lock.get("suite_fingerprint", ""))
    expected = _pack_fingerprint(role=role, suite_fingerprint=suite_fingerprint, files=actual)
    if lock.get("pack_fingerprint") != expected:
        raise ModernPackError(f"{role} pack fingerprint mismatch")
    return expected, suite_fingerprint


def _opaque_identifier(
    key: bytes,
    *,
    suite_fingerprint: str,
    track_id: str,
    kind: str,
    value: str,
) -> str:
    prefix = _ID_KINDS[kind]
    message = f"HebOCRBench/suite-pack/1\0{suite_fingerprint}\0{track_id}\0{kind}\0{value}"
    digest = hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return f"hbo-v1-{prefix}-{digest}"


def _participant_image_shard(public_page_id: str) -> str:
    if not _OPAQUE_ID.fullmatch(public_page_id):
        raise ModernPackError("participant image page identifier is not opaque")
    return hashlib.sha256(public_page_id.encode("utf-8")).hexdigest()[:2]


def _image_sharding_manifest() -> dict[str, object]:
    return {
        "scheme": _IMAGE_SHARD_SCHEME,
        "hash_algorithm": "sha256",
        "hash_input": "opaque-page-id-utf8",
        "prefix_hex_chars": 2,
        "max_files_per_directory": _MAX_FILES_PER_IMAGE_DIRECTORY,
        "path_template": ("tracks/{track_id}/images/{shard}/{opaque_page_id}{source_suffix}"),
    }


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != sha256_file(source):
            raise ModernPackError(f"pack destination collision: {destination}")
        return
    shutil.copyfile(source, destination)


def _required_headline_tracks(suite: ModernSuiteSpec) -> tuple[str, ...]:
    expected = set(DEFAULT_HEADLINE_TRACKS)
    observed = {track_id for track_id, item in suite.tracks.items() if item.headline}
    if observed != expected:
        raise ModernPackError(
            "suite headline tracks differ from the five official Modern Hebrew tracks"
        )
    for track_id in DEFAULT_HEADLINE_TRACKS:
        if suite.tracks[track_id].maturity != "certified":
            raise ModernPackError(f"headline track {track_id} is not certified")
    return DEFAULT_HEADLINE_TRACKS


def _verify_manifest_inventory(root: Path, manifest: Mapping[str, Any], track_id: str) -> None:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ModernPackError(f"{track_id}.manifest has no frozen file inventory")
    for entry in files:
        if not isinstance(entry, Mapping):
            raise ModernPackError(f"{track_id}.manifest inventory entry is invalid")
        relative = _safe_relative(entry.get("path"), label=f"{track_id}.manifest path")
        path = root / relative
        if not path.is_file():
            raise ModernPackError(f"{track_id}.manifest file is missing: {relative}")
        if entry.get("size_bytes") != path.stat().st_size:
            raise ModernPackError(f"{track_id}.manifest size is stale: {relative}")
        if entry.get("sha256") != sha256_file(path):
            raise ModernPackError(f"{track_id}.manifest hash is stale: {relative}")


def _verify_track_root(
    root: Path, track_id: str, suite: ModernSuiteSpec
) -> list[dict[str, object]]:
    if not root.is_dir():
        raise ModernPackError(f"track root is not a directory: {track_id}={root}")
    required = (
        "manifest.json",
        "dataset.lock.json",
        "FROZEN.json",
        "CERTIFIED.json",
        "certification.json",
        "gold.jsonl",
    )
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise ModernPackError(
            f"{track_id} is not frozen and certified: missing {', '.join(missing)}"
        )

    manifest = _read_object(root / "manifest.json", f"{track_id}.manifest")
    dataset_lock = _read_object(root / "dataset.lock.json", f"{track_id}.dataset lock")
    frozen = _read_object(root / "FROZEN.json", f"{track_id}.FROZEN")
    certified = _read_object(root / "CERTIFIED.json", f"{track_id}.CERTIFIED")
    _verify_manifest_inventory(root, manifest, track_id)

    suite_track = suite.tracks[track_id]
    dataset_fingerprint = suite_track.dataset_fingerprint
    if manifest.get("track_id") != track_id:
        raise ModernPackError(f"{track_id}.manifest does not bind the track ID")
    if manifest.get("dataset_fingerprint") != dataset_fingerprint:
        raise ModernPackError(f"{track_id}.manifest differs from the suite dataset fingerprint")
    if any(
        marker.get("dataset_fingerprint") != dataset_fingerprint
        for marker in (dataset_lock, frozen, certified)
    ):
        raise ModernPackError(f"{track_id} dataset fingerprint differs across locked artifacts")
    if frozen.get("manifest_sha256") != sha256_file(root / "manifest.json"):
        raise ModernPackError(f"{track_id}.FROZEN does not bind manifest.json")
    if certified.get("certified") is not True:
        raise ModernPackError(f"{track_id}.CERTIFIED is not certified")
    if certified.get("certification_sha256") != suite_track.certification_sha256:
        raise ModernPackError(f"{track_id}.CERTIFIED differs from the suite certification")
    if sha256_file(root / "certification.json") != suite_track.certification_sha256:
        raise ModernPackError(f"{track_id}.certification.json differs from the suite lock")
    gold_path = root / "gold.jsonl"
    if sha256_file(gold_path) != suite_track.gold_sha256:
        raise ModernPackError(f"{track_id}.gold.jsonl differs from the suite lock")
    if dataset_lock.get("records_sha256") != suite_track.gold_sha256:
        raise ModernPackError(f"{track_id}.dataset.lock does not bind gold.jsonl")

    records = [dict(record) for record in load_jsonl(gold_path)]
    if not records:
        raise ModernPackError(f"{track_id}.gold.jsonl is empty")
    page_ids = [str(record.get("page_id", "")) for record in records]
    if any(not page_id for page_id in page_ids) or len(page_ids) != len(set(page_ids)):
        raise ModernPackError(f"{track_id}.gold.jsonl has missing or duplicate page IDs")
    if manifest.get("page_count") != len(records):
        raise ModernPackError(f"{track_id}.manifest page_count is stale")
    return sorted(records, key=lambda item: str(item["page_id"]))


def _id_pair(
    key: bytes,
    *,
    suite_fingerprint: str,
    track_id: str,
    kind: str,
    original: str,
    context: str = "",
) -> tuple[str, str]:
    if not original:
        raise ModernPackError(f"{track_id} has an empty {kind} identifier")
    value = f"{context}\0{original}" if context else original
    return (
        _opaque_identifier(
            key,
            suite_fingerprint=suite_fingerprint,
            track_id=track_id,
            kind=kind,
            value=value,
        ),
        original,
    )


def _page_aliases(
    record: Mapping[str, object], *, key: bytes, suite: ModernSuiteSpec, track_id: str
) -> dict[str, Any]:
    original_page = str(record.get("page_id", ""))
    original_document = str(record.get("document_id", ""))
    public_page, _ = _id_pair(
        key,
        suite_fingerprint=suite.suite_fingerprint,
        track_id=track_id,
        kind="page",
        original=original_page,
    )
    public_document, _ = _id_pair(
        key,
        suite_fingerprint=suite.suite_fingerprint,
        track_id=track_id,
        kind="document",
        original=original_document,
    )
    aliases: dict[str, dict[str, str]] = {
        "region": {},
        "line": {},
        "table": {},
        "field": {},
    }

    def add(kind: str, original: object) -> None:
        original_id = str(original or "")
        public, original_id = _id_pair(
            key,
            suite_fingerprint=suite.suite_fingerprint,
            track_id=track_id,
            kind=kind,
            original=original_id,
            context=original_page,
        )
        if public in aliases[kind] or original_id in aliases[kind].values():
            raise ModernPackError(
                f"{track_id}/{original_page} has duplicate {kind} identifier {original_id!r}"
            )
        aliases[kind][public] = original_id

    for region in record.get("regions", []):  # type: ignore[assignment]
        if not isinstance(region, Mapping):
            raise ModernPackError(f"{track_id}/{original_page} has an invalid region")
        add("region", region.get("region_id"))
        for line in region.get("lines", []):  # type: ignore[assignment]
            if not isinstance(line, Mapping):
                raise ModernPackError(f"{track_id}/{original_page} has an invalid line")
            add("line", line.get("line_id"))
    for table in record.get("tables", []):  # type: ignore[assignment]
        if isinstance(table, Mapping):
            add("table", table.get("table_id"))
    for field in record.get("form_fields", []):  # type: ignore[assignment]
        if isinstance(field, Mapping):
            add("field", field.get("field_id"))
    return {
        "public_page_id": public_page,
        "original_page_id": original_page,
        "public_document_id": public_document,
        "original_document_id": original_document,
        "identifiers": aliases,
    }


def _reverse(values: Mapping[str, str]) -> dict[str, str]:
    return {original: public for public, original in values.items()}


def _oracle_regions(
    record: Mapping[str, object], aliases: Mapping[str, Any]
) -> list[dict[str, object]]:
    identifiers = aliases["identifiers"]
    region_ids = _reverse(identifiers["region"])
    line_ids = _reverse(identifiers["line"])
    result: list[dict[str, object]] = []
    for raw_region in record.get("regions", []):  # type: ignore[assignment]
        if not isinstance(raw_region, Mapping):
            continue
        region: dict[str, object] = {
            "region_id": region_ids[str(raw_region["region_id"])],
            "type": deepcopy(raw_region.get("type", "text")),
            "polygon": deepcopy(raw_region.get("polygon", [])),
            "base_direction": deepcopy(raw_region.get("base_direction", "rtl")),
            "lines": [],
        }
        for field in ("language", "reading_index"):
            if field in raw_region:
                region[field] = deepcopy(raw_region[field])
        lines: list[dict[str, object]] = []
        for raw_line in raw_region.get("lines", []):  # type: ignore[assignment]
            if not isinstance(raw_line, Mapping):
                continue
            line = {
                "line_id": line_ids[str(raw_line["line_id"])],
                "polygon": deepcopy(raw_line.get("polygon", [])),
                "base_direction": deepcopy(raw_line.get("base_direction", "rtl")),
                "language": deepcopy(raw_line.get("language", "he")),
            }
            if "baseline" in raw_line:
                line["baseline"] = deepcopy(raw_line["baseline"])
            lines.append(line)
        region["lines"] = lines
        result.append(region)
    return result


def _oracle_reading_order(
    record: Mapping[str, object], aliases: Mapping[str, Any]
) -> dict[str, object]:
    raw = record.get("reading_order")
    if not isinstance(raw, Mapping):
        return {"edges": []}
    identifiers = aliases["identifiers"]
    node_map = {
        **_reverse(identifiers["line"]),
        **_reverse(identifiers["region"]),
    }
    result: dict[str, object] = {"edges": []}
    edges = []
    for edge in raw.get("edges", []):  # type: ignore[assignment]
        if isinstance(edge, list) and len(edge) == 2:
            left, right = str(edge[0]), str(edge[1])
            if left in node_map and right in node_map:
                edges.append([node_map[left], node_map[right]])
    result["edges"] = edges
    groups = []
    for group in raw.get("unordered_groups", []):  # type: ignore[assignment]
        if isinstance(group, list) and all(str(item) in node_map for item in group):
            groups.append([node_map[str(item)] for item in group])
    if groups:
        result["unordered_groups"] = groups
    return result


def _participant_record(
    record: Mapping[str, object],
    *,
    root: Path,
    participant: Path,
    suite: ModernSuiteSpec,
    track_id: str,
    aliases: Mapping[str, Any],
) -> dict[str, object]:
    raw_image = record.get("image")
    if not isinstance(raw_image, Mapping):
        raise ModernPackError(f"{track_id}/{record.get('page_id')}: image descriptor is missing")
    relative = _safe_relative(
        raw_image.get("path"), label=f"{track_id}/{record.get('page_id')} image path"
    )
    source = (root / relative).resolve()
    resolved_root = root.resolve()
    if source != resolved_root and resolved_root not in source.parents:
        raise ModernPackError(f"{track_id}/{record.get('page_id')}: image path escapes root")
    if not source.is_file():
        raise ModernPackError(f"{track_id}/{record.get('page_id')}: image is missing")
    actual_sha = sha256_file(source)
    if raw_image.get("sha256") != actual_sha:
        raise ModernPackError(f"{track_id}/{record.get('page_id')}: image SHA-256 mismatch")
    suffix = source.suffix.lower() or ".img"
    public_page_id = str(aliases["public_page_id"])
    image_shard = _participant_image_shard(public_page_id)
    public_image_path = (
        Path("tracks") / track_id / "images" / image_shard / f"{public_page_id}{suffix}"
    )
    _copy_file(source, participant / public_image_path)
    public_image: dict[str, object] = {
        "path": public_image_path.as_posix(),
        "sha256": actual_sha,
    }
    for field in ("width", "height", "rotation_degrees"):
        if field in raw_image:
            public_image[field] = deepcopy(raw_image[field])
    result: dict[str, object] = {
        "schema_version": "1.0",
        "suite_fingerprint": suite.suite_fingerprint,
        "track_id": track_id,
        "page_id": aliases["public_page_id"],
        "document_id": aliases["public_document_id"],
        "image": public_image,
    }
    if track_id in _ORACLE_TRACKS:
        result["regions"] = _oracle_regions(record, aliases)
        result["reading_order"] = _oracle_reading_order(record, aliases)
    return result


def _contains_key(value: object, keys: frozenset[str]) -> bool:
    if isinstance(value, Mapping):
        return any(str(key) in keys or _contains_key(child, keys) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_key(item, keys) for item in value)
    return False


def _strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _participant_identifier_values(item: Mapping[str, object]) -> set[str]:
    """Return only fields that carry participant-facing identifiers.

    Comparing original IDs with every string in an input record produces false
    positives for legitimate semantic values such as a table whose original ID
    is literally ``"table"``.  Participant records are constructed from a
    strict allowlist, so leakage verification should inspect the identifier
    slots themselves and require every one of them to be an opaque alias.
    """

    identifiers = {
        str(item.get("page_id", "")),
        str(item.get("document_id", "")),
    }
    for region in item.get("regions", []):  # type: ignore[assignment]
        if not isinstance(region, Mapping):
            continue
        identifiers.add(str(region.get("region_id", "")))
        for line in region.get("lines", []):  # type: ignore[assignment]
            if isinstance(line, Mapping):
                identifiers.add(str(line.get("line_id", "")))
    reading_order = item.get("reading_order")
    if isinstance(reading_order, Mapping):
        for edge in reading_order.get("edges", []):  # type: ignore[assignment]
            if isinstance(edge, list):
                identifiers.update(str(value) for value in edge)
        for group in reading_order.get("unordered_groups", []):  # type: ignore[assignment]
            if isinstance(group, list):
                identifiers.update(str(value) for value in group)
    return identifiers - {""}


def _participant_oracle_ids(item: Mapping[str, object]) -> dict[str, set[str]]:
    result = {"region": set(), "line": set()}
    for region in item.get("regions", []):  # type: ignore[assignment]
        if not isinstance(region, Mapping):
            continue
        region_id = str(region.get("region_id", ""))
        if region_id:
            result["region"].add(region_id)
        for line in region.get("lines", []):  # type: ignore[assignment]
            if isinstance(line, Mapping):
                line_id = str(line.get("line_id", ""))
                if line_id:
                    result["line"].add(line_id)
    return result


def _atomic_publish(temporary: Path, output: Path) -> None:
    if not output.exists():
        os.replace(temporary, output)
        return
    backup = output.parent / f".{output.name}.backup-{secrets.token_hex(8)}"
    os.replace(output, backup)
    try:
        os.replace(temporary, output)
    except Exception:
        os.replace(backup, output)
        raise
    shutil.rmtree(backup, ignore_errors=True)


def build_modern_suite_packs(
    suite_lock: str | Path | ModernSuiteSpec | Mapping[str, Any],
    track_roots: Mapping[str, str | Path],
    output_root: str | Path,
    *,
    id_key: bytes,
    overwrite: bool = False,
) -> ModernPackBuildResult:
    """Build bound participant/organizer packs for all five headline tracks."""

    if len(id_key) < 32:
        raise ModernPackError("identifier HMAC key must contain at least 32 bytes")
    suite = _suite(suite_lock)
    track_ids = _required_headline_tracks(suite)
    roots = {str(key): Path(value).resolve() for key, value in track_roots.items()}
    if set(roots) != set(track_ids):
        raise ModernPackError("track_roots must contain exactly the five headline track roots")
    records_by_track = {
        track_id: _verify_track_root(roots[track_id], track_id, suite) for track_id in track_ids
    }

    output = Path(output_root).absolute()
    if output.exists() and not overwrite:
        raise ModernPackError(f"suite pack output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.suite-packs-", dir=output.parent))
    participant = temporary / "participant"
    organizer = temporary / "organizer"
    participant.mkdir()
    organizer.mkdir()
    try:
        suite_payload = suite.to_dict()
        write_json(participant / "suite-lock.json", suite_payload)
        suite_lock_sha256 = sha256_file(participant / "suite-lock.json")
        track_manifest: dict[str, dict[str, object]] = {}
        total_pages = 0
        for track_id in track_ids:
            inputs: list[dict[str, object]] = []
            mappings: list[dict[str, object]] = []
            for record in records_by_track[track_id]:
                aliases = _page_aliases(record, key=id_key, suite=suite, track_id=track_id)
                inputs.append(
                    _participant_record(
                        record,
                        root=roots[track_id],
                        participant=participant,
                        suite=suite,
                        track_id=track_id,
                        aliases=aliases,
                    )
                )
                mappings.append(
                    {
                        "schema_version": "1.0",
                        "suite_fingerprint": suite.suite_fingerprint,
                        "track_id": track_id,
                        "dataset_fingerprint": suite.tracks[track_id].dataset_fingerprint,
                        **aliases,
                    }
                )
            inputs_path = participant / "tracks" / track_id / "inputs.jsonl"
            map_path = organizer / "maps" / f"{track_id}.jsonl"
            write_jsonl(inputs_path, inputs)
            write_jsonl(map_path, mappings)
            total_pages += len(inputs)
            image_shard_counts: dict[str, int] = {}
            for item in inputs:
                image = item.get("image")
                if not isinstance(image, Mapping) or not image.get("path"):
                    raise ModernPackError(f"{track_id}: built participant image is invalid")
                shard = Path(str(image["path"])).parent.name
                image_shard_counts[shard] = image_shard_counts.get(shard, 0) + 1
            track_manifest[track_id] = {
                "page_count": len(inputs),
                "image_count": len(inputs),
                "image_shard_count": len(image_shard_counts),
                "max_images_per_shard": max(image_shard_counts.values()),
                "dataset_fingerprint": suite.tracks[track_id].dataset_fingerprint,
                "gold_sha256": suite.tracks[track_id].gold_sha256,
                "inputs_sha256": sha256_file(inputs_path),
                "map_sha256": sha256_file(map_path),
            }

        write_json(
            participant / "PACK-MANIFEST.json",
            {
                "schema_version": "1.0",
                "benchmark": suite.benchmark,
                "benchmark_version": suite.benchmark_version,
                "role": "participant",
                "suite_version": suite.suite_version,
                "suite_fingerprint": suite.suite_fingerprint,
                "suite_lock_sha256": suite_lock_sha256,
                "identifier_scheme": "HMAC-SHA256-24hex-v1",
                "image_sharding": _image_sharding_manifest(),
                "track_count": len(track_ids),
                "page_count": total_pages,
                "tracks": {
                    key: {field: value for field, value in item.items() if field != "map_sha256"}
                    for key, item in sorted(track_manifest.items())
                },
                "contains_gold": False,
                "contains_original_identifiers": False,
                "contains_source_urls": False,
            },
        )
        (participant / "README.md").write_text(
            "# HebOCRBench Modern Hebrew participant pack\n\n"
            "Run each model on the five track input files. The organizer remaps opaque "
            "submission identifiers and scores against the unchanged certified references. "
            "Image files are distributed across deterministic two-hex SHA-256 shard "
            "directories for Hub-safe publication.\n",
            encoding="utf-8",
        )
        participant_fingerprint = _write_lock(
            participant,
            role="participant",
            suite_fingerprint=suite.suite_fingerprint,
            name="PACK-LOCK.json",
        )

        (organizer / "ID-KEY.bin").write_bytes(id_key)
        write_json(
            organizer / "ORGANIZER-MANIFEST.json",
            {
                "schema_version": "1.0",
                "benchmark": suite.benchmark,
                "benchmark_version": suite.benchmark_version,
                "role": "organizer",
                "suite_version": suite.suite_version,
                "suite_fingerprint": suite.suite_fingerprint,
                "participant_pack_fingerprint": participant_fingerprint,
                "identifier_key_sha256": hashlib.sha256(id_key).hexdigest(),
                "track_count": len(track_ids),
                "page_count": total_pages,
                "tracks": dict(sorted(track_manifest.items())),
                "gold_storage": "unchanged-certified-track-roots",
            },
        )
        (organizer / "README.md").write_text(
            "# HebOCRBench organizer map\n\n"
            "Keep this directory private. Remap submissions, then score against each "
            "suite-locked track root without copying or rewriting its gold file.\n",
            encoding="utf-8",
        )
        organizer_fingerprint = _write_lock(
            organizer,
            role="organizer",
            suite_fingerprint=suite.suite_fingerprint,
            name="ORGANIZER-LOCK.json",
        )

        report = verify_modern_suite_packs(
            participant,
            organizer,
            suite_lock=suite,
            track_roots=roots,
        )
        if not report.valid:
            raise ModernPackError("newly built Modern suite packs failed verification")
        _atomic_publish(temporary, output)
        return ModernPackBuildResult(
            participant_root=output / "participant",
            organizer_root=output / "organizer",
            suite_fingerprint=suite.suite_fingerprint,
            page_count=total_pages,
            participant_fingerprint=participant_fingerprint,
            organizer_fingerprint=organizer_fingerprint,
        )
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _verify_aliases(
    item: Mapping[str, Any], *, key: bytes, suite_fingerprint: str, track_id: str
) -> None:
    original_page = str(item.get("original_page_id", ""))
    original_document = str(item.get("original_document_id", ""))
    expected_page, _ = _id_pair(
        key,
        suite_fingerprint=suite_fingerprint,
        track_id=track_id,
        kind="page",
        original=original_page,
    )
    expected_document, _ = _id_pair(
        key,
        suite_fingerprint=suite_fingerprint,
        track_id=track_id,
        kind="document",
        original=original_document,
    )
    if item.get("public_page_id") != expected_page:
        raise ModernPackError(f"{track_id}: page identifier map is not HMAC-derived")
    if item.get("public_document_id") != expected_document:
        raise ModernPackError(f"{track_id}: document identifier map is not HMAC-derived")
    identifiers = item.get("identifiers")
    if not isinstance(identifiers, Mapping):
        raise ModernPackError(f"{track_id}: nested identifier map is missing")
    for kind in ("region", "line", "table", "field"):
        values = identifiers.get(kind)
        if not isinstance(values, Mapping):
            raise ModernPackError(f"{track_id}: {kind} identifier map is missing")
        for public, original in values.items():
            expected, _ = _id_pair(
                key,
                suite_fingerprint=suite_fingerprint,
                track_id=track_id,
                kind=kind,
                original=str(original),
                context=original_page,
            )
            if public != expected:
                raise ModernPackError(f"{track_id}: {kind} identifier map is not HMAC-derived")


def verify_modern_suite_packs(
    participant_root: str | Path,
    organizer_root: str | Path,
    *,
    suite_lock: str | Path | ModernSuiteSpec | Mapping[str, Any] | None = None,
    track_roots: Mapping[str, str | Path] | None = None,
) -> ModernPackVerificationReport:
    """Verify secrecy, HMAC aliases, byte inventories and optional source roots."""

    participant = Path(participant_root)
    organizer = Path(organizer_root)
    packed_suite = load_modern_suite_lock(participant / "suite-lock.json")
    suite = _suite(suite_lock) if suite_lock is not None else packed_suite
    if packed_suite.suite_fingerprint != suite.suite_fingerprint:
        raise ModernPackError("participant suite lock differs from the expected suite")
    track_ids = _required_headline_tracks(suite)
    participant_manifest = _read_object(participant / "PACK-MANIFEST.json", "participant manifest")
    organizer_manifest = _read_object(organizer / "ORGANIZER-MANIFEST.json", "organizer manifest")
    if participant_manifest.get("suite_fingerprint") != suite.suite_fingerprint:
        raise ModernPackError("participant manifest differs from the suite lock")
    if participant_manifest.get("suite_lock_sha256") != sha256_file(
        participant / "suite-lock.json"
    ):
        raise ModernPackError("participant manifest has a stale suite-lock hash")
    if participant_manifest.get("contains_gold") is not False:
        raise ModernPackError("participant manifest does not declare gold exclusion")
    if participant_manifest.get("identifier_scheme") != "HMAC-SHA256-24hex-v1":
        raise ModernPackError("participant manifest has an invalid identifier scheme")
    if participant_manifest.get("image_sharding") != _image_sharding_manifest():
        raise ModernPackError("participant manifest has an invalid image sharding contract")
    if any(path.name == "gold.jsonl" for path in participant.rglob("gold.jsonl")):
        raise ModernPackError("participant pack contains a gold.jsonl file")

    participant_fingerprint, participant_suite = _verify_lock(
        participant, role="participant", name="PACK-LOCK.json"
    )
    if participant_suite != suite.suite_fingerprint:
        raise ModernPackError("participant lock differs from the suite")
    if organizer_manifest.get("participant_pack_fingerprint") != participant_fingerprint:
        raise ModernPackError("organizer manifest is not bound to the participant pack")
    organizer_fingerprint, organizer_suite = _verify_lock(
        organizer, role="organizer", name="ORGANIZER-LOCK.json"
    )
    if organizer_suite != suite.suite_fingerprint:
        raise ModernPackError("organizer lock differs from the suite")
    if organizer_manifest.get("suite_fingerprint") != suite.suite_fingerprint:
        raise ModernPackError("organizer manifest differs from the suite")
    key_path = organizer / "ID-KEY.bin"
    if not key_path.is_file():
        raise ModernPackError("organizer identifier key is missing")
    key = key_path.read_bytes()
    if len(key) < 32 or hashlib.sha256(key).hexdigest() != organizer_manifest.get(
        "identifier_key_sha256"
    ):
        raise ModernPackError("organizer identifier key is missing or altered")

    roots: dict[str, Path] | None = None
    original_records: dict[str, list[dict[str, object]]] = {}
    if track_roots is not None:
        roots = {str(name): Path(path).resolve() for name, path in track_roots.items()}
        if set(roots) != set(track_ids):
            raise ModernPackError("track_roots must contain exactly the five headline roots")
        original_records = {
            track_id: _verify_track_root(roots[track_id], track_id, suite) for track_id in track_ids
        }

    total_pages = 0
    participant_tracks = participant_manifest.get("tracks")
    organizer_tracks = organizer_manifest.get("tracks")
    if not isinstance(participant_tracks, Mapping) or not isinstance(organizer_tracks, Mapping):
        raise ModernPackError("pack manifests have no per-track bindings")
    if (
        participant_manifest.get("track_count") != len(track_ids)
        or organizer_manifest.get("track_count") != len(track_ids)
        or set(participant_tracks) != set(track_ids)
        or set(organizer_tracks) != set(track_ids)
    ):
        raise ModernPackError("pack manifest track inventory differs from the suite")
    expected_participant_track_files: set[str] = set()
    expected_organizer_map_files: set[str] = set()
    for track_id in track_ids:
        inputs_path = participant / "tracks" / track_id / "inputs.jsonl"
        map_path = organizer / "maps" / f"{track_id}.jsonl"
        expected_participant_track_files.add(inputs_path.relative_to(participant).as_posix())
        expected_organizer_map_files.add(map_path.relative_to(organizer).as_posix())
        inputs = load_jsonl(inputs_path)
        mappings = load_jsonl(map_path)
        if not inputs or len(inputs) != len(mappings):
            raise ModernPackError(f"{track_id}: participant inputs and organizer map differ")
        participant_track = participant_tracks.get(track_id)
        organizer_track = organizer_tracks.get(track_id)
        if not isinstance(participant_track, Mapping) or not isinstance(organizer_track, Mapping):
            raise ModernPackError(f"{track_id}: pack manifest binding is missing")
        expected_track = suite.tracks[track_id]
        inputs_sha256 = sha256_file(inputs_path)
        if any(
            item.get("inputs_sha256") != inputs_sha256
            for item in (participant_track, organizer_track)
        ):
            raise ModernPackError(f"{track_id}: participant inputs hash is stale")
        if organizer_track.get("map_sha256") != sha256_file(map_path):
            raise ModernPackError(f"{track_id}: organizer map hash is stale")
        for item in (participant_track, organizer_track):
            if item.get("page_count") != len(inputs):
                raise ModernPackError(f"{track_id}: pack manifest page count is stale")
            if item.get("dataset_fingerprint") != expected_track.dataset_fingerprint:
                raise ModernPackError(f"{track_id}: pack manifest dataset binding is stale")
            if item.get("gold_sha256") != expected_track.gold_sha256:
                raise ModernPackError(f"{track_id}: pack manifest gold binding is stale")
        public_ids = [str(item.get("page_id", "")) for item in inputs]
        mapped_ids = [str(item.get("public_page_id", "")) for item in mappings]
        if public_ids != mapped_ids or len(public_ids) != len(set(public_ids)):
            raise ModernPackError(f"{track_id}: public page identifiers are missing or duplicate")
        if not all(_OPAQUE_ID.fullmatch(value) for value in public_ids):
            raise ModernPackError(f"{track_id}: participant page identifiers are not opaque")
        if any(_contains_key(item, _GOLD_TEXT_KEYS) for item in inputs):
            raise ModernPackError(f"{track_id}: gold text key leaked into participant inputs")
        if any(_URL.search(value) for item in inputs for value in _strings(item)):
            raise ModernPackError(f"{track_id}: URL leaked into participant inputs")

        original_identifiers: set[str] = set()
        for item in mappings:
            if item.get("track_id") != track_id:
                raise ModernPackError(f"{track_id}: organizer map has the wrong track ID")
            if item.get("suite_fingerprint") != suite.suite_fingerprint:
                raise ModernPackError(f"{track_id}: organizer map differs from the suite")
            if item.get("dataset_fingerprint") != suite.tracks[track_id].dataset_fingerprint:
                raise ModernPackError(f"{track_id}: organizer map differs from the track root")
            _verify_aliases(
                item,
                key=key,
                suite_fingerprint=suite.suite_fingerprint,
                track_id=track_id,
            )
            original_identifiers.update(
                {
                    str(item.get("original_page_id", "")),
                    str(item.get("original_document_id", "")),
                }
            )
            nested = item["identifiers"]
            for kind in ("region", "line", "table", "field"):
                original_identifiers.update(str(value) for value in nested[kind].values())

        for participant_input, mapping in zip(inputs, mappings, strict=True):
            if participant_input.get("document_id") != mapping.get("public_document_id"):
                raise ModernPackError(
                    f"{track_id}: participant document identifier differs from organizer map"
                )
            exposed = _participant_oracle_ids(participant_input)
            mapped = mapping["identifiers"]
            for kind in ("region", "line"):
                missing = exposed[kind] - set(mapped[kind])
                if missing:
                    raise ModernPackError(
                        f"{track_id}: organizer map omits a participant {kind} identifier"
                    )

        participant_identifiers = {
            value for item in inputs for value in _participant_identifier_values(item)
        }
        if not all(_OPAQUE_ID.fullmatch(value) for value in participant_identifiers):
            raise ModernPackError(f"{track_id}: participant layout identifier is not opaque")
        leaked = sorted((original_identifiers - {""}) & participant_identifiers)
        if leaked:
            raise ModernPackError(f"{track_id}: original identifier leaked: {leaked[0]}")
        referenced_images: set[str] = set()
        image_shard_counts: dict[str, int] = {}
        for item in inputs:
            if item.get("track_id") != track_id:
                raise ModernPackError(f"{track_id}: participant input has the wrong track ID")
            image = item.get("image")
            if not isinstance(image, Mapping):
                raise ModernPackError(f"{track_id}: participant input has no image")
            relative = _safe_relative(image.get("path"), label=f"{track_id} participant image")
            expected_prefix = Path("tracks") / track_id / "images"
            public_page_id = str(item.get("page_id", ""))
            expected_shard = _participant_image_shard(public_page_id)
            if (
                relative.parent.parent != expected_prefix
                or not _IMAGE_SHARD.fullmatch(relative.parent.name)
                or relative.parent.name != expected_shard
                or relative.stem != public_page_id
                or not relative.suffix
            ):
                raise ModernPackError(f"{track_id}: participant image path is not anonymous")
            image_path = participant / relative
            if not image_path.is_file() or image.get("sha256") != sha256_file(image_path):
                raise ModernPackError(f"{track_id}: participant image is missing or altered")
            relative_text = relative.as_posix()
            if relative_text in referenced_images:
                raise ModernPackError(f"{track_id}: participant image path is duplicated")
            referenced_images.add(relative_text)
            expected_participant_track_files.add(relative_text)
            image_shard_counts[expected_shard] = image_shard_counts.get(expected_shard, 0) + 1

        image_root = participant / "tracks" / track_id / "images"
        actual_images = {
            path.relative_to(participant).as_posix()
            for path in image_root.rglob("*")
            if path.is_file()
        }
        if actual_images != referenced_images:
            raise ModernPackError(f"{track_id}: participant image inventory differs from inputs")
        image_stats = {
            "image_count": len(referenced_images),
            "image_shard_count": len(image_shard_counts),
            "max_images_per_shard": max(image_shard_counts.values()),
        }
        if image_stats["max_images_per_shard"] > _MAX_FILES_PER_IMAGE_DIRECTORY:
            raise ModernPackError(f"{track_id}: participant image shard exceeds Hub file limit")
        for item in (participant_track, organizer_track):
            if any(item.get(field) != value for field, value in image_stats.items()):
                raise ModernPackError(f"{track_id}: pack manifest image sharding is stale")

        if roots is not None:
            expected_original_ids = [str(item["page_id"]) for item in original_records[track_id]]
            observed_original_ids = [str(item.get("original_page_id", "")) for item in mappings]
            if observed_original_ids != expected_original_ids:
                raise ModernPackError(f"{track_id}: organizer map differs from original gold IDs")
            for participant_input, mapping, original_record in zip(
                inputs, mappings, original_records[track_id], strict=True
            ):
                expected_aliases = _page_aliases(
                    original_record,
                    key=key,
                    suite=suite,
                    track_id=track_id,
                )
                observed_aliases = {
                    field: mapping.get(field)
                    for field in (
                        "public_page_id",
                        "original_page_id",
                        "public_document_id",
                        "original_document_id",
                        "identifiers",
                    )
                }
                if observed_aliases != expected_aliases:
                    raise ModernPackError(
                        f"{track_id}: organizer aliases differ from certified root"
                    )
                expected_input_fields = {
                    "schema_version",
                    "suite_fingerprint",
                    "track_id",
                    "page_id",
                    "document_id",
                    "image",
                }
                if track_id in _ORACLE_TRACKS:
                    expected_input_fields.update({"regions", "reading_order"})
                if set(participant_input) != expected_input_fields:
                    raise ModernPackError(
                        f"{track_id}: participant input fields differ from certified contract"
                    )
                if (
                    participant_input.get("schema_version") != "1.0"
                    or participant_input.get("suite_fingerprint") != suite.suite_fingerprint
                    or participant_input.get("track_id") != track_id
                    or participant_input.get("page_id") != expected_aliases["public_page_id"]
                    or participant_input.get("document_id")
                    != expected_aliases["public_document_id"]
                ):
                    raise ModernPackError(
                        f"{track_id}: participant input identity differs from certified root"
                    )
                if track_id in _ORACLE_TRACKS and (
                    participant_input.get("regions")
                    != _oracle_regions(original_record, expected_aliases)
                    or participant_input.get("reading_order")
                    != _oracle_reading_order(original_record, expected_aliases)
                ):
                    raise ModernPackError(
                        f"{track_id}: participant oracle layout differs from certified root"
                    )
                original_image = original_record.get("image")
                participant_image = participant_input.get("image")
                if not isinstance(original_image, Mapping) or not isinstance(
                    participant_image, Mapping
                ):
                    raise ModernPackError(f"{track_id}: participant image binding is incomplete")
                original_relative = _safe_relative(
                    original_image.get("path"), label=f"{track_id} original image"
                )
                participant_relative = _safe_relative(
                    participant_image.get("path"), label=f"{track_id} participant image"
                )
                expected_image = {"sha256": original_image.get("sha256")}
                for field in ("width", "height", "rotation_degrees"):
                    if field in original_image:
                        expected_image[field] = original_image[field]
                observed_image = {
                    str(field): value
                    for field, value in participant_image.items()
                    if field != "path"
                }
                expected_suffix = original_relative.suffix.lower() or ".img"
                if (
                    observed_image != expected_image
                    or participant_relative.suffix != expected_suffix
                ):
                    raise ModernPackError(
                        f"{track_id}: participant image differs from certified root"
                    )
        total_pages += len(inputs)

    actual_participant_track_files = {
        path.relative_to(participant).as_posix()
        for path in (participant / "tracks").rglob("*")
        if path.is_file()
    }
    if actual_participant_track_files != expected_participant_track_files:
        raise ModernPackError("participant track file inventory differs from inputs")
    actual_organizer_map_files = {
        path.relative_to(organizer).as_posix()
        for path in (organizer / "maps").rglob("*")
        if path.is_file()
    }
    if actual_organizer_map_files != expected_organizer_map_files:
        raise ModernPackError("organizer map file inventory differs from manifests")
    expected_participant_files = expected_participant_track_files | {
        "PACK-LOCK.json",
        "PACK-MANIFEST.json",
        "README.md",
        "suite-lock.json",
    }
    actual_participant_files = {
        path.relative_to(participant).as_posix()
        for path in participant.rglob("*")
        if path.is_file()
    }
    if actual_participant_files != expected_participant_files:
        raise ModernPackError("participant pack contains an unexpected file")
    expected_organizer_files = expected_organizer_map_files | {
        "ID-KEY.bin",
        "ORGANIZER-LOCK.json",
        "ORGANIZER-MANIFEST.json",
        "README.md",
    }
    actual_organizer_files = {
        path.relative_to(organizer).as_posix() for path in organizer.rglob("*") if path.is_file()
    }
    if actual_organizer_files != expected_organizer_files:
        raise ModernPackError("organizer pack contains an unexpected file")
    if participant_manifest.get("page_count") != total_pages:
        raise ModernPackError("participant manifest page count is stale")
    if organizer_manifest.get("page_count") != total_pages:
        raise ModernPackError("organizer manifest page count is stale")
    return ModernPackVerificationReport(
        valid=True,
        suite_fingerprint=suite.suite_fingerprint,
        page_count=total_pages,
        participant_fingerprint=participant_fingerprint,
        organizer_fingerprint=organizer_fingerprint,
        checks={
            "five_headline_tracks": True,
            "frozen_certified_roots": roots is not None,
            "no_gold_text": True,
            "no_original_identifiers": True,
            "no_source_urls": True,
            "hmac_identifiers": True,
            "two_hex_image_sharding": True,
            "participant_inventory": True,
            "organizer_inventory": True,
            "suite_binding": True,
        },
    )


def _prediction_records(
    value: str | Path | Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    if isinstance(value, (str, Path)):
        return [dict(item) for item in load_jsonl(value)]
    return [dict(item) for item in value]


def _remap_layout_ids(
    record: dict[str, object],
    mapping: Mapping[str, Any],
    *,
    require_known_region_line_ids: bool,
) -> None:
    identifiers = mapping["identifiers"]
    region_ids = identifiers["region"]
    line_ids = identifiers["line"]
    table_ids = identifiers["table"]
    field_ids = identifiers["field"]

    def replace(
        value: object, values: Mapping[str, object], kind: str, *, required: bool
    ) -> object:
        identifier = str(value or "")
        if identifier in values:
            return values[identifier]
        if required and identifier:
            raise ModernPackError(f"prediction {kind} ID is not in the organizer map")
        return value

    for region in record.get("regions", []):  # type: ignore[assignment]
        if not isinstance(region, dict):
            continue
        region["region_id"] = replace(
            region.get("region_id"),
            region_ids,
            "region",
            required=require_known_region_line_ids,
        )
        for line in region.get("lines", []):  # type: ignore[assignment]
            if isinstance(line, dict):
                line["line_id"] = replace(
                    line.get("line_id"),
                    line_ids,
                    "line",
                    required=require_known_region_line_ids,
                )
    for table in record.get("tables", []):  # type: ignore[assignment]
        if not isinstance(table, dict):
            continue
        table["table_id"] = replace(table.get("table_id"), table_ids, "table", required=False)
        table["region_id"] = replace(
            table.get("region_id"),
            region_ids,
            "table region",
            required=require_known_region_line_ids,
        )
    for field in record.get("form_fields", []):  # type: ignore[assignment]
        if not isinstance(field, dict):
            continue
        field["field_id"] = replace(field.get("field_id"), field_ids, "field", required=False)
        for key in ("label_region_id", "value_region_id"):
            field[key] = replace(
                field.get(key),
                region_ids,
                "field region",
                required=require_known_region_line_ids,
            )
    reading_order = record.get("reading_order")
    if isinstance(reading_order, dict):
        node_ids = {**line_ids, **region_ids}
        edges = reading_order.get("edges")
        if isinstance(edges, list):
            reading_order["edges"] = [
                [
                    replace(
                        edge[0],
                        node_ids,
                        "reading-order node",
                        required=require_known_region_line_ids,
                    ),
                    replace(
                        edge[1],
                        node_ids,
                        "reading-order node",
                        required=require_known_region_line_ids,
                    ),
                ]
                if isinstance(edge, list) and len(edge) == 2
                else edge
                for edge in edges
            ]
        groups = reading_order.get("unordered_groups")
        if isinstance(groups, list):
            reading_order["unordered_groups"] = [
                [
                    replace(
                        item,
                        node_ids,
                        "reading-order node",
                        required=require_known_region_line_ids,
                    )
                    for item in group
                ]
                if isinstance(group, list)
                else group
                for group in groups
            ]


def _atomic_write_jsonl(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        write_jsonl(temporary, records)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def remap_pack_predictions(
    predictions: str | Path | Sequence[Mapping[str, object]],
    organizer_root: str | Path,
    *,
    track_id: str | None = None,
    output_path: str | Path | None = None,
    require_complete: bool = True,
) -> list[dict[str, object]]:
    """Map opaque submission IDs back for scoring against unchanged root gold.

    A single-track submission may omit ``track_id`` when the caller supplies it.
    Mixed-suite submissions must carry ``track_id`` on every prediction.
    """

    organizer = Path(organizer_root)
    manifest = _read_object(organizer / "ORGANIZER-MANIFEST.json", "organizer manifest")
    _, locked_suite_fingerprint = _verify_lock(
        organizer, role="organizer", name="ORGANIZER-LOCK.json"
    )
    suite_fingerprint = str(manifest.get("suite_fingerprint", ""))
    if suite_fingerprint != locked_suite_fingerprint:
        raise ModernPackError("organizer manifest and lock bind different suites")
    key_path = organizer / "ID-KEY.bin"
    if not key_path.is_file():
        raise ModernPackError("organizer identifier key is missing")
    key = key_path.read_bytes()
    if len(key) < 32 or hashlib.sha256(key).hexdigest() != manifest.get("identifier_key_sha256"):
        raise ModernPackError("organizer identifier key is missing or altered")
    manifest_tracks = manifest.get("tracks")
    if not isinstance(manifest_tracks, Mapping):
        raise ModernPackError("organizer manifest has no per-track bindings")
    allowed_tracks = set(DEFAULT_HEADLINE_TRACKS)
    if manifest.get("track_count") != len(allowed_tracks) or set(manifest_tracks) != allowed_tracks:
        raise ModernPackError("organizer manifest track inventory is invalid")
    declared_page_count = 0
    for declared_track in allowed_tracks:
        declared = manifest_tracks[declared_track]
        count = declared.get("page_count") if isinstance(declared, Mapping) else None
        if not isinstance(count, int) or count < 1:
            raise ModernPackError("organizer manifest page counts are invalid")
        declared_page_count += count
    if manifest.get("page_count") != declared_page_count:
        raise ModernPackError("organizer manifest total page count is stale")
    records = _prediction_records(predictions)
    if not records:
        raise ModernPackError("prediction submission is empty")
    maps: dict[str, dict[str, Mapping[str, Any]]] = {}

    def mapping_for(selected_track: str) -> dict[str, Mapping[str, Any]]:
        if selected_track not in allowed_tracks:
            raise ModernPackError(f"prediction uses unknown headline track {selected_track!r}")
        if selected_track not in maps:
            map_path = organizer / "maps" / f"{selected_track}.jsonl"
            track_manifest = manifest_tracks.get(selected_track)
            if not isinstance(track_manifest, Mapping):
                raise ModernPackError(f"{selected_track}: organizer manifest binding is missing")
            if track_manifest.get("map_sha256") != sha256_file(map_path):
                raise ModernPackError(f"{selected_track}: organizer map hash is stale")
            items = load_jsonl(map_path)
            if not items or len(items) != track_manifest.get("page_count"):
                raise ModernPackError(f"{selected_track}: organizer map page count is stale")
            indexed = {str(item.get("public_page_id", "")): item for item in items}
            if len(indexed) != len(items):
                raise ModernPackError(f"{selected_track}: organizer map has duplicate page IDs")
            for item in items:
                if item.get("track_id") != selected_track:
                    raise ModernPackError(f"{selected_track}: organizer map has the wrong track ID")
                if item.get("suite_fingerprint") != suite_fingerprint:
                    raise ModernPackError(f"{selected_track}: organizer map differs from suite")
                if item.get("dataset_fingerprint") != track_manifest.get("dataset_fingerprint"):
                    raise ModernPackError(f"{selected_track}: organizer map differs from manifest")
                _verify_aliases(
                    item,
                    key=key,
                    suite_fingerprint=suite_fingerprint,
                    track_id=selected_track,
                )
            maps[selected_track] = indexed
        return maps[selected_track]

    remapped: list[dict[str, object]] = []
    seen: dict[str, set[str]] = {}
    for position, raw in enumerate(records):
        selected_track = track_id or str(raw.get("track_id", ""))
        if not selected_track:
            raise ModernPackError(
                f"prediction {position} has no track_id and no track_id argument was supplied"
            )
        if track_id is not None and raw.get("track_id") not in (None, track_id):
            raise ModernPackError(f"prediction {position} conflicts with track_id={track_id}")
        public_page_id = str(raw.get("page_id", ""))
        mapping = mapping_for(selected_track).get(public_page_id)
        if mapping is None:
            raise ModernPackError(
                f"{selected_track}: prediction page ID is not in the organizer map: {public_page_id}"
            )
        if public_page_id in seen.setdefault(selected_track, set()):
            raise ModernPackError(f"{selected_track}: duplicate prediction for {public_page_id}")
        seen[selected_track].add(public_page_id)
        output = deepcopy(raw)
        output["page_id"] = mapping["original_page_id"]
        if "document_id" in output:
            if output["document_id"] != mapping["public_document_id"]:
                raise ModernPackError(
                    f"{selected_track}: prediction document ID differs from organizer map"
                )
            output["document_id"] = mapping["original_document_id"]
        _remap_layout_ids(
            output,
            mapping,
            require_known_region_line_ids=selected_track in _ORACLE_TRACKS,
        )
        remapped.append(output)

    if require_complete:
        expected_tracks = {track_id} if track_id is not None else set(maps)
        for selected_track in expected_tracks:
            expected_ids = set(mapping_for(selected_track))
            if seen.get(selected_track, set()) != expected_ids:
                missing = len(expected_ids - seen.get(selected_track, set()))
                raise ModernPackError(
                    f"{selected_track}: submission is incomplete ({missing} pages missing)"
                )
    if output_path is not None:
        _atomic_write_jsonl(Path(output_path), remapped)
    return remapped


__all__ = [
    "ModernPackBuildResult",
    "ModernPackError",
    "ModernPackVerificationReport",
    "build_modern_suite_packs",
    "remap_pack_predictions",
    "verify_modern_suite_packs",
]
