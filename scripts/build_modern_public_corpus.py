#!/usr/bin/env python3
"""Canonical materializer for contemporary Modern-Hebrew public PDFs.

The stable acquisition engine is wrapped with the v1 entity registry and URL
normalization policy. Keeping those catalog details declarative prevents API
schema additions from leaking into OCR scoring code.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from pathlib import Path
import re

_ENGINE_PATH = Path(__file__).with_name("_build_modern_public_corpus_engine.py")
_SPEC = importlib.util.spec_from_file_location("hebocrbench_public_corpus_engine", _ENGINE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load public-corpus engine: {_ENGINE_PATH}")
_ENGINE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ENGINE)

ENTITY_SPECS = {
    "KNS_DocumentAgenda": ("DocumentAgendaID", "agenda", "agenda_document"),
    "KNS_DocumentBill": ("DocumentBillID", "bill", "bill_document"),
    "KNS_DocumentCommitteeSession": (
        "DocumentCommitteeSessionID",
        "committee",
        "committee_session_document",
    ),
    "KNS_DocumentLaw": ("DocumentLawID", "law", "legislation_material"),
    "KNS_DocumentPlenumSession": (
        "DocumentPlenumSessionID",
        "plenum",
        "plenum_session_document",
    ),
    "KNS_DocumentQuery": (
        "DocumentQueryID",
        "query",
        "parliamentary_query_document",
    ),
}
DEFAULT_ENTITIES = tuple(ENTITY_SPECS)


def _record_id(entity: str, row: Mapping[str, object]) -> str | None:
    value = row.get(ENTITY_SPECS[entity][0])
    return str(value) if value not in (None, "") else None


def _document_type(entity: str) -> str:
    return ENTITY_SPECS[entity][2]


def _normalize_public_url(value: object) -> str:
    url = str(value or "").strip().replace("\\", "/")
    return re.sub(r"(?<!:)/{2,}", "/", url)


def _candidate(
    entity: str,
    row: Mapping[str, object],
    *,
    minimum_knesset_number: int,
) -> dict[str, object] | None:
    url = _normalize_public_url(row.get("FilePath"))
    if str(row.get("ApplicationDesc") or "").upper() != "PDF" or not url.lower().endswith(
        ".pdf"
    ):
        return None
    knesset_number = _ENGINE._knesset_number_from_url(url)
    if knesset_number is None or knesset_number < minimum_knesset_number:
        return None
    record_id = _record_id(entity, row)
    if record_id is None:
        return None
    group_type_id = row.get("GroupTypeID")
    group_description = str(row.get("GroupTypeDesc") or "מסמך ציבורי").strip()
    prefix = ENTITY_SPECS[entity][1]
    return {
        "document_id": f"knesset-{prefix}-{record_id}",
        "catalog_table": entity,
        "catalog_record_id": record_id,
        "group_type_id": group_type_id,
        "group_type_description": group_description,
        "title": group_description or f"מסמך הכנסת {record_id}",
        "document_type": _document_type(entity),
        "pdf_url": url,
        "knesset_number": knesset_number,
        "catalog_last_updated": str(row.get("LastUpdatedDate") or ""),
        "catalog_record": dict(row),
    }


_ENGINE.DEFAULT_ENTITIES = DEFAULT_ENTITIES
_ENGINE.ENTITY_SPECS = ENTITY_SPECS
_ENGINE._record_id = _record_id
_ENGINE._document_type = _document_type
_ENGINE._normalize_public_url = _normalize_public_url
_ENGINE._candidate = _candidate

for _name in dir(_ENGINE):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_ENGINE, _name))


def _preflight_pages(*args, **kwargs):
    # Preserve the public monkeypatch/test seam while delegating to the stable engine.
    _ENGINE.rank_page_candidates = globals()["rank_page_candidates"]
    _ENGINE.convert_modern_pdf_page = globals()["convert_modern_pdf_page"]
    return _ENGINE._preflight_pages(*args, **kwargs)


main = _ENGINE.main

if __name__ == "__main__":
    raise SystemExit(main())
