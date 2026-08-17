from __future__ import annotations

from hebocrbench.baselines import perfect_prediction
from hebocrbench.config import BenchmarkConfig
from hebocrbench.evaluator import evaluate_dataset


def _policy(**overrides):
    conformance = {
        "diagnostic_track": "bidi_diagnostic",
        "gate_quality_metrics": False,
        "gate_all_bidi_controls": False,
        "min_exact_line_rate": 0.98,
        "min_ltr_run_exact_rate": 0.99,
        "min_numeric_exact_rate": 0.995,
        "min_bracket_exact_rate": 0.995,
        "min_visual_order_gain": 0.25,
        "max_visual_order_error_rate": 0.25,
        "max_visual_order_failure_count": 0,
        "max_bidi_control_count": 0,
        "max_unsafe_bidi_control_count": 0,
        "max_unbalanced_bidi_controls": 0,
    }
    conformance.update(overrides)
    return BenchmarkConfig.from_mapping({"conformance": conformance})


def test_ordinary_recognition_errors_lower_quality_without_blocking_rank(gold_page):
    prediction = perfect_prediction(gold_page)
    prediction["regions"][0]["lines"][0]["text"] = (
        "בשנת 2062 הופעלה גירסה OCR-v2.1."
    )

    run = evaluate_dataset([gold_page], [prediction], config=_policy())
    result = run.metrics["conformance"]

    assert result["status"] == "conformant"
    assert result["hard_failed_checks"] == []
    assert result["quality_failed_checks"]
    assert result["quality_status"] == "below_targets"
    assert result["quality_metrics_gated"] is False
    assert result["strict_line_exact_rate"] == 0.0


def test_legacy_policy_can_still_gate_quality_metrics(gold_page):
    prediction = perfect_prediction(gold_page)
    prediction["regions"][0]["lines"][0]["text"] = (
        "בשנת 2062 הופעלה גירסה OCR-v2.1."
    )

    run = evaluate_dataset(
        [gold_page],
        [prediction],
        config=_policy(gate_quality_metrics=True),
    )
    result = run.metrics["conformance"]

    assert result["status"] == "non_conformant"
    assert result["hard_failed_checks"] == []
    assert result["quality_failed_checks"]
    assert result["failed_checks"] == result["quality_failed_checks"]


def test_directional_marks_are_reported_as_quality_without_hard_failure(gold_page):
    prediction = perfect_prediction(gold_page)
    line = prediction["regions"][0]["lines"][0]
    line["text"] = "\u200f" + line["text"] + "\u200e"

    run = evaluate_dataset([gold_page], [prediction], config=_policy())
    result = run.metrics["conformance"]

    assert result["status"] == "conformant"
    assert result["bidi_control_count"] == 2
    assert result["unsafe_bidi_control_count"] == 0
    assert result["hard_failed_checks"] == []
    assert any("bidi_control_count" in item for item in result["quality_failed_checks"])


def test_bidi_overrides_remain_a_hard_failure(gold_page):
    prediction = perfect_prediction(gold_page)
    line = prediction["regions"][0]["lines"][0]
    line["text"] = "\u202e" + line["text"] + "\u202c"

    run = evaluate_dataset([gold_page], [prediction], config=_policy())
    result = run.metrics["conformance"]

    assert result["status"] == "non_conformant"
    assert result["unsafe_bidi_control_count"] == 2
    assert any("unsafe_bidi_control_count" in item for item in result["hard_failed_checks"])


def test_balanced_isolates_are_allowed_but_unbalanced_controls_fail(gold_page):
    balanced = perfect_prediction(gold_page)
    balanced_line = balanced["regions"][0]["lines"][0]
    balanced_line["text"] = "\u2067" + balanced_line["text"] + "\u2069"

    balanced_result = evaluate_dataset([gold_page], [balanced], config=_policy()).metrics[
        "conformance"
    ]
    assert balanced_result["status"] == "conformant"
    assert balanced_result["unsafe_bidi_control_count"] == 0
    assert balanced_result["unbalanced_bidi_controls"] == 0

    unbalanced = perfect_prediction(gold_page)
    unbalanced["regions"][0]["lines"][0]["text"] = (
        "\u2067" + unbalanced["regions"][0]["lines"][0]["text"]
    )
    unbalanced_result = evaluate_dataset(
        [gold_page], [unbalanced], config=_policy()
    ).metrics["conformance"]
    assert unbalanced_result["status"] == "non_conformant"
    assert any(
        "unbalanced_bidi_controls" in item
        for item in unbalanced_result["hard_failed_checks"]
    )


def test_clear_visual_order_storage_remains_a_hard_failure(gold_page):
    gold_page["regions"][0]["lines"][0]["text"] = "שלום"
    prediction = perfect_prediction(gold_page)
    prediction["regions"][0]["lines"][0]["text"] = "םולש"

    run = evaluate_dataset([gold_page], [prediction], config=_policy())
    result = run.metrics["conformance"]

    assert result["status"] == "non_conformant"
    assert result["visual_order_failure_count"] == 1
    assert any("visual_order_failure_count" in item for item in result["hard_failed_checks"])
