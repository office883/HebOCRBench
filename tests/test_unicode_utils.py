import unicodedata

import pytest

from hebocrbench.unicode_utils import (
    BIDI_CONTROLS,
    bidi_hygiene,
    classify_hebrew_mark,
    graphemes,
    has_hebrew_presentation_forms,
    normalize_strict,
    strip_hebrew_marks,
)


def test_normalize_strict_uses_nfc_and_removes_only_directional_controls():
    decomposed = "ש\u05b8\u05c1לו\u05b9ם"
    text = "\u200f" + decomposed + "\r\n2026"
    result = normalize_strict(text)
    assert result == unicodedata.normalize("NFC", decomposed) + "\n2026"
    assert "2026" in result
    assert not any(ch in BIDI_CONTROLS for ch in result)


def test_graphemes_keep_base_and_multiple_marks_together():
    assert graphemes("ש\u05b8\u05c1ל") == ["ש\u05b8\u05c1", "ל"]


@pytest.mark.parametrize(
    ("char", "expected"),
    [
        ("\u05b8", "vowel"),
        ("\u05bc", "dagesh_mapiq"),
        ("\u05c1", "shin_sin_dot"),
        ("\u05bd", "meteg_rafe"),
        ("\u0591", "cantillation"),
        ("\u05c4", "other_hebrew_mark"),
    ],
)
def test_classify_hebrew_mark(char, expected):
    assert classify_hebrew_mark(char) == expected


def test_strip_hebrew_marks_keeps_letters_punctuation_and_digits():
    assert strip_hebrew_marks("שָׁלוֹם־2026") == "שלום־2026"


def test_presentation_forms_are_detected():
    assert has_hebrew_presentation_forms("שׁלום")
    assert not has_hebrew_presentation_forms("שלום")


def test_bidi_hygiene_detects_unbalanced_and_invisible_controls():
    report = bidi_hygiene("abc\u2067שלום\u200b")
    assert report["bidi_control_count"] == 1
    assert report["bidi_override_count"] == 0
    assert report["unbalanced_isolates"] == 1
    assert report["zero_width_count"] == 1


def test_bidi_hygiene_separates_benign_marks_from_unsafe_controls():
    report = bidi_hygiene("אב\u200fג\u202eדה\u202c\u2067ו\u2069")

    assert report["bidi_control_count"] == 5
    assert report["bidi_mark_count"] == 1
    assert report["bidi_override_count"] == 1
    assert report["bidi_embedding_count"] == 1
    assert report["bidi_isolate_count"] == 2
    assert report["unsafe_bidi_control_count"] == 2
    assert report["unbalanced_embeddings"] == 0
    assert report["unbalanced_isolates"] == 0
