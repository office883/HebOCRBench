from __future__ import annotations

from hebocrbench.config import BenchmarkConfig
from hebocrbench.evaluator import evaluate_page
from hebocrbench.table_metrics import match_tables


def _cell(text: str, col: int = 0):
    return {
        "row_start": 0,
        "row_end": 1,
        "col_start": col,
        "col_end": col + 1,
        "text": text,
    }


def _table(table_id: str, x: int, text: str, *, polygon: bool = True):
    value = {
        "table_id": table_id,
        "n_rows": 1,
        "n_cols": 1,
        "cells": [_cell(text)],
    }
    if polygon:
        value["polygon"] = [[x, 0], [x + 100, 0], [x + 100, 100], [x, 100]]
    return value


def _page(tables):
    return {
        "schema_version": "1.0",
        "page_id": "p",
        "document_id": "d",
        "track": "tables_blind",
        "regions": [],
        "reading_order": {"edges": []},
        "tables": tables,
        "form_fields": [],
    }


def _blind():
    return BenchmarkConfig.from_mapping({"matching": {"use_shared_ids": False}})


def test_blind_tables_with_disjoint_ids_match_by_geometry():
    result = evaluate_page(
        _page([_table("gold", 0, "שלום")]),
        _page([_table("prediction", 0, "שלום")]),
        config=_blind(),
    )

    tables = result.metrics["tables"]
    assert tables["metric_family"] == "HebGrid-1.0"
    assert tables["matched_tables"] == 1
    assert tables["table_presence_precision"] == 1.0
    assert tables["table_presence_recall"] == 1.0
    assert tables["cell_text_gcer"] == 0.0


def test_geometry_prevents_identical_table_topologies_from_cross_matching():
    gold = [_table("g-left", 0, "שמאל"), _table("g-right", 300, "ימין")]
    prediction = [_table("p-right", 300, "ימין"), _table("p-left", 0, "שמאל")]

    assignment = match_tables(gold, prediction)

    assert {(m.gold_index, m.prediction_index) for m in assignment.matches} == {(0, 1), (1, 0)}


def test_structural_fallback_matches_without_table_polygons():
    gold = [_table("g", 0, "א", polygon=False)]
    prediction = [_table("p", 0, "א", polygon=False)]

    assignment = match_tables(gold, prediction)

    assert len(assignment.matches) == 1
    assert assignment.matches[0].method == "structure"


def test_missing_and_extra_tables_are_counted_as_presence_errors():
    gold = [_table("g1", 0, "א"), _table("g2", 300, "ב")]
    prediction = [_table("p1", 0, "א"), _table("extra", 600, "ג")]

    result = evaluate_page(_page(gold), _page(prediction), config=_blind()).metrics["tables"]

    assert result["matched_tables"] == 1
    assert result["missing_tables"] == 1
    assert result["hallucinated_tables"] == 1
    assert result["table_presence_precision"] == 0.5
    assert result["table_presence_recall"] == 0.5
