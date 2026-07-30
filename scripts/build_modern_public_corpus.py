#!/usr/bin/env python3
"""Canonical materializer for contemporary Modern-Hebrew public PDFs.

The stable acquisition engine is wrapped with the v1 entity registry, URL
normalization, cheap page screening and fail-closed coverage policy. Expensive
structure extraction is deferred until a page has passed independent text-layer
verification.
"""

from __future__ import annotations

import argparse
import importlib.util
from collections.abc import Mapping
from pathlib import Path
import re
import unicodedata

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

_ORIGINAL_PREFLIGHT = _ENGINE._preflight_pages
_ORIGINAL_BUILD_PARSER = _ENGINE.build_parser
_ORIGINAL_MATERIALIZE = _ENGINE.materialize


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


def _basic_page_evidence(page) -> dict[str, object]:
    """Collect inexpensive page evidence without running table detection."""

    text = _ENGINE.normalize_strict(page.get_text("text", sort=False).replace("\r", "\n"))
    alphabetic = sum(char.isalpha() for char in text)
    hebrew_letters = len(_ENGINE.HEBREW_RE.findall(text))
    arabic_letters = sum(
        char.isalpha() and "ARABIC" in unicodedata.name(char, "") for char in text
    )
    form_signal = sum(1 for pattern in _ENGINE.FORM_PATTERNS if pattern.search(text))
    mixed_bidi = bool(_ENGINE.HEBREW_RE.search(text)) and bool(
        _ENGINE.LATIN_RE.search(text) or _ENGINE.NUMBER_RE.search(text)
    )
    strong = _ENGINE.first_strong_direction(text)
    usable = (
        hebrew_letters >= 120
        and hebrew_letters / max(1, alphabetic) >= 0.50
        and arabic_letters / max(1, alphabetic) <= 0.02
        and not _ENGINE.contains_biblical_mark(text)
        and not any(char in _ENGINE.BIDI_CONTROLS for char in text)
        and strong in {"rtl", "neutral"}
    )
    return {
        "page_number": page.number + 1,
        "usable": usable,
        "character_count": len(text),
        "hebrew_letters": hebrew_letters,
        "hebrew_letter_ratio": hebrew_letters / max(1, alphabetic),
        "arabic_letter_ratio": arabic_letters / max(1, alphabetic),
        "table_count": 0,
        "table_detection": "deferred_until_verified",
        "form_signal": form_signal,
        "mixed_bidi": mixed_bidi,
        "text_head": " ".join(text.split())[:400],
        "width": float(page.rect.width),
        "height": float(page.rect.height),
        "fonts": _ENGINE._page_fonts(page),
    }


def _preflight_pages(
    pdf_path: Path,
    candidate: Mapping[str, object],
    evidence: list[dict[str, object]],
    *,
    maximum_pages: int,
    minimum_agreement: float,
    dpi: int,
):
    """Verify extra candidates, then retain the most structurally useful pages."""

    _ENGINE.rank_page_candidates = globals()["rank_page_candidates"]
    _ENGINE.convert_modern_pdf_page = globals()["convert_modern_pdf_page"]
    probe_limit = min(
        len(evidence),
        max(maximum_pages, maximum_pages * 2, maximum_pages + 12),
    )
    accepted, records, rejections = _ORIGINAL_PREFLIGHT(
        pdf_path,
        candidate,
        evidence,
        maximum_pages=probe_limit,
        minimum_agreement=minimum_agreement,
        dpi=dpi,
    )
    evidence_by_page = {int(item["page_number"]): item for item in evidence}
    for page_number in accepted:
        item = evidence_by_page[page_number]
        item["table_count"] = len(records[page_number].get("tables", []))
        item["table_detection"] = "verified_page_conversion"

    def priority(page_number: int) -> tuple[int, int, int, int, int, int]:
        item = evidence_by_page[page_number]
        table_count = int(item.get("table_count", 0) or 0)
        form_signal = int(item.get("form_signal", 0) or 0)
        mixed = int(bool(item.get("mixed_bidi", False)))
        regions = len(records[page_number].get("regions", []))
        hebrew = int(item.get("hebrew_letters", 0) or 0)
        return (
            int(table_count > 0),
            int(form_signal > 0),
            mixed,
            table_count + form_signal + int(regions > 1),
            hebrew,
            -page_number,
        )

    selected = sorted(
        sorted(accepted, key=priority, reverse=True)[:maximum_pages]
    )
    return selected, {number: records[number] for number in selected}, rejections


def build_parser() -> argparse.ArgumentParser:
    parser = _ORIGINAL_BUILD_PARSER()
    parser.add_argument("--minimum-template-families", type=int, default=50)
    return parser


def materialize(args: argparse.Namespace) -> dict[str, object]:
    summary = _ORIGINAL_MATERIALIZE(args)
    minimum = int(getattr(args, "minimum_template_families", 0) or 0)
    if int(summary.get("template_family_count", 0)) >= minimum:
        return summary

    summary = dict(summary)
    summary["target_reached"] = False
    summary.setdefault("parameters", {})["minimum_template_families"] = minimum
    output = Path(args.output).resolve()
    _ENGINE._json_write(output / "evidence" / "summary.json", summary)
    _ENGINE.write_source_evidence(
        output,
        source_id=_ENGINE.SOURCE_ID,
        source_version=_ENGINE.SOURCE_VERSION,
        artifact_id=_ENGINE.ARTIFACT_ID,
        requested_revision=_ENGINE.SOURCE_VERSION,
        extra=summary,
    )
    raise _ENGINE.MaterializationError(
        "template-family coverage target was not reached: "
        f"{summary.get('template_family_count', 0)} < {minimum}"
    )


_ENGINE.DEFAULT_ENTITIES = DEFAULT_ENTITIES
_ENGINE.ENTITY_SPECS = ENTITY_SPECS
_ENGINE._record_id = _record_id
_ENGINE._document_type = _document_type
_ENGINE._normalize_public_url = _normalize_public_url
_ENGINE._candidate = _candidate
_ENGINE._basic_page_evidence = _basic_page_evidence
_ENGINE._preflight_pages = _preflight_pages
_ENGINE.build_parser = build_parser
_ENGINE.materialize = materialize

for _name in dir(_ENGINE):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_ENGINE, _name))

main = _ENGINE.main

if __name__ == "__main__":
    raise SystemExit(main())
