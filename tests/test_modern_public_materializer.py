from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_modern_public_corpus",
    ROOT / "scripts" / "build_modern_public_corpus.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakePage:
    def __init__(self, text: str, number: int) -> None:
        self._text = text
        self.number = number
        self.rect = SimpleNamespace(width=595.0, height=842.0)

    def get_text(self, mode: str, *, sort: bool = False):
        if mode == "text":
            return self._text
        if mode == "dict":
            return {"blocks": []}
        raise AssertionError(mode)

    def find_tables(self):
        return SimpleNamespace(tables=[])


def test_form_patterns_are_reusable_across_pages() -> None:
    text = "טופס שם מלא: ישראל ישראלי תאריך: 26.07.2026 חתימה: ______ " * 20
    first = MODULE._basic_page_evidence(FakePage(text, 0))
    second = MODULE._basic_page_evidence(FakePage(text, 1))

    assert first["form_signal"] >= 4
    assert second["form_signal"] == first["form_signal"]
