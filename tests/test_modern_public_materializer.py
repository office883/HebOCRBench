from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def test_preflight_preserves_each_page_rejection_reason(tmp_path, monkeypatch) -> None:
    evidence = [
        {"page_number": 1, "usable": True},
        {"page_number": 2, "usable": True},
    ]
    candidate = {
        "document_id": "doc-1",
        "pdf_url": "https://example.invalid/doc.pdf",
        "document_type": "public_document",
    }

    monkeypatch.setattr(MODULE, "rank_page_candidates", lambda pages, maximum: [1, 2])

    def fake_convert(*args, page_number: int, **kwargs):
        if page_number == 1:
            raise MODULE.ModernPdfError(
                "independent extractors disagree: agreement=0.750000, minimum=0.980000, "
                "anchor_order=0.990000, anchor_content=1.000000, "
                "punctuation_content=0.750000"
            )
        return {"page_id": "accepted-page"}

    monkeypatch.setattr(MODULE, "convert_modern_pdf_page", fake_convert)

    accepted, records, rejections = MODULE._preflight_pages(
        tmp_path / "source.pdf",
        candidate,
        evidence,
        maximum_pages=2,
        minimum_agreement=0.98,
        dpi=200,
    )

    assert accepted == [2]
    assert records == {2: {"page_id": "accepted-page"}}
    assert rejections == [
        {
            "page_number": 1,
            "error_type": "ModernPdfError",
            "reason": pytest.approx if False else (
                "independent extractors disagree: agreement=0.750000, minimum=0.980000, "
                "anchor_order=0.990000, anchor_content=1.000000, "
                "punctuation_content=0.750000"
            ),
        }
    ]
