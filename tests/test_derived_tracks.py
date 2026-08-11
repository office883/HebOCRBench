from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from hebocrbench.derived_tracks import build_canonical_track_roots


def _source_root(root: Path) -> Path:
    source = root / "source"
    (source / "images").mkdir(parents=True)
    image_path = source / "images" / "page.png"
    Image.new("RGB", (100, 80), "white").save(image_path)
    record = {
        "schema_version": "1.0",
        "page_id": "page-1",
        "document_id": "document-1",
        "split": "test",
        "track": "modern_page_ocr",
        "image": {
            "path": "images/page.png",
            "width": 100,
            "height": 80,
            "rotation_degrees": 0,
            "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        },
        "metadata": {
            "source_id": "fixture-source",
            "source_version": "1",
            "source_page_id": "fixture-page-1",
            "source_url": "https://example.invalid/fixture",
            "rights_uri": "https://creativecommons.org/publicdomain/zero/1.0/",
            "redistribution": "allowed",
            "citation_key": "fixture-source",
            "license": "CC0-1.0",
            "document_type": "fixture",
            "template_family": "fixture-template",
            "layout_type": "mixed",
            "source_type": "generated_fixture",
            "vocalization": "none",
            "languages": ["he"],
            "script": "Hebr",
            "script_style": "modern_square_print",
            "era": "modern",
            "source_collection": "Derived track fixture",
        },
        "regions": [
            {
                "region_id": "region-1",
                "type": "body",
                "polygon": [[5, 5], [95, 5], [95, 50], [5, 50]],
                "base_direction": "rtl",
                "reading_index": 0,
                "lines": [
                    {
                        "line_id": "line-1",
                        "polygon": [[10, 10], [90, 10], [90, 25], [10, 25]],
                        "baseline": [[90, 24], [10, 24]],
                        "text": "שלום 2026",
                        "base_direction": "rtl",
                        "language": "he",
                    },
                    {
                        "line_id": "line-2",
                        "polygon": [[10, 30], [90, 30], [90, 45], [10, 45]],
                        "baseline": [[90, 44], [10, 44]],
                        "text": "בדיקה",
                        "base_direction": "rtl",
                        "language": "he",
                    },
                ],
            }
        ],
        "reading_order": {"edges": []},
        "tables": [
            {
                "table_id": "table-1",
                "polygon": [[5, 55], [95, 55], [95, 75], [5, 75]],
                "n_rows": 1,
                "n_cols": 1,
                "cells": [
                    {
                        "row_start": 0,
                        "row_end": 1,
                        "col_start": 0,
                        "col_end": 1,
                        "text": "א",
                    }
                ],
            }
        ],
        "form_fields": [],
    }
    (source / "gold.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (source / "attribution.jsonl").write_text("{}\n", encoding="utf-8")
    (source / "citations.bib").write_text("@misc{fixture}\n", encoding="utf-8")
    (source / "licenses").mkdir()
    (source / "licenses" / "fixture-source.txt").write_text("CC0-1.0\n", encoding="utf-8")
    (source / "source_reports").mkdir()
    (source / "source_reports" / "fixture-source.json").write_text(
        json.dumps({"verification_status": "verified_acquisition"}) + "\n",
        encoding="utf-8",
    )
    fingerprint = "b" * 64
    manifest = {
        "benchmark": "HebOCRBench",
        "benchmark_version": "1.0.0",
        "profile": "fixture-profile-v1",
        "profile_scope": "track-component",
        "track_id": "modern-page-ocr-v1",
        "dataset_fingerprint": fingerprint,
        "registry_fingerprint": "a" * 64,
        "source_ids": ["fixture-source"],
        "accepted_source_ids": [],
        "source_verification": {"fixture-source": {"verification_status": "verified_acquisition"}},
        "license_tiers": ["bundled"],
        "page_count": 1,
    }
    manifest_path = source / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    (source / "dataset.lock.json").write_text(
        json.dumps(
            {
                "dataset_fingerprint": fingerprint,
                "registry_version": "fixture-v1",
                "source_licenses": {"fixture-source": "CC0-1.0"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (source / "FROZEN.json").write_text(
        json.dumps(
            {
                "dataset_fingerprint": fingerprint,
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return source


def test_derives_four_roots_from_one_frozen_page_root(tmp_path: Path) -> None:
    source = _source_root(tmp_path)
    first = build_canonical_track_roots(source, tmp_path / "first")
    second = build_canonical_track_roots(source, tmp_path / "second")
    assert first.keys() == second.keys()
    assert all(
        first[track]["dataset_fingerprint"] == second[track]["dataset_fingerprint"]
        for track in first
    )
    assert first["modern-page-ocr-v1"]["page_count"] == 1
    assert first["modern-line-recognition-v1"]["page_count"] == 2
    assert first["modern-tables-v1"]["page_count"] == 1
    assert first["modern-robustness-v1"]["page_count"] == 7

    robustness = [
        json.loads(line)
        for line in (tmp_path / "first" / "modern-robustness-v1" / "gold.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {item["metadata"]["parent_page_id"] for item in robustness} == {"page-1"}
    assert {item["split"] for item in robustness} == {"test"}
    assert all(item["image"]["width"] == 100 for item in robustness)
    assert all(item["image"]["height"] == 80 for item in robustness)
    page = json.loads(
        (tmp_path / "first" / "modern-page-ocr-v1" / "gold.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert page["split"] == "test"
    assert page["metadata"]["source_corpus_split"] == "test"
    assert page["metadata"]["benchmark_split_policy"] == "locked_all_test_v1"
