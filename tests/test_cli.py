from __future__ import annotations

import json

from hebocrbench.cli import main


def test_cli_generate_validate_baseline_and_evaluate(tmp_path):
    dataset = tmp_path / "dataset"
    assert main(["generate", "--output", str(dataset), "--limit", "3", "--variants", "clean"]) == 0
    assert (dataset / "gold.jsonl").exists()
    assert main(["validate", "--gold", str(dataset / "gold.jsonl"), "--dataset-root", str(dataset)]) == 0

    predictions = tmp_path / "perfect.jsonl"
    assert main([
        "baseline",
        "--gold", str(dataset / "gold.jsonl"),
        "--kind", "perfect",
        "--output", str(predictions),
    ]) == 0
    report = tmp_path / "report"
    assert main([
        "evaluate",
        "--gold", str(dataset / "gold.jsonl"),
        "--predictions", str(predictions),
        "--output", str(report),
    ]) == 0
    metrics = json.loads((report / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["recognition"]["line_gcer"] == 0.0
    assert metrics["conformance"]["status"] == "conformant"


def test_cli_sanity_matrix_catches_seeded_failures(tmp_path):
    output = tmp_path / "sanity"
    assert main([
        "sanity",
        "--output", str(output),
        "--variants", "clean",
        "--limit", "28",
    ]) == 0
    result = json.loads((output / "sanity_matrix.json").read_text(encoding="utf-8"))
    assert result["passed"] is True
    assert result["systems"]["perfect"]["conformance"] == "conformant"
    assert result["systems"]["reverse_text"]["visual_order_failure_rate"] > 0
    assert result["systems"]["strip_marks"]["base_letter_cer"] == 0.0
    assert result["systems"]["strip_marks"]["line_gcer"] > 0.0


def test_cli_evaluate_accepts_explicit_dataset_root_for_detached_gold(tmp_path):
    dataset = tmp_path / "dataset"
    assert main(["generate", "--output", str(dataset), "--limit", "1", "--variants", "clean"]) == 0

    detached = tmp_path / "inputs"
    detached.mkdir()
    detached_gold = detached / "gold.jsonl"
    detached_gold.write_text((dataset / "gold.jsonl").read_text(encoding="utf-8"), encoding="utf-8")

    predictions = tmp_path / "perfect.jsonl"
    assert main([
        "baseline",
        "--gold", str(detached_gold),
        "--kind", "perfect",
        "--output", str(predictions),
    ]) == 0

    report = tmp_path / "report"
    assert main([
        "evaluate",
        "--gold", str(detached_gold),
        "--dataset-root", str(dataset),
        "--predictions", str(predictions),
        "--output", str(report),
    ]) == 0


def test_cli_evaluate_copies_consistent_prediction_model_to_run_manifest(tmp_path):
    dataset = tmp_path / "dataset"
    assert main(["generate", "--output", str(dataset), "--limit", "1", "--variants", "clean"]) == 0
    predictions = tmp_path / "perfect.jsonl"
    assert main([
        "baseline",
        "--gold", str(dataset / "gold.jsonl"),
        "--kind", "perfect",
        "--output", str(predictions),
    ]) == 0

    report = tmp_path / "report"
    assert main([
        "evaluate",
        "--gold", str(dataset / "gold.jsonl"),
        "--dataset-root", str(dataset),
        "--predictions", str(predictions),
        "--output", str(report),
    ]) == 0
    manifest = json.loads((report / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["model"] == {"name": "perfect", "version": "1"}


def test_cli_compare_builds_leaderboard_from_report_root(tmp_path):
    dataset = tmp_path / "dataset"
    assert main(["generate", "--output", str(dataset), "--limit", "1", "--variants", "clean"]) == 0
    reports = tmp_path / "reports"
    for kind in ("perfect", "reverse_text"):
        predictions = tmp_path / f"{kind}.jsonl"
        assert main([
            "baseline", "--gold", str(dataset / "gold.jsonl"),
            "--kind", kind, "--output", str(predictions),
        ]) == 0
        assert main([
            "evaluate", "--gold", str(dataset / "gold.jsonl"),
            "--dataset-root", str(dataset),
            "--predictions", str(predictions),
            "--output", str(reports / kind),
        ]) == 0

    output = tmp_path / "comparison"
    assert main(["compare", "--reports", str(reports), "--output", str(output)]) == 0
    assert (output / "comparison.html").exists()
    payload = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert len(payload["runs"]) == 2
