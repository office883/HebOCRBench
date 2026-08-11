"""PAGE XML to HebOCRBench conversion.

Text is consumed in XML logical order. Geometry is never used to reverse or
reconstruct strings, which is essential for Hebrew mixed with LTR runs.
"""

from __future__ import annotations

from pathlib import Path
import unicodedata
import xml.etree.ElementTree as ET

from . import ConversionContext
from .common import (
    children_named,
    descendants_named,
    first_descendant,
    image_descriptor,
    normalize_direction,
    normalize_language,
    ordered_edges,
    parse_points,
    repair_source_polygon,
)


def _unicode_text(element: ET.Element) -> str | None:
    for text_equiv in children_named(element, "TextEquiv"):
        node = first_descendant(text_equiv, "Unicode")
        if node is not None and node.text is not None:
            return unicodedata.normalize("NFC", node.text)
    return None


def _line_text(line: ET.Element) -> str:
    direct = _unicode_text(line)
    if direct is not None:
        return direct
    words: list[str] = []
    for word in children_named(line, "Word"):
        text = _unicode_text(word)
        if text is not None:
            words.append(text)
    return " ".join(words)


def _coords(
    element: ET.Element,
    *,
    element_type: str,
    repairs: list[dict[str, object]],
) -> list[list[float]]:
    node = next(
        (child for child in list(element) if child.tag.rsplit("}", 1)[-1] == "Coords"), None
    )
    if node is None:
        raise ValueError(f"PAGE element {element.get('id', '<unknown>')} has no Coords")
    points = parse_points(node.get("points"))
    element_id = element.get("id", "<unknown>")
    repaired, audit = repair_source_polygon(
        points, element_id=element_id, element_type=element_type
    )
    if audit is not None:
        repairs.append(audit)
    return repaired


def _baseline(line: ET.Element) -> list[list[float]] | None:
    node = next((child for child in list(line) if child.tag.rsplit("}", 1)[-1] == "Baseline"), None)
    if node is None:
        return None
    points = parse_points(node.get("points"))
    return points if len(points) >= 2 else None


def _reading_order(page: ET.Element) -> tuple[list[list[str]], dict[str, int]]:
    indexed: list[tuple[int, str]] = []
    for node in descendants_named(page, "RegionRefIndexed"):
        reference = node.get("regionRef") or node.get("REGIONREF")
        if reference:
            indexed.append((int(node.get("index", node.get("INDEX", "0"))), reference))
    return ordered_edges(indexed)


def convert_pagexml_file(
    annotation_path: str | Path,
    image_root: str | Path,
    context: ConversionContext,
    *,
    dimension_tolerance: int = 0,
) -> dict[str, object]:
    annotation = Path(annotation_path)
    root = ET.parse(annotation).getroot()
    page = first_descendant(root, "Page")
    if page is None:
        raise ValueError(f"PAGE document has no Page element: {annotation}")

    image_name = page.get("imageFilename")
    if not image_name:
        raise ValueError(f"PAGE Page has no imageFilename: {annotation}")
    image_path = Path(image_root) / image_name
    image = image_descriptor(
        image_path,
        relative_name=Path(image_name).as_posix(),
        declared_width=int(page.get("imageWidth", "0")),
        declared_height=int(page.get("imageHeight", "0")),
        dimension_tolerance=dimension_tolerance,
    )
    page_language = normalize_language(page.get("primaryLanguage"), "he")
    page_direction = normalize_direction(page.get("readingDirection"), page_language)
    edges, reading_indices = _reading_order(page)

    regions: list[dict[str, object]] = []
    geometry_repairs: list[dict[str, object]] = []
    for region in children_named(page, "TextRegion"):
        region_id = region.get("id")
        if not region_id:
            raise ValueError(f"TextRegion without id in {annotation}")
        language = normalize_language(region.get("primaryLanguage"), page_language)
        direction = normalize_direction(region.get("readingDirection"), language or page_direction)
        lines: list[dict[str, object]] = []
        for line in children_named(region, "TextLine"):
            line_id = line.get("id")
            if not line_id:
                raise ValueError(f"TextLine without id in region {region_id}")
            line_language = normalize_language(line.get("primaryLanguage"), language)
            line_direction = normalize_direction(line.get("readingDirection"), line_language)
            value: dict[str, object] = {
                "line_id": line_id,
                "polygon": _coords(line, element_type="page_text_line", repairs=geometry_repairs),
                "text": _line_text(line),
                "base_direction": line_direction,
                "language": line_language,
            }
            baseline = _baseline(line)
            if baseline is not None:
                value["baseline"] = baseline
            lines.append(value)
        region_value: dict[str, object] = {
            "region_id": region_id,
            "type": region.get("type", "text"),
            "polygon": _coords(region, element_type="page_text_region", repairs=geometry_repairs),
            "base_direction": direction,
            "language": language,
            "lines": lines,
        }
        if region_id in reading_indices:
            region_value["reading_index"] = reading_indices[region_id]
        regions.append(region_value)

    metadata = context.metadata(annotation_path=str(annotation))
    if geometry_repairs:
        metadata["geometry_repairs"] = geometry_repairs
        metadata["geometry_repair_count"] = len(geometry_repairs)

    document_id = context.source_id
    page_token = image_path.stem
    return {
        "schema_version": "1.0",
        "page_id": f"{context.source_id}-{page_token}",
        "document_id": document_id,
        "split": context.split,
        "track": context.track,
        "image": image,
        "metadata": metadata,
        "regions": regions,
        "reading_order": {"edges": edges, "unordered_groups": []},
        "tables": [],
        "form_fields": [],
    }
