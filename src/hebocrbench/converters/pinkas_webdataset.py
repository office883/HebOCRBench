"""Locked, fail-closed materialization of the real Pinkas handwriting holdout."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile
from typing import Mapping
import unicodedata

from PIL import Image

from . import ConversionContext


PINKAS_TEST_ARCHIVE_SHA256 = "d986a3527d1ddae19cf2f09f3ff5e84458eeb5e1f6f9cb4e2a48d895dfcd5eb6"
PINKAS_SOURCE_DATASET = "zenodo/pinkas_dataset"
PINKAS_TEST_PAGE_COUNTS = (
    ("Page 132_1.xml", 38),
    ("Page 132_2.xml", 52),
    ("Page 133_1.xml", 48),
    ("Page 133_2.xml", 42),
    ("Page 134_1.xml", 41),
    ("Page 134_2.xml", 45),
)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
_MAX_JSON_BYTES = 1024 * 1024
_MAX_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_MEMBERS = 10_000
_MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
_PROVENANCE_FIELDS = (
    "curated_config",
    "curated_granularity",
    "curation_group_id",
    "curation_reason",
    "granularity",
    "image_kind",
    "language",
    "modality",
    "original_split",
    "quality_tier",
    "recommended",
    "rights_status",
    "rtl_text_order",
    "script",
    "source_bbox_xyxy",
    "source_dataset",
    "source_image",
    "source_license",
    "source_line_id",
    "source_page",
    "source_raw_text_order",
    "source_split",
    "source_url",
    "split",
    "standalone_policy",
    "standalone_selected",
    "text_correction",
    "text_sha256",
)


@dataclass(frozen=True, slots=True)
class PinkasWebDatasetPolicy:
    """Immutable identity and coverage requirements for one locked TAR."""

    archive_sha256: str
    expected_page_counts: tuple[tuple[str, int], ...]
    expected_records: int
    source_dataset: str = PINKAS_SOURCE_DATASET


PINKAS_TEST_POLICY = PinkasWebDatasetPolicy(
    archive_sha256=PINKAS_TEST_ARCHIVE_SHA256,
    expected_page_counts=PINKAS_TEST_PAGE_COUNTS,
    expected_records=266,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_name(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Pinkas TAR contains an unsafe member path: {name!r}")
    return path


def _safe_token(value: object) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._")
    return token or "item"


def _member_bytes(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    maximum: int,
) -> bytes:
    if member.size < 0 or member.size > maximum:
        raise ValueError(f"Pinkas TAR member {member.name!r} has forbidden size {member.size}")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"Pinkas TAR member cannot be read: {member.name!r}")
    with stream:
        data = stream.read(maximum + 1)
    if len(data) != member.size or len(data) > maximum:
        raise ValueError(f"Pinkas TAR member size mismatch: {member.name!r}")
    return data


def _tar_members(
    archive: tarfile.TarFile,
) -> tuple[dict[str, tarfile.TarInfo], dict[str, tarfile.TarInfo]]:
    members = archive.getmembers()
    if len(members) > _MAX_MEMBERS:
        raise ValueError(f"Pinkas TAR has too many members: {len(members)}")
    total_size = 0
    names: set[str] = set()
    json_members: dict[str, tarfile.TarInfo] = {}
    image_members: dict[str, tarfile.TarInfo] = {}
    for member in members:
        safe_name = _safe_member_name(member.name)
        normalized = safe_name.as_posix()
        if normalized in names:
            raise ValueError(f"Pinkas TAR contains a duplicate member: {normalized}")
        names.add(normalized)
        if member.isdir():
            continue
        if not member.isfile():
            raise ValueError(f"Pinkas TAR contains a non-regular member: {normalized}")
        total_size += max(member.size, 0)
        if total_size > _MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError("Pinkas TAR exceeds the allowed aggregate member size")
        suffix = safe_name.suffix.lower()
        key = safe_name.with_suffix("").as_posix()
        if suffix == ".json":
            if key in json_members:
                raise ValueError(f"Pinkas TAR contains duplicate JSON sample key: {key}")
            json_members[key] = member
        elif suffix in _IMAGE_SUFFIXES:
            if key in image_members:
                raise ValueError(f"Pinkas TAR contains duplicate image sample key: {key}")
            image_members[key] = member
        else:
            raise ValueError(f"Pinkas TAR contains an unsupported member: {normalized}")
    return json_members, image_members


def _metadata_object(data: bytes, member_name: str) -> Mapping[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid Pinkas JSON member {member_name!r}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"Pinkas JSON member is not an object: {member_name!r}")
    return value


def _required_text(metadata: Mapping[str, object], key: str, item_id: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Pinkas sample {item_id!r} has no nonempty {key}")
    return value.strip()


def _validate_selected_metadata(
    metadata: Mapping[str, object],
    *,
    sample_key: str,
    policy: PinkasWebDatasetPolicy,
) -> tuple[str, str, str]:
    item_id = _required_text(metadata, "id", sample_key)
    if item_id != PurePosixPath(sample_key).name:
        raise ValueError(f"Pinkas sample ID/member mismatch: id={item_id!r}, member={sample_key!r}")
    expected_values = {
        "source_dataset": policy.source_dataset,
        "source_license": "cc-by-4.0",
        "rights_status": "explicit_open",
        "language": "he",
        "script": "Hebr",
        "curated_config": "historical_handwriting_lines",
        "modality": "historical_hebrew_handwritten_line",
        "granularity": "line",
        "split": "test",
        "source_split": "test",
    }
    for key, expected in expected_values.items():
        if metadata.get(key) != expected:
            raise ValueError(
                f"Pinkas sample {item_id!r} has {key}={metadata.get(key)!r}, expected {expected!r}"
            )
    raw_text = metadata.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError(f"Pinkas sample {item_id!r} has no nonempty text")
    text_digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    if metadata.get("text_sha256") != text_digest:
        raise ValueError(f"Pinkas sample {item_id!r} text SHA-256 mismatch")
    text = unicodedata.normalize("NFC", raw_text)
    source_page = _required_text(metadata, "source_page", item_id)
    _required_text(metadata, "source_line_id", item_id)
    return item_id, text, source_page


def _validate_image(
    data: bytes,
    metadata: Mapping[str, object],
    *,
    item_id: str,
) -> tuple[str, int, int]:
    digest = hashlib.sha256(data).hexdigest()
    if metadata.get("image_sha256") != digest:
        raise ValueError(f"Pinkas sample {item_id!r} image SHA-256 mismatch")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except (OSError, ValueError) as exc:
        raise ValueError(f"Pinkas sample {item_id!r} has an invalid image: {exc}") from exc
    if metadata.get("image_width") != width or metadata.get("image_height") != height:
        raise ValueError(f"Pinkas sample {item_id!r} image dimensions do not match metadata")
    return digest, width, height


def _write_image(
    data: bytes,
    *,
    digest: str,
    item_id: str,
    suffix: str,
    build_root: Path,
    source_id: str,
) -> Path:
    normalized_suffix = ".jpg" if suffix.lower() == ".jpeg" else suffix.lower()
    relative = (
        Path("images")
        / _safe_token(source_id)
        / f"{digest[:20]}-{_safe_token(item_id)}{normalized_suffix}"
    )
    destination = build_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _sha256_file(destination) != digest:
            raise ValueError(f"Pinkas image destination collision: {relative.as_posix()}")
        return relative
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return relative


def _record(
    metadata: Mapping[str, object],
    *,
    item_id: str,
    text: str,
    source_page: str,
    image_path: Path,
    image_digest: str,
    width: int,
    height: int,
    archive_relative: str,
    archive_sha256: str,
    json_member: str,
    image_member: str,
    context: ConversionContext,
) -> dict[str, object]:
    polygon = [[0, 0], [width, 0], [width, height], [0, height]]
    record_metadata = context.metadata(annotation_path=archive_relative)
    record_metadata.update(
        {key: metadata[key] for key in _PROVENANCE_FIELDS if metadata.get(key) is not None}
    )
    record_metadata.update(
        {
            "source_item_id": item_id,
            "source_page_id": source_page,
            "source_archive_sha256": archive_sha256,
            "source_json_member": json_member,
            "source_image_member": image_member,
            "benchmark_data_status": "real-public-fixed",
            "coverage_scope": "narrow-single-collection",
            "split_isolation": "page-disjoint-from-cached-train",
            "writer_disjoint": False,
            "writer_identity_available": False,
            "document_id_method": "pinkas_source_page_v1",
        }
    )
    return {
        "schema_version": "1.0",
        "page_id": f"{context.source_id}-{_safe_token(item_id)}",
        "document_id": f"{context.source_id}-{_safe_token(source_page)}",
        "split": context.split,
        "track": context.track,
        "image": {
            "path": image_path.as_posix(),
            "width": width,
            "height": height,
            "rotation_degrees": 0,
            "sha256": image_digest,
        },
        "metadata": record_metadata,
        "regions": [
            {
                "region_id": "content",
                "type": "text",
                "polygon": polygon,
                "base_direction": "rtl",
                "language": "he",
                "reading_index": 0,
                "lines": [
                    {
                        "line_id": "content-line",
                        "polygon": polygon,
                        "text": text,
                        "base_direction": "rtl",
                        "language": "he",
                        "reading_index": 0,
                    }
                ],
            }
        ],
        "reading_order": {"edges": [], "unordered_groups": []},
        "tables": [],
        "form_fields": [],
    }


def convert_pinkas_webdataset_tar(
    archive_path: str | Path,
    source_root: str | Path,
    build_root: str | Path,
    context: ConversionContext,
    *,
    policy: PinkasWebDatasetPolicy = PINKAS_TEST_POLICY,
) -> list[dict[str, object]]:
    """Materialize only the locked 266-line Pinkas subset from its mixed TAR.

    The TAR is never extracted. Every member is inspected as an in-memory stream,
    all non-Pinkas records are ignored, and the exact archive hash, page
    distribution, sample count, image hashes, dimensions and text hashes must
    match before any returned record can be certified.
    """

    archive_path = Path(archive_path).resolve()
    source_root = Path(source_root).resolve()
    build_root = Path(build_root).resolve()
    if archive_path != source_root and source_root not in archive_path.parents:
        raise ValueError("Pinkas TAR path escapes the source root")
    expected_sha256 = policy.archive_sha256.lower()
    if len(expected_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha256):
        raise ValueError("Pinkas policy archive SHA-256 is invalid")
    actual_sha256 = _sha256_file(archive_path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"Pinkas TAR SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    archive_relative = archive_path.relative_to(source_root).as_posix()
    records: list[dict[str, object]] = []
    page_counts: Counter[str] = Counter()
    item_ids: set[str] = set()
    image_hashes: set[str] = set()
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            json_members, image_members = _tar_members(archive)
            for sample_key in sorted(json_members):
                json_member = json_members[sample_key]
                metadata = _metadata_object(
                    _member_bytes(archive, json_member, maximum=_MAX_JSON_BYTES),
                    json_member.name,
                )
                if metadata.get("source_dataset") != policy.source_dataset:
                    continue
                item_id, text, source_page = _validate_selected_metadata(
                    metadata,
                    sample_key=sample_key,
                    policy=policy,
                )
                if item_id in item_ids:
                    raise ValueError(f"Duplicate Pinkas item ID: {item_id}")
                image_member_info = image_members.get(sample_key)
                if image_member_info is None:
                    raise ValueError(f"Pinkas sample {item_id!r} has no paired image member")
                image_data = _member_bytes(
                    archive,
                    image_member_info,
                    maximum=_MAX_IMAGE_BYTES,
                )
                image_digest, width, height = _validate_image(
                    image_data,
                    metadata,
                    item_id=item_id,
                )
                if image_digest in image_hashes:
                    raise ValueError(f"Duplicate Pinkas image SHA-256: {image_digest}")
                relative_image = _write_image(
                    image_data,
                    digest=image_digest,
                    item_id=item_id,
                    suffix=PurePosixPath(image_member_info.name).suffix,
                    build_root=build_root,
                    source_id=context.source_id,
                )
                records.append(
                    _record(
                        metadata,
                        item_id=item_id,
                        text=text,
                        source_page=source_page,
                        image_path=relative_image,
                        image_digest=image_digest,
                        width=width,
                        height=height,
                        archive_relative=archive_relative,
                        archive_sha256=actual_sha256,
                        json_member=json_member.name,
                        image_member=image_member_info.name,
                        context=context,
                    )
                )
                item_ids.add(item_id)
                image_hashes.add(image_digest)
                page_counts[source_page] += 1
    except (OSError, tarfile.TarError) as exc:
        raise ValueError(f"Cannot read locked Pinkas TAR {archive_path}: {exc}") from exc

    expected_pages = dict(policy.expected_page_counts)
    if len(records) != policy.expected_records:
        raise ValueError(
            f"Locked Pinkas subset count mismatch: expected {policy.expected_records}, "
            f"got {len(records)}"
        )
    if dict(sorted(page_counts.items())) != dict(sorted(expected_pages.items())):
        raise ValueError(
            "Locked Pinkas page distribution mismatch: "
            f"expected {dict(sorted(expected_pages.items()))}, "
            f"got {dict(sorted(page_counts.items()))}"
        )
    return records


__all__ = [
    "PINKAS_SOURCE_DATASET",
    "PINKAS_TEST_ARCHIVE_SHA256",
    "PINKAS_TEST_PAGE_COUNTS",
    "PINKAS_TEST_POLICY",
    "PinkasWebDatasetPolicy",
    "convert_pinkas_webdataset_tar",
]
