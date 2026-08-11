"""Immutable image/text manifest conversion for simple OCR and character sources."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Mapping

from PIL import Image

from . import ConversionContext
from .common import image_descriptor


_PARQUET_METADATA_FIELDS = (
    "text_original",
    "source_repo",
    "source_revision",
    "source_split",
    "source_file",
    "source_row_index",
    "text_group_id",
    "contrast_p98_p2",
    "image_std",
    "ink_fraction",
    "image_format",
    "human_source",
    "writer",
    "source_doc",
    "recommended_sampling_weight",
)


def _safe_token(value: object) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._")
    return token or "item"


def _image_suffix(format_name: object, path_name: object) -> str:
    if isinstance(path_name, str) and Path(path_name).suffix:
        suffix = Path(path_name).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}:
            return suffix
    normalized = str(format_name or "png").strip().lower()
    return {
        "jpeg": ".jpg",
        "jpg": ".jpg",
        "png": ".png",
        "tiff": ".tiff",
        "tif": ".tif",
        "webp": ".webp",
    }.get(normalized, ".img")


def _write_embedded_image(
    data: bytes,
    *,
    expected_sha256: object,
    image_format: object,
    source_path: object,
    item_id: str,
    build_root: Path,
    source_id: str,
) -> tuple[Path, str, int, int]:
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and str(expected_sha256).lower() != digest:
        raise ValueError(f"Embedded image SHA-256 mismatch for row {item_id}")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except (OSError, ValueError) as exc:
        raise ValueError(f"Embedded image is invalid for row {item_id}: {exc}") from exc
    suffix = _image_suffix(image_format, source_path)
    relative = Path("images") / source_id / f"{digest[:20]}-{_safe_token(item_id)}{suffix}"
    destination = build_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Embedded image destination collision: {relative}")
    else:
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
    return relative, digest, width, height


def _parquet_row_record(
    row: Mapping[str, object],
    *,
    row_index: int,
    manifest_path: Path,
    source_root: Path,
    build_root: Path,
    context: ConversionContext,
    manifest_sha256: str,
) -> dict[str, object]:
    text_value = row.get("text", row.get("label"))
    if text_value is None:
        raise ValueError(f"Parquet row {row_index} has no text or label")
    text = unicodedata.normalize("NFC", str(text_value))
    item_id = _safe_token(row.get("sample_id") or row.get("item_id") or row_index)
    image_value = row.get("image")
    if not isinstance(image_value, Mapping):
        raise ValueError(f"Parquet row {item_id} has no Hugging Face Image value")
    embedded = image_value.get("bytes")
    source_path = image_value.get("path")
    if isinstance(embedded, memoryview):
        embedded = embedded.tobytes()
    if embedded is not None and not isinstance(embedded, bytes):
        raise ValueError(f"Parquet row {item_id} image bytes are not binary")
    if embedded is not None:
        relative, digest, width, height = _write_embedded_image(
            embedded,
            expected_sha256=row.get("image_sha256"),
            image_format=row.get("image_format"),
            source_path=source_path,
            item_id=item_id,
            build_root=build_root,
            source_id=context.source_id,
        )
    elif isinstance(source_path, str) and source_path:
        candidate = (source_root / source_path).resolve()
        if source_root.resolve() not in candidate.parents:
            raise ValueError(f"Parquet row {item_id} image path escapes source root")
        descriptor = image_descriptor(
            candidate,
            relative_name=source_path,
            declared_width=int(row.get("width", 0) or 0),
            declared_height=int(row.get("height", 0) or 0),
        )
        expected = row.get("image_sha256")
        if expected and str(expected).lower() != descriptor["sha256"]:
            raise ValueError(f"Parquet row {item_id} image SHA-256 mismatch")
        relative = (
            Path("images")
            / context.source_id
            / (f"{str(descriptor['sha256'])[:20]}-{item_id}{candidate.suffix.lower()}")
        )
        destination = build_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            import shutil

            shutil.copyfile(candidate, destination)
        digest = str(descriptor["sha256"])
        width = int(descriptor["width"])
        height = int(descriptor["height"])
    else:
        raise ValueError(f"Parquet row {item_id} has neither image bytes nor image path")

    declared_width = int(row.get("width", 0) or 0)
    declared_height = int(row.get("height", 0) or 0)
    if declared_width > 0 and declared_width != width:
        raise ValueError(f"Parquet row {item_id} image width mismatch")
    if declared_height > 0 and declared_height != height:
        raise ValueError(f"Parquet row {item_id} image height mismatch")
    writer = str(row.get("writer") or "").strip()
    source_doc = str(row.get("source_doc") or "").strip()
    document_key = source_doc or writer or item_id
    polygon = [[0, 0], [width, 0], [width, height], [0, height]]
    annotation_relative = manifest_path.relative_to(source_root).as_posix()
    metadata = context.metadata(annotation_path=annotation_relative)
    metadata.update(
        {key: row[key] for key in _PARQUET_METADATA_FIELDS if key in row and row[key] is not None}
    )
    metadata.update(
        {
            "source_item_id": item_id,
            "source_annotation_path": annotation_relative,
            "source_manifest_sha256": manifest_sha256,
            "parquet_row_index": row_index,
            "document_id_method": "source_doc_then_writer_then_item_v1",
        }
    )
    return {
        "schema_version": "1.0",
        "page_id": f"{context.source_id}-{item_id}",
        "document_id": f"{context.source_id}-{_safe_token(document_key)}",
        "split": context.split,
        "track": context.track,
        "image": {
            "path": relative.as_posix(),
            "width": width,
            "height": height,
            "rotation_degrees": 0,
            "sha256": digest,
        },
        "metadata": metadata,
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


def convert_hf_image_text_parquet(
    manifest_path: str | Path,
    source_root: str | Path,
    build_root: str | Path,
    context: ConversionContext,
) -> list[dict[str, object]]:
    """Convert every immutable Hugging Face Image/text row in one Parquet shard."""

    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise ValueError(
            "Parquet OCR sources require the 'corpus' extra with pyarrow installed"
        ) from exc
    manifest = Path(manifest_path).absolute()
    source = Path(source_root).absolute()
    output = Path(build_root).resolve()
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    records: list[dict[str, object]] = []
    parquet_file = parquet.ParquetFile(manifest)
    required_columns = {"image", "text"}
    available = set(parquet_file.schema_arrow.names)
    if not required_columns <= available:
        raise ValueError(f"Parquet shard must contain image and text columns: {manifest}")
    row_index = 0
    for batch in parquet_file.iter_batches(batch_size=128):
        for row in batch.to_pylist():
            if not isinstance(row, Mapping):
                raise ValueError(f"Parquet row {row_index} is not an object")
            records.append(
                _parquet_row_record(
                    row,
                    row_index=row_index,
                    manifest_path=manifest,
                    source_root=source,
                    build_root=output,
                    context=context,
                    manifest_sha256=manifest_sha256,
                )
            )
            row_index += 1
    if not records:
        raise ValueError(f"Parquet shard contains no rows: {manifest}")
    return records


def convert_hf_image_text_manifest(
    manifest_path: str | Path,
    source_root: str | Path,
    context: ConversionContext,
) -> dict[str, object]:
    manifest = Path(manifest_path)
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"Image/text manifest must contain an object: {manifest}")
    image_value = value.get("image") or value.get("image_path")
    if not isinstance(image_value, str) or not image_value:
        raise ValueError(f"Image/text manifest has no image path: {manifest}")
    image_path = (Path(source_root) / image_value).resolve()
    root = Path(source_root).resolve()
    if root not in image_path.parents:
        raise ValueError(f"Image path escapes source root: {image_value}")
    text_value = value.get("text", value.get("label"))
    if text_value is None:
        raise ValueError(f"Image/text manifest has no text or label: {manifest}")
    text = unicodedata.normalize("NFC", str(text_value))
    declared_width = int(value.get("width", 0) or 0)
    declared_height = int(value.get("height", 0) or 0)
    image = image_descriptor(
        image_path,
        relative_name=image_path.relative_to(root).as_posix(),
        declared_width=declared_width,
        declared_height=declared_height,
    )
    expected_hash = value.get("image_sha256")
    if expected_hash is not None and str(expected_hash).lower() != image["sha256"]:
        raise ValueError(f"Image SHA-256 mismatch in {manifest}")
    item_id = str(value.get("item_id", manifest.stem))
    document_id = str(value.get("document_id", item_id))
    polygon = [[0, 0], [image["width"], 0], [image["width"], image["height"]], [0, image["height"]]]
    metadata = context.metadata(annotation_path=str(manifest))
    metadata.update(
        {
            "source_item_id": item_id,
            "source_manifest_sha256": __import__("hashlib")
            .sha256(manifest.read_bytes())
            .hexdigest(),
        }
    )
    return {
        "schema_version": "1.0",
        "page_id": f"{context.source_id}-{item_id}",
        "document_id": f"{context.source_id}-{document_id}",
        "split": context.split,
        "track": context.track,
        "image": image,
        "metadata": metadata,
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
