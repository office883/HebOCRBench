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
        raise AssertionError("cheap page evidence must not run table detection")


def _evidence(page_number: int) -> dict[str, object]:
    return {
        "page_number": page_number,
        "usable": True,
        "form_signal": 0,
        "mixed_bidi": True,
        "hebrew_letters": 500,
        "table_count": 0,
    }


def test_form_patterns_are_reusable_across_pages_without_table_detection() -> None:
    text = "טופס שם מלא: ישראל ישראלי תאריך: 26.07.2026 חתימה: ______ " * 20
    first = MODULE._basic_page_evidence(FakePage(text, 0))
    second = MODULE._basic_page_evidence(FakePage(text, 1))

    assert first["form_signal"] >= 4
    assert second["form_signal"] == first["form_signal"]
    assert first["table_count"] == 0
    assert first["table_detection"] == "deferred_until_verified"


def test_preflight_preserves_each_page_rejection_reason(tmp_path, monkeypatch) -> None:
    evidence = [_evidence(1), _evidence(2)]
    candidate = {
        "document_id": "doc-1",
        "pdf_url": "https://example.invalid/doc.pdf",
        "document_type": "public_document",
    }

    monkeypatch.setattr(MODULE, "rank_page_candidates", lambda pages, maximum: [1, 2])

    def fake_convert(pdf_path, page_number: int, *args, **kwargs):
        if page_number == 1:
            raise MODULE.ModernPdfError(
                "independent extractors disagree: agreement=0.750000, minimum=0.980000, "
                "anchor_order=0.990000, anchor_content=1.000000, "
                "punctuation_content=0.750000"
            )
        return {"page_id": "accepted-page", "regions": [], "tables": []}

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
    assert records == {
        2: {"page_id": "accepted-page", "regions": [], "tables": []}
    }
    assert rejections == [
        {
            "page_number": 1,
            "error_type": "ModernPdfError",
            "reason": (
                "independent extractors disagree: agreement=0.750000, minimum=0.980000, "
                "anchor_order=0.990000, anchor_content=1.000000, "
                "punctuation_content=0.750000"
            ),
        }
    ]


def test_preflight_prefers_verified_table_page_after_lazy_detection(
    tmp_path, monkeypatch
) -> None:
    evidence = [_evidence(1), _evidence(2)]
    candidate = {
        "document_id": "doc-1",
        "pdf_url": "https://example.invalid/doc.pdf",
        "document_type": "public_document",
    }
    monkeypatch.setattr(MODULE, "rank_page_candidates", lambda pages, maximum: [1, 2])

    def fake_convert(pdf_path, page_number: int, *args, **kwargs):
        return {
            "page_id": f"page-{page_number}",
            "regions": [{"region_id": "r1"}],
            "tables": [{}] if page_number == 2 else [],
        }

    monkeypatch.setattr(MODULE, "convert_modern_pdf_page", fake_convert)

    accepted, records, rejections = MODULE._preflight_pages(
        tmp_path / "source.pdf",
        candidate,
        evidence,
        maximum_pages=1,
        minimum_agreement=0.98,
        dpi=200,
    )

    assert accepted == [2]
    assert list(records) == [2]
    assert rejections == []
    assert evidence[1]["table_count"] == 1
    assert evidence[1]["table_detection"] == "verified_page_conversion"


def test_parser_exposes_template_family_release_gate() -> None:
    args = MODULE.build_parser().parse_args(["--minimum-template-families", "17"])
    assert args.minimum_template_families == 17


def test_materializer_covers_all_relevant_knesset_pdf_entities() -> None:
    assert MODULE.DEFAULT_ENTITIES == (
        "KNS_DocumentAgenda",
        "KNS_DocumentBill",
        "KNS_DocumentCommitteeSession",
        "KNS_DocumentLaw",
        "KNS_DocumentPlenumSession",
        "KNS_DocumentQuery",
    )


@pytest.mark.parametrize(
    ("entity", "record_key", "prefix", "document_type"),
    [
        ("KNS_DocumentAgenda", "DocumentAgendaID", "agenda", "agenda_document"),
        ("KNS_DocumentBill", "DocumentBillID", "bill", "bill_document"),
        (
            "KNS_DocumentCommitteeSession",
            "DocumentCommitteeSessionID",
            "committee",
            "committee_session_document",
        ),
        ("KNS_DocumentLaw", "DocumentLawID", "law", "legislation_material"),
        (
            "KNS_DocumentPlenumSession",
            "DocumentPlenumSessionID",
            "plenum",
            "plenum_session_document",
        ),
        (
            "KNS_DocumentQuery",
            "DocumentQueryID",
            "query",
            "parliamentary_query_document",
        ),
    ],
)
def test_candidate_normalizes_knesset_urls_and_maps_entity_metadata(
    entity: str,
    record_key: str,
    prefix: str,
    document_type: str,
) -> None:
    row = {
        record_key: "12345",
        "ApplicationDesc": "PDF",
        "FilePath": r"https://fs.knesset.gov.il/25\\folder\\sample.pdf",
        "GroupTypeID": 7,
        "GroupTypeDesc": "מסמך בדיקה",
        "LastUpdatedDate": "2026-07-26T12:00:00",
    }

    candidate = MODULE._candidate(entity, row, minimum_knesset_number=20)

    assert candidate is not None
    assert candidate["document_id"] == f"knesset-{prefix}-12345"
    assert candidate["document_type"] == document_type
    assert candidate["pdf_url"] == "https://fs.knesset.gov.il/25/folder/sample.pdf"
    assert candidate["knesset_number"] == 25
