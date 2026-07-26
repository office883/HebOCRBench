from hebocrbench.text_metrics import evaluate_text


def test_exact_text_has_zero_error():
    result = evaluate_text("שלום עולם", "שלום עולם")
    assert result.exact
    assert result.codepoint.distance == 0
    assert result.grapheme.distance == 0
    assert result.word.distance == 0


def test_grapheme_metric_treats_base_plus_mark_as_one_user_character():
    result = evaluate_text("שָ", "שַ")
    assert result.codepoint.distance == 1
    assert result.codepoint.n_ref == 2
    assert result.grapheme.distance == 1
    assert result.grapheme.n_ref == 1


def test_strict_metric_does_not_fold_hebrew_maqaf_to_ascii_hyphen():
    result = evaluate_text("בית־ספר", "בית-ספר")
    assert result.grapheme.distance == 1
    assert result.punctuation.distance == 1
    assert not result.exact


def test_base_letter_profile_exposes_mark_only_failure():
    result = evaluate_text("שָׁלוֹם", "שלום")
    assert result.base_letter.distance == 0
    assert result.grapheme.distance > 0


def test_final_letter_confusion_is_counted():
    result = evaluate_text("מלך", "מלכ")
    assert result.final_letter_confusions == 1


def test_empty_reference_preserves_insertions():
    result = evaluate_text("", "אב")
    assert result.codepoint.insertions == 2
    assert result.codepoint_rate == 2.0
