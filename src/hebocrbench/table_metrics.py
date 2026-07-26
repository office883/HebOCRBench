"""Identifier-independent logical-grid metrics for Hebrew structured documents."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

from .alignment import align_sequences, error_rate, merge_alignments
from .geometry import polygon_iou
from .unicode_utils import graphemes, normalize_strict

Cell = Mapping[str, object]
Span = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class TableMatch:
    gold_index: int
    prediction_index: int
    score: float
    method: str
    location_iou: float | None
    structure_score: float


@dataclass(frozen=True, slots=True)
class TableMatchResult:
    gold_count: int
    prediction_count: int
    matches: tuple[TableMatch, ...]
    unmatched_gold_indices: tuple[int, ...]
    unmatched_prediction_indices: tuple[int, ...]

    @property
    def precision(self) -> float:
        if self.gold_count == self.prediction_count == 0:
            return 1.0
        return len(self.matches) / max(1, self.prediction_count)

    @property
    def recall(self) -> float:
        if self.gold_count == self.prediction_count == 0:
            return 1.0
        return len(self.matches) / max(1, self.gold_count)

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)



def cell_span(cell: Cell) -> Span:
    return (
        int(cell["row_start"]),
        int(cell["row_end"]),
        int(cell["col_start"]),
        int(cell["col_end"]),
    )


def _f1(correct: int, predicted: int, reference: int) -> tuple[float, float, float]:
    if predicted == reference == 0:
        return 1.0, 1.0, 1.0
    precision = correct / max(1, predicted)
    recall = correct / max(1, reference)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def _count_accuracy(reference: int, prediction: int) -> float:
    return max(0.0, 1.0 - abs(reference - prediction) / max(1, reference, prediction))


def _spans(table: Mapping[str, object]) -> set[Span]:
    return {cell_span(cell) for cell in table.get("cells", [])}  # type: ignore[arg-type]


def _structure_similarity(
    gold: Mapping[str, object], prediction: Mapping[str, object]
) -> float:
    gold_spans = _spans(gold)
    pred_spans = _spans(prediction)
    _, _, span_f1 = _f1(len(gold_spans & pred_spans), len(pred_spans), len(gold_spans))
    return mean(
        (
            _count_accuracy(int(gold.get("n_rows", 0)), int(prediction.get("n_rows", 0))),
            _count_accuracy(int(gold.get("n_cols", 0)), int(prediction.get("n_cols", 0))),
            _count_accuracy(len(gold_spans), len(pred_spans)),
            span_f1,
        )
    )


def _content_similarity(
    gold: Mapping[str, object], prediction: Mapping[str, object]
) -> float:
    def ordered_text(table: Mapping[str, object]) -> str:
        cells = sorted(
            table.get("cells", []),  # type: ignore[arg-type]
            key=lambda cell: cell_span(cell),
        )
        return "\n".join(normalize_strict(str(cell.get("text", ""))) for cell in cells)

    alignment = align_sequences(
        graphemes(ordered_text(gold)), graphemes(ordered_text(prediction))
    )
    return max(0.0, 1.0 - min(1.0, error_rate(alignment)))


def table_similarity(
    gold: Mapping[str, object], prediction: Mapping[str, object]
) -> tuple[float, str, float | None, float]:
    """Return assignment similarity without consulting ``table_id``.

    When both sides publish a table polygon, location is authoritative: tables
    with no spatial overlap are not cross-matched merely because they share a
    common grid shape.  If polygons are unavailable, topology is primary and
    cell content is used only as a low-weight deterministic tie-breaker.
    """

    structure = _structure_similarity(gold, prediction)
    gold_polygon = gold.get("polygon")
    pred_polygon = prediction.get("polygon")
    if isinstance(gold_polygon, Sequence) and isinstance(pred_polygon, Sequence):
        location = polygon_iou(gold_polygon, pred_polygon)  # type: ignore[arg-type]
        if location <= 0.0:
            return 0.0, "geometry", 0.0, structure
        return 0.80 * location + 0.20 * structure, "geometry", location, structure
    content = _content_similarity(gold, prediction)
    return 0.85 * structure + 0.15 * content, "structure", None, structure


def match_tables(
    gold_tables: Sequence[Mapping[str, object]],
    prediction_tables: Sequence[Mapping[str, object]],
    *,
    threshold: float = 0.45,
) -> TableMatchResult:
    """Assign tables one-to-one with geometry-first Hungarian matching."""

    if not gold_tables or not prediction_tables:
        return TableMatchResult(
            gold_count=len(gold_tables),
            prediction_count=len(prediction_tables),
            matches=(),
            unmatched_gold_indices=tuple(range(len(gold_tables))),
            unmatched_prediction_indices=tuple(range(len(prediction_tables))),
        )

    matrix = np.zeros((len(gold_tables), len(prediction_tables)), dtype=float)
    evidence: dict[tuple[int, int], tuple[str, float | None, float]] = {}
    for gi, gold in enumerate(gold_tables):
        for pi, prediction in enumerate(prediction_tables):
            score, method, location, structure = table_similarity(gold, prediction)
            matrix[gi, pi] = score
            evidence[(gi, pi)] = (method, location, structure)

    rows, cols = linear_sum_assignment(-matrix)
    matches: list[TableMatch] = []
    matched_gold: set[int] = set()
    matched_prediction: set[int] = set()
    for gi, pi in zip(rows.tolist(), cols.tolist(), strict=True):
        score = float(matrix[gi, pi])
        if score < threshold:
            continue
        method, location, structure = evidence[(gi, pi)]
        matches.append(TableMatch(gi, pi, score, method, location, structure))
        matched_gold.add(gi)
        matched_prediction.add(pi)
    matches.sort(key=lambda item: (item.gold_index, item.prediction_index))
    return TableMatchResult(
        gold_count=len(gold_tables),
        prediction_count=len(prediction_tables),
        matches=tuple(matches),
        unmatched_gold_indices=tuple(
            index for index in range(len(gold_tables)) if index not in matched_gold
        ),
        unmatched_prediction_indices=tuple(
            index for index in range(len(prediction_tables)) if index not in matched_prediction
        ),
    )


def _grid_map(table: Mapping[str, object]) -> dict[tuple[int, int], Span]:
    grid: dict[tuple[int, int], Span] = {}
    for cell in table.get("cells", []):  # type: ignore[assignment]
        span = cell_span(cell)
        for row in range(span[0], span[1]):
            for col in range(span[2], span[3]):
                grid[(row, col)] = span
    return grid


def evaluate_table(gold: Mapping[str, object], prediction: Mapping[str, object]) -> dict[str, object]:
    gold_cells: Sequence[Cell] = gold.get("cells", [])  # type: ignore[assignment]
    pred_cells: Sequence[Cell] = prediction.get("cells", [])  # type: ignore[assignment]
    gold_by_span = {cell_span(cell): cell for cell in gold_cells}
    pred_by_span = {cell_span(cell): cell for cell in pred_cells}
    matching_spans = set(gold_by_span) & set(pred_by_span)
    span_precision, span_recall, span_f1 = _f1(
        len(matching_spans), len(pred_by_span), len(gold_by_span)
    )

    alignments = []
    exact = 0
    for span, gold_cell in gold_by_span.items():
        pred_cell = pred_by_span.get(span)
        gold_text = normalize_strict(str(gold_cell.get("text", "")))
        pred_text = "" if pred_cell is None else normalize_strict(str(pred_cell.get("text", "")))
        alignment = align_sequences(graphemes(gold_text), graphemes(pred_text))
        alignments.append(alignment)
        if pred_cell is not None and gold_text == pred_text:
            exact += 1
    for span, pred_cell in pred_by_span.items():
        if span not in gold_by_span:
            alignments.append(
                align_sequences([], graphemes(normalize_strict(str(pred_cell.get("text", "")))))
            )
    merged = merge_alignments(alignments)

    gold_grid = _grid_map(gold)
    pred_grid = _grid_map(prediction)
    correct_slots = sum(1 for slot, span in gold_grid.items() if pred_grid.get(slot) == span)
    if not gold_grid and not pred_grid:
        grid_slot_accuracy = 1.0
    else:
        grid_slot_accuracy = correct_slots / max(1, len(gold_grid))

    gold_rows = int(gold.get("n_rows", 0))
    pred_rows = int(prediction.get("n_rows", 0))
    gold_cols = int(gold.get("n_cols", 0))
    pred_cols = int(prediction.get("n_cols", 0))
    gold_polygon = gold.get("polygon")
    pred_polygon = prediction.get("polygon")
    location_iou = None
    if isinstance(gold_polygon, Sequence) and isinstance(pred_polygon, Sequence):
        location_iou = polygon_iou(gold_polygon, pred_polygon)  # type: ignore[arg-type]
    return {
        "gold_rows": gold_rows,
        "prediction_rows": pred_rows,
        "gold_columns": gold_cols,
        "prediction_columns": pred_cols,
        "row_count_accuracy": _count_accuracy(gold_rows, pred_rows),
        "column_count_accuracy": _count_accuracy(gold_cols, pred_cols),
        "cell_span_precision": span_precision,
        "cell_span_recall": span_recall,
        "cell_span_f1": span_f1,
        "grid_slot_accuracy": grid_slot_accuracy,
        "table_location_iou": location_iou,
        "cell_text_gcer": error_rate(merged),
        "cell_exact_rate": exact / max(1, len(gold_by_span)),
        "missing_cells": len(set(gold_by_span) - set(pred_by_span)),
        "hallucinated_cells": len(set(pred_by_span) - set(gold_by_span)),
        "text_alignment": merged.to_dict(),
        "metric_family": "HebGrid-1.0",
    }
