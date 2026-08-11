from __future__ import annotations

import csv
import json

import pytest

from hebocrbench.baselines import reverse_text_prediction
from hebocrbench.evaluator import evaluate_dataset
from hebocrbench.modern_score import ModernScoreError, load_modern_report_bundle
from hebocrbench.report import write_evaluation_artifacts


def test_report_writes_machine_and_rtl_human_artifacts(tmp_path, gold_page):
    run = evaluate_dataset([gold_page], [reverse_text_prediction(gold_page)])
    paths = write_evaluation_artifacts(run, tmp_path)
    expected = {
        "metrics",
        "per_page",
        "errors",
        "summary",
        "html",
        "run_manifest",
    }
    assert expected <= set(paths)
    metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    assert metrics["conformance"]["status"] == "non_conformant"
    html = paths["html"].read_text(encoding="utf-8")
    assert '<html lang="he" dir="rtl">' in html
    assert 'class="codepoints" dir="ltr"' in html
    assert "סדר Unicode לוגי" in html
    assert "לא־תואם" in html
    errors = [json.loads(line) for line in paths["errors"].read_text(encoding="utf-8").splitlines()]
    assert errors[0]["reference_codepoints"][0]["codepoint"].startswith("U+")
    with paths["summary"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row["metric"] == "recognition.line_gcer" for row in rows)


def test_run_manifest_records_unicode_and_dependency_versions(tmp_path, gold_page):
    run = evaluate_dataset([gold_page], [reverse_text_prediction(gold_page)])
    paths = write_evaluation_artifacts(run, tmp_path)
    manifest = json.loads(paths["run_manifest"].read_text(encoding="utf-8"))
    assert manifest["unicode_data_version"]
    assert manifest["libraries"]["rapidfuzz"]
    assert manifest["libraries"]["regex"]
    assert manifest["artifacts"]["metrics"]["sha256"]
    assert manifest["artifacts"]["metrics"]["size_bytes"] == paths["metrics"].stat().st_size


def test_modern_report_loader_rejects_metrics_tampering(tmp_path, gold_page):
    run = evaluate_dataset([gold_page], [reverse_text_prediction(gold_page)])
    paths = write_evaluation_artifacts(run, tmp_path)
    metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    metrics["recognition"]["line_gcer"] = 0.0
    paths["metrics"].write_text(
        json.dumps(metrics, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(ModernScoreError, match="metrics.json (?:size|hash) differs"):
        load_modern_report_bundle(tmp_path)


def test_run_manifest_records_modern_suite_evidence(tmp_path, gold_page):
    run = evaluate_dataset([gold_page], [reverse_text_prediction(gold_page)])
    evidence = {
        "suite_version": "1.0.0",
        "suite_fingerprint": "a" * 64,
        "track_id": "modern-bidi-v1",
        "gold_sha256": "b" * 64,
    }
    paths = write_evaluation_artifacts(run, tmp_path, suite_evidence=evidence)
    manifest = json.loads(paths["run_manifest"].read_text(encoding="utf-8"))
    assert manifest["benchmark_suite"] == evidence
