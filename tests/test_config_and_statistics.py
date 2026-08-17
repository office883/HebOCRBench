from __future__ import annotations

from copy import deepcopy

from hebocrbench.baselines import perfect_prediction, reverse_text_prediction
from hebocrbench.config import BenchmarkConfig, load_benchmark_config
from hebocrbench.evaluator import evaluate_dataset
from hebocrbench.statistics import bootstrap_document_intervals


def test_default_config_loads_and_can_be_overridden(tmp_path):
    path = tmp_path / "benchmark.yaml"
    path.write_text(
        """
schema_version: '1.0'
matching:
  use_shared_ids: false
  line_iou_threshold: 0.42
conformance:
  min_exact_line_rate: 0.5
statistics:
  bootstrap_samples: 25
  seed: 7
""",
        encoding="utf-8",
    )
    config = load_benchmark_config(path)
    assert config.matching.use_shared_ids is False
    assert config.matching.line_iou_threshold == 0.42
    assert config.conformance.min_exact_line_rate == 0.5
    assert config.statistics.bootstrap_samples == 25
    assert config.statistics.seed == 7


def test_document_bootstrap_is_deterministic(gold_page):
    second = deepcopy(gold_page)
    second["page_id"] = "doc2-p1"
    second["document_id"] = "doc2"
    second["regions"][0]["lines"][0]["text"] = "אב"
    run = evaluate_dataset(
        [gold_page, second],
        [perfect_prediction(gold_page), reverse_text_prediction(second)],
    )
    first = bootstrap_document_intervals(run.pages, samples=40, seed=11)
    second_result = bootstrap_document_intervals(run.pages, samples=40, seed=11)
    assert first == second_result
    assert first["line_gcer"]["lower"] <= first["line_gcer"]["upper"]
    assert first["sampling_unit"] == "document"


def test_evaluator_emits_configured_bootstrap_intervals(gold_page):
    config = BenchmarkConfig.from_mapping({"statistics": {"bootstrap_samples": 10, "seed": 3}})
    run = evaluate_dataset([gold_page], [perfect_prediction(gold_page)], config=config)
    assert run.metrics["confidence_intervals"]["line_gcer"]["lower"] == 0.0
    assert run.metrics["confidence_intervals"]["line_gcer"]["upper"] == 0.0


def test_operational_metrics_aggregate_prediction_timings(gold_page):
    first = perfect_prediction(gold_page)
    first["timing_ms"] = 100.0
    second_gold = deepcopy(gold_page)
    second_gold["page_id"] = "doc1-p2"
    second = perfect_prediction(second_gold)
    second["timing_ms"] = 300.0
    run = evaluate_dataset([gold_page, second_gold], [first, second])
    operational = run.metrics["operational"]
    assert operational["timed_pages"] == 2
    assert operational["latency_ms_p50"] == 200.0
    assert operational["latency_ms_p95"] == 290.0
    assert operational["throughput_pages_per_minute"] == 300.0


def test_conformance_policy_can_separate_quality_from_hard_unicode_gates():
    config = BenchmarkConfig.from_mapping(
        {
            "conformance": {
                "gate_quality_metrics": False,
                "gate_all_bidi_controls": False,
                "min_visual_order_gain": 0.25,
                "max_visual_order_error_rate": 0.25,
                "max_unsafe_bidi_control_count": 0,
            }
        }
    )

    assert config.conformance.gate_quality_metrics is False
    assert config.conformance.gate_all_bidi_controls is False
    assert config.conformance.min_visual_order_gain == 0.25
    assert config.conformance.max_visual_order_error_rate == 0.25
    assert config.conformance.max_unsafe_bidi_control_count == 0
