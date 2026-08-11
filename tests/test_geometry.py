import math

import hebocrbench.geometry as geometry
from hebocrbench.geometry import match_geometries, polygon_iou


def rect(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def test_polygon_iou_for_overlapping_rectangles():
    assert math.isclose(polygon_iou(rect(0, 0, 10, 10), rect(5, 0, 15, 10)), 1 / 3)


def test_hungarian_matching_maximizes_total_iou():
    gold = [{"polygon": rect(0, 0, 10, 10)}, {"polygon": rect(20, 0, 30, 10)}]
    pred = [{"polygon": rect(20, 0, 30, 10)}, {"polygon": rect(0, 0, 10, 10)}]
    result = match_geometries(gold, pred, iou_threshold=0.5)
    assert {(m.gold_index, m.prediction_index) for m in result.matches} == {(0, 1), (1, 0)}
    assert result.f1 == 1.0
    assert result.mean_iou == 1.0


def test_unmatched_and_split_merge_diagnostics_are_exposed():
    gold = [{"polygon": rect(0, 0, 20, 10)}]
    pred = [
        {"polygon": rect(0, 0, 10, 10)},
        {"polygon": rect(10, 0, 20, 10)},
    ]
    result = match_geometries(
        gold,
        pred,
        iou_threshold=0.4,
        diagnostic_overlap_threshold=0.2,
    )
    assert len(result.matches) == 1
    assert len(result.unmatched_prediction_indices) == 1
    assert result.split_gold_items == 1


def test_matching_normalizes_each_polygon_once(monkeypatch):
    calls = 0
    original = geometry._polygon

    def counted(points):
        nonlocal calls
        calls += 1
        return original(points)

    monkeypatch.setattr(geometry, "_polygon", counted)
    gold = [{"polygon": rect(index * 20, 0, index * 20 + 10, 10)} for index in range(3)]
    prediction = [{"polygon": rect(index * 20, 0, index * 20 + 10, 10)} for index in range(4)]

    match_geometries(gold, prediction)

    assert calls == len(gold) + len(prediction)
