from __future__ import annotations

from copy import deepcopy
import json
import math
import random

import pytest

from hebocrbench.robustness_metrics import (
    RobustnessMetricsError,
    compute_paired_robustness,
)


def _page(
    parent: str,
    variant: str,
    level: str,
    *,
    control: bool = False,
    document_id: str | None = None,
    split: str = "test",
) -> dict[str, object]:
    return {
        "page_id": f"{parent}::{variant}",
        "document_id": document_id or f"doc-{parent}",
        "split": split,
        "track": "modern_robustness",
        "metadata": {
            "parent_page_id": parent,
            "parent_image_sha256": f"sha-{parent}",
            "source_id": "public-modern",
            "source_version": "1",
            "source_page_id": parent,
            "document_type": "fixture",
            "template_family": "fixture-template",
            "source_collection": "fixture",
            "degradation_family": variant,
            "degradation_level": level,
            "degradation_is_control": control,
        },
    }


def _gold() -> list[dict[str, object]]:
    return [
        _page("a", "clean", "control", control=True),
        _page("a", "blur", "medium"),
        _page("a", "jpeg", "strong"),
        _page("b", "clean", "control", control=True),
        _page("b", "blur", "medium"),
        _page("b", "jpeg", "strong"),
    ]


def _metadata_update(page: dict[str, object], **updates: object) -> dict[str, object]:
    page["metadata"].update(updates)
    return page


def _metrics() -> list[dict[str, object]]:
    values = {
        "a::clean": (0.2, 0.1),
        "a::blur": (0.5, 0.3),
        "a::jpeg": (0.3, 0.2),
        "b::clean": (0.1, 0.2),
        "b::blur": (0.4, 0.5),
        "b::jpeg": (0.2, 0.1),
    }
    return [
        {"page_id": page_id, "page_order_gcer": page, "line_gcer": line}
        for page_id, (page, line) in values.items()
    ]


def test_computes_paired_deltas_macro_and_slices() -> None:
    result = compute_paired_robustness(_gold(), _metrics())

    assert result["metric_family"] == "HebRobustnessPairs-1.0"
    assert result["coverage"]["pair_coverage"] == 1.0
    assert result["coverage"]["parents_fully_scored"] == 2
    assert result["failures"] == {"count": 0, "pairs": []}

    pairs = {pair["child_page_id"]: pair for pair in result["pairs"]}
    assert pairs["a::blur"]["metrics"]["line_gcer"]["delta"] == pytest.approx(0.2)
    assert pairs["b::jpeg"]["metrics"]["line_gcer"]["delta"] == pytest.approx(-0.1)

    macro_line = result["summary"]["macro"]["metrics"]["line_gcer"]
    assert macro_line["count"] == 4
    assert macro_line["mean_delta"] == pytest.approx(0.125)
    assert macro_line["median_delta"] == pytest.approx(0.15)
    assert macro_line["p90_delta"] == pytest.approx(0.27)

    blur = result["summary"]["by_family"]["blur"]
    assert blur["scored_pairs"] == 2
    assert blur["metrics"]["page_order_gcer"]["mean_delta"] == pytest.approx(0.3)
    assert result["summary"]["by_level"]["strong"]["expected_pairs"] == 2


def test_accepts_serialized_evaluator_rows() -> None:
    nested = [
        {
            "page_id": row["page_id"],
            "metrics": {
                "recognition": {
                    "page_order_gcer": row["page_order_gcer"],
                    "line_gcer": row["line_gcer"],
                }
            },
        }
        for row in _metrics()
    ]
    result = compute_paired_robustness(_gold(), nested)
    assert result["coverage"]["scored_pairs"] == 4


def test_result_is_deterministic_under_input_reordering() -> None:
    gold = _gold()
    metrics = _metrics()
    expected = compute_paired_robustness(gold, metrics)
    random.Random(91).shuffle(gold)
    random.Random(47).shuffle(metrics)
    actual = compute_paired_robustness(gold, metrics)
    assert actual == expected
    json.dumps(actual, ensure_ascii=False, allow_nan=False)


def test_missing_invalid_and_extra_rows_are_reported_as_failed_pairs() -> None:
    metrics = _metrics()
    metrics = [row for row in metrics if row["page_id"] != "a::jpeg"]
    for row in metrics:
        if row["page_id"] == "b::clean":
            row["line_gcer"] = math.nan
    metrics.append({"page_id": "not-gold", "page_order_gcer": 0.0, "line_gcer": 0.0})

    result = compute_paired_robustness(_gold(), metrics)

    coverage = result["coverage"]
    assert coverage["missing_metric_page_ids"] == ["a::jpeg"]
    assert coverage["invalid_metric_page_ids"] == ["b::clean"]
    assert coverage["extra_metric_page_ids"] == ["not-gold"]
    assert coverage["expected_pairs"] == 4
    assert coverage["scored_pairs"] == 1
    assert coverage["failed_pairs"] == 3
    assert coverage["pair_coverage"] == 0.25
    assert result["failures"]["count"] == 3
    failures = {item["child_page_id"]: item["reasons"] for item in result["failures"]["pairs"]}
    assert failures["a::jpeg"] == ["child:missing_evaluation"]
    assert failures["b::blur"] == ["control:invalid_metric:line_gcer"]
    assert failures["b::jpeg"] == ["control:invalid_metric:line_gcer"]
    assert result["summary"]["macro"]["metrics"]["line_gcer"]["count"] == 1


@pytest.mark.parametrize(
    "records,match",
    [
        ([_page("a", "blur", "medium")], "exactly one clean control"),
        ([_page("a", "clean", "control", control=True)], "at least one degraded child"),
        (
            [
                _page("a", "clean", "control", control=True),
                _metadata_update(_page("a", "clean-copy", "medium"), degradation_family="clean"),
            ],
            "cannot use degradation_family 'clean'",
        ),
    ],
)
def test_rejects_invalid_control_child_structure(
    records: list[dict[str, object]], match: str
) -> None:
    with pytest.raises(RobustnessMetricsError, match=match):
        compute_paired_robustness(records, [])


@pytest.mark.parametrize("field", ["split", "document_id", "parent_image_sha256"])
def test_rejects_children_outside_control_split_or_ancestry(field: str) -> None:
    control = _page("a", "clean", "control", control=True)
    child = _page("a", "blur", "medium")
    if field == "split":
        child["split"] = "validation"
    elif field == "document_id":
        child["document_id"] = "different-document"
    else:
        child["metadata"][field] = "different-sha"

    with pytest.raises(RobustnessMetricsError):
        compute_paired_robustness([control, child], [])


def test_rejects_duplicate_gold_and_evaluation_page_ids() -> None:
    gold = _gold()
    with pytest.raises(RobustnessMetricsError, match="duplicate gold page_id"):
        compute_paired_robustness([gold[0], deepcopy(gold[0])], [])

    with pytest.raises(RobustnessMetricsError, match="duplicate evaluation page_id"):
        compute_paired_robustness(gold, [_metrics()[0], deepcopy(_metrics()[0])])
