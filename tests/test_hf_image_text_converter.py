from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image

from hebocrbench._corpus_builder_engine import _upstream_split
from hebocrbench.converters import ConversionContext
from hebocrbench.converters.hf_image_text import (
    _parquet_row_record,
    convert_hf_image_text_manifest,
)
from hebocrbench.corpus_registry import load_registry


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
            "languages": ["he"],
            "script": "Hebr",
            "script_style": "handwriting",
            "era": "modern",
            "document_type": "character",
            "layout_type": "isolated",
            "vocalization": "partial",
            "source_type": "form",
            "source_collection": "fixture",
        },
    )

    record = convert_hf_image_text_manifest(manifest, tmp_path, context)

    assert record["image"]["sha256"] == digest
    assert record["regions"][0]["lines"][0]["text"] == "שָ"
    assert record["document_id"] == "fixture-writer-1"


def test_embedded_parquet_row_preserves_writer_lineage_and_hash(tmp_path: Path):
    stream = io.BytesIO()
    Image.new("L", (48, 16), 255).save(stream, format="PNG")
    data = stream.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    manifest = tmp_path / "test-00000.parquet"
    manifest.write_bytes(b"fixture parquet identity")
    context = ConversionContext(
        source_id="handwriting",
        source_version="locked-revision",
        split="test",
        track="modern_handwriting",
        license_expression="LicenseRef-Source-Metadata",
        rights_uri="https://example.test/rights",
        redistribution="conditional",
        citation_key="handwriting",
        source_url="https://example.test/",
        metadata_defaults={"source_type": "real_human_handwriting"},
    )

    record = _parquet_row_record(
        {
            "image": {"bytes": data, "path": None},
            "text": "ש\u05b8",
            "sample_id": "sample-1",
            "image_sha256": digest,
            "image_format": "PNG",
            "width": 48,
            "height": 16,
            "writer": "writer-102",
            "source_doc": "",
            "source_repo": "fixture/source",
            "source_revision": "abc123",
        },
        row_index=7,
        manifest_path=manifest,
        source_root=tmp_path,
        build_root=tmp_path / "build",
        context=context,
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )

    assert record["page_id"] == "handwriting-sample-1"
    assert record["document_id"] == "handwriting-writer-102"
    assert record["regions"][0]["lines"][0]["text"] == "שָ"
    assert record["metadata"]["writer"] == "writer-102"
    assert record["metadata"]["source_revision"] == "abc123"
    assert record["image"]["sha256"] == digest
    assert (tmp_path / "build" / record["image"]["path"]).is_file()


def test_upstream_split_is_detected_from_hugging_face_shard_filename():
    source = load_registry().sources["modern-handwriting-lines-v1"]

    assert _upstream_split(source, Path("stage3_human_finetune/test-00000.parquet")) == "test"
