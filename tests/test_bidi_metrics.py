import math

from hebocrbench.bidi_metrics import (
    bracket_metrics,
    ltr_run_metrics,
    pairwise_word_order_accuracy,
    visual_order_diagnostic,
)


def test_reversed_pure_hebrew_is_flagged_as_visual_order_not_rescored():
    result = visual_order_diagnostic("שלום", "םולש")
    assert result["visual_order_suspected"]
    assert result["logical_error_rate"] > result["visual_error_rate"]


def test_ltr_runs_preserve_internal_digit_and_version_order():
    good = ltr_run_metrics(
        "בשנת 2026 הופעלה OCR-v2.1.",
        "בשנת 2026 הופעלה OCR-v2.1.",
    )
    bad = ltr_run_metrics(
        "בשנת 2026 הופעלה OCR-v2.1.",
        "בשנת 6202 הופעלה 1.2v-RCO.",
    )
    assert good["exact_rate"] == 1.0
    assert good["numeric_exact_rate"] == 1.0
    assert bad["exact_rate"] < 1.0
    assert bad["numeric_exact_rate"] == 0.0


def test_pairwise_word_order_detects_swap():
    result = pairwise_word_order_accuracy("אחד שני שלושה", "אחד שלושה שני")
    assert math.isclose(result["accuracy"], 2 / 3)
    assert result["coverage"] == 1.0


def test_brackets_are_compared_semantically_not_by_visual_mirroring():
    good = bracket_metrics("סעיף 3(ב)[2]", "סעיף 3(ב)[2]")
    bad = bracket_metrics("סעיף 3(ב)[2]", "סעיף 3)ב([2]")
    assert good["exact"]
    assert not bad["exact"]
    assert bad["error_rate"] > 0


def test_noisy_text_is_not_called_visual_order_from_a_small_accidental_gain():
    result = visual_order_diagnostic(
        "בשנת 2026 הופעלה גרסה OCR-v2.1.",
        "OCR-v2.1 טקסט אקראי",
        min_visual_order_gain=0.25,
        max_visual_order_error_rate=0.25,
    )

    assert result["visual_order_gain"] > 0
    assert result["visual_error_rate"] > 0.25
    assert not result["visual_order_suspected"]
