"""Polygon matching and document-layout diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment
from shapely.geometry import Polygon
from shapely.errors import GEOSException


@dataclass(frozen=True, slots=True)
class GeometryMatch:
    gold_index: int
    prediction_index: int
    iou: float


@dataclass(slots=True)
class GeometryMatchResult:
    gold_count: int
    prediction_count: int
    matches: list[GeometryMatch]
    unmatched_gold_indices: list[int]
    unmatched_prediction_indices: list[int]
    split_gold_items: int = 0
    merged_prediction_items: int = 0

    @property
    def precision(self) -> float:
        return (
            1.0
            if self.prediction_count == 0 and self.gold_count == 0
            else len(self.matches) / max(1, self.prediction_count)
        )

    @property
    def recall(self) -> float:
        return (
            1.0
            if self.gold_count == 0 and self.prediction_count == 0
            else len(self.matches) / max(1, self.gold_count)
        )

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)

    @property
    def mean_iou(self) -> float:
        return 0.0 if not self.matches else sum(m.iou for m in self.matches) / len(self.matches)

    def to_dict(self) -> dict[str, object]:
        return {
            "gold_count": self.gold_count,
            "prediction_count": self.prediction_count,
            "matched": len(self.matches),
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "mean_iou": self.mean_iou,
            "unmatched_gold_indices": self.unmatched_gold_indices,
            "unmatched_prediction_indices": self.unmatched_prediction_indices,
            "split_gold_items": self.split_gold_items,
            "merged_prediction_items": self.merged_prediction_items,
            "matches": [
                {
                    "gold_index": match.gold_index,
                    "prediction_index": match.prediction_index,
                    "iou": match.iou,
                }
                for match in self.matches
            ],
        }


def _polygon(points: Sequence[Sequence[float]]) -> Polygon:
    if len(points) < 3:
        return Polygon()
    try:
        poly = Polygon(points)
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly
    except (ValueError, TypeError, GEOSException):
        return Polygon()


def polygon_iou(
    gold_polygon: Sequence[Sequence[float]], prediction_polygon: Sequence[Sequence[float]]
) -> float:
    gold = _polygon(gold_polygon)
    pred = _polygon(prediction_polygon)
    return _polygon_iou_objects(gold, pred)


def _polygon_iou_objects(gold: Polygon, pred: Polygon) -> float:
    """Compute IoU for already-normalized shapes without rebuilding them."""

    if gold.is_empty or pred.is_empty:
        return 0.0
    gold_min_x, gold_min_y, gold_max_x, gold_max_y = gold.bounds
    pred_min_x, pred_min_y, pred_max_x, pred_max_y = pred.bounds
    if (
        gold_max_x <= pred_min_x
        or pred_max_x <= gold_min_x
        or gold_max_y <= pred_min_y
        or pred_max_y <= gold_min_y
    ):
        return 0.0
    try:
        intersection = gold.intersection(pred).area
    except GEOSException:
        return 0.0
    if intersection <= 0:
        return 0.0
    # Inclusion/exclusion avoids a second expensive GEOS overlay operation.
    union = gold.area + pred.area - intersection
    return 0.0 if union <= 0 else float(intersection / union)


def polygon_bounds(points: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    poly = _polygon(points)
    return (0.0, 0.0, 0.0, 0.0) if poly.is_empty else tuple(map(float, poly.bounds))


def match_geometries(
    gold_items: Sequence[Mapping[str, object]],
    prediction_items: Sequence[Mapping[str, object]],
    *,
    polygon_key: str = "polygon",
    iou_threshold: float = 0.5,
    diagnostic_overlap_threshold: float = 0.1,
) -> GeometryMatchResult:
    gold_count = len(gold_items)
    pred_count = len(prediction_items)
    if gold_count == 0 or pred_count == 0:
        return GeometryMatchResult(
            gold_count=gold_count,
            prediction_count=pred_count,
            matches=[],
            unmatched_gold_indices=list(range(gold_count)),
            unmatched_prediction_indices=list(range(pred_count)),
        )

    # Normalize each polygon once.  The prior pairwise implementation rebuilt,
    # validated and repaired both shapes for every matrix cell, which made the
    # 4,900-page robustness track needlessly slow and memory hungry.
    gold_polygons = [
        _polygon(item.get(polygon_key, []))  # type: ignore[arg-type]
        for item in gold_items
    ]
    prediction_polygons = [
        _polygon(item.get(polygon_key, []))  # type: ignore[arg-type]
        for item in prediction_items
    ]
    matrix = np.zeros((gold_count, pred_count), dtype=float)
    for gi, gold in enumerate(gold_polygons):
        for pi, pred in enumerate(prediction_polygons):
            matrix[gi, pi] = _polygon_iou_objects(gold, pred)

    row_indices, col_indices = linear_sum_assignment(-matrix)
    matches: list[GeometryMatch] = []
    matched_gold: set[int] = set()
    matched_pred: set[int] = set()
    for gi, pi in zip(row_indices.tolist(), col_indices.tolist(), strict=True):
        iou = float(matrix[gi, pi])
        if iou >= iou_threshold:
            matches.append(GeometryMatch(gi, pi, iou))
            matched_gold.add(gi)
            matched_pred.add(pi)

    split_gold_items = sum(
        1
        for gi in range(gold_count)
        if int(np.count_nonzero(matrix[gi, :] >= diagnostic_overlap_threshold)) > 1
    )
    merged_prediction_items = sum(
        1
        for pi in range(pred_count)
        if int(np.count_nonzero(matrix[:, pi] >= diagnostic_overlap_threshold)) > 1
    )
    return GeometryMatchResult(
        gold_count=gold_count,
        prediction_count=pred_count,
        matches=matches,
        unmatched_gold_indices=[i for i in range(gold_count) if i not in matched_gold],
        unmatched_prediction_indices=[i for i in range(pred_count) if i not in matched_pred],
        split_gold_items=split_gold_items,
        merged_prediction_items=merged_prediction_items,
    )
