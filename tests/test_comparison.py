from __future__ import annotations

import csv
import json

from hebocrbench.baselines import perfect_prediction, reverse_text_prediction
from hebocrbench.comparison import write_comparison_artifacts
from hebocrbench.evaluator import evaluate_dataset
from hebocrbench.report import write_evaluation_artifacts


def test_comparison_writes_certified_ranking_and_rtl_report(tmp_path, gold_page):
    reports = tmp_path / "reports"
    write_evaluation_artifacts(
        evaluate_dataset([gold_page], [perfect_prediction(gold_page)]),
        reports / "perfect",
        model_manifest={"name": "Perfect baseline", "version": "1"},
    )
    write_evaluation_artifacts(
        evaluate_dataset([gold_page], [reverse_text_prediction(gold_page)]),
        reports / "reversed",
        model_manifest={"name": "Reversed baseline", "version": "1"},
    )

    paths = write_comparison_artifacts(reports, tmp_path / "comparison")
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    by_name = {row["run_id"]: row for row in payload["runs"]}
    assert by_name["perfect"]["certified_rank"] == 1
    assert by_name["perfect"]["conformance"] == "conformant"
    assert by_name["reversed"]["certified_rank"] is None
    assert by_name["reversed"]["visual_order_failure_count"] > 0

    with paths["csv"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["run_id"] for row in rows} == {"perfect", "reversed"}

    html = paths["html"].read_text(encoding="utf-8")
    assert '<html lang="he" dir="rtl">' in html
    assert "דירוג מאושר" in html
    assert "לא־תואם" in html
