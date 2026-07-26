from copy import deepcopy

from hebocrbench.baselines import (
    empty_prediction,
    perfect_prediction,
    reverse_text_prediction,
    strip_marks_prediction,
    swap_region_order_prediction,
)
from hebocrbench.evaluator import evaluate_dataset, evaluate_page


def test_perfect_prediction_scores_zero_and_passes_bidi_gate(gold_page):
    prediction = perfect_prediction(gold_page)
    page = evaluate_page(gold_page, prediction)
    assert page.metrics["recognition"]["line_gcer"] == 0.0
    assert page.metrics["recognition"]["page_order_gcer"] == 0.0
    run = evaluate_dataset([gold_page], [prediction])
    assert run.metrics["conformance"]["status"] == "conformant"


def test_empty_prediction_is_not_silently_ignored(gold_page):
    page = evaluate_page(gold_page, empty_prediction(gold_page))
    assert page.metrics["recognition"]["line_gcer"] == 1.0
    assert page.metrics["recognition"]["line_exact_rate"] == 0.0


def test_reversed_hebrew_is_penalized_and_flagged(gold_page):
    gold_page["regions"][0]["lines"][0]["text"] = "שלום"
    page = evaluate_page(gold_page, reverse_text_prediction(gold_page))
    assert page.metrics["recognition"]["line_gcer"] > 0.5
    assert page.metrics["bidi"]["visual_order_failure_rate"] == 1.0


def test_mark_stripping_keeps_base_letters_but_fails_strict_gcer(gold_page):
    gold_page["regions"][0]["lines"][0]["text"] = "שָׁלוֹם"
    page = evaluate_page(gold_page, strip_marks_prediction(gold_page))
    assert page.metrics["recognition"]["base_letter_cer"] == 0.0
    assert page.metrics["recognition"]["line_gcer"] > 0.0
    assert page.metrics["diacritics"]["mark_recall"] == 0.0


def test_swapped_columns_preserve_line_recognition_but_fail_page_order(gold_page):
    first = gold_page["regions"][0]
    first["region_id"] = "right"
    first["reading_index"] = 0
    first["lines"][0]["line_id"] = "right-l1"
    first["lines"][0]["text"] = "עמודה ימנית"
    second = deepcopy(first)
    second["region_id"] = "left"
    second["reading_index"] = 1
    second["polygon"] = [[40, 40], [560, 40], [560, 360], [40, 360]]
    second["lines"][0]["line_id"] = "left-l1"
    second["lines"][0]["text"] = "עמודה שמאלית"
    first["polygon"] = [[640, 40], [1160, 40], [1160, 360], [640, 360]]
    gold_page["regions"] = [first, second]
    gold_page["reading_order"]["edges"] = [["right", "left"]]

    prediction = swap_region_order_prediction(gold_page)
    page = evaluate_page(gold_page, prediction)
    assert page.metrics["recognition"]["line_gcer"] == 0.0
    assert page.metrics["recognition"]["page_order_gcer"] > 0.0
    assert page.metrics["reading_order"]["pairwise_accuracy"] == 0.0


def test_dataset_accounts_for_missing_and_extra_pages(gold_page):
    extra = perfect_prediction(gold_page)
    extra["page_id"] = "extra-page"
    run = evaluate_dataset([gold_page], [extra])
    assert run.metrics["coverage"]["missing_prediction_pages"] == 1
    assert run.metrics["coverage"]["extra_prediction_pages"] == 1
    assert run.metrics["recognition"]["line_gcer"] == 1.0
