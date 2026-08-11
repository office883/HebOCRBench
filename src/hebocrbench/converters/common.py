"""Shared conversion helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Iterable

from PIL import Image
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity


_LANGUAGE_MAP = {
    "hebrew": "he",
    "heb": "he",
    "he": "he",
    "english": "en",
    "eng": "en",
    "en": "en",
}

_DIRECTION_MAP = {
    "right-to-left": "rtl",
    "right_to_left": "rtl",
    "rtl": "rtl",
    "left-to-right": "ltr",
    "left_to_right": "ltr",
    "ltr": "ltr",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def children_named(element, name: str):
    return [child for child in list(element) if local_name(child.tag) == name]


def descendants_named(element, name: str):
    return [node for node in element.iter() if local_name(node.tag) == name]


def first_descendant(element, name: str):
    return next((node for node in element.iter() if local_name(node.tag) == name), None)


def parse_points(value: str | None) -> list[list[float]]:
    """Parse PAGE/ALTO point lists without guessing mixed encodings.

    PAGE commonly serializes points as ``x,y x,y``. ALTO permits the same
    representation, while real-world ALTO 4.x exports also use a flat
    ``x y x y`` sequence. A string that mixes the two encodings is rejected so
    malformed upstream data cannot be silently re-paired.
    """

    if not value:
        return []
    tokens = [token for token in re.split(r"\s+", value.strip()) if token]
    if not tokens:
        return []

    comma_flags = ["," in token for token in tokens]
    if all(comma_flags):
        points: list[list[float]] = []
        for token in tokens:
            parts = token.split(",")
            if len(parts) != 2 or not all(part.strip() for part in parts):
                raise ValueError(f"Invalid coordinate token: {token!r}")
            points.append([float(parts[0]), float(parts[1])])
        return points

    if any(comma_flags):
        raise ValueError(f"Mixed coordinate encodings are not supported: {value!r}")
    if len(tokens) % 2:
        raise ValueError(f"Flat coordinate list has an odd number of values: {value!r}")

    numbers = [float(token) for token in tokens]
    return [[numbers[index], numbers[index + 1]] for index in range(0, len(numbers), 2)]


def _polygon_exterior(geometry: BaseGeometry) -> list[list[float]]:
    if not isinstance(geometry, Polygon):
        raise ValueError(f"Expected Polygon after repair, got {geometry.geom_type}")
    normalized = orient(geometry, sign=1.0)
    return [[float(x), float(y)] for x, y in list(normalized.exterior.coords)[:-1]]


def repair_source_polygon(
    points: list[list[float]],
    *,
    element_id: str,
    element_type: str,
) -> tuple[list[list[float]], dict[str, object] | None]:
    """Return a valid polygon and an explicit repair audit record when needed.

    Source contours are retained exactly when they already form a valid,
    positive-area polygon. Self-touching contours are repaired with GEOS
    ``buffer(0)``. If that operation yields multiple polygon components, their
    convex hull is used rather than silently discarding all but the largest
    component. Zero-area geometry is rejected; callers may make an explicit,
    documented rectangle fallback when the source format provides one.
    """

    if len(points) < 3:
        raise ValueError(f"{element_type} {element_id!r} has fewer than 3 polygon points")
    source = [[float(point[0]), float(point[1])] for point in points]
    polygon = Polygon(source)
    if not polygon.is_empty and polygon.area > 0 and polygon.is_valid:
        return source, None

    original_reason = explain_validity(polygon)
    repaired = polygon.buffer(0)
    method = "buffer_0"
    if repaired.geom_type != "Polygon" and not repaired.is_empty:
        repaired = repaired.convex_hull
        method = "buffer_0_then_convex_hull"

    if (
        repaired.is_empty
        or repaired.area <= 0
        or repaired.geom_type != "Polygon"
        or not repaired.is_valid
    ):
        raise ValueError(f"Could not repair {element_type} {element_id!r}: {original_reason}")

    output = _polygon_exterior(repaired)
    return output, {
        "element_id": element_id,
        "element_type": element_type,
        "method": method,
        "reason": original_reason,
        "original_point_count": len(source),
        "repaired_point_count": len(output),
        "original_area": float(polygon.area),
        "repaired_area": float(repaired.area),
    }


def rectangle(x: object, y: object, width: object, height: object) -> list[list[float]]:
    left = float(x)
    top = float(y)
    right = left + float(width)
    bottom = top + float(height)
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def normalize_language(value: str | None, fallback: str = "he") -> str:
    if not value:
        return fallback
    lowered = value.strip().lower().replace("_", "-")
    return _LANGUAGE_MAP.get(lowered, lowered.split("-", 1)[0] or fallback)


def normalize_direction(value: str | None, language: str | None = None) -> str:
    if value:
        normalized = _DIRECTION_MAP.get(value.strip().lower())
        if normalized:
            return normalized
    return "rtl" if normalize_language(language, "") == "he" else "ltr"


def image_descriptor(
    image_path: Path,
    *,
    relative_name: str,
    declared_width: int,
    declared_height: int,
    dimension_tolerance: int = 0,
) -> dict[str, object]:
    if not image_path.is_file():
        raise FileNotFoundError(f"Image referenced by annotation does not exist: {image_path}")
    with Image.open(image_path) as image:
        width, height = image.size
    if dimension_tolerance < 0:
        raise ValueError("dimension_tolerance must be non-negative")
    if declared_width > 0 and abs(width - declared_width) > dimension_tolerance:
        raise ValueError(
            f"Image width mismatch for {image_path}: annotation={declared_width}, image={width}"
        )
    if declared_height > 0 and abs(height - declared_height) > dimension_tolerance:
        raise ValueError(
            f"Image height mismatch for {image_path}: annotation={declared_height}, image={height}"
        )
    digest = hashlib.sha256()
    with image_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": relative_name,
        "width": width,
        "height": height,
        "rotation_degrees": 0,
        "sha256": digest.hexdigest(),
    }


def ordered_edges(indexed_ids: Iterable[tuple[int, str]]) -> tuple[list[list[str]], dict[str, int]]:
    ordered = [item for _, item in sorted(indexed_ids, key=lambda pair: (pair[0], pair[1]))]
    edges = [[left, right] for left, right in zip(ordered, ordered[1:])]
    indices = {item: index for index, item in enumerate(ordered)}
    return edges, indices
