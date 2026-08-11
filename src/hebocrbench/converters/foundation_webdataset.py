"""Locked materialization of synthetic Hebrew Foundation diagnostic shards.

These sources are useful for narrow capability probes, but they are public and
synthetic. The selected rows come only from a locked ``test_synthetic`` shard;
the corresponding train shards are read solely to prove ID, text and font-file
disjointness. The converter stamps that status into every record and refuses to
turn the diagnostics into real-world or headline benchmark evidence.
"""

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

from ..unicode_utils import classify_hebrew_mark, graphemes
from . import ConversionContext


FOUNDATION_REVISION = "1e277f98b17ad2efb9e6b13abbb7a06afe569a03"


@dataclass(frozen=True, slots=True)
class FoundationWebDatasetPolicy:
    """Exact identity and semantic limits of one Foundation diagnostic shard."""

    source_id: str
    archive_sha256: str
    expected_archive_records: int
    expected_selected_records: int
    expected_profile: str
    expected_niqqud_marks: int
    expected_cantillation_marks: int = 0
    expected_font_family: str | None = None
    train_archive_filename: str | None = None
    train_archive_sha256: str | None = None
    expected_train_records: int | None = None
    expected_test_unique_source_ids: int | None = None
    expected_train_unique_source_ids: int | None = None
    expected_test_unique_texts: int | None = None
    expected_train_unique_texts: int | None = None
    expected_test_unique_font_sha256: int | None = None
    expected_train_unique_font_sha256: int | None = None
    expected_font_family_overlap: int | None = None
    expected_profile_counts: tuple[tuple[str, int], ...] = ()


BIBLICAL_NIQQUD_POLICY = FoundationWebDatasetPolicy(
    source_id="biblical-niqqud-synthetic-diagnostic-v1",
    archive_sha256="12886b77eefb54f73ed2ea9ba9ddf4766de60ed2635126248344739626608927",
    expected_archive_records=5_000,
    expected_selected_records=500,
    expected_profile="niqqud",
    expected_niqqud_marks=12_167,
    train_archive_filename="train-niqqud-000.tar",
    train_archive_sha256="05cd60b91ce566b23dd7024665026a615f2127b7abfaf8e8a10afd92d3945ff4",
    expected_train_records=20_000,
    expected_test_unique_source_ids=368,
    expected_train_unique_source_ids=13_246,
    expected_test_unique_texts=368,
    expected_train_unique_texts=13_246,
    expected_test_unique_font_sha256=17,
    expected_train_unique_font_sha256=96,
    expected_font_family_overlap=0,
    expected_profile_counts=(
        ("mixed_bidi", 750),
        ("modern_natural", 2_500),
        ("modern_structured", 750),
        ("niqqud", 500),
        ("rashi", 500),
    ),
)

RASHI_PRINT_POLICY = FoundationWebDatasetPolicy(
    source_id="rashi-print-synthetic-diagnostic-v1",
    archive_sha256="12886b77eefb54f73ed2ea9ba9ddf4766de60ed2635126248344739626608927",
    expected_archive_records=5_000,
    expected_selected_records=500,
    expected_profile="rashi",
    expected_niqqud_marks=0,
    expected_font_family="Noto Rashi Hebrew",
    train_archive_filename="train-rashi-000.tar",
    train_archive_sha256="f1adca1ba117160266325b2002abe62896f06986242ef145a34296852f7190ee",
    expected_train_records=10_000,
    expected_test_unique_source_ids=448,
    expected_train_unique_source_ids=8_956,
    expected_test_unique_texts=473,
    expected_train_unique_texts=9_894,
    expected_test_unique_font_sha256=3,
    expected_train_unique_font_sha256=4,
    expected_font_family_overlap=1,
    expected_profile_counts=(
        ("mixed_bidi", 750),
        ("modern_natural", 2_500),
        ("modern_structured", 750),
        ("niqqud", 500),
        ("rashi", 500),
    ),
)

FOUNDATION_POLICIES = {
    policy.source_id: policy for policy in (BIBLICAL_NIQQUD_POLICY, RASHI_PRINT_POLICY)
}

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_MAX_JSON_BYTES = 1024 * 1024
_MAX_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_MEMBERS = 50_000
_MAX_ARCHIVE_MEMBER_BYTES = 4 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _ValidatedSample:
    item_id: str
    text: str
    metadata: Mapping[str, object]
    image_member: str
    image_suffix: str
    image_sha256: str
    width: int
    height: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Foundation TAR contains an unsafe member path: {name!r}")
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
        raise ValueError(f"Foundation TAR member {member.name!r} has forbidden size {member.size}")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"Foundation TAR member cannot be read: {member.name!r}")
    with stream:
        data = stream.read(maximum + 1)
    if len(data) != member.size or len(data) > maximum:
        raise ValueError(f"Foundation TAR member size mismatch: {member.name!r}")
    return data


def _paired_members(
    archive: tarfile.TarFile,
) -> tuple[dict[str, tarfile.TarInfo], dict[str, tarfile.TarInfo]]:
    members = archive.getmembers()
    if len(members) > _MAX_MEMBERS:
        raise ValueError(f"Foundation TAR has too many members: {len(members)}")
    total_size = 0
    names: set[str] = set()
    json_members: dict[str, tarfile.TarInfo] = {}
    image_members: dict[str, tarfile.TarInfo] = {}
    for member in members:
        safe_name = _safe_member_name(member.name)
        normalized = safe_name.as_posix()
        if normalized in names:
            raise ValueError(f"Foundation TAR contains a duplicate member: {normalized}")
        names.add(normalized)
        if member.isdir():
            continue
        if not member.isfile():
            raise ValueError(f"Foundation TAR contains a non-regular member: {normalized}")
        total_size += max(member.size, 0)
        if total_size > _MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError("Foundation TAR exceeds the allowed aggregate member size")
        suffix = safe_name.suffix.lower()
        key = safe_name.with_suffix("").as_posix()
        target = json_members if suffix == ".json" else image_members
        if suffix != ".json" and suffix not in _IMAGE_SUFFIXES:
            raise ValueError(f"Foundation TAR contains an unsupported member: {normalized}")
        if key in target:
            raise ValueError(f"Foundation TAR contains a duplicate sample member: {normalized}")
        target[key] = member
    if set(json_members) != set(image_members):
        missing_images = sorted(set(json_members) - set(image_members))[:5]
        missing_json = sorted(set(image_members) - set(json_members))[:5]
        raise ValueError(
            "Foundation TAR image/JSON pairing mismatch: "
            f"missing_images={missing_images}, missing_json={missing_json}"
        )
    return json_members, image_members


def _json_object(data: bytes, member_name: str) -> Mapping[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid Foundation JSON member {member_name!r}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"Foundation JSON member is not an object: {member_name!r}")
    return value


def _required_text(metadata: Mapping[str, object], key: str, item_id: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Foundation sample {item_id!r} has no nonempty {key}")
    return value


def _validate_metadata(
    metadata: Mapping[str, object],
    *,
    sample_key: str,
    policy: FoundationWebDatasetPolicy,
) -> tuple[str, str]:
    item_id = _required_text(metadata, "id", sample_key)
    if item_id != PurePosixPath(sample_key).name:
        raise ValueError(
            f"Foundation sample ID/member mismatch: id={item_id!r}, member={sample_key!r}"
        )
    expected = {
        "dataset": "Hebrew OCR Foundation",
        "version": "1.0.0",
        "split": "test_synthetic",
        "profile": policy.expected_profile,
        "normalization": "NFC",
        "base_direction": "rtl",
    }
    for key, wanted in expected.items():
        if metadata.get(key) != wanted:
            raise ValueError(
                f"Foundation sample {item_id!r} has {key}={metadata.get(key)!r}, "
                f"expected {wanted!r}"
            )
    text = _required_text(metadata, "text_logical", item_id)
    if unicodedata.normalize("NFC", text) != text:
        raise ValueError(f"Foundation sample {item_id!r} text is not NFC")
    if metadata.get("codepoints") != len(text):
        raise ValueError(f"Foundation sample {item_id!r} codepoint count mismatch")
    if metadata.get("graphemes") != len(graphemes(text)):
        raise ValueError(f"Foundation sample {item_id!r} grapheme count mismatch")
    font = metadata.get("font")
    if not isinstance(font, Mapping):
        raise ValueError(f"Foundation sample {item_id!r} has no font provenance")
    family = _required_text(font, "family", item_id)
    if font.get("pool") != "test_synthetic":
        raise ValueError(
            f"Foundation sample {item_id!r} has font.pool={font.get('pool')!r}, "
            "expected 'test_synthetic'"
        )
    font_sha256 = _required_text(font, "sha256", item_id).lower()
    if len(font_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in font_sha256):
        raise ValueError(f"Foundation sample {item_id!r} has invalid font SHA-256")
    if policy.expected_font_family is not None and family != policy.expected_font_family:
        raise ValueError(
            f"Foundation sample {item_id!r} font family is {family!r}, "
            f"expected {policy.expected_font_family!r}"
        )
    return item_id, text


def _metadata_identity_sets(
    archive_path: Path,
    *,
    expected_profile: str,
    expected_split: str,
    expected_archive_sha256: str,
    expected_records: int,
) -> dict[str, set[str]]:
    """Read only JSON identities from a locked train reference shard."""

    actual_sha256 = _sha256_file(archive_path)
    if actual_sha256 != expected_archive_sha256:
        raise ValueError(
            f"Foundation train-reference TAR SHA-256 mismatch: {actual_sha256} != "
            f"{expected_archive_sha256}"
        )
    identities = {
        "item_ids": set(),
        "source_ids": set(),
        "texts": set(),
        "font_sha256": set(),
        "font_families": set(),
    }
    count = 0
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            json_members, image_members = _paired_members(archive)
            if set(json_members) != set(image_members):
                raise ValueError("Foundation train-reference image/JSON pairing mismatch")
            if len(json_members) != expected_records:
                raise ValueError(
                    "Foundation train-reference record count mismatch: "
                    f"{len(json_members)} != {expected_records}"
                )
            for sample_key in sorted(json_members):
                member = json_members[sample_key]
                metadata = _json_object(
                    _member_bytes(archive, member, maximum=_MAX_JSON_BYTES),
                    member.name,
                )
                item_id = _required_text(metadata, "id", sample_key)
                if item_id != PurePosixPath(sample_key).name:
                    raise ValueError(f"Foundation train sample ID/member mismatch: {item_id!r}")
                expected = {
                    "dataset": "Hebrew OCR Foundation",
                    "version": "1.0.0",
                    "split": expected_split,
                    "profile": expected_profile,
                    "normalization": "NFC",
                    "base_direction": "rtl",
                }
                for key, wanted in expected.items():
                    if metadata.get(key) != wanted:
                        raise ValueError(
                            f"Foundation train sample {item_id!r} has "
                            f"{key}={metadata.get(key)!r}, expected {wanted!r}"
                        )
                text = _required_text(metadata, "text_logical", item_id)
                source_id = _required_text(metadata, "source_id", item_id)
                font = metadata.get("font")
                if not isinstance(font, Mapping):
                    raise ValueError(f"Foundation train sample {item_id!r} has no font provenance")
                if font.get("pool") != "train":
                    raise ValueError(f"Foundation train sample {item_id!r} has non-train font pool")
                identities["item_ids"].add(item_id)
                identities["source_ids"].add(source_id)
                identities["texts"].add(text)
                identities["font_sha256"].add(_required_text(font, "sha256", item_id))
                identities["font_families"].add(_required_text(font, "family", item_id))
                count += 1
    except tarfile.TarError as exc:
        raise ValueError(f"Cannot read Foundation train-reference TAR: {exc}") from exc
    if count != expected_records:
        raise ValueError(
            f"Foundation train-reference scan count mismatch: {count} != {expected_records}"
        )
    return identities


def _validate_disjointness(
    test_identities: Mapping[str, set[str]],
    train_identities: Mapping[str, set[str]],
    *,
    policy: FoundationWebDatasetPolicy,
) -> dict[str, object]:
    expected_counts = {
        "test.source_ids": policy.expected_test_unique_source_ids,
        "train.source_ids": policy.expected_train_unique_source_ids,
        "test.texts": policy.expected_test_unique_texts,
        "train.texts": policy.expected_train_unique_texts,
        "test.font_sha256": policy.expected_test_unique_font_sha256,
        "train.font_sha256": policy.expected_train_unique_font_sha256,
    }
    values = {
        "test.source_ids": test_identities["source_ids"],
        "train.source_ids": train_identities["source_ids"],
        "test.texts": test_identities["texts"],
        "train.texts": train_identities["texts"],
        "test.font_sha256": test_identities["font_sha256"],
        "train.font_sha256": train_identities["font_sha256"],
    }
    for label, expected in expected_counts.items():
        if expected is not None and len(values[label]) != expected:
            raise ValueError(
                f"Foundation disjointness identity count mismatch for {label}: "
                f"{len(values[label])} != {expected}"
            )
    overlaps = {
        key: sorted(test_identities[key] & train_identities[key])
        for key in ("item_ids", "source_ids", "texts", "font_sha256", "font_families")
    }
    for key in ("item_ids", "source_ids", "texts", "font_sha256"):
        if overlaps[key]:
            raise ValueError(
                f"Foundation synthetic test is not disjoint from train by {key}: "
                f"{overlaps[key][:3]}"
            )
    expected_family_overlap = policy.expected_font_family_overlap
    if (
        expected_family_overlap is not None
        and len(overlaps["font_families"]) != expected_family_overlap
    ):
        raise ValueError(
            "Foundation font-family overlap count mismatch: "
            f"{len(overlaps['font_families'])} != {expected_family_overlap}"
        )
    return {
        "item_id_overlap": 0,
        "source_id_overlap": 0,
        "text_overlap": 0,
        "font_sha256_overlap": 0,
        "font_family_overlap": len(overlaps["font_families"]),
        "font_family_overlap_values": overlaps["font_families"],
        "test_unique_source_ids": len(test_identities["source_ids"]),
        "train_unique_source_ids": len(train_identities["source_ids"]),
        "test_unique_texts": len(test_identities["texts"]),
        "train_unique_texts": len(train_identities["texts"]),
        "test_unique_font_sha256": len(test_identities["font_sha256"]),
        "train_unique_font_sha256": len(train_identities["font_sha256"]),
    }


def _validate_image(
    data: bytes,
    metadata: Mapping[str, object],
    *,
    item_id: str,
) -> tuple[str, int, int]:
    digest = hashlib.sha256(data).hexdigest()
    image_metadata = metadata.get("image")
    if not isinstance(image_metadata, Mapping):
        raise ValueError(f"Foundation sample {item_id!r} has no image metadata")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except (OSError, ValueError) as exc:
        raise ValueError(f"Foundation sample {item_id!r} has an invalid image: {exc}") from exc
    if image_metadata.get("width") != width or image_metadata.get("height") != height:
        raise ValueError(f"Foundation sample {item_id!r} image dimensions do not match metadata")
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
    relative = (
        Path("images")
        / _safe_token(source_id)
        / f"{digest[:20]}-{_safe_token(item_id)}{suffix.lower()}"
    )
    destination = build_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _sha256_file(destination) != digest:
            raise ValueError(f"Foundation image destination collision: {relative.as_posix()}")
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
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Foundation image write verification failed: {item_id}")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return relative


def _record(
    sample: _ValidatedSample,
    *,
    image_path: Path,
    archive_relative: str,
    archive_sha256: str,
    context: ConversionContext,
    policy: FoundationWebDatasetPolicy,
    disjointness: Mapping[str, object],
) -> dict[str, object]:
    polygon = [[0, 0], [sample.width, 0], [sample.width, sample.height], [0, sample.height]]
    source_text_id = str(sample.metadata.get("source_id") or sample.item_id)
    font = sample.metadata.get("font")
    render = sample.metadata.get("render")
    record_metadata = context.metadata(annotation_path=archive_relative)
    record_metadata.update(
        {
            "source_item_id": sample.item_id,
            "source_text_id": source_text_id,
            "source_archive_sha256": archive_sha256,
            "source_image_member": sample.image_member,
            "source_upstream_split": "test_synthetic",
            "source_profile": policy.expected_profile,
            "font": dict(font) if isinstance(font, Mapping) else {},
            "render": dict(render) if isinstance(render, Mapping) else {},
            "benchmark_data_status": "synthetic-public-fixed",
            "synthetic": True,
            "public_fixed": True,
            "held_out_test": True,
            "held_out_from_foundation_train": True,
            "headline_eligible": False,
            "rankable": False,
            "score_policy": "separate-diagnostic-no-headline-blending",
            "train_disjointness": dict(disjointness),
            "train_reference_archive_sha256": policy.train_archive_sha256,
            "document_id_method": "foundation_source_text_id_v1",
        }
    )
    if policy.expected_profile == "niqqud":
        record_metadata.update(
            {
                "coverage_scope": "synthetic-niqqud-only",
                "cantillation_status": "absent",
                "biblical_coverage_status": "unmet",
            }
        )
    else:
        record_metadata.update(
            {
                "coverage_scope": "synthetic-single-rashi-font",
                "historical_scan_status": "absent",
                "historical_coverage_status": "unmet",
            }
        )
    return {
        "schema_version": "1.0",
        "page_id": f"{context.source_id}-{_safe_token(sample.item_id)}",
        "document_id": f"{context.source_id}-{_safe_token(source_text_id)}",
        "split": context.split,
        "track": context.track,
        "image": {
            "path": image_path.as_posix(),
            "width": sample.width,
            "height": sample.height,
            "rotation_degrees": 0,
            "sha256": sample.image_sha256,
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
                        "text": sample.text,
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


def convert_foundation_webdataset_tar(
    archive_path: str | Path,
    source_root: str | Path,
    build_root: str | Path,
    context: ConversionContext,
    *,
    policy: FoundationWebDatasetPolicy | None = None,
) -> list[dict[str, object]]:
    """Validate and materialize one exact synthetic diagnostic shard."""

    archive_link_path = Path(archive_path).absolute()
    source_root = Path(source_root).resolve()
    build_root = Path(build_root).resolve()
    if archive_link_path != source_root and source_root not in archive_link_path.parents:
        raise ValueError("Foundation archive escapes the configured source root")
    archive_path = archive_link_path.resolve()
    selected_policy = policy or FOUNDATION_POLICIES.get(context.source_id)
    if selected_policy is None:
        raise ValueError(f"No locked Foundation policy for source {context.source_id!r}")
    if selected_policy.source_id != context.source_id:
        raise ValueError(
            f"Foundation policy/source mismatch: {selected_policy.source_id} != {context.source_id}"
        )
    archive_sha256 = _sha256_file(archive_path)
    if archive_sha256 != selected_policy.archive_sha256:
        raise ValueError(
            f"Foundation TAR SHA-256 mismatch: {archive_sha256} != {selected_policy.archive_sha256}"
        )
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            json_members, image_members = _paired_members(archive)
            if len(json_members) != selected_policy.expected_archive_records:
                raise ValueError(
                    "Foundation diagnostic archive record count mismatch: "
                    f"{len(json_members)} != {selected_policy.expected_archive_records}"
                )
            samples: list[_ValidatedSample] = []
            marks: Counter[str] = Counter()
            profile_counts: Counter[str] = Counter()
            for sample_key in sorted(json_members):
                json_member = json_members[sample_key]
                image_member = image_members[sample_key]
                metadata = _json_object(
                    _member_bytes(archive, json_member, maximum=_MAX_JSON_BYTES),
                    json_member.name,
                )
                profile = metadata.get("profile")
                if not isinstance(profile, str) or not profile:
                    raise ValueError(f"Foundation sample {sample_key!r} has no nonempty profile")
                profile_counts[profile] += 1
                if profile != selected_policy.expected_profile:
                    continue
                item_id, text = _validate_metadata(
                    metadata,
                    sample_key=sample_key,
                    policy=selected_policy,
                )
                for character in text:
                    category = classify_hebrew_mark(character)
                    if category is not None:
                        marks["cantillation" if category == "cantillation" else "niqqud"] += 1
                image_data = _member_bytes(archive, image_member, maximum=_MAX_IMAGE_BYTES)
                image_sha256, width, height = _validate_image(
                    image_data,
                    metadata,
                    item_id=item_id,
                )
                samples.append(
                    _ValidatedSample(
                        item_id=item_id,
                        text=text,
                        metadata=dict(metadata),
                        image_member=image_member.name,
                        image_suffix=PurePosixPath(image_member.name).suffix.lower(),
                        image_sha256=image_sha256,
                        width=width,
                        height=height,
                    )
                )
    except tarfile.TarError as exc:
        raise ValueError(f"Cannot read Foundation TAR: {exc}") from exc

    if len(samples) != selected_policy.expected_selected_records:
        raise ValueError(
            "Foundation selected diagnostic record count mismatch: "
            f"{len(samples)} != {selected_policy.expected_selected_records}"
        )
    if selected_policy.expected_profile_counts:
        expected_profile_counts = dict(selected_policy.expected_profile_counts)
        if dict(sorted(profile_counts.items())) != dict(sorted(expected_profile_counts.items())):
            raise ValueError(
                "Foundation test profile distribution mismatch: "
                f"{dict(sorted(profile_counts.items()))} != "
                f"{dict(sorted(expected_profile_counts.items()))}"
            )

    actual_niqqud = marks["niqqud"]
    actual_cantillation = marks["cantillation"]
    if actual_niqqud != selected_policy.expected_niqqud_marks:
        raise ValueError(
            f"Foundation niqqud-mark count mismatch: {actual_niqqud} != "
            f"{selected_policy.expected_niqqud_marks}"
        )
    if actual_cantillation != selected_policy.expected_cantillation_marks:
        raise ValueError(
            f"Foundation cantillation-mark count mismatch: {actual_cantillation} != "
            f"{selected_policy.expected_cantillation_marks}"
        )

    test_identities = {
        "item_ids": {sample.item_id for sample in samples},
        "source_ids": {
            _required_text(sample.metadata, "source_id", sample.item_id) for sample in samples
        },
        "texts": {sample.text for sample in samples},
        "font_sha256": {
            _required_text(sample.metadata["font"], "sha256", sample.item_id)
            for sample in samples
            if isinstance(sample.metadata.get("font"), Mapping)
        },
        "font_families": {
            _required_text(sample.metadata["font"], "family", sample.item_id)
            for sample in samples
            if isinstance(sample.metadata.get("font"), Mapping)
        },
    }
    if (
        selected_policy.train_archive_filename is None
        or selected_policy.train_archive_sha256 is None
        or selected_policy.expected_train_records is None
    ):
        disjointness: dict[str, object] = {
            "audit_status": "not-configured",
            "item_id_overlap": None,
            "source_id_overlap": None,
            "text_overlap": None,
            "font_sha256_overlap": None,
        }
    else:
        train_archive = archive_link_path.parent / selected_policy.train_archive_filename
        if not train_archive.is_file():
            raise ValueError(f"Foundation train-reference archive is missing: {train_archive.name}")
        train_identities = _metadata_identity_sets(
            train_archive,
            expected_profile=selected_policy.expected_profile,
            expected_split="train",
            expected_archive_sha256=selected_policy.train_archive_sha256,
            expected_records=selected_policy.expected_train_records,
        )
        disjointness = {
            "audit_status": "verified",
            **_validate_disjointness(
                test_identities,
                train_identities,
                policy=selected_policy,
            ),
        }

    archive_relative = archive_link_path.relative_to(source_root).as_posix()
    by_key = {
        PurePosixPath(sample.image_member).with_suffix("").as_posix(): sample for sample in samples
    }
    records: list[dict[str, object]] = []
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            _, image_members = _paired_members(archive)
            for sample_key in sorted(by_key):
                sample = by_key[sample_key]
                image_data = _member_bytes(
                    archive,
                    image_members[sample_key],
                    maximum=_MAX_IMAGE_BYTES,
                )
                if hashlib.sha256(image_data).hexdigest() != sample.image_sha256:
                    raise ValueError(f"Foundation image changed while converting: {sample.item_id}")
                relative_image = _write_image(
                    image_data,
                    digest=sample.image_sha256,
                    item_id=sample.item_id,
                    suffix=sample.image_suffix,
                    build_root=build_root,
                    source_id=context.source_id,
                )
                records.append(
                    _record(
                        sample,
                        image_path=relative_image,
                        archive_relative=archive_relative,
                        archive_sha256=archive_sha256,
                        context=context,
                        policy=selected_policy,
                        disjointness=disjointness,
                    )
                )
    except tarfile.TarError as exc:
        raise ValueError(f"Cannot reread Foundation TAR: {exc}") from exc
    return records


__all__ = [
    "BIBLICAL_NIQQUD_POLICY",
    "FOUNDATION_POLICIES",
    "FOUNDATION_REVISION",
    "FoundationWebDatasetPolicy",
    "RASHI_PRINT_POLICY",
    "convert_foundation_webdataset_tar",
]
