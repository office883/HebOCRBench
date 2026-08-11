"""Fail-closed conversion of the OmiLab HaZefira PAGE/ALTO ground truth.

The public archive contains one PAGE XML file and one ALTO XML file for each
scan.  PAGE is the authoritative text/geometry representation used for gold;
the paired ALTO file is independently checked for identical line identities
and identical non-whitespace text.  This prevents accidental conversion of a
partial or mismatched export while avoiding duplicate pages in the benchmark.

The upstream project describes a mixture of square (Meruba) and Rashi print,
but it does not annotate which individual regions or lines are Rashi.  The
converter therefore records the corpus-level description and explicitly
forbids a pure-Rashi interpretation of the resulting track.
"""

from __future__ import annotations

from pathlib import Path
import unicodedata
import xml.etree.ElementTree as ET

from ..io import sha256_file
from ..unicode_utils import dangling_combining_mark_indices
from . import ConversionContext
from .common import children_named, descendants_named, first_descendant, local_name
from .pagexml import convert_pagexml_file


class HistoricalPressConversionError(ValueError):
    """The paired historical-press annotations are incomplete or disagree."""


def _page_line_text(line: ET.Element) -> str:
    for text_equiv in children_named(line, "TextEquiv"):
        unicode_node = first_descendant(text_equiv, "Unicode")
        if unicode_node is not None:
            return unicodedata.normalize("NFC", unicode_node.text or "")
    return ""


def _alto_line_text(line: ET.Element) -> str:
    pieces: list[str] = []
    for child in list(line):
        kind = local_name(child.tag)
        if kind == "String":
            pieces.append(child.get("CONTENT", ""))
        elif kind == "SP":
            pieces.append(" ")
        elif kind == "HYP":
            pieces.append(child.get("CONTENT", "-"))
    return unicodedata.normalize("NFC", "".join(pieces))


def _line_map(
    root: ET.Element,
    *,
    id_attribute: str,
    text_reader,
    label: str,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in descendants_named(root, "TextLine"):
        line_id = line.get(id_attribute)
        if not line_id:
            raise HistoricalPressConversionError(f"{label} TextLine has no {id_attribute}")
        if line_id in result:
            raise HistoricalPressConversionError(f"{label} repeats TextLine ID {line_id!r}")
        result[line_id] = text_reader(line)
    return result


def _without_whitespace(text: str) -> str:
    return "".join(character for character in text if not character.isspace())


def _paired_alto_path(page_annotation: Path, source_root: Path) -> Path:
    try:
        relative = page_annotation.resolve().relative_to(source_root.resolve())
    except ValueError as exc:
        raise HistoricalPressConversionError(
            f"PAGE annotation is outside source root: {page_annotation}"
        ) from exc
    parts = list(relative.parts)
    try:
        page_index = len(parts) - 1 - parts[::-1].index("page")
    except ValueError as exc:
        raise HistoricalPressConversionError(
            f"PAGE annotation path has no page/ component: {relative.as_posix()}"
        ) from exc
    parts[page_index] = "alto"
    paired = source_root.joinpath(*parts)
    if not paired.is_file():
        raise HistoricalPressConversionError(
            f"Paired ALTO annotation is missing: {paired.relative_to(source_root).as_posix()}"
        )
    return paired


def convert_historical_press_pagealto_file(
    annotation_path: str | Path,
    source_root: str | Path,
    image_root: str | Path,
    context: ConversionContext,
) -> dict[str, object]:
    """Convert one HaZefira page and verify its paired ALTO representation."""

    page_annotation = Path(annotation_path)
    root = Path(source_root)
    alto_annotation = _paired_alto_path(page_annotation, root)
    try:
        page_root = ET.parse(page_annotation).getroot()
        alto_root = ET.parse(alto_annotation).getroot()
    except ET.ParseError as exc:
        raise HistoricalPressConversionError(f"Cannot parse paired PAGE/ALTO XML: {exc}") from exc

    page_lines = _line_map(
        page_root,
        id_attribute="id",
        text_reader=_page_line_text,
        label="PAGE",
    )
    alto_lines = _line_map(
        alto_root,
        id_attribute="ID",
        text_reader=_alto_line_text,
        label="ALTO",
    )
    if set(page_lines) != set(alto_lines):
        missing = sorted(set(page_lines) - set(alto_lines))
        extra = sorted(set(alto_lines) - set(page_lines))
        raise HistoricalPressConversionError(
            "PAGE/ALTO line identities disagree "
            f"(missing_in_alto={missing[:5]}, extra_in_alto={extra[:5]})"
        )
    text_mismatches = [
        line_id
        for line_id in sorted(page_lines)
        if _without_whitespace(page_lines[line_id]) != _without_whitespace(alto_lines[line_id])
    ]
    if text_mismatches:
        raise HistoricalPressConversionError(
            "PAGE/ALTO non-whitespace text disagrees for line IDs: "
            + ", ".join(text_mismatches[:10])
        )

    record = convert_pagexml_file(
        page_annotation,
        image_root,
        context,
        dimension_tolerance=1,
    )
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        raise HistoricalPressConversionError("PAGE converter returned no metadata mapping")
    page_relative = page_annotation.resolve().relative_to(root.resolve()).as_posix()
    alto_relative = alto_annotation.resolve().relative_to(root.resolve()).as_posix()
    page_node = first_descendant(page_root, "Page")
    if page_node is None:
        raise HistoricalPressConversionError("PAGE annotation has no Page element")
    image = record.get("image")
    if not isinstance(image, dict):
        raise HistoricalPressConversionError("PAGE converter returned no image mapping")
    declared_dimensions = {
        "width": int(page_node.get("imageWidth", "0")),
        "height": int(page_node.get("imageHeight", "0")),
    }
    actual_dimensions = {
        "width": int(image["width"]),
        "height": int(image["height"]),
    }
    metadata.update(
        {
            "source_page_identity": page_annotation.stem,
            "pagexml_annotation_path": page_relative,
            "pagexml_annotation_sha256": sha256_file(page_annotation),
            "alto_annotation_path": alto_relative,
            "alto_annotation_sha256": sha256_file(alto_annotation),
            "paired_annotation_formats": ["PAGE XML 2013-07-15", "ALTO 2.0"],
            "paired_annotation_line_count": len(page_lines),
            "page_alto_line_identity_parity": True,
            "page_alto_nonwhitespace_text_parity": True,
            "script_description_scope": "corpus-level-mixed-square-rashi",
            "rashi_region_or_line_labels_available": False,
            "pure_rashi_claim": False,
        }
    )
    if declared_dimensions != actual_dimensions:
        metadata["image_dimension_reconciliation"] = {
            "policy": "accept-locked-upstream-one-pixel-discrepancy-use-image-bytes",
            "declared": declared_dimensions,
            "actual": actual_dimensions,
        }
    preserved_anomalies: list[dict[str, object]] = []
    for region in record.get("regions", []):
        if not isinstance(region, dict):
            continue
        for line in region.get("lines", []):
            if not isinstance(line, dict):
                continue
            text = line.get("text")
            if not isinstance(text, str):
                continue
            indices = dangling_combining_mark_indices(text)
            if not indices:
                continue
            line_id = str(line.get("line_id", ""))
            tags = line.setdefault("tags", [])
            if isinstance(tags, list):
                tags.append("source-dangling-combining-mark-preserved")
            spans = line.setdefault("uncertain_spans", [])
            if not isinstance(spans, list):
                raise HistoricalPressConversionError("Line uncertain_spans is not a list")
            for index in indices:
                spans.append(
                    {
                        "start": index,
                        "end": index + 1,
                        "reason": "source-dangling-combining-mark-preserved",
                        "codepoint": f"U+{ord(text[index]):04X}",
                    }
                )
                preserved_anomalies.append(
                    {
                        "line_id": line_id,
                        "index": index,
                        "codepoint": f"U+{ord(text[index]):04X}",
                    }
                )
    if preserved_anomalies:
        metadata["preserved_source_unicode_anomalies"] = preserved_anomalies
    return record


def validate_historical_press_corpus(
    records: list[dict[str, object]],
    *,
    expected_pages: int,
    expected_lines: int,
) -> None:
    """Enforce the immutable public archive inventory after conversion."""

    actual_pages = len(records)
    actual_lines = 0
    for record in records:
        regions = record.get("regions", [])
        if not isinstance(regions, list):
            raise HistoricalPressConversionError("Converted page has no regions list")
        for region in regions:
            if not isinstance(region, dict) or not isinstance(region.get("lines"), list):
                raise HistoricalPressConversionError("Converted region has no lines list")
            actual_lines += len(region["lines"])
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            raise HistoricalPressConversionError("Converted page has no metadata mapping")
        if metadata.get("pure_rashi_claim") is not False:
            raise HistoricalPressConversionError("Historical press page permits a pure-Rashi claim")
        if metadata.get("page_alto_line_identity_parity") is not True:
            raise HistoricalPressConversionError("Historical press page lacks PAGE/ALTO parity")
    if actual_pages != expected_pages or actual_lines != expected_lines:
        raise HistoricalPressConversionError(
            "Historical press inventory mismatch: "
            f"expected {expected_pages} pages/{expected_lines} lines, "
            f"got {actual_pages} pages/{actual_lines} lines"
        )


__all__ = [
    "HistoricalPressConversionError",
    "convert_historical_press_pagealto_file",
    "validate_historical_press_corpus",
]
