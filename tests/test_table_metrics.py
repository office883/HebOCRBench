from hebocrbench.table_metrics import evaluate_table


def cell(r0, r1, c0, c1, text):
    return {
        "row_start": r0,
        "row_end": r1,
        "col_start": c0,
        "col_end": c1,
        "text": text,
    }


def test_logical_column_zero_is_independent_of_physical_x_position():
    gold = {
        "n_rows": 1,
        "n_cols": 2,
        "cells": [cell(0, 1, 0, 1, "שם"), cell(0, 1, 1, 2, "סכום")],
    }
    pred = {
        "n_rows": 1,
        "n_cols": 2,
        "cells": [cell(0, 1, 0, 1, "שם"), cell(0, 1, 1, 2, "סכום")],
    }
    result = evaluate_table(gold, pred)
    assert result["cell_span_f1"] == 1.0
    assert result["cell_text_gcer"] == 0.0


def test_swapped_logical_columns_damage_content_even_when_all_words_exist():
    gold = {
        "n_rows": 1,
        "n_cols": 2,
        "cells": [cell(0, 1, 0, 1, "שם"), cell(0, 1, 1, 2, "סכום")],
    }
    pred = {
        "n_rows": 1,
        "n_cols": 2,
        "cells": [cell(0, 1, 0, 1, "סכום"), cell(0, 1, 1, 2, "שם")],
    }
    result = evaluate_table(gold, pred)
    assert result["cell_span_f1"] == 1.0
    assert result["cell_exact_rate"] == 0.0
    assert result["cell_text_gcer"] > 0


def test_missing_merged_cell_is_counted():
    gold = {
        "n_rows": 2,
        "n_cols": 2,
        "cells": [cell(0, 1, 0, 2, "כותרת"), cell(1, 2, 0, 1, "א"), cell(1, 2, 1, 2, "ב")],
    }
    pred = {
        "n_rows": 2,
        "n_cols": 2,
        "cells": [cell(1, 2, 0, 1, "א"), cell(1, 2, 1, 2, "ב")],
    }
    result = evaluate_table(gold, pred)
    assert result["missing_cells"] == 1
    assert result["cell_span_recall"] == 2 / 3
