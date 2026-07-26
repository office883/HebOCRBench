"""Normative scope checks for contemporary Modern Hebrew benchmark text."""

from __future__ import annotations

import unicodedata

from .unicode_utils import BIDI_CONTROLS, normalize_strict

# Hebrew cantillation marks and the two rare Masoretic upper/lower dots.
_BIBLICAL_CODEPOINTS = frozenset(range(0x0591, 0x05B0)) | {0x05C4, 0x05C5}
# Dedicated Yiddish orthographic letters: double-vav, vav-yod and double-yod.
_YIDDISH_CODEPOINTS = frozenset({0x05F0, 0x05F1, 0x05F2})


class ModernScopeError(ValueError):
    """Text is outside the benchmark's Modern Hebrew language contract."""


def contains_biblical_mark(text: str) -> bool:
    """Return whether text contains cantillation or Masoretic annotation marks."""

    return any(ord(char) in _BIBLICAL_CODEPOINTS for char in text)


def contains_yiddish_codepoint(text: str) -> bool:
    """Return whether text uses a dedicated Yiddish Hebrew-block letter."""

    return any(ord(char) in _YIDDISH_CODEPOINTS for char in text)


def modern_scope_issues(text: str) -> tuple[str, ...]:
    """Return stable issue codes for text outside the official Modern Hebrew scope."""

    normalized = normalize_strict(text)
    issues: list[str] = []
    if not any("\u05d0" <= char <= "\u05ea" for char in normalized):
        issues.append("no_hebrew_letters")
    if contains_yiddish_codepoint(normalized):
        issues.append("yiddish_codepoint")
    if contains_biblical_mark(normalized):
        issues.append("biblical_mark")
    if any(char in BIDI_CONTROLS for char in text):
        issues.append("bidi_control")
    if any(char == "\ufffd" or unicodedata.category(char) == "Co" for char in normalized):
        issues.append("invalid_unicode_content")
    return tuple(issues)


def require_modern_hebrew_text(text: str) -> str:
    """Normalize and return text, or reject it with explicit scope issue codes."""

    normalized = normalize_strict(text)
    issues = modern_scope_issues(text)
    if issues:
        raise ModernScopeError("text is outside Modern Hebrew scope: " + ", ".join(issues))
    return normalized


__all__ = [
    "ModernScopeError",
    "contains_biblical_mark",
    "contains_yiddish_codepoint",
    "modern_scope_issues",
    "require_modern_hebrew_text",
]
