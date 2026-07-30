from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_modern_public_corpus", ROOT / "scripts" / "build_modern_public_corpus.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


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
def test_all_official_pdf_entities_are_mapped(
    entity: str, record_key: str, prefix: str, document_type: str
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


def test_default_entity_set_is_complete() -> None:
    assert MODULE.DEFAULT_ENTITIES == tuple(MODULE.ENTITY_SPECS)
