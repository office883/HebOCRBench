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
