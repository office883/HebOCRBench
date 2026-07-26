from copy import deepcopy

from hebocrbench.baselines import perfect_prediction, reverse_text_prediction
from hebocrbench.evaluator import evaluate_dataset


def test_slice_metrics_are_reported_by_track_and_vocalization(gold_page):
    second = deepcopy(gold_page)
    second["page_id"] = "doc2-p1"
    second["document_id"] = "doc2"
    second["track"] = "printed_modern"
    second["metadata"]["vocalization"] = "full"
    second["regions"][0]["lines"][0]["text"] = "שָׁלוֹם"

    run = evaluate_dataset(
        [gold_page, second],
        [perfect_prediction(gold_page), reverse_text_prediction(second)],
    )
    slices = run.metrics["slices"]
    assert "track=bidi_diagnostic" in slices
    assert "track=printed_modern" in slices
    assert "vocalization=full" in slices
    assert slices["track=bidi_diagnostic"]["line_gcer"] == 0.0
    assert slices["track=printed_modern"]["line_gcer"] > 0.0


def test_document_macro_is_not_replaced_by_micro_only(gold_page):
    second = deepcopy(gold_page)
    second["page_id"] = "doc2-p1"
    second["document_id"] = "doc2"
    second["regions"][0]["lines"][0]["text"] = "אב"
    run = evaluate_dataset(
        [gold_page, second],
        [perfect_prediction(gold_page), reverse_text_prediction(second)],
    )
    assert "macro_page_line_gcer" in run.metrics["recognition"]
    assert "macro_document_line_gcer" in run.metrics["recognition"]
