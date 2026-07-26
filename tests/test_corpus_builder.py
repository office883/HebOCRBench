from __future__ import annotations

import json
from pathlib import Path
import shutil

from PIL import Image
import pytest
import yaml

from hebocrbench.corpus_builder import BuildError, build_corpus
from hebocrbench.corpus_registry import load_registry
from hebocrbench.corpus_stats import compute_corpus_stats
from hebocrbench.io import load_jsonl


def _write_registry(path: Path) -> Path:
    payload = {
        "schema_version": "1.0",
        "registry_version": "fixture-1",
        "benchmark": "HebOCRBench",
        "sources": {
            "page-source": {
                "title": "PAGE fixture",
                "version": "1",
                "task": "end_to_end_ocr",
                "track": "modern_page_ocr",
                "languages": ["he", "en"],
                "script": "Hebr",
                "status": "core",
                "converter": "pagexml",
                "homepage": "https://example.invalid/page",
                "citation": {"key": "page-fixture", "text": "PAGE fixture citation"},
                "license": {
                    "spdx": "CC-BY-4.0",
                    "tier": "open",
                    "redistribution": "allowed",
                    "requires_acceptance": False,
                    "uri": "https://creativecommons.org/licenses/by/4.0/",
                },
                "artifacts": [],
                "discovery": {
                    "annotation_globs": ["**/*.xml"],
                    "image_roots": ["."],
                    "exclude_globs": [],
                    "split_from_path": True,
                },
                "split": {
                    "strategy": "upstream",
                    "group_fields": ["document_id"],
                    "upstream_map": {"train": "train"},
                },
                "metadata": {
                    "script_style": "modern_square_print",
                    "era": "modern",
                    "document_type": "manuscript",
                    "layout_type": "two_column",
                    "vocalization": "none",
                    "source_type": "scan",
                    "source_collection": "PAGE fixture",
                },
            },
            "alto-source": {
                "title": "ALTO fixture",
                "version": "1",
                "task": "recognition_and_layout",
                "track": "modern_page_ocr",
                "languages": ["he", "en"],
                "script": "Hebr",
                "status": "core",
                "converter": "alto",
                "homepage": "https://example.invalid/alto",
                "citation": {"key": "alto-fixture", "text": "ALTO fixture citation"},
                "license": {
                    "spdx": "CC-BY-NC-SA-4.0",
                    "tier": "research-nc",
                    "redistribution": "conditional",
                    "requires_acceptance": True,
                    "uri": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                },
                "artifacts": [],
                "discovery": {
                    "annotation_globs": ["**/*.xml"],
                    "image_roots": ["."],
                    "exclude_globs": [],
                    "split_from_path": True,
                },
                "split": {
                    "strategy": "upstream",
                    "group_fields": ["document_id"],
                    "upstream_map": {"dev": "dev"},
                },
                "metadata": {
                    "script_style": "modern_square_print",
                    "era": "modern",
                    "document_type": "book",
                    "layout_type": "two_column",
                    "vocalization": "mixed",
                    "source_type": "scan",
                    "source_collection": "ALTO fixture",
                },
            },
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _source_trees(tmp_path: Path) -> dict[str, Path]:
    page_root = tmp_path / "page-source"
    (page_root / "train").mkdir(parents=True)
    shutil.copy("tests/fixtures/page/sample.xml", page_root / "train" / "sample.xml")
    Image.new("RGB", (1200, 800), "white").save(page_root / "train" / "page.jpg")

    alto_root = tmp_path / "alto-source"
    (alto_root / "dev").mkdir(parents=True)
    shutil.copy("tests/fixtures/alto/sample.xml", alto_root / "dev" / "sample.xml")
    Image.new("RGB", (1000, 600), "white").save(alto_root / "dev" / "alto-page.png")
    return {"page-source": page_root, "alto-source": alto_root}


def test_build_corpus_materializes_real_sources_with_manifest_attribution_and_audit(tmp_path):
    registry = load_registry(_write_registry(tmp_path / "registry.yaml"))
    roots = _source_trees(tmp_path)
    output = tmp_path / "build"

    result = build_corpus(
        registry,
        roots,
        output,
        source_ids={"page-source", "alto-source"},
        accepted_source_ids={"alto-source"},
        benchmark_version="1.0.0",
        profile="fixture-public-research",
    )

    assert result.page_count == 2
    assert result.audit.is_valid
    assert result.dataset_fingerprint == json.loads((output / "manifest.json").read_text())["dataset_fingerprint"]
    assert (output / "gold.jsonl").exists()
    assert (output / "stats.json").exists()
    assert (output / "audit.json").exists()
    assert (output / "attribution.jsonl").exists()
    assert (output / "citations.bib").exists()
    assert (output / "licenses" / "page-source.txt").exists()
    assert (output / "licenses" / "alto-source.txt").exists()
    assert (output / "dataset.lock.json").exists()

    records = load_jsonl(output / "gold.jsonl")
    assert {record["split"] for record in records} == {"train", "dev"}
    assert {record["metadata"]["source_id"] for record in records} == {
        "page-source",
        "alto-source",
    }
    assert all((output / record["image"]["path"]).is_file() for record in records)
    assert all(record["image"].get("sha256") for record in records)
    assert all(not Path(record["metadata"]["source_annotation_path"]).is_absolute() for record in records)

    second = build_corpus(
        registry,
        roots,
        tmp_path / "build-2",
        source_ids={"alto-source", "page-source"},
        accepted_source_ids={"alto-source"},
        benchmark_version="1.0.0",
        profile="fixture-public-research",
    )
    assert second.dataset_fingerprint == result.dataset_fingerprint


def test_build_corpus_refuses_restricted_source_without_acceptance(tmp_path):
    registry = load_registry(_write_registry(tmp_path / "registry.yaml"))
    roots = _source_trees(tmp_path)

    with pytest.raises(BuildError, match="acceptance"):
        build_corpus(
            registry,
            roots,
            tmp_path / "build",
            source_ids={"alto-source"},
            accepted_source_ids=set(),
            benchmark_version="1.0.0",
            profile="research",
        )


def test_build_corpus_fails_when_source_has_no_annotations(tmp_path):
    registry = load_registry(_write_registry(tmp_path / "registry.yaml"))
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(BuildError, match="no annotations"):
        build_corpus(
            registry,
            {"page-source": empty},
            tmp_path / "build",
            source_ids={"page-source"},
            accepted_source_ids=set(),
            benchmark_version="1.0.0",
            profile="open",
        )


def test_compute_corpus_stats_reports_script_specific_coverage(gold_page):
    gold_page["regions"][0]["lines"][0]["text"] = "שָׁלוֹם 2026 OCR-v2.1 ״בדיקה״"
    gold_page["metadata"].update({"source_id": "fixture", "languages": ["he", "en"]})

    stats = compute_corpus_stats([gold_page])

    assert stats["pages"] == 1
    assert stats["hebrew_letters"] >= 8
    assert stats["combining_marks"] >= 3
    assert stats["mixed_bidi_lines"] == 1
    assert stats["numeric_runs"] >= 1
    assert stats["latin_runs"] >= 1
    assert stats["hebrew_punctuation"] >= 2


def test_build_corpus_accepts_locked_modern_pdf_manifests(tmp_path, monkeypatch, gold_page):
    payload = {
        "schema_version": "1.0",
        "registry_version": "fixture-modern-1",
        "benchmark": "HebOCRBench",
        "sources": {
            "modern-public": {
                "title": "Modern public documents",
                "version": "1",
                "task": "page_ocr",
                "track": "modern_page_ocr",
                "languages": ["he", "en"],
                "script": "Hebr",
                "status": "core",
                "converter": "modern-pdf",
                "homepage": "https://example.invalid/modern",
                "citation": {"key": "modern-public", "text": "Modern public fixture"},
                "license": {
                    "spdx": "LicenseRef-Public-Document",
                    "tier": "external-review",
                    "redistribution": "federated-only",
                    "requires_acceptance": False,
                },
                "artifacts": [],
                "discovery": {
                    "annotation_globs": ["manifests/*.json"],
                    "image_roots": ["."],
                    "exclude_globs": [],
                    "split_from_path": False,
                },
                "split": {
                    "strategy": "hash_group",
                    "group_fields": ["metadata.template_family"],
                    "ratios": {"train": 1.0},
                    "seed": 20260725,
                },
                "metadata": {
                    "script_style": "modern_square_print",
                    "era": "modern",
                    "document_type": "public_document",
                    "layout_type": "mixed",
                    "vocalization": "none",
                    "source_type": "digital_pdf",
                },
            }
        },
    }
    registry_path = tmp_path / "registry-modern.yaml"
    registry_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    registry = load_registry(registry_path)
    source_root = tmp_path / "modern-public"
    (source_root / "manifests").mkdir(parents=True)
    (source_root / "manifests" / "doc.json").write_text("{}", encoding="utf-8")

    def fake_converter(manifest_path, root, output_root, context):
        image_path = Path(output_root) / "images" / "modern-public" / "page.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1200, 800), "white").save(image_path)
        record = json.loads(json.dumps(gold_page))
        record["page_id"] = "modern-public-doc-p0001"
        record["document_id"] = "modern-public-doc"
        record["split"] = "train"
        record["track"] = "modern_page_ocr"
        record["image"]["path"] = "images/modern-public/page.png"
        record["image"]["sha256"] = __import__("hashlib").sha256(image_path.read_bytes()).hexdigest()
        record["metadata"].update({
            "source_id": context.source_id,
            "source_version": context.source_version,
            "source_url": context.source_url,
            "rights_uri": context.rights_uri,
            "redistribution": context.redistribution,
            "citation_key": context.citation_key,
            "license": context.license_expression,
            "source_annotation_path": "manifests/doc.json",
            "source_page_id": "manifests/doc.json#page=1",
            "source_image_path": "images/modern-public/page.png",
            "document_id_method": "locked_modern_pdf_manifest_v1",
            "template_family": "fixture-template",
            "era": "modern",
            "script_style": "modern_square_print",
        })
        return [record]

    monkeypatch.setattr("hebocrbench.corpus_builder.convert_modern_pdf_manifest", fake_converter)
    result = build_corpus(
        registry,
        {"modern-public": source_root},
        tmp_path / "modern-build",
        source_ids={"modern-public"},
        accepted_source_ids=set(),
        benchmark_version="1.0.0",
        profile="modern-hebrew-print-v1",
    )
    assert result.page_count == 1
    record = load_jsonl(tmp_path / "modern-build" / "gold.jsonl")[0]
    assert record["metadata"]["template_family"] == "fixture-template"
