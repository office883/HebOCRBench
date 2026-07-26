#!/usr/bin/env python3
"""Build a frozen source root of contemporary Modern Hebrew public PDFs.

The materializer discovers official Knesset PDF records, downloads immutable
bytes, accepts only pages whose Unicode/logical-order text layer passes the
HebOCRBench dual-extractor gate, and writes per-document manifests for the
normal corpus builder. It does not create benchmark gold directly: the normal
builder still performs conversion, deterministic split assignment, validation,
leakage audit, freeze and certification.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import fitz  # noqa: E402

from hebocrbench.bidi_metrics import first_strong_direction  # noqa: E402
from hebocrbench.converters import ConversionContext  # noqa: E402
from hebocrbench.converters.modern_pdf import (  # noqa: E402
    ModernPdfError,
    convert_modern_pdf_page,
)
from hebocrbench.modern_public import (  # noqa: E402
    rank_page_candidates,
    template_family_id,
    write_source_evidence,
)
from hebocrbench.modern_scope import contains_biblical_mark  # noqa: E402
from hebocrbench.unicode_utils import BIDI_CONTROLS, normalize_strict  # noqa: E402

API_BASE = "https://knesset.gov.il/Odata/ParliamentInfo.svc/"
SOURCE_ID = "modern-public-documents-v1"
SOURCE_VERSION = "2026-07-26-v1"
ARTIFACT_ID = "knesset-open-data-api"
DEFAULT_ENTITIES = (
    "KNS_DocumentLaw",
    "KNS_DocumentCommitteeSession",
    "KNS_DocumentPlenumSession",
)
HEBREW_RE = re.compile(r"[\u05D0-\u05EA]")
LATIN_RE = re.compile(r"[A-Za-z]")
NUMBER_RE = re.compile(r"\d")
FORM_PATTERNS = (
    re.compile(pattern)
    for pattern in (
        r"\bטופס\b",
        r"\bשם(?:\s+מלא)?\s*[:：]",
        r"\bתאריך\s*[:：]",
        r"\bחתימה\s*[:：]?",
        r"\bמספר\s+(?:זהות|תיק|בקשה)\b",
        r"_{3,}",
    )
)


class MaterializationError(RuntimeError):
    """The public-document source cannot be materialized safely."""


def _http_bytes(url: str, *, timeout: int = 120, attempts: int = 4) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "HebOCRBench/1.0 (+https://github.com/office883/HebOCRBench)",
                    "Accept": "application/json,application/pdf,*/*;q=0.5",
                },
            )
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - locked public URLs
                return response.read()
        except Exception as exc:  # noqa: BLE001 - preserve final network cause
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise MaterializationError(f"download failed after {attempts} attempts: {url}: {last}")


def _http_json(url: str, *, timeout: int = 120) -> Mapping[str, object]:
    try:
        value = json.loads(_http_bytes(url, timeout=timeout).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterializationError(f"invalid JSON response from {url}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise MaterializationError(f"JSON response is not an object: {url}")
    return value


def _query_records(entity: str, *, maximum: int) -> Iterable[Mapping[str, object]]:
    offset = 0
    yielded = 0
    while yielded < maximum:
        length = min(500, maximum - yielded)
        query = urlencode(
            {
                "$top": length,
                "$skip": offset,
                "$orderby": "LastUpdatedDate desc",
                "$format": "json",
            }
        )
        response = _http_json(f"{API_BASE}{entity}?{query}")
        raw_rows = response.get("value", [])
        if not isinstance(raw_rows, list) or not raw_rows:
            return
        for row in raw_rows:
            if isinstance(row, Mapping):
                yield row
                yielded += 1
        if len(raw_rows) < length:
            return
        offset += length


def _knesset_number_from_url(url: str) -> int | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if not parts:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def _record_id(entity: str, row: Mapping[str, object]) -> str | None:
    keys = {
        "KNS_DocumentLaw": "DocumentLawID",
        "KNS_DocumentCommitteeSession": "DocumentCommitteeSessionID",
        "KNS_DocumentPlenumSession": "DocumentPlenumSessionID",
    }
    value = row.get(keys[entity])
    return str(value) if value not in (None, "") else None


def _document_type(entity: str) -> str:
    return {
        "KNS_DocumentLaw": "legislation_material",
        "KNS_DocumentCommitteeSession": "committee_session_document",
        "KNS_DocumentPlenumSession": "plenum_session_document",
    }[entity]


def _candidate(
    entity: str,
    row: Mapping[str, object],
    *,
    minimum_knesset_number: int,
) -> dict[str, object] | None:
    url = str(row.get("FilePath") or "").strip()
    if str(row.get("ApplicationDesc") or "").upper() != "PDF":
        return None
    if not url.lower().endswith(".pdf"):
        return None
    knesset_number = _knesset_number_from_url(url)
    if knesset_number is None or knesset_number < minimum_knesset_number:
        return None
    record_id = _record_id(entity, row)
    if record_id is None:
        return None
    group_type_id = row.get("GroupTypeID")
    group_description = str(row.get("GroupTypeDesc") or "מסמך ציבורי").strip()
    prefix = {
        "KNS_DocumentLaw": "law",
        "KNS_DocumentCommitteeSession": "committee",
        "KNS_DocumentPlenumSession": "plenum",
    }[entity]
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


def _page_fonts(page: fitz.Page) -> list[str]:
    fonts: set[str] = set()
    for block in page.get_text("dict", sort=False).get("blocks", []):
        if not isinstance(block, Mapping):
            continue
        for line in block.get("lines", []):
            if not isinstance(line, Mapping):
                continue
            for span in line.get("spans", []):
                if isinstance(span, Mapping) and span.get("font"):
                    fonts.add(str(span["font"]))
    return sorted(fonts)


def _basic_page_evidence(page: fitz.Page) -> dict[str, object]:
    text = normalize_strict(page.get_text("text", sort=False).replace("\r", "\n"))
    alphabetic = sum(char.isalpha() for char in text)
    hebrew_letters = len(HEBREW_RE.findall(text))
    arabic_letters = sum(
        char.isalpha() and "ARABIC" in __import__("unicodedata").name(char, "")
        for char in text
    )
    try:
        table_count = len(page.find_tables().tables)
    except Exception:  # noqa: BLE001 - table detector is best-effort evidence
        table_count = 0
    form_signal = sum(1 for pattern in FORM_PATTERNS if pattern.search(text))
    mixed_bidi = bool(HEBREW_RE.search(text)) and bool(LATIN_RE.search(text) or NUMBER_RE.search(text))
    strong = first_strong_direction(text)
    usable = (
        hebrew_letters >= 120
        and hebrew_letters / max(1, alphabetic) >= 0.50
        and arabic_letters / max(1, alphabetic) <= 0.02
        and not contains_biblical_mark(text)
        and not any(char in BIDI_CONTROLS for char in text)
        and strong in {"rtl", "neutral"}
    )
    return {
        "page_number": page.number + 1,
        "usable": usable,
        "character_count": len(text),
        "hebrew_letters": hebrew_letters,
        "hebrew_letter_ratio": hebrew_letters / max(1, alphabetic),
        "arabic_letter_ratio": arabic_letters / max(1, alphabetic),
        "table_count": table_count,
        "form_signal": form_signal,
        "mixed_bidi": mixed_bidi,
        "text_head": " ".join(text.split())[:400],
        "width": float(page.rect.width),
        "height": float(page.rect.height),
        "fonts": _page_fonts(page),
    }


def _context(candidate: Mapping[str, object]) -> ConversionContext:
    return ConversionContext(
        source_id=SOURCE_ID,
        source_version=SOURCE_VERSION,
        split="train",
        track="modern_page_ocr",
        license_expression="LicenseRef-Israeli-Public-Documents",
        rights_uri="https://www.knesset.gov.il/",
        redistribution="allowed-with-attribution",
        citation_key="hebocrbench-modern-public-documents-v1",
        source_url=str(candidate["pdf_url"]),
        metadata_defaults={
            "languages": ["he", "en"],
            "script": "Hebr",
            "script_style": "modern_square_print",
            "era": "contemporary",
            "document_type": str(candidate["document_type"]),
            "layout_type": "mixed",
            "vocalization": "none",
            "source_type": "real_public_document",
            "source_collection": "Israeli public modern documents",
        },
    )


def _preflight_pages(
    pdf_path: Path,
    candidate: Mapping[str, object],
    evidence: list[dict[str, object]],
    *,
    maximum_pages: int,
    minimum_agreement: float,
    dpi: int,
) -> tuple[list[int], dict[int, Mapping[str, object]]]:
    ranked = rank_page_candidates(evidence, maximum=min(len(evidence), maximum_pages * 3))
    accepted: list[int] = []
    accepted_records: dict[int, Mapping[str, object]] = {}
    context = _context(candidate)
    with tempfile.TemporaryDirectory(prefix="hebocrbench-modern-preflight-") as temporary:
        root = Path(temporary)
        for page_number in ranked:
            try:
                record = convert_modern_pdf_page(
                    pdf_path,
                    page_number,
                    root,
                    context,
                    document_id=str(candidate["document_id"]),
                    page_id=f"preflight-p{page_number:04d}",
                    dpi=dpi,
                    min_agreement=minimum_agreement,
                    min_hebrew_letters=120,
                    min_hebrew_ratio=0.50,
                )
            except (ModernPdfError, OSError, ValueError):
                continue
            accepted.append(page_number)
            accepted_records[page_number] = record
            if len(accepted) >= maximum_pages:
                break
    return sorted(accepted), accepted_records


def _json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def materialize(args: argparse.Namespace) -> dict[str, object]:
    output = Path(args.output).resolve()
    if output.exists():
        if not args.overwrite:
            raise MaterializationError(f"output exists: {output}; pass --overwrite")
        shutil.rmtree(output)
    (output / "pdfs").mkdir(parents=True)
    (output / "manifests").mkdir()
    (output / "evidence").mkdir()

    accepted_documents = 0
    accepted_pages = 0
    table_pages = 0
    form_pages = 0
    mixed_bidi_pages = 0
    rejected: list[dict[str, object]] = []
    catalog_snapshot: list[dict[str, object]] = []
    seen_pdf_hashes: set[str] = set()
    template_counts: Counter[str] = Counter()
    document_summaries: list[dict[str, object]] = []

    for entity in args.entities:
        for row in _query_records(entity, maximum=args.maximum_records_per_entity):
            candidate = _candidate(
                entity,
                row,
                minimum_knesset_number=args.minimum_knesset_number,
            )
            if candidate is None:
                continue
            catalog_snapshot.append(dict(candidate["catalog_record"]))
            document_id = str(candidate["document_id"])
            try:
                pdf_bytes = _http_bytes(str(candidate["pdf_url"]), timeout=args.timeout)
                if not pdf_bytes.startswith(b"%PDF"):
                    raise MaterializationError("downloaded bytes are not a PDF")
                if len(pdf_bytes) > args.maximum_pdf_bytes:
                    raise MaterializationError(
                        f"PDF exceeds maximum size ({len(pdf_bytes)} > {args.maximum_pdf_bytes})"
                    )
                pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
                if pdf_hash in seen_pdf_hashes:
                    raise MaterializationError("duplicate PDF bytes")
                pdf_path = output / "pdfs" / f"{document_id}.pdf"
                pdf_path.write_bytes(pdf_bytes)
                document = fitz.open(pdf_path)
                if document.page_count <= 0 or document.page_count > args.maximum_pdf_pages:
                    raise MaterializationError(
                        f"unsupported PDF page count: {document.page_count}"
                    )
                page_evidence = [_basic_page_evidence(page) for page in document]
                usable = [item for item in page_evidence if bool(item["usable"])]
                if not usable:
                    raise MaterializationError("no usable Modern Hebrew text-layer pages")
                accepted_page_numbers, records = _preflight_pages(
                    pdf_path,
                    candidate,
                    usable,
                    maximum_pages=args.maximum_pages_per_document,
                    minimum_agreement=args.minimum_agreement,
                    dpi=args.dpi,
                )
                if not accepted_page_numbers:
                    raise MaterializationError("all candidate pages failed dual-extractor verification")
                first_number = accepted_page_numbers[0]
                first_evidence = next(
                    item for item in usable if int(item["page_number"]) == first_number
                )
                family = template_family_id(
                    catalog_table=entity,
                    group_type_id=candidate["group_type_id"],
                    width=float(first_evidence["width"]),
                    height=float(first_evidence["height"]),
                    fonts=list(first_evidence["fonts"]),
                    header_text=str(first_evidence["text_head"]),
                )
                if template_counts[family] >= args.maximum_documents_per_template:
                    raise MaterializationError("template-family cap reached")
                selected_evidence = {
                    int(item["page_number"]): item
                    for item in usable
                    if int(item["page_number"]) in accepted_page_numbers
                }
                selected_table_pages = sum(
                    int(selected_evidence[number]["table_count"] > 0)
                    for number in accepted_page_numbers
                )
                selected_form_pages = sum(
                    int(selected_evidence[number]["form_signal"] > 0)
                    for number in accepted_page_numbers
                )
                selected_mixed_pages = sum(
                    int(bool(selected_evidence[number]["mixed_bidi"]))
                    for number in accepted_page_numbers
                )
                region_counts = [
                    len(records[number].get("regions", [])) for number in accepted_page_numbers
                ]
                manifest = {
                    "schema_version": "1.0",
                    "document_id": document_id,
                    "pdf_path": f"pdfs/{document_id}.pdf",
                    "pdf_sha256": pdf_hash,
                    "pages": accepted_page_numbers,
                    "dpi": args.dpi,
                    "minimum_text_layer_agreement": args.minimum_agreement,
                    "metadata": {
                        "title": candidate["title"],
                        "publisher": "הכנסת",
                        "publication_date": str(candidate["catalog_last_updated"])[:10],
                        "template_family": family,
                        "source_page_url": candidate["pdf_url"],
                        "catalog_table": entity,
                        "catalog_record_id": candidate["catalog_record_id"],
                        "group_type_id": candidate["group_type_id"],
                        "group_type_description": candidate["group_type_description"],
                        "knesset_number": candidate["knesset_number"],
                        "catalog_last_updated": candidate["catalog_last_updated"],
                        "document_type": candidate["document_type"],
                        "layout_type": (
                            "table" if selected_table_pages else "multi_region" if max(region_counts) > 1 else "single_column"
                        ),
                        "selected_table_pages": selected_table_pages,
                        "selected_form_pages": selected_form_pages,
                        "selected_mixed_bidi_pages": selected_mixed_pages,
                    },
                }
                _json_write(output / "manifests" / f"{document_id}.json", manifest)
                document_summaries.append(
                    {
                        "document_id": document_id,
                        "pdf_sha256": pdf_hash,
                        "pdf_size_bytes": len(pdf_bytes),
                        "pdf_page_count": document.page_count,
                        "selected_pages": accepted_page_numbers,
                        "template_family": family,
                        "table_pages": selected_table_pages,
                        "form_pages": selected_form_pages,
                        "mixed_bidi_pages": selected_mixed_pages,
                        "catalog_table": entity,
                        "catalog_record_id": candidate["catalog_record_id"],
                        "source_url": candidate["pdf_url"],
                    }
                )
                seen_pdf_hashes.add(pdf_hash)
                template_counts[family] += 1
                accepted_documents += 1
                accepted_pages += len(accepted_page_numbers)
                table_pages += selected_table_pages
                form_pages += selected_form_pages
                mixed_bidi_pages += selected_mixed_pages
                document.close()
                if (
                    accepted_documents >= args.target_documents
                    and accepted_pages >= args.target_pages
                    and table_pages >= args.minimum_table_pages
                    and form_pages >= args.minimum_form_pages
                    and mixed_bidi_pages >= args.minimum_mixed_bidi_pages
                ):
                    break
            except Exception as exc:  # noqa: BLE001 - every rejection is evidence
                rejected.append(
                    {
                        "document_id": document_id,
                        "catalog_table": entity,
                        "catalog_record_id": candidate["catalog_record_id"],
                        "pdf_url": candidate["pdf_url"],
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
                (output / "pdfs" / f"{document_id}.pdf").unlink(missing_ok=True)
            finally:
                try:
                    document.close()  # type: ignore[possibly-undefined]
                except Exception:
                    pass
        if (
            accepted_documents >= args.target_documents
            and accepted_pages >= args.target_pages
            and table_pages >= args.minimum_table_pages
            and form_pages >= args.minimum_form_pages
            and mixed_bidi_pages >= args.minimum_mixed_bidi_pages
        ):
            break

    summary = {
        "schema_version": "1.0",
        "source_id": SOURCE_ID,
        "source_version": SOURCE_VERSION,
        "accepted_document_count": accepted_documents,
        "accepted_page_count": accepted_pages,
        "template_family_count": len(template_counts),
        "table_page_count": table_pages,
        "form_page_count": form_pages,
        "mixed_bidi_page_count": mixed_bidi_pages,
        "rejected_document_count": len(rejected),
        "entities": list(args.entities),
        "parameters": {
            "target_documents": args.target_documents,
            "target_pages": args.target_pages,
            "minimum_table_pages": args.minimum_table_pages,
            "minimum_form_pages": args.minimum_form_pages,
            "minimum_mixed_bidi_pages": args.minimum_mixed_bidi_pages,
            "minimum_knesset_number": args.minimum_knesset_number,
            "maximum_pages_per_document": args.maximum_pages_per_document,
            "maximum_documents_per_template": args.maximum_documents_per_template,
            "minimum_agreement": args.minimum_agreement,
            "dpi": args.dpi,
        },
    }
    required = (
        accepted_documents >= args.target_documents
        and accepted_pages >= args.target_pages
        and table_pages >= args.minimum_table_pages
        and form_pages >= args.minimum_form_pages
        and mixed_bidi_pages >= args.minimum_mixed_bidi_pages
    )
    summary["target_reached"] = required
    _json_write(output / "evidence" / "summary.json", summary)
    _json_write(output / "evidence" / "documents.json", document_summaries)
    _json_write(output / "evidence" / "rejections.json", rejected)
    _json_write(output / "evidence" / "catalog-snapshot.json", catalog_snapshot)
    evidence = write_source_evidence(
        output,
        source_id=SOURCE_ID,
        source_version=SOURCE_VERSION,
        artifact_id=ARTIFACT_ID,
        requested_revision=SOURCE_VERSION,
        extra=summary,
    )
    summary["tree_sha256"] = evidence["inventory"]["tree_sha256"]
    if not required:
        raise MaterializationError("coverage targets were not reached: " + json.dumps(summary))
    return summary


def _push_to_hub(root: Path, repo_id: str, *, private: bool) -> None:
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise MaterializationError("--push-to-hub requires huggingface_hub") from exc
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise MaterializationError("HF_TOKEN is required for --push-to-hub")
    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
    api.upload_folder(
        repo_id=repo_id,
        repo_type="dataset",
        folder_path=str(root),
        path_in_repo="source",
        commit_message="Materialize verified Modern Hebrew public-document source",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="source-snapshots/modern-public-documents-v1")
    parser.add_argument("--target-documents", type=int, default=100)
    parser.add_argument("--target-pages", type=int, default=500)
    parser.add_argument("--minimum-table-pages", type=int, default=25)
    parser.add_argument("--minimum-form-pages", type=int, default=20)
    parser.add_argument("--minimum-mixed-bidi-pages", type=int, default=100)
    parser.add_argument("--minimum-knesset-number", type=int, default=20)
    parser.add_argument("--maximum-pages-per-document", type=int, default=20)
    parser.add_argument("--maximum-documents-per-template", type=int, default=20)
    parser.add_argument("--maximum-records-per-entity", type=int, default=12000)
    parser.add_argument("--maximum-pdf-bytes", type=int, default=50_000_000)
    parser.add_argument("--maximum-pdf-pages", type=int, default=300)
    parser.add_argument("--minimum-agreement", type=float, default=0.98)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--entities", nargs="+", default=list(DEFAULT_ENTITIES))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--push-to-hub", metavar="OWNER/DATASET")
    parser.add_argument("--public", action="store_true", help="make a newly created Hub repo public")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = materialize(args)
    if args.push_to_hub:
        _push_to_hub(Path(args.output).resolve(), args.push_to_hub, private=not args.public)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
