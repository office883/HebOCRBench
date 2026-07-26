from hebocrbench.alignment import align_sequences, error_rate


def test_alignment_counts_gold_to_prediction_operations():
    result = align_sequences(list("שלום"), list("שלם"))
    assert result.substitutions == 0
    assert result.deletions == 1
    assert result.insertions == 0
    assert result.correct == 3
    assert result.distance == 1
    assert error_rate(result) == 0.25


def test_alignment_handles_grapheme_sequences():
    result = align_sequences(["שָ", "ל"], ["שַ", "ל"])
    assert result.substitutions == 1
    assert result.correct == 1
    assert result.confusions[("שָ", "שַ")] == 1


def test_empty_reference_does_not_divide_by_zero():
    result = align_sequences([], ["א", "ב"])
    assert result.insertions == 2
    assert error_rate(result) == 2.0
