"""Bidi-aware geometric reconstruction for Modern-Hebrew PDF words.

PDF text streams often expose correct logical spelling inside each word while
enumerating word boxes in a visual or producer-specific order. This module
reconstructs line order from geometry and directional runs without reversing
the characters inside a word.
"""

from __future__ import annotations

from typing import Any, Sequence
import unicodedata


def _word_direction(text: str) -> str:
    has_number = False
    for char in text:
        bidi = unicodedata.bidirectional(char)
        if bidi in {"R", "AL"}:
            return "rtl"
        if bidi == "L":
            return "ltr"
        if bidi in {"EN", "AN"}:
            has_number = True
    return "ltr" if has_number else "neutral"


def _base_direction(engine: Any, row: Sequence[Any]) -> str:
    source_text = " ".join(item.text for item in sorted(row, key=lambda item: item.order))
    direction = engine.first_strong_direction(source_text)
    if direction in {"rtl", "ltr"}:
        return direction
    directions = [_word_direction(item.text) for item in row]
    rtl = sum(value == "rtl" for value in directions)
    ltr = sum(value == "ltr" for value in directions)
    return "rtl" if rtl >= ltr else "ltr"


def _resolved_directions(directions: Sequence[str], base_direction: str) -> list[str]:
    result = list(directions)
    for index, value in enumerate(result):
        if value != "neutral":
            continue
        left = next(
            (
                result[position]
                for position in range(index - 1, -1, -1)
                if result[position] != "neutral"
            ),
            None,
        )
        right = next(
            (
                result[position]
                for position in range(index + 1, len(result))
                if result[position] != "neutral"
            ),
            None,
        )
        result[index] = left if left is not None and left == right else base_direction
    return result


def visual_words_to_logical(engine: Any, row: Sequence[Any]) -> list[Any]:
    """Return a logical word sequence from one visual row of word boxes."""

    visual = sorted(row, key=lambda item: (item.x0, item.order))
    if not visual:
        return []
    base_direction = _base_direction(engine, row)
    directions = _resolved_directions(
        [_word_direction(item.text) for item in visual],
        base_direction,
    )

    runs: list[tuple[str, list[Any]]] = []
    for item, direction in zip(visual, directions):
        if not runs or runs[-1][0] != direction:
            runs.append((direction, [item]))
        else:
            runs[-1][1].append(item)

    if base_direction == "rtl":
        runs.reverse()

    logical: list[Any] = []
    for direction, items in runs:
        if direction == "rtl":
            items = list(reversed(items))
        logical.extend(items)
    return logical


def install(engine: Any) -> None:
    """Install geometric word-order reconstruction into the PDF engine."""

    def extract_regions(page: Any, scale: float) -> tuple[list[dict[str, object]], str]:
        words = engine._words(page)
        if not words:
            return [], ""
        by_block: dict[int, list[Any]] = {}
        for word in words:
            by_block.setdefault(word.block, []).append(word)

        regions: list[dict[str, object]] = []
        logical_lines: list[str] = []
        for region_index, block_id in enumerate(sorted(by_block)):
            rows = engine._cluster_visual_rows(by_block[block_id])
            lines: list[dict[str, object]] = []
            for line_index, row in enumerate(rows):
                ordered = visual_words_to_logical(engine, row)
                text = engine._smart_join(item.text for item in ordered)
                if not text:
                    continue
                x0 = min(item.x0 for item in row)
                y0 = min(item.y0 for item in row)
                x1 = max(item.x1 for item in row)
                y1 = max(item.y1 for item in row)
                direction = engine.first_strong_direction(text)
                if direction == "neutral":
                    direction = "rtl"
                baseline = (
                    [[x1 * scale, y1 * scale], [x0 * scale, y1 * scale]]
                    if direction == "rtl"
                    else [[x0 * scale, y1 * scale], [x1 * scale, y1 * scale]]
                )
                lines.append(
                    {
                        "line_id": f"b{block_id}-l{line_index}",
                        "polygon": engine._rect(x0, y0, x1, y1, scale),
                        "baseline": baseline,
                        "text": text,
                        "base_direction": direction,
                        "language": engine._language(text),
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

    engine._word_direction = _word_direction
    engine._visual_words_to_logical = lambda row: visual_words_to_logical(engine, row)
    engine._extract_regions = extract_regions
