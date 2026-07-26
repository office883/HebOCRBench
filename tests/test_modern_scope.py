from hebocrbench.modern_scope import (
    contains_biblical_mark,
    modern_scope_issues,
    require_modern_hebrew_text,
)


def test_modern_scope_accepts_contemporary_mixed_bidi_text():
    text = "הבקשה ID-2026-17 תישלח ל־qa@example.com עד 14:30."
    assert contains_biblical_mark(text) is False
    assert modern_scope_issues(text) == ()
    assert require_modern_hebrew_text(text) == text


def test_modern_scope_rejects_yiddish_letters_and_biblical_accents():
    issues = modern_scope_issues("ווײַזט בְּרֵאשִׁ֖ית")
    assert "yiddish_codepoint" in issues
    assert "biblical_mark" in issues


def test_modern_scope_requires_hebrew_letters():
    issues = modern_scope_issues("OCR-v2.1 2026")
    assert "no_hebrew_letters" in issues
