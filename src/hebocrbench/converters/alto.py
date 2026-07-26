"""ALTO XML to HebOCRBench conversion preserving source logical order."""

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
    local_name,
    normalize_direction,
    normalize_language,
    ordered_edges,
    parse_points,
    rectangle,
    repair_source_polygon,
)


def _polygon_or_rectangle(
    element: ET.Element,
    *,
    element_type: str,
    repairs: list[dict[str, object]],
) -> list[list[float]]:
    element_id = element.get("ID", "<unknown>")
    for shape in children_named(element, "Shape"):
        polygon = first_descendant(shape, "Polygon")
        if polygon is None:
            continue
        points = parse_points(polygon.get("POINTS") or polygon.get("points"))
        if len(points) < 3:
            continue
        try:
            repaired, audit = repair_source_polygon(
                points, element_id=element_id, element_type=element_type
            )
        except ValueError:
            break
        if audit is not None:
            repairs.append(audit)
        return repaired

    required = (
        element.get("HPOS"),
        element.get("VPOS"),
        element.get("WIDTH"),
        element.get("HEIGHT"),
    )
    if any(value is None for value in required):
        raise ValueError(f"ALTO element {element_id} lacks a usable shape and rectangle")
    points = rectangle(*required)
    repaired, audit = repair_source_polygon(
        points, element_id=element_id, element_type=element_type
    )
    if audit is not None:
        audit["method"] = "rectangle_fallback"
        repairs.append(audit)
    elif first_descendant(element, "Polygon") is not None:
        repairs.append(
            {
                "element_id": element_id,
                "element_type": element_type,
                "method": "rectangle_fallback",
                "reason": "source polygon was unusable",
                "original_point_count": 0,
                "repaired_point_count": len(repaired),
                "repaired_area": float(element.get("WIDTH", "0"))
                * float(element.get("HEIGHT", "0")),
            }
        )
    return repaired


def _string_text(node: ET.Element) -> str:
    content = node.get("CONTENT")
    if content is not None:
        return content
    glyphs: list[str] = []
    for glyph in children_named(node, "Glyph"):
        value = glyph.get("CONTENT")
        if value is not None:
            glyphs.append(value)
    return "".join(glyphs)


def _line_text(line: ET.Element) -> str:
    pieces: list[str] = []
    for child in list(line):
        kind = local_name(child.tag)
        if kind == "String":
            pieces.append(_string_text(child))
        elif kind == "SP":
            pieces.append(" ")
        elif kind == "HYP":
            pieces.append(child.get("CONTENT", "-"))
    return unicodedata.normalize("NFC", "".join(pieces))


def _baseline(line: ET.Element) -> list[list[float]] | None:
    value = line.get("BASELINE")
    if not value:
        return None
    tokens = value.strip().split()
    if "," in value or len(tokens) >= 4:
        points = parse_points(value)
        return points if len(points) >= 2 else None
    if len(tokens) != 1:
        raise ValueError(f"Ambiguous ALTO BASELINE value: {value!r}")
    y = float(tokens[0])
    x = float(line.get("HPOS", "0"))
    width = float(line.get("WIDTH", "0"))
    return [[x, y], [x + width, y]]


def _reading_order(root: ET.Element) -> tuple[list[list[str]], dict[str, int]]:
    indexed: list[tuple[int, str]] = []
    for node in descendants_named(root, "RegionRefIndexed"):
        reference = node.get("REGIONREF") or node.get("regionRef")
        if reference:
            indexed.append((int(node.get("INDEX", node.get("index", "0"))), reference))
    return ordered_edges(indexed)


def convert_alto_file(
    annotation_path: str | Path,
    image_root: str | Path,
    context: ConversionContext,
) -> dict[str, object]:
    annotation = Path(annotation_path)
    root = ET.parse(annotation).getroot()
    page = first_descendant(root, "Page")
    if page is None:
        raise ValueError(f"ALTO document has no Page element: {annotation}")
    file_name_node = first_descendant(root, "fileName")
    image_name = (file_name_node.text or "").strip() if file_name_node is not None else ""
    if not image_name:
        raise ValueError(f"ALTO document has no source image fileName: {annotation}")
    image_path = Path(image_root) / image_name
    image = image_descriptor(
        image_path,
        relative_name=Path(image_name).as_posix(),
        declared_width=int(float(page.get("WIDTH", "0"))),
        declared_height=int(float(page.get("HEIGHT", "0"))),
    )
    edges, reading_indices = _reading_order(root)

    regions: list[dict[str, object]] = []
    geometry_repairs: list[dict[str, object]] = []
    for block in descendants_named(page, "TextBlock"):
        region_id = block.get("ID")
        if not region_id:
            raise ValueError(f"TextBlock without ID in {annotation}")
        language = normalize_language(block.get("LANG"), "he")
        direction = normalize_direction(block.get("BASEDIRECTION"), language)
        lines: list[dict[str, object]] = []
        for line in children_named(block, "TextLine"):
            line_id = line.get("ID")
            if not line_id:
                raise ValueError(f"TextLine without ID in block {region_id}")
            line_language = normalize_language(line.get("LANG"), language)
            line_direction = normalize_direction(line.get("BASEDIRECTION"), line_language)
            value: dict[str, object] = {
                "line_id": line_id,
                "polygon": _polygon_or_rectangle(
                    line, element_type="alto_text_line", repairs=geometry_repairs
                ),
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
            "type": "text",
            "polygon": _polygon_or_rectangle(
                block, element_type="alto_text_block", repairs=geometry_repairs
            ),
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

    page_token = image_path.stem
    return {
        "schema_version": "1.0",
        "page_id": f"{context.source_id}-{page_token}",
        "document_id": context.source_id,
        "split": context.split,
        "track": context.track,
        "image": image,
        "metadata": metadata,
        "regions": regions,
        "reading_order": {"edges": edges, "unordered_groups": []},
        "tables": [],
        "form_fields": [],
    }
