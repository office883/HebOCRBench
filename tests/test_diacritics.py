from hebocrbench.diacritics import evaluate_diacritics


def test_missing_all_marks_has_zero_recall_but_no_hallucination():
    result = evaluate_diacritics("שָׁלוֹם", "שלום")
    assert result.base_pairs == 4
    assert result.reference_marks == 3
    assert result.predicted_marks == 0
    assert result.mark_recall == 0.0
    assert result.mark_precision == 1.0
    assert result.hallucinated_mark_rate == 0.0
    assert result.dropped_mark_rate == 1.0


def test_hallucinated_mark_on_unmarked_reference_is_reported():
    result = evaluate_diacritics("שלום", "שָׁלוֹם")
    assert result.hallucinated_unmarked_bases == 2
    assert result.hallucinated_mark_rate == 0.5


def test_mark_categories_are_broken_down():
    result = evaluate_diacritics("שָׁ֑", "שַׂ֑")
    assert result.by_category["vowel"]["substitutions"] == 1
    assert result.by_category["shin_sin_dot"]["substitutions"] == 1
    assert result.by_category["cantillation"]["correct"] == 1


def test_marks_on_unrecognized_bases_are_counted_as_misses_not_a_vacuous_pass():
    result = evaluate_diacritics("שָׁלוֹם", "Project Details")

    assert result.base_pairs == 0
    assert result.reference_bases == 4
    assert result.predicted_bases == 0
    assert result.reference_marks == 3
    assert result.predicted_marks == 0
    assert result.correct_marks == 0
    assert result.deletions == 3
    assert result.mark_recall == 0.0
    assert result.mark_f1 == 0.0
    assert result.by_category["vowel"]["deletions"] == 2
    assert result.by_category["shin_sin_dot"]["deletions"] == 1
    assert result.by_category["vowel"]["f1"] == 0.0


def test_marks_on_different_base_letters_cannot_match_each_other():
    result = evaluate_diacritics("שָ", "בָ")

    assert result.reference_marks == 1
    assert result.predicted_marks == 1
    assert result.correct_marks == 0
    assert result.deletions == 1
    assert result.insertions == 1
    assert result.mark_f1 == 0.0


def test_unvocalized_rashi_style_line_keeps_vacuous_no_mark_score():
    result = evaluate_diacritics("הערך צריך שיפוץ", "הערך צריך שיפוץ")

    assert result.reference_marks == 0
    assert result.predicted_marks == 0
    assert result.mark_f1 == 1.0
