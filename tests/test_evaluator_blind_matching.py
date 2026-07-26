from __future__ import annotations

from hebocrbench.config import BenchmarkConfig
from hebocrbench.evaluator import evaluate_page


def _region(region_id: str, x0: int, x1: int, text: str, reading_index: int):
    return {
        "region_id": region_id,
        "type": "paragraph",
        "polygon": [[x0, 0], [x1, 0], [x1, 100], [x0, 100]],
        "reading_index": reading_index,
        "lines": [
            {
                "line_id": f"{region_id}-line",
                "polygon": [[x0, 10], [x1, 10], [x1, 50], [x0, 50]],
                "text": text,
            }
        ],
    }


def _page(*, ids=("g-left", "g-right"), edge=None, swapped_geometry=False):
    first_x, second_x = ((110, 210), (0, 100)) if swapped_geometry else ((0, 100), (110, 210))
    regions = [
        _region(ids[0], *first_x, "אבג", 0),
        _region(ids[1], *second_x, "דהו", 1),
    ]
    return {
        "schema_version": "1.0",
        "page_id": "p1",
        "document_id": "d1",
        "track": "page_ocr_blind",
        "regions": regions,
        "reading_order": {"edges": [edge or [ids[0], ids[1]]]},
        "tables": [],
        "form_fields": [],
    }


def _blind_config() -> BenchmarkConfig:
    return BenchmarkConfig.from_mapping(
        {
            "matching": {
                "use_shared_ids": False,
                "line_iou_threshold": 0.3,
                "region_iou_threshold": 0.5,
            }
        }
    )


def test_blind_reading_order_uses_geometry_assignment_not_gold_ids():
    gold = _page()
    prediction = _page(ids=("pred-a", "pred-b"))

    result = evaluate_page(gold, prediction, config=_blind_config())

    assert result.metrics["layout"]["regions"]["f1"] == 1.0
    assert result.metrics["reading_order"]["edge_f1"] == 1.0
    assert result.metrics["reading_order"]["pairwise_accuracy"] == 1.0
    assert result.metrics["reading_order"]["assignment"]["prediction_to_gold_id"] == {
        "pred-a": "g-left",
        "pred-b": "g-right",
    }


def test_blind_reading_order_detects_swapped_prediction_edge_after_assignment():
    gold = _page()
    prediction = _page(ids=("pred-a", "pred-b"), edge=["pred-b", "pred-a"])

    result = evaluate_page(gold, prediction, config=_blind_config())

    assert result.metrics["reading_order"]["edge_f1"] == 0.0
    assert result.metrics["reading_order"]["pairwise_accuracy"] == 0.0


def test_unmatched_prediction_edges_reduce_reading_order_precision():
    gold = _page()
    prediction = _page(ids=("pred-a", "pred-b"))
    prediction["regions"].append(_region("extra", 220, 320, "זחט", 2))
    prediction["reading_order"] = {"edges": [["pred-a", "pred-b"], ["pred-b", "extra"]]}

    result = evaluate_page(gold, prediction, config=_blind_config())

    assert result.metrics["reading_order"]["edge_recall"] == 1.0
    assert result.metrics["reading_order"]["edge_precision"] == 0.5
    assert result.metrics["reading_order"]["edge_f1"] == 2 / 3
