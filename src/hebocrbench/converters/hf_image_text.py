"""Immutable image/text manifest conversion for simple OCR and character sources."""

from __future__ import annotations

import json
from pathlib import Path
import unicodedata
from typing import Mapping

from . import ConversionContext
from .common import image_descriptor


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
