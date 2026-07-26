from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from hebocrbench.converters import ConversionContext
from hebocrbench.converters.hf_image_text import convert_hf_image_text_manifest


def test_image_text_manifest_is_hash_locked_and_nfc(tmp_path: Path):
    image = tmp_path / "images" / "sample.png"
    image.parent.mkdir()
    Image.new("L", (32, 40), 255).save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    manifest = tmp_path / "sample.json"
    manifest.write_text(
        json.dumps(
            {
                "item_id": "sample",
                "document_id": "writer-1",
                "image": "images/sample.png",
                "image_sha256": digest,
                "width": 32,
                "height": 40,
                "text": "ש\u05b8",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    context = ConversionContext(
        source_id="fixture",
        source_version="1",
        split="train",
        track="characters",
        license_expression="CC-BY-4.0",
        rights_uri="https://example.test/rights",
        redistribution="allowed",
        citation_key="fixture",
        source_url="https://example.test/",
        metadata_defaults={
            "languages": ["he"], "script": "Hebr", "script_style": "handwriting",
            "era": "modern", "document_type": "character", "layout_type": "isolated",
            "vocalization": "partial", "source_type": "form", "source_collection": "fixture",
        },
    )

    record = convert_hf_image_text_manifest(manifest, tmp_path, context)

    assert record["image"]["sha256"] == digest
    assert record["regions"][0]["lines"][0]["text"] == "שָ"
    assert record["document_id"] == "fixture-writer-1"
