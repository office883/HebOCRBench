from __future__ import annotations

from hebocrbench.converters import _modern_pdf_engine as engine
from hebocrbench.converters._modern_pdf_geometry import visual_words_to_logical


def _word(text: str, x0: float, source_word: int) -> engine._Word:
    return engine._Word(
        x0=x0,
        y0=10.0,
        x1=x0 + 12.0,
        y1=20.0,
        text=text,
        block=0,
        source_line=0,
        source_word=source_word,
    )


def test_visual_words_to_logical_preserves_ltr_run_inside_rtl_line() -> None:
    visual = [
        _word("חדש", 0.0, 4),
        _word("OCR", 20.0, 1),
        _word("v2", 40.0, 2),
        _word("2026", 60.0, 3),
        _word("סעיף", 80.0, 0),
    ]

    logical = visual_words_to_logical(engine, visual)

    assert [item.text for item in logical] == ["סעיף", "OCR", "v2", "2026", "חדש"]


def test_visual_words_to_logical_never_reverses_hebrew_word_characters() -> None:
    visual = [
        _word("חדש", 0.0, 2),
        _word("בדיקה", 20.0, 1),
        _word("מסמך", 40.0, 0),
    ]

    logical = visual_words_to_logical(engine, visual)

    assert [item.text for item in logical] == ["מסמך", "בדיקה", "חדש"]


def test_visual_words_reassemble_legacy_nun_from_contiguous_fragments() -> None:
    visual = [
        engine._Word(0.0, 10.0, 12.0, 20.0, "ית", 0, 0, 2),
        engine._Word(12.05, 10.0, 15.0, 20.0, "ð", 0, 0, 1),
        engine._Word(15.05, 10.0, 39.0, 20.0, "ראשו", 0, 0, 0),
    ]

    logical = visual_words_to_logical(engine, visual)

    assert [item.text for item in logical] == ["ראשונית"]


def test_visual_words_do_not_join_legacy_nun_across_real_word_gap() -> None:
    visual = [
        engine._Word(0.0, 10.0, 18.0, 20.0, "והל", 0, 0, 2),
        engine._Word(18.05, 10.0, 21.0, 20.0, "ð", 0, 0, 1),
        engine._Word(24.0, 10.0, 48.0, 20.0, "לאשר", 0, 0, 0),
    ]

    logical = visual_words_to_logical(engine, visual)

    assert [item.text for item in logical] == ["לאשר", "נוהל"]


def test_visual_words_join_legacy_nun_while_preserving_closing_bracket() -> None:
    visual = [
        engine._Word(0.0, 10.0, 18.0, 20.0, "וסח", 0, 0, 1),
        engine._Word(18.02, 10.0, 24.0, 20.0, "ð]", 0, 0, 0),
    ]

    logical = visual_words_to_logical(engine, visual)

    assert [item.text for item in logical] == ["נוסח]"]
