import pytest

from hebocrbench.reading_order import (
    ReadingOrderCycleError,
    reading_order_metrics,
    topological_order,
)


def test_topological_order_respects_explicit_edges():
    assert topological_order(["r1", "r2", "r3"], [["r1", "r2"], ["r2", "r3"]]) == [
        "r1",
        "r2",
        "r3",
    ]


def test_cycle_is_rejected():
    with pytest.raises(ReadingOrderCycleError):
        topological_order(["r1", "r2"], [["r1", "r2"], ["r2", "r1"]])


def test_pairwise_order_penalizes_swapped_rtl_columns():
    result = reading_order_metrics(
        gold_nodes=["right", "left"],
        gold_edges=[["right", "left"]],
        prediction_nodes=["right", "left"],
        prediction_edges=[["left", "right"]],
    )
    assert result["edge_f1"] == 0.0
    assert result["pairwise_accuracy"] == 0.0


def test_transitive_precedence_is_scored():
    result = reading_order_metrics(
        gold_nodes=["a", "b", "c"],
        gold_edges=[["a", "b"], ["b", "c"]],
        prediction_nodes=["a", "b", "c"],
        prediction_edges=[["a", "c"], ["c", "b"]],
    )
    assert result["comparable_pairs"] == 3
    assert result["pairwise_accuracy"] < 1.0
