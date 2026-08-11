from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image
import pytest

from hebocrbench import __version__
from hebocrbench.corpus_registry import load_registry
from hebocrbench.evaluator import evaluate_dataset
from hebocrbench.io import load_jsonl, sha256_file, write_json, write_jsonl
from hebocrbench.modern_suite import (
    DEFAULT_HEADLINE_TRACKS,
    build_modern_suite_lock,
    parse_modern_suite_lock,
    suite_evidence_for_track,
    with_suite_fingerprint,
)
from hebocrbench.official_score import OfficialScoreError, verify_and_combine_modern_reports
from hebocrbench.profiles import load_profiles, profile_fingerprint
from hebocrbench.report import write_evaluation_artifacts
from hebocrbench.tracks import load_track


PROFILE_ID = "modern-hebrew-print-v1"
_REGISTRY = load_registry()
_PROFILE = load_profiles(registry=_REGISTRY).profiles[PROFILE_ID]
PROFILE_FINGERPRINT = profile_fingerprint(_PROFILE)
REGISTRY_FINGERPRINT = _REGISTRY.fingerprint
MODEL_IDENTITY = {
    "system_id": "fixture-ocr::1",
    "family": "fixture-ocr",
    "name": "Fixture OCR",
    "version": "1",
}


def _inventory(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
        if path.name != "manifest.json"
    ]


def _metadata() -> dict[str, object]:
    return {
        "source_id": "official-score-fixture",
        "source_page_id": "source-page-1",
        "source_document_id": "source-document-1",
        "source_url": "https://example.invalid/official-score-fixture",
        "license": "CC0-1.0",
        "document_type": "fixture",
        "template_family": "fixture",
        "layout_type": "single_line",
        "source_type": "fixture",
        "vocalization": "none",
        "languages": ["he", "en"],
        "script": "Hebr",
        "script_style": "modern_square_print",
        "era": "modern",
        "source_collection": "official-score-fixture",
        "mixed_bidi": True,
    }


def _region(prefix: str, *, text: str) -> dict[str, object]:
    return {
        "region_id": f"{prefix}-region",
        "type": "body",
        "polygon": [[0, 0], [63, 0], [63, 31], [0, 31]],
        "base_direction": "rtl",
        "language": "he",
        "reading_index": 0,
        "lines": [
            {
                "line_id": f"{prefix}-line",
                "polygon": [[1, 1], [62, 1], [62, 30], [1, 30]],
                "baseline": [[62, 28], [1, 28]],
                "text": text,
                "base_direction": "rtl",
                "language": "he",
                "reading_index": 0,
            }
        ],
    }


def _table(prefix: str, *, text: str) -> dict[str, object]:
    return {
        "table_id": f"{prefix}-table",
        "region_id": f"{prefix}-region",
        "polygon": [[0, 0], [63, 0], [63, 31], [0, 31]],
        "n_rows": 1,
        "n_cols": 1,
        "cells": [
            {
                "row_start": 0,
                "row_end": 1,
                "col_start": 0,
                "col_end": 1,
                "text": text,
                "polygon": [[1, 1], [62, 1], [62, 30], [1, 30]],
            }
        ],
    }


def _gold_record(
    root: Path,
    track_id: str,
    *,
    suffix: str = "one",
    degradation: str | None = None,
) -> dict[str, object]:
    page_id = f"{track_id}-{suffix}"
    image_path = root / "images" / f"{page_id}.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 32), "white").save(image_path)
    text = "בדיקה 2026 OCR (תקין)"
    split = "diagnostic" if track_id == "modern-bidi-v1" else "test"
    gold_track = track_id.removesuffix("-v1").replace("-", "_")
    metadata = _metadata()
    if track_id == "modern-robustness-v1":
        assert degradation is not None
        metadata.update(
            {
                "parent_page_id": "robust-parent-1",
                "parent_image_sha256": "f" * 64,
                "degradation_family": "clean" if degradation == "clean" else "blur",
                "degradation_level": "control" if degradation == "clean" else "medium",
                "degradation_is_control": degradation == "clean",
                "degradation": [] if degradation == "clean" else [{"type": "blur"}],
            }
        )
    tables = [_table("gold", text="תא 2026")] if track_id == "modern-tables-v1" else []
    return {
        "schema_version": "1.0",
        "page_id": page_id,
        "document_id": (
            "robust-document-1" if track_id == "modern-robustness-v1" else f"document-{track_id}"
        ),
        "split": split,
        "track": gold_track,
        "image": {
            "path": image_path.relative_to(root).as_posix(),
            "width": 64,
            "height": 32,
            "rotation_degrees": 0,
            "sha256": sha256_file(image_path),
        },
        "metadata": metadata,
        "regions": [_region("gold", text=text)],
        "reading_order": {"edges": []},
        "tables": tables,
        "form_fields": [],
    }


def _prediction(gold: dict[str, object], track_id: str) -> dict[str, object]:
    text = "בדיקה 2026 OCR (תקין)"
    tables = [_table("pred", text="תא 2026")] if track_id == "modern-tables-v1" else []
    return {
        "schema_version": "1.0",
        "page_id": gold["page_id"],
        "regions": [_region("pred", text=text)],
        "reading_order": {"edges": []},
        "tables": tables,
        "form_fields": [],
        "timing_ms": 1.0,
        "status": "ok",
        "failure": None,
        "api_failures": 0,
        "model": {
            **MODEL_IDENTITY,
            "adapter": "blind-fixture",
            "oracle_layout": False,
        },
    }


def _write_certified_root(parent: Path, track_id: str) -> Path:
    root = parent / track_id
    if track_id == "modern-robustness-v1":
        records = [
            _gold_record(root, track_id, suffix="clean", degradation="clean"),
            _gold_record(root, track_id, suffix="blur", degradation="blur"),
        ]
    else:
        records = [_gold_record(root, track_id)]
    write_jsonl(root / "gold.jsonl", records)
    write_json(root / "stats.json", {"pages": len(records)})
    write_json(root / "audit.json", {"is_valid": True})
    dataset_fingerprint = hashlib.sha256(f"dataset:{track_id}".encode()).hexdigest()
    write_json(
        root / "dataset.lock.json",
        {
            "schema_version": "1.0",
            "dataset_fingerprint": dataset_fingerprint,
            "records_sha256": sha256_file(root / "gold.jsonl"),
            "stats_sha256": sha256_file(root / "stats.json"),
        },
    )
    write_json(
        root / "certification.json",
        {
            "schema_version": "1.0",
            "track_id": track_id,
            "dataset_fingerprint": dataset_fingerprint,
            "valid": True,
        },
    )
    manifest = {
        "schema_version": "1.0",
        "benchmark": "HebOCRBench",
        "benchmark_version": __version__,
        "profile": PROFILE_ID,
        "profile_scope": "track-component",
        "track_id": track_id,
        "dataset_fingerprint": dataset_fingerprint,
        "registry_fingerprint": REGISTRY_FINGERPRINT,
        "page_count": len(records),
        "files": _inventory(root),
    }
    write_json(root / "manifest.json", manifest)
    write_json(
        root / "FROZEN.json",
        {
            "schema_version": "1.0",
            "benchmark_version": __version__,
            "dataset_fingerprint": dataset_fingerprint,
            "manifest_sha256": sha256_file(root / "manifest.json"),
            "verified_files": len(manifest["files"]),
        },
    )
    write_json(
        root / "CERTIFIED.json",
        {
            "schema_version": "1.0",
            "certified": True,
            "benchmark_version": __version__,
            "registry_fingerprint": REGISTRY_FINGERPRINT,
            "dataset_fingerprint": dataset_fingerprint,
            "certification_sha256": sha256_file(root / "certification.json"),
        },
    )
    return root


def _selection_hash(records: list[dict[str, object]]) -> str:
    selected = [
        {
            "page_id": str(record["page_id"]),
            "image_sha256": str(record["image"]["sha256"]),
        }
        for record in sorted(records, key=lambda item: str(item["page_id"]))
    ]
    encoded = json.dumps(
        selected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write_report(
    reports_root: Path,
    track_id: str,
    track_root: Path,
    suite: Any,
) -> None:
    gold = sorted(
        load_jsonl(track_root / "gold.jsonl"),
        key=lambda record: str(record["page_id"]),
    )
    predictions = [_prediction(record, track_id) for record in gold]
    prediction_path = reports_root / "predictions" / f"{track_id}.jsonl"
    write_jsonl(prediction_path, predictions)
    spec = load_track(track_id)
    run = evaluate_dataset(gold, predictions, config=spec.benchmark_config)
    run.configuration.update(
        {
            "official_track_id": track_id,
            "official_track_version": spec.version,
            "official_track_fingerprint": spec.config_fingerprint,
            "evaluation_split": "diagnostic" if track_id == "modern-bidi-v1" else "test",
            "source_evaluation_pages": len(gold),
            "evaluated_evaluation_pages": len(gold),
            "evaluation_selection_sha256": _selection_hash(gold),
            "baseline_runner_schema_version": "1.0",
            "limited_smoke_run": False,
            "input_mode": (
                "blind_whole_line_image"
                if track_id == "modern-line-recognition-v1"
                else "blind_full_page_image"
            ),
            "gold_assistance": False,
            "oracle_layout": False,
        }
    )
    model_manifest = {
        **MODEL_IDENTITY,
        "runner": "hebocrbench.baseline_runner",
        "runner_schema_version": "1.0",
        "adapter": "blind-fixture",
        "oracle_layout": False,
    }
    write_evaluation_artifacts(
        run,
        reports_root / track_id,
        gold_path=track_root / "gold.jsonl",
        predictions_path=prediction_path,
        model_manifest=model_manifest,
        suite_evidence=suite_evidence_for_track(
            suite,
            track_id,
            track_root / "gold.jsonl",
        ),
    )


@pytest.fixture
def official_fixture(tmp_path: Path) -> tuple[dict[str, Path], Any, Path]:
    roots = {
        track_id: _write_certified_root(tmp_path / "roots", track_id)
        for track_id in DEFAULT_HEADLINE_TRACKS
    }
    suite = parse_modern_suite_lock(
        build_modern_suite_lock(
            roots,
            profile_id=PROFILE_ID,
            profile_fingerprint=PROFILE_FINGERPRINT,
            registry_fingerprint=REGISTRY_FINGERPRINT,
        )
    )
    reports_root = tmp_path / "reports"
    for track_id in DEFAULT_HEADLINE_TRACKS:
        _write_report(reports_root, track_id, roots[track_id], suite)
    return roots, suite, reports_root


def _read_manifest(report_root: Path) -> dict[str, Any]:
    return json.loads((report_root / "run_manifest.json").read_text(encoding="utf-8"))


def _write_manifest(report_root: Path, manifest: dict[str, Any]) -> None:
    write_json(report_root / "run_manifest.json", manifest)


def _refresh_prediction_evidence(report_root: Path, manifest: dict[str, Any]) -> None:
    prediction_path = Path(manifest["inputs"]["predictions"]["path"])
    manifest["inputs"]["predictions"].update(
        {
            "sha256": sha256_file(prediction_path),
            "size_bytes": prediction_path.stat().st_size,
        }
    )


def test_genuine_locked_inputs_are_recomputed_and_admitted(official_fixture) -> None:
    roots, suite, reports_root = official_fixture

    result = verify_and_combine_modern_reports(reports_root, roots, suite)

    assert result["score_admission"] == {
        "schema_version": "1.0",
        "status": "verified_recomputed",
        "component_roots_verified": True,
        "artifact_hashes_verified": True,
        "metrics_recomputed": True,
        "blind_input_contract_verified": True,
        "oracle_layout": False,
        "gold_assistance": False,
    }
    assert result["status"] == "rankable"


def test_tampered_metrics_and_matching_manifest_hash_are_rejected(official_fixture) -> None:
    roots, suite, reports_root = official_fixture
    report_root = reports_root / "modern-line-recognition-v1"
    metrics_path = report_root / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["recognition"]["line_gcer"] = 0.125
    write_json(metrics_path, metrics)
    manifest = _read_manifest(report_root)
    manifest["artifacts"]["metrics"].update(
        {
            "sha256": sha256_file(metrics_path),
            "size_bytes": metrics_path.stat().st_size,
        }
    )
    _write_manifest(report_root, manifest)

    with pytest.raises(OfficialScoreError, match="stored metrics differ"):
        verify_and_combine_modern_reports(reports_root, roots, suite)


def test_self_consistent_dummy_suite_lock_is_rejected_against_roots(official_fixture) -> None:
    roots, suite, reports_root = official_fixture
    dummy = deepcopy(suite.to_dict())
    dummy["tracks"]["modern-page-ocr-v1"]["dataset_fingerprint"] = "d" * 64
    dummy = with_suite_fingerprint(dummy)

    with pytest.raises(OfficialScoreError, match="component roots"):
        verify_and_combine_modern_reports(reports_root, roots, dummy)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("oracle_layout", True, "oracle layout is forbidden"),
        ("gold_assistance", True, "gold_assistance must be explicitly false"),
    ],
)
def test_privileged_configuration_is_rejected(
    official_fixture,
    field: str,
    value: bool,
    message: str,
) -> None:
    roots, suite, reports_root = official_fixture
    report_root = reports_root / "modern-page-ocr-v1"
    manifest = _read_manifest(report_root)
    manifest["configuration"][field] = value
    if field == "oracle_layout":
        manifest["model"][field] = value
    _write_manifest(report_root, manifest)

    with pytest.raises(OfficialScoreError, match=message):
        verify_and_combine_modern_reports(reports_root, roots, suite)


def test_prediction_reusing_gold_layout_identifier_is_rejected(official_fixture) -> None:
    roots, suite, reports_root = official_fixture
    report_root = reports_root / "modern-page-ocr-v1"
    manifest = _read_manifest(report_root)
    prediction_path = Path(manifest["inputs"]["predictions"]["path"])
    predictions = load_jsonl(prediction_path)
    gold = load_jsonl(roots["modern-page-ocr-v1"] / "gold.jsonl")
    predictions[0]["regions"][0]["region_id"] = gold[0]["regions"][0]["region_id"]
    write_jsonl(prediction_path, predictions)
    _refresh_prediction_evidence(report_root, manifest)
    _write_manifest(report_root, manifest)

    with pytest.raises(OfficialScoreError, match="reuses gold layout identifiers"):
        verify_and_combine_modern_reports(reports_root, roots, suite)


def test_incomplete_prediction_coverage_is_rejected(official_fixture) -> None:
    roots, suite, reports_root = official_fixture
    report_root = reports_root / "modern-line-recognition-v1"
    manifest = _read_manifest(report_root)
    prediction_path = Path(manifest["inputs"]["predictions"]["path"])
    write_jsonl(prediction_path, [])
    _refresh_prediction_evidence(report_root, manifest)
    _write_manifest(report_root, manifest)

    with pytest.raises(OfficialScoreError, match="prediction coverage is incomplete"):
        verify_and_combine_modern_reports(reports_root, roots, suite)
