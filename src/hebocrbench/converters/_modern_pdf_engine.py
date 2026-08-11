"""Verified conversion of contemporary Hebrew PDF text layers.

A PDF text layer is not accepted merely because text can be copied from it.
The converter reconstructs logical lines from PyMuPDF word objects, compares
that token sequence with Poppler's independent ``pdftotext -layout`` output,
and rejects pages whose extractors materially disagree.  Geometry is taken
from the PDF word boxes and scaled to the rendered benchmark image.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable, Mapping, Sequence
import unicodedata

import pymupdf as fitz
from rapidfuzz.distance import Levenshtein

from . import ConversionContext
from ..bidi_metrics import first_strong_direction
from ..modern_scope import contains_biblical_mark
from ..unicode_utils import BIDI_CONTROLS, normalize_strict

getattr(fitz, "no_recommend_layout", lambda: None)()


class ModernPdfError(ValueError):
    """A PDF page cannot serve as verified Modern Hebrew ground truth."""


@dataclass(frozen=True, slots=True)
class _Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    source_line: int
    source_word: int

    @property
    def order(self) -> tuple[int, int, int]:
        return self.block, self.source_line, self.source_word

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def height(self) -> float:
        return max(0.1, self.y1 - self.y0)


_TOKEN_RE = re.compile(
    r"(?:https?://|www\.)[^\s]+|"
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|"
    r"[A-Za-z0-9]+(?:[._:/?&=%+#@\-][A-Za-z0-9]+)+|"
    r"[\u05D0-\u05EA]+(?:[׳״'][\u05D0-\u05EA]+)*|"
    r"[A-Za-z]+|"
    r"[0-9]+(?:[.,:/\-][0-9]+)*|"
    r"[^\s]"
)
_CLOSE_PUNCTUATION = frozenset(".,;:!?%)]}׳״")
_OPEN_PUNCTUATION = frozenset("([{״")
_JOINERS = frozenset("־-–—/")
_PDF_GLYPH_REPAIRS = {
    "\uf027": ("☎", "private-use telephone glyph mapped from the rendered footer"),
    "\uf0b7": ("•", "private-use bullet glyph mapped from the rendered list marker"),
}
_ETH_RUN = re.compile(r"ð+")
_FORM_SIGNAL_PATTERNS = {
    "form_word": re.compile(r"\bטופס\b"),
    "name_label": re.compile(r"\bשם(?:\s+מלא)?\s*[:：]"),
    "date_label": re.compile(r"\bתאריך\s*[:：]"),
    "signature_word": re.compile(r"\bחתימה\s*[:：]?"),
    "identifier_label": re.compile(r"\bמספר\s+(?:זהות|תיק|בקשה)\b"),
    "underscore_run": re.compile(r"_{3,}"),
}


def _is_latin_neighbor(char: str) -> bool:
    return bool(char and char != "ð" and re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ]", char))


def _page_capability_evidence(text: str, *, table_count: int) -> dict[str, object]:
    """Publish page-level slice evidence without pretending it is annotation.

    Form signals are discovery hints only.  They must never be converted into
    form fields without aligned human or source-structured values.
    """

    signals = [name for name, pattern in _FORM_SIGNAL_PATTERNS.items() if pattern.search(text)]
    return {
        "mixed_bidi": bool(re.search(r"[\u05D0-\u05EA]", text))
        and bool(re.search(r"[A-Za-z0-9]", text)),
        "table_count": int(table_count),
        "form_signal_count": len(signals),
        "form_signals": signals,
        "form_ground_truth_available": False,
    }


def _repair_modern_pdf_text(text: str) -> tuple[str, list[dict[str, object]]]:
    """Repair only visually verified, deterministic PDF cmap artifacts.

    Some public PDFs map a Hebrew nun to a standalone U+00F0 and legacy symbol
    fonts expose telephone/bullet glyphs in the private-use area.  These are
    text-layer encoding defects, not characters visible on the page.  The
    repair allowlist is intentionally tiny and every substitution is counted.
    Latin words containing a genuine eth remain untouched.
    """

    normalized = normalize_strict(text)
    repairs: list[dict[str, object]] = []
    bom_count = normalized.count("\ufeff")
    if bom_count:
        normalized = normalized.replace("\ufeff", "")
        repairs.append(
            {
                "source": "U+FEFF",
                "replacement": "",
                "count": bom_count,
                "reason": "embedded PDF BOM removed",
            }
        )
    for source, (replacement, reason) in _PDF_GLYPH_REPAIRS.items():
        count = normalized.count(source)
        if count:
            normalized = normalized.replace(source, replacement)
            repairs.append(
                {
                    "source": f"U+{ord(source):04X}",
                    "replacement": f"U+{ord(replacement):04X}",
                    "count": count,
                    "reason": reason,
                }
            )
    eth_count = 0

    def repair_eth_run(match: re.Match[str]) -> str:
        nonlocal eth_count
        before = normalized[match.start() - 1] if match.start() else ""
        after = normalized[match.end()] if match.end() < len(normalized) else ""
        if _is_latin_neighbor(before) or _is_latin_neighbor(after):
            return match.group(0)
        eth_count += len(match.group(0))
        return "נ" * len(match.group(0))

    normalized = _ETH_RUN.sub(repair_eth_run, normalized)
    if eth_count:
        repairs.append(
            {
                "source": "U+00F0",
                "replacement": "U+05E0",
                "count": eth_count,
                "reason": "standalone legacy-font glyph mapped to visible Hebrew nun",
            }
        )
    return unicodedata.normalize("NFC", normalized), repairs


def _comparison_tokens(text: str) -> tuple[str, ...]:
    normalized, _ = _repair_modern_pdf_text(text)
    normalized = normalized.replace("\f", " ")
    return tuple(match.group(0) for match in _TOKEN_RE.finditer(normalized))


def _is_anchor_token(token: str) -> bool:
    return any(char.isalnum() for char in token)


def _multiset_f1(first: Sequence[str], second: Sequence[str]) -> float:
    if not first and not second:
        return 1.0
    if not first or not second:
        return 0.0
    first_counts = Counter(first)
    second_counts = Counter(second)
    matched = sum((first_counts & second_counts).values())
    precision = matched / len(second)
    recall = matched / len(first)
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0


def _agreement_components(first: str, second: str) -> dict[str, float]:
    first_tokens = _comparison_tokens(first)
    second_tokens = _comparison_tokens(second)
    if not first_tokens or not second_tokens:
        return {
            "anchor_order": 0.0,
            "anchor_content": 0.0,
            "punctuation_content": 0.0,
            "overall": 0.0,
        }

    first_anchors = tuple(token for token in first_tokens if _is_anchor_token(token))
    second_anchors = tuple(token for token in second_tokens if _is_anchor_token(token))
    first_punctuation = tuple(token for token in first_tokens if not _is_anchor_token(token))
    second_punctuation = tuple(token for token in second_tokens if not _is_anchor_token(token))

    anchor_order = (
        float(Levenshtein.normalized_similarity(first_anchors, second_anchors))
        if first_anchors and second_anchors
        else 0.0
    )
    anchor_content = _multiset_f1(first_anchors, second_anchors)
    punctuation_content = _multiset_f1(first_punctuation, second_punctuation)
    overall = min(anchor_order, anchor_content, punctuation_content)
    return {
        "anchor_order": anchor_order,
        "anchor_content": anchor_content,
        "punctuation_content": punctuation_content,
        "overall": overall,
    }


def text_layer_agreement(
    first: str,
    second: str,
    *,
    minimum: float | None = None,
) -> float:
    """Return strict content-and-order agreement for independent PDF extractors.

    Poppler may place neutral punctuation on the opposite visual side of an RTL
    token even when both extractors recovered the same logical content.  The
    verifier therefore compares the ordered lexical anchors separately from the
    punctuation multiset.  It still rejects missing punctuation, changed tokens,
    or a materially different logical token order.
    """

    components = _agreement_components(first, second)
    score = components["overall"]
    if minimum is not None and score + 1e-12 < minimum:
        raise ModernPdfError(
            "independent extractors disagree: "
            f"agreement={score:.6f}, minimum={minimum:.6f}, "
            f"anchor_order={components['anchor_order']:.6f}, "
            f"anchor_content={components['anchor_content']:.6f}, "
            f"punctuation_content={components['punctuation_content']:.6f}"
        )
    return score


def _smart_join(tokens: Iterable[str]) -> str:
    output = ""
    for raw in tokens:
        token, _ = _repair_modern_pdf_text(str(raw))
        token = token.strip()
        if not token:
            continue
        if not output:
            output = token
        elif token[0] in _CLOSE_PUNCTUATION or token in _JOINERS:
            output += token
        elif output[-1] in _OPEN_PUNCTUATION or output[-1] in _JOINERS:
            output += token
        else:
            output += " " + token
    return unicodedata.normalize("NFC", output)


def _vertical_match(word: _Word, row: Sequence[_Word]) -> float:
    top = max(word.y0, min(item.y0 for item in row))
    bottom = min(word.y1, max(item.y1 for item in row))
    overlap = max(0.0, bottom - top)
    return overlap / min(
        word.height, max(0.1, max(item.y1 for item in row) - min(item.y0 for item in row))
    )


def _cluster_visual_rows(words: Sequence[_Word]) -> list[list[_Word]]:
    rows: list[list[_Word]] = []
    for word in sorted(words, key=lambda item: (item.center_y, item.x0, item.order)):
        best_index: int | None = None
        best_score = 0.0
        for index, row in enumerate(rows):
            score = _vertical_match(word, row)
            center = sum(item.center_y for item in row) / len(row)
            tolerance = max(word.height, max(item.height for item in row)) * 0.55
            if score >= 0.45 or abs(word.center_y - center) <= tolerance:
                if score >= best_score:
                    best_index = index
                    best_score = score
        if best_index is None:
            rows.append([word])
        else:
            rows[best_index].append(word)
    rows.sort(key=lambda row: (min(item.y0 for item in row), min(item.x0 for item in row)))
    return rows


def _words(page: fitz.Page) -> list[_Word]:
    result: list[_Word] = []
    for raw in page.get_text("words", sort=False):
        if len(raw) < 8:
            continue
        x0, y0, x1, y1, text, block, line, word = raw[:8]
        raw_text = str(text)
        # A small set of legacy Hebrew PDFs exposes the visible letter nun as
        # a separate U+00F0 word object.  Keep that exact sentinel until the
        # geometry layer can join it to the physically contiguous fragments
        # of the same Hebrew word.  All other deterministic cmap repairs are
        # safe to apply at word-object level.
        if "ð" in raw_text:
            clean = raw_text
        else:
            clean, _ = _repair_modern_pdf_text(raw_text)
        clean = clean.strip()
        if not clean or clean == "\ufffd":
            continue
        result.append(
            _Word(
                float(x0),
                float(y0),
                float(x1),
                float(y1),
                clean,
                int(block),
                int(line),
                int(word),
            )
        )
    return result


def _language(text: str) -> str:
    return "he" if any("\u05d0" <= char <= "\u05ea" for char in text) else "en"


def _rect(x0: float, y0: float, x1: float, y1: float, scale: float) -> list[list[float]]:
    return [
        [x0 * scale, y0 * scale],
        [x1 * scale, y0 * scale],
        [x1 * scale, y1 * scale],
        [x0 * scale, y1 * scale],
    ]


def _extract_regions(page: fitz.Page, scale: float) -> tuple[list[dict[str, object]], str]:
    words = _words(page)
    if not words:
        return [], ""
    by_block: dict[int, list[_Word]] = {}
    for word in words:
        by_block.setdefault(word.block, []).append(word)

    regions: list[dict[str, object]] = []
    logical_lines: list[str] = []
    for region_index, block_id in enumerate(sorted(by_block)):
        rows = _cluster_visual_rows(by_block[block_id])
        lines: list[dict[str, object]] = []
        for line_index, row in enumerate(rows):
            ordered = sorted(row, key=lambda item: item.order)
            text = _smart_join(item.text for item in ordered)
            if not text:
                continue
            x0 = min(item.x0 for item in row)
            y0 = min(item.y0 for item in row)
            x1 = max(item.x1 for item in row)
            y1 = max(item.y1 for item in row)
            direction = first_strong_direction(text)
            if direction == "neutral":
                direction = "rtl"
            lines.append(
                {
                    "line_id": f"b{block_id}-l{line_index}",
                    "polygon": _rect(x0, y0, x1, y1, scale),
                    "baseline": [[x1 * scale, y1 * scale], [x0 * scale, y1 * scale]],
                    "text": text,
                    "base_direction": direction,
                    "language": _language(text),
                    "tags": ["source:verified_pdf_text_layer"],
                }
            )
            logical_lines.append(text)
        if not lines:
            continue
        all_points = [point for line in lines for point in line["polygon"]]
        x0 = min(float(point[0]) for point in all_points)
        y0 = min(float(point[1]) for point in all_points)
        x1 = max(float(point[0]) for point in all_points)
        y1 = max(float(point[1]) for point in all_points)
        rtl_lines = sum(line["base_direction"] == "rtl" for line in lines)
        regions.append(
            {
                "region_id": f"b{block_id}",
                "type": "body",
                "polygon": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                "base_direction": "rtl" if rtl_lines >= len(lines) / 2 else "ltr",
                "reading_index": region_index,
                "lines": lines,
            }
        )
    return regions, "\n".join(logical_lines)


def _pdftotext_layout(pdf_path: Path, page_number: int) -> str:
    executable = shutil.which("pdftotext")
    if executable is None:
        raise ModernPdfError("pdftotext is required for independent text-layer verification")
    completed = subprocess.run(
        [
            executable,
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-layout",
            "-enc",
            "UTF-8",
            str(pdf_path),
            "-",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise ModernPdfError(f"pdftotext failed for page {page_number}: {detail}")
    value, _ = _repair_modern_pdf_text(
        completed.stdout.decode("utf-8", "replace").replace("\f", "")
    )
    return value


def _reading_edges(regions: Sequence[dict[str, object]]) -> list[list[str]]:
    ids = [str(region["region_id"]) for region in regions]
    return [[left, right] for left, right in zip(ids, ids[1:])]


def _extract_tables(page: fitz.Page, scale: float) -> list[dict[str, object]]:
    try:
        found = page.find_tables().tables
    except Exception:
        return []
    output: list[dict[str, object]] = []
    for table_index, table in enumerate(found):
        n_rows = int(getattr(table, "row_count", 0) or 0)
        n_cols = int(getattr(table, "col_count", 0) or 0)
        if n_rows <= 0 or n_cols <= 0:
            continue
        matrix = table.extract()
        raw_cells = list(getattr(table, "cells", []) or [])
        cells: list[dict[str, object]] = []
        for row in range(n_rows):
            for col in range(n_cols):
                index = row * n_cols + col
                bbox = raw_cells[index] if index < len(raw_cells) else None
                text = ""
                if row < len(matrix) and matrix[row] is not None and col < len(matrix[row]):
                    text, _ = _repair_modern_pdf_text(str(matrix[row][col] or ""))
                    text = text.strip()
                cell: dict[str, object] = {
                    "row_start": row,
                    "row_end": row + 1,
                    "col_start": col,
                    "col_end": col + 1,
                    "text": text,
                }
                if bbox is not None and len(bbox) >= 4:
                    cell["polygon"] = _rect(*map(float, bbox[:4]), scale)
                cells.append(cell)
        bbox = tuple(map(float, table.bbox))
        output.append(
            {
                "table_id": f"table-{table_index}",
                "region_id": None,
                "polygon": _rect(*bbox, scale),
                "n_rows": n_rows,
                "n_cols": n_cols,
                "cells": cells,
            }
        )
    return output


def _safe(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return token or "item"


def convert_modern_pdf_page(
    pdf_path: str | Path,
    page_number: int,
    output_root: str | Path,
    context: ConversionContext,
    *,
    document_id: str,
    page_id: str,
    dpi: int = 200,
    min_agreement: float = 0.985,
    min_hebrew_letters: int = 4,
    min_hebrew_ratio: float = 0.40,
) -> dict[str, object]:
    """Convert and verify one 1-indexed PDF page into a gold record."""

    source = Path(pdf_path)
    if not source.is_file():
        raise ModernPdfError(f"PDF does not exist: {source}")
    if page_number < 1:
        raise ModernPdfError("page_number is 1-indexed and must be positive")
    if dpi < 72:
        raise ModernPdfError("dpi must be at least 72")

    document = fitz.open(source)
    try:
        if page_number > document.page_count:
            raise ModernPdfError(f"page {page_number} exceeds PDF page count {document.page_count}")
        page = document[page_number - 1]
        scale = dpi / 72.0
        regions, pymupdf_text = _extract_regions(page, scale)
        hebrew_letters = sum("\u05d0" <= char <= "\u05ea" for char in pymupdf_text)
        alphabetic = sum(char.isalpha() for char in pymupdf_text)
        if not regions or hebrew_letters < min_hebrew_letters:
            raise ModernPdfError("no usable text layer with sufficient Hebrew content")
        if hebrew_letters / max(1, alphabetic) < min_hebrew_ratio:
            raise ModernPdfError("text layer is not predominantly Modern Hebrew")
        if contains_biblical_mark(pymupdf_text):
            raise ModernPdfError("Biblical accent marks are outside the Modern Hebrew scope")
        if any(char in BIDI_CONTROLS for char in pymupdf_text):
            raise ModernPdfError("text layer contains forbidden directional controls")
        forbidden_categories = sorted(
            {
                f"U+{ord(char):04X}"
                for char in pymupdf_text
                if unicodedata.category(char) in {"Co", "Cs", "Cf"}
            }
        )
        if forbidden_categories:
            raise ModernPdfError(
                "text layer contains unrepaired private/control characters: "
                + ", ".join(forbidden_categories)
            )

        poppler_text = _pdftotext_layout(source, page_number)
        agreement = text_layer_agreement(
            pymupdf_text,
            poppler_text,
            minimum=min_agreement,
        )

        root = Path(output_root)
        image_relative = Path("images") / _safe(document_id) / f"{_safe(page_id)}-{dpi}dpi.png"
        image_path = root / image_relative
        image_path.parent.mkdir(parents=True, exist_ok=True)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        png_bytes = pixmap.tobytes("png")
        image_path.write_bytes(png_bytes)
        image_sha256 = hashlib.sha256(png_bytes).hexdigest()

        tables = _extract_tables(page, scale)
        metadata = context.metadata(annotation_path=f"{source.as_posix()}#page={page_number}")
        raw_word_text = "\n".join(
            str(word[4]) for word in page.get_text("words", sort=False) if len(word) >= 5
        )
        _, text_layer_repairs = _repair_modern_pdf_text(raw_word_text)
        metadata.update(
            {
                "pdf_page_number": page_number,
                "pdf_page_count": document.page_count,
                **_page_capability_evidence(pymupdf_text, table_count=len(tables)),
                "text_layer_verification": {
                    "agreement": agreement,
                    "minimum": min_agreement,
                    "extractors": ["pymupdf-words", "pdftotext-layout"],
                    "pymupdf_token_count": len(_comparison_tokens(pymupdf_text)),
                    "pdftotext_token_count": len(_comparison_tokens(poppler_text)),
                    "pymupdf_text_sha256": hashlib.sha256(pymupdf_text.encode("utf-8")).hexdigest(),
                    "pdftotext_sha256": hashlib.sha256(poppler_text.encode("utf-8")).hexdigest(),
                    "repair_policy": "verified-pdf-cmap-repair-v1",
                    "repairs": text_layer_repairs,
                },
            }
        )
        return {
            "schema_version": "1.0",
            "page_id": page_id,
            "document_id": document_id,
            "split": context.split,
            "track": context.track,
            "image": {
                "path": image_relative.as_posix(),
                "width": int(pixmap.width),
                "height": int(pixmap.height),
                "rotation_degrees": 0,
                "sha256": image_sha256,
            },
            "metadata": metadata,
            "regions": regions,
            "reading_order": {"edges": _reading_edges(regions)},
            "tables": tables,
            "form_fields": [],
        }
    finally:
        document.close()


def convert_modern_pdf_manifest(
    manifest_path: str | Path,
    source_root: str | Path,
    output_root: str | Path,
    context: ConversionContext,
) -> list[dict[str, object]]:
    """Convert every declared page in a locked Modern Hebrew PDF manifest."""

    manifest = Path(manifest_path)
    root = Path(source_root).resolve()
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModernPdfError(f"cannot read modern PDF manifest {manifest}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ModernPdfError("modern PDF manifest must contain a JSON object")
    if value.get("schema_version") != "1.0":
        raise ModernPdfError("modern PDF manifest schema_version must be '1.0'")

    raw_document_id = str(value.get("document_id", "")).strip()
    if not raw_document_id:
        raise ModernPdfError("modern PDF manifest requires document_id")
    raw_pdf_path = str(value.get("pdf_path", "")).strip()
    if not raw_pdf_path:
        raise ModernPdfError("modern PDF manifest requires pdf_path")
    pdf_path = (root / raw_pdf_path).resolve()
    if pdf_path != root and root not in pdf_path.parents:
        raise ModernPdfError("modern PDF path escapes source root")
    if not pdf_path.is_file():
        raise ModernPdfError(f"modern PDF does not exist: {raw_pdf_path}")

    expected_pdf_hash = str(value.get("pdf_sha256", "")).lower().strip()
    actual_pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if expected_pdf_hash and expected_pdf_hash != actual_pdf_hash:
        raise ModernPdfError(
            f"modern PDF SHA-256 mismatch: expected {expected_pdf_hash}, got {actual_pdf_hash}"
        )

    raw_pages = value.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise ModernPdfError("modern PDF manifest requires a non-empty pages array")
    pages: list[int] = []
    for raw_page in raw_pages:
        if isinstance(raw_page, bool):
            raise ModernPdfError("modern PDF page numbers must be positive integers")
        try:
            page_number = int(raw_page)
        except (TypeError, ValueError) as exc:
            raise ModernPdfError("modern PDF page numbers must be positive integers") from exc
        if page_number < 1:
            raise ModernPdfError("modern PDF page numbers must be positive integers")
        if page_number not in pages:
            pages.append(page_number)

    raw_metadata = value.get("metadata", {})
    if not isinstance(raw_metadata, Mapping):
        raise ModernPdfError("modern PDF manifest metadata must be an object")
    metadata_overrides = dict(raw_metadata)
    template_family = str(metadata_overrides.get("template_family", "")).strip()
    if not template_family:
        raise ModernPdfError("modern PDF manifest metadata requires template_family")
    forbidden_metadata = {
        "source_id",
        "source_version",
        "source_annotation_path",
        "citation_key",
        "license",
        "rights_uri",
        "redistribution",
    }
    overlap = sorted(forbidden_metadata & set(metadata_overrides))
    if overlap:
        raise ModernPdfError(
            "modern PDF manifest cannot override provenance fields: " + ", ".join(overlap)
        )

    dpi = int(value.get("dpi", 200))
    min_agreement = float(value.get("minimum_text_layer_agreement", 0.985))
    document_id = f"{context.source_id}-{_safe(raw_document_id)}"
    manifest_relative = manifest.resolve().relative_to(root).as_posix()
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()

    records: list[dict[str, object]] = []
    for page_number in pages:
        page_id = f"{document_id}-p{page_number:04d}"
        record = convert_modern_pdf_page(
            pdf_path,
            page_number,
            output_root,
            context,
            document_id=document_id,
            page_id=page_id,
            dpi=dpi,
            min_agreement=min_agreement,
        )
        metadata = record.get("metadata")
        assert isinstance(metadata, dict)
        metadata.update(metadata_overrides)
        metadata.update(
            {
                "source_annotation_path": manifest_relative,
                "source_page_id": f"{manifest_relative}#page={page_number}",
                "source_manifest_sha256": manifest_hash,
                "source_pdf_path": pdf_path.relative_to(root).as_posix(),
                "source_pdf_sha256": actual_pdf_hash,
                "document_id_method": "locked_modern_pdf_manifest_v1",
            }
        )
        records.append(record)
    return records
