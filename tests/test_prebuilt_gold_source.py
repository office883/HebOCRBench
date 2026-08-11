from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
import pytest

from hebocrbench.corpus_builder import BuildError, build_corpus
from hebocrbench.corpus_registry import load_registry


def _registry(path: Path) -> Path:
    payload = {
        "schema_version": "1.0",
        "registry_version": "1.0.0",
        "benchmark": "HebOCRBench",
        "sources": {
            "diag": {
                "title": "Diagnostic prebuilt gold",
                "version": "1.0.0",
                "task": "conformance",
                "track": "modern_bidi",
                "languages": ["he"],
                "script": "Hebr",
                "status": "diagnostic",
                "converter": "none",
                "homepage": "urn:test:diag",
                "citation": {"key": "diag", "text": "Diagnostic fixture"},
                "license": {
                    "spdx": "CC0-1.0",
                    "tier": "bundled",
                    "redistribution": "allowed",
                    "requires_acceptance": False,
                    "uri": "https://creativecommons.org/publicdomain/zero/1.0/",
                },
                "artifacts": [],
                "discovery": {
                    "annotation_globs": ["gold.jsonl"],
                    "image_roots": ["images"],
                    "exclude_globs": [],
                    "split_from_path": False,
                },
                "split": {
                    "strategy": "fixed",
                    "group_fields": ["document_id"],
                    "ratios": {"diagnostic": 1.0},
                    "seed": 1,
                },
                "metadata": {
                    "script_style": "modern_square_print",
                    "era": "modern",
                    "document_type": "diagnostic_card",
                    "layout_type": "single_line",
                    "vocalization": "none",
                    "source_type": "benchmark_generated",
                },
            }
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _source(root: Path, *, image_path: str = "images/page.png") -> Path:
    (root / "images").mkdir(parents=True)
    Image.new("RGB", (64, 32), "white").save(root / "images" / "page.png")
    record = {
        "schema_version": "1.0",
        "page_id": "diag-page",
        "document_id": "diag-doc",
        "split": "diagnostic",
        "track": "bidi_diagnostic",
        "image": {
            "path": image_path,
            "width": 64,
            "height": 32,
            "rotation_degrees": 0,
            "sha256": "0" * 64,
        },
        "metadata": {
            "languages": ["he"],
            "script": "Hebr",
            "script_style": "modern_square_print",
            "era": "modern",
            "document_type": "diagnostic_card",
            "layout_type": "single_line",
            "vocalization": "none",
        },
        "regions": [
            {
                "region_id": "r1",
                "type": "body",
                "polygon": [[0, 0], [64, 0], [64, 32], [0, 32]],
                "base_direction": "rtl",
                "reading_index": 0,
                "lines": [
                    {
                        "line_id": "l1",
                        "polygon": [[0, 0], [64, 0], [64, 32], [0, 32]],
                        "baseline": [[60, 24], [4, 24]],
                        "text": "עברית 2026",
                        "base_direction": "rtl",
                        "language": "he",
                        "tags": ["bidi:mixed"],
                    }
                ],
            }
        ],
        "reading_order": {"edges": []},
        "tables": [],
        "form_fields": [],
    }
    (root / "gold.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return root


def test_build_corpus_accepts_prebuilt_gold_and_rebinds_provenance(tmp_path: Path) -> None:
    registry = load_registry(_registry(tmp_path / "registry.json"))
    source = _source(tmp_path / "source")
    result = build_corpus(
        registry,
        {"diag": source},
        tmp_path / "build",
        source_ids={"diag"},
        accepted_source_ids=set(),
        benchmark_version="1.0.0",
        profile="diagnostic",
    )
    assert result.page_count == 1
    record = json.loads((result.output_root / "gold.jsonl").read_text(encoding="utf-8"))
    assert record["metadata"]["source_id"] == "diag"
    assert record["metadata"]["source_version"] == "1.0.0"
    assert record["metadata"]["source_annotation_path"] == "gold.jsonl#page_id=diag-page"
    assert record["image"]["path"].startswith("images/diag/")
    assert (result.output_root / record["image"]["path"]).is_file()
    assert record["image"]["sha256"] != "0" * 64
    assert record["track"] == "modern_bidi"


def test_prebuilt_gold_rejects_image_path_escape(tmp_path: Path) -> None:
    registry = load_registry(_registry(tmp_path / "registry.json"))
    source = _source(tmp_path / "source", image_path="../outside.png")
    (tmp_path / "outside.png").write_bytes(b"not-an-image")
    with pytest.raises(BuildError, match="escapes source root"):
        build_corpus(
            registry,
            {"diag": source},
            tmp_path / "build",
            source_ids={"diag"},
            accepted_source_ids=set(),
            benchmark_version="1.0.0",
            profile="diagnostic",
        )
