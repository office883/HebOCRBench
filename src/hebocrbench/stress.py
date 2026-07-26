"""Synthetic Hebrew OCR diagnostic-suite generation.

The generator renders *logical-order* Unicode text with Pillow/libraqm.  It
never mirrors an image or reverses a string.  Font files are referenced at
runtime and are never copied into the generated suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import resources
from io import BytesIO
import os
from pathlib import Path
import random
import subprocess
import unicodedata
from typing import Any, Mapping, Sequence

from PIL import (
    Image,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    ImageFont,
    __version__ as PILLOW_VERSION,
)
import yaml

from .io import sha256_file, write_json, write_jsonl

DEFAULT_VARIANTS = ("clean", "blur", "jpeg", "low_contrast")
SUPPORTED_VARIANTS = frozenset({"clean", "blur", "jpeg", "low_contrast", "speckle"})


@dataclass(frozen=True, slots=True)
class GenerationResult:
    root: Path
    gold_path: Path
    manifest_path: Path
    page_count: int
    font_path: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "gold_path": str(self.gold_path),
            "manifest_path": str(self.manifest_path),
            "page_count": self.page_count,
            "font_path": str(self.font_path),
        }


def discover_hebrew_font(explicit: str | Path | None = None) -> Path:
    """Find a Hebrew-capable font without redistributing it."""

    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("HEBOCRBENCH_FONT"):
        candidates.append(Path(os.environ["HEBOCRBENCH_FONT"]).expanduser())
    candidates.extend(
        [
            Path("/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSerifHebrew-Regular.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/Library/Fonts/Arial Hebrew.ttf"),
            Path.home() / "Library/Fonts/Arial Hebrew.ttf",
            Path("C:/Windows/Fonts/arial.ttf"),
        ]
    )
    try:
        completed = subprocess.run(
            ["fc-match", "-f", "%{file}\n", "Noto Sans Hebrew"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        candidates.extend(
            Path(line.strip()) for line in completed.stdout.splitlines() if line.strip()
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            font = ImageFont.truetype(str(candidate), size=36, layout_engine=ImageFont.Layout.RAQM)
            # This is not a complete glyph-coverage test, but it rejects files
            # that cannot even render a representative Hebrew cluster.
            if font.getbbox("שָׁלוֹם"):
                return candidate.resolve()
        except (OSError, ValueError):
            continue
    raise FileNotFoundError(
        "No Hebrew-capable TrueType/OpenType font found. Set HEBOCRBENCH_FONT or pass font_path."
    )


def _default_cases_path() -> Path:
    return Path(resources.files("hebocrbench").joinpath("data/stress_cases.yaml"))


def load_stress_cases(path: str | Path | None = None) -> list[dict[str, Any]]:
    source = Path(path) if path is not None else _default_cases_path()
    value = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or not isinstance(value.get("cases"), list):
        raise ValueError(f"Stress case file {source} must contain a 'cases' list")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value["cases"]):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Case #{index} must be a mapping")
        case_id = str(raw.get("id", ""))
        text = raw.get("text")
        if not case_id or case_id in seen:
            raise ValueError(f"Missing or duplicate case id: {case_id!r}")
        if not isinstance(text, str) or not text:
            raise ValueError(f"Case {case_id} must contain non-empty text")
        seen.add(case_id)
        normalized = dict(raw)
        normalized["text"] = unicodedata.normalize("NFC", text)
        cases.append(normalized)
    return cases


def _load_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path), size=size, layout_engine=ImageFont.Layout.RAQM)


def _text_bbox(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    direction: str = "rtl",
    language: str = "he",
) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=font, direction=direction, language=language)


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    *,
    max_width: int,
    max_height: int,
    language: str,
    start_size: int = 76,
    min_size: int = 28,
) -> ImageFont.FreeTypeFont:
    for size in range(start_size, min_size - 1, -2):
        font = _load_font(font_path, size)
        left, top, right, bottom = _text_bbox(draw, text, font, direction="rtl", language=language)
        if right - left <= max_width and bottom - top <= max_height:
            return font
    return _load_font(font_path, min_size)


def _draw_rtl(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    language: str = "he",
    anchor: str = "rm",
    fill: tuple[int, int, int] = (20, 20, 20),
) -> None:
    draw.text(
        xy,
        text,
        font=font,
        fill=fill,
        anchor=anchor,
        direction="rtl",
        language=language,
    )


def _stable_rng(seed: int, page_id: str, variant: str) -> random.Random:
    digest = sha256(f"{seed}\0{page_id}\0{variant}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _apply_variant(
    image: Image.Image, variant: str, *, seed: int, page_id: str
) -> tuple[Image.Image, list[dict[str, object]]]:
    if variant not in SUPPORTED_VARIANTS:
        raise ValueError(f"Unsupported degradation variant: {variant}")
    if variant == "clean":
        return image.copy(), []
    if variant == "blur":
        radius = 1.25
        return image.filter(ImageFilter.GaussianBlur(radius=radius)), [
            {"type": "gaussian_blur", "radius": radius}
        ]
    if variant == "low_contrast":
        factor = 0.34
        softened = ImageEnhance.Contrast(image).enhance(factor)
        softened = ImageEnhance.Brightness(softened).enhance(1.08)
        return softened, [{"type": "contrast", "factor": factor}]
    if variant == "jpeg":
        quality = 32
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, subsampling=2, optimize=False)
        buffer.seek(0)
        degraded = Image.open(buffer).convert("RGB")
        degraded.load()
        return degraded, [{"type": "jpeg_reencode", "quality": quality}]

    # Deterministic salt-and-pepper noise without adding NumPy as a direct API
    # dependency.  The suite's images are small enough for sparse point noise.
    degraded = image.copy()
    draw = ImageDraw.Draw(degraded)
    rng = _stable_rng(seed, page_id, variant)
    count = max(1, image.width * image.height // 450)
    for _ in range(count):
        x = rng.randrange(image.width)
        y = rng.randrange(image.height)
        shade = 0 if rng.random() < 0.65 else 255
        draw.point((x, y), fill=(shade, shade, shade))
    return degraded, [{"type": "salt_pepper", "point_count": count}]


def _save_png(image: Image.Image, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG", optimize=False, compress_level=6)
    return sha256_file(path)


def _rect(x0: int, y0: int, x1: int, y1: int) -> list[list[int]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _metadata(
    *,
    font_id: str,
    language: str = "he",
    vocalization: str = "none",
    document_type: str,
    layout_type: str,
    template_id: str,
    degradation: list[dict[str, object]],
    script_style: str = "modern_square_print",
) -> dict[str, object]:
    return {
        "languages": [language],
        "script": "Hebr",
        "script_style": script_style,
        "era": "modern",
        "document_type": document_type,
        "layout_type": layout_type,
        "vocalization": vocalization,
        "source_type": "synthetic",
        "font_id": font_id,
        "writer_id": None,
        "scribe_id": None,
        "template_id": template_id,
        "source_collection": "hebocrbench-synthetic-seed-v1",
        "license": "CC0-1.0",
        "degradation": degradation,
    }


def _line_card_base(case: Mapping[str, Any], font_path: Path) -> tuple[Image.Image, dict[str, Any]]:
    width, height = 1800, 420
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (24, 24, width - 24, height - 24), radius=18, outline=(215, 215, 215), width=2
    )
    text = str(case["text"])
    language = str(case.get("language", "he"))
    font = _fit_font(
        draw,
        text,
        font_path,
        max_width=width - 170,
        max_height=height - 125,
        language=language,
    )
    _draw_rtl(draw, (width - 82, height / 2), text, font, language=language)
    region_polygon = _rect(50, 55, width - 50, height - 55)
    line_polygon = _rect(70, 92, width - 70, height - 90)
    page = {
        "regions": [
            {
                "region_id": "r1",
                "type": "body",
                "polygon": region_polygon,
                "base_direction": "rtl",
                "reading_index": 0,
                "language": language,
                "lines": [
                    {
                        "line_id": "l1",
                        "polygon": line_polygon,
                        "baseline": [[width - 78, 270], [78, 270]],
                        "text": text,
                        "base_direction": "rtl",
                        "language": language,
                        "tags": list(case.get("tags", [])),
                    }
                ],
            }
        ],
        "reading_order": {"edges": []},
        "tables": [],
        "form_fields": [],
    }
    return image, page


def _table_base(font_path: Path) -> tuple[Image.Image, dict[str, Any]]:
    width, height = 1800, 1320
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(font_path, 62)
    cell_font = _load_font(font_path, 38)
    _draw_rtl(draw, (width - 110, 105), "טבלת הזמנות — עמודות לוגיות מימין לשמאל", title_font)

    rows = [
        ["פריט", "כמות", "מחיר", "תאריך"],
        ["מחברת", "12", "₪48.00", "22/07/2026"],
        ["עיפרון HB", "30", "₪15.90", "23/07/2026"],
        ["סרגל 30 cm", "5", "₪27.50", "24/07/2026"],
    ]
    n_rows, n_cols = len(rows), len(rows[0])
    x0, y0, x1, y1 = 110, 230, width - 110, 1090
    cell_w = (x1 - x0) / n_cols
    cell_h = (y1 - y0) / n_rows
    cells: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []

    for boundary in range(n_cols + 1):
        x = round(x0 + boundary * cell_w)
        draw.line((x, y0, x, y1), fill=(45, 45, 45), width=2)
    for boundary in range(n_rows + 1):
        y = round(y0 + boundary * cell_h)
        draw.line((x0, y, x1, y), fill=(45, 45, 45), width=2)

    for row_index, row in enumerate(rows):
        logical_row_texts: list[str] = []
        row_top = round(y0 + row_index * cell_h)
        row_bottom = round(y0 + (row_index + 1) * cell_h)
        for logical_col, text in enumerate(row):
            # Logical column zero is the first/read-first Hebrew column and is
            # therefore the rightmost physical cell.
            physical_col = n_cols - 1 - logical_col
            left = round(x0 + physical_col * cell_w)
            right = round(x0 + (physical_col + 1) * cell_w)
            _draw_rtl(
                draw,
                (right - 24, (row_top + row_bottom) / 2),
                text,
                cell_font,
                language="he",
            )
            cells.append(
                {
                    "row_start": row_index,
                    "row_end": row_index + 1,
                    "col_start": logical_col,
                    "col_end": logical_col + 1,
                    "text": text,
                    "polygon": _rect(left + 2, row_top + 2, right - 2, row_bottom - 2),
                }
            )
            logical_row_texts.append(text)
        lines.append(
            {
                "line_id": f"table-row-{row_index}",
                "polygon": _rect(x0 + 8, row_top + 8, x1 - 8, row_bottom - 8),
                "baseline": [[x1 - 12, row_bottom - 30], [x0 + 12, row_bottom - 30]],
                "text": " | ".join(logical_row_texts),
                "base_direction": "rtl",
                "language": "he",
                "tags": ["structure:table", f"table:row:{row_index}"],
            }
        )

    page = {
        "regions": [
            {
                "region_id": "title",
                "type": "heading",
                "polygon": _rect(90, 45, width - 90, 175),
                "base_direction": "rtl",
                "reading_index": 0,
                "language": "he",
                "lines": [
                    {
                        "line_id": "title-l1",
                        "polygon": _rect(100, 55, width - 100, 165),
                        "baseline": [[width - 110, 130], [110, 130]],
                        "text": "טבלת הזמנות — עמודות לוגיות מימין לשמאל",
                        "base_direction": "rtl",
                        "language": "he",
                        "tags": ["structure:title"],
                    }
                ],
            },
            {
                "region_id": "table",
                "type": "table",
                "polygon": _rect(x0, y0, x1, y1),
                "base_direction": "rtl",
                "reading_index": 1,
                "language": "he",
                "lines": lines,
            },
        ],
        "reading_order": {"edges": [["title", "table"]]},
        "tables": [
            {
                "table_id": "orders",
                "region_id": "table",
                "n_rows": n_rows,
                "n_cols": n_cols,
                "cells": cells,
            }
        ],
        "form_fields": [],
    }
    return image, page


def _form_base(font_path: Path) -> tuple[Image.Image, dict[str, Any]]:
    width, height = 1800, 1320
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(font_path, 62)
    body_font = _load_font(font_path, 45)
    _draw_rtl(draw, (width - 110, 105), "טופס בקשה לדוגמה", title_font)
    fields = [
        ("name", "שם מלא", "דנה כהן"),
        ("id", "מספר מזהה", "123456789"),
        ("date", "תאריך", "22/07/2026"),
        ("email", "דוא״ל", "dana@example.com"),
    ]
    regions: list[dict[str, Any]] = [
        {
            "region_id": "title",
            "type": "heading",
            "polygon": _rect(90, 45, width - 90, 175),
            "base_direction": "rtl",
            "reading_index": 0,
            "language": "he",
            "lines": [
                {
                    "line_id": "title-l1",
                    "polygon": _rect(100, 55, width - 100, 165),
                    "baseline": [[width - 110, 130], [110, 130]],
                    "text": "טופס בקשה לדוגמה",
                    "base_direction": "rtl",
                    "language": "he",
                    "tags": ["structure:title"],
                }
            ],
        }
    ]
    form_fields: list[dict[str, Any]] = []
    previous = "title"
    edges: list[list[str]] = []
    for index, (field_id, label, value) in enumerate(fields, start=1):
        top = 235 + (index - 1) * 220
        bottom = top + 150
        draw.rounded_rectangle(
            (110, top, width - 110, bottom), radius=12, outline=(80, 80, 80), width=2
        )
        _draw_rtl(draw, (width - 145, (top + bottom) / 2), f"{label}:", body_font)
        draw.text(
            (930, (top + bottom) / 2),
            value,
            font=body_font,
            fill=(20, 20, 20),
            anchor="lm",
            direction="ltr" if any("A" <= ch <= "z" or ch.isdigit() for ch in value) else "rtl",
            language="he",
        )
        region_id = f"field-{field_id}"
        text = f"{label}: {value}"
        regions.append(
            {
                "region_id": region_id,
                "type": "form_field",
                "polygon": _rect(110, top, width - 110, bottom),
                "base_direction": "rtl",
                "reading_index": index,
                "language": "he",
                "lines": [
                    {
                        "line_id": f"{region_id}-l1",
                        "polygon": _rect(125, top + 12, width - 125, bottom - 12),
                        "baseline": [[width - 140, bottom - 42], [140, bottom - 42]],
                        "text": text,
                        "base_direction": "rtl",
                        "language": "he",
                        "tags": ["structure:form", f"field:{field_id}"],
                    }
                ],
            }
        )
        edges.append([previous, region_id])
        previous = region_id
        form_fields.append(
            {
                "field_id": field_id,
                "label_text": label,
                "value_text": value,
                "label_region_id": region_id,
                "value_region_id": region_id,
            }
        )
    return image, {
        "regions": regions,
        "reading_order": {"edges": edges},
        "tables": [],
        "form_fields": form_fields,
    }


def _columns_base(font_path: Path) -> tuple[Image.Image, dict[str, Any]]:
    width, height = 1800, 1500
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(font_path, 62)
    body_font = _load_font(font_path, 39)
    title = "עמוד דו־טורי: קוראים תחילה את הטור הימני"
    _draw_rtl(draw, (width - 100, 95), title, title_font)
    right_lines = [
        "זהו הטור הימני, והוא הראשון בסדר הקריאה.",
        "בשורה הזאת מופיעים 2026 ו־OCR-v2.1 יחד.",
        "הסדר הלוגי נשמר גם כאשר הספרות נעות משמאל לימין.",
        "לאחר סיום הטור עוברים אל הטור השמאלי.",
    ]
    left_lines = [
        "זהו הטור השמאלי, והוא השני בסדר הקריאה.",
        "כתובת qa@example.com נשמרת כרצף LTR פנימי.",
        "סעיף 3(ב)(2) אינו מתהפך ואינו משתקף.",
        "הערת סיום: דיוק אותיות לבדו אינו מספיק.",
    ]
    regions: list[dict[str, Any]] = []
    for region_id, physical_left, physical_right, reading_index, lines in (
        ("right", 950, 1710, 1, right_lines),
        ("left", 90, 850, 2, left_lines),
    ):
        line_objects: list[dict[str, Any]] = []
        for index, text in enumerate(lines):
            top = 255 + index * 250
            _draw_rtl(draw, (physical_right - 25, top + 65), text, body_font)
            line_objects.append(
                {
                    "line_id": f"{region_id}-l{index + 1}",
                    "polygon": _rect(physical_left + 12, top, physical_right - 12, top + 145),
                    "baseline": [[physical_right - 20, top + 95], [physical_left + 20, top + 95]],
                    "text": text,
                    "base_direction": "rtl",
                    "language": "he",
                    "tags": ["layout:two_columns", f"column:{region_id}"],
                }
            )
        regions.append(
            {
                "region_id": region_id,
                "type": "body",
                "polygon": _rect(physical_left, 220, physical_right, 1280),
                "base_direction": "rtl",
                "reading_index": reading_index,
                "language": "he",
                "lines": line_objects,
            }
        )
    regions.insert(
        0,
        {
            "region_id": "title",
            "type": "heading",
            "polygon": _rect(80, 35, width - 80, 175),
            "base_direction": "rtl",
            "reading_index": 0,
            "language": "he",
            "lines": [
                {
                    "line_id": "title-l1",
                    "polygon": _rect(95, 45, width - 95, 165),
                    "baseline": [[width - 100, 125], [100, 125]],
                    "text": title,
                    "base_direction": "rtl",
                    "language": "he",
                    "tags": ["structure:title", "layout:two_columns"],
                }
            ],
        },
    )
    return image, {
        "regions": regions,
        "reading_order": {"edges": [["title", "right"], ["right", "left"]]},
        "tables": [],
        "form_fields": [],
    }


def _materialize_page(
    *,
    base_image: Image.Image,
    page_fragment: Mapping[str, Any],
    root: Path,
    page_id: str,
    document_id: str,
    track: str,
    split: str,
    metadata: Mapping[str, Any],
    variant: str,
    seed: int,
) -> dict[str, Any]:
    image, degradation = _apply_variant(base_image, variant, seed=seed, page_id=page_id)
    image_rel = Path("images") / f"{page_id}.png"
    digest = _save_png(image, root / image_rel)
    page = {
        "schema_version": "1.0",
        "page_id": page_id,
        "document_id": document_id,
        "split": split,
        "track": track,
        "image": {
            "path": image_rel.as_posix(),
            "width": image.width,
            "height": image.height,
            "rotation_degrees": 0,
            "sha256": digest,
        },
        "metadata": dict(metadata),
        "regions": page_fragment.get("regions", []),
        "reading_order": page_fragment.get("reading_order", {"edges": []}),
        "tables": page_fragment.get("tables", []),
        "form_fields": page_fragment.get("form_fields", []),
    }
    page["metadata"]["degradation"] = degradation
    return page


def generate_stress_suite(
    output_dir: str | Path,
    *,
    cases_path: str | Path | None = None,
    seed: int = 20260722,
    variants: Sequence[str] = DEFAULT_VARIANTS,
    limit: int | None = None,
    font_path: str | Path | None = None,
    include_structured: bool = True,
) -> GenerationResult:
    """Generate a deterministic, schema-valid diagnostic dataset."""

    if not variants:
        raise ValueError("At least one degradation variant is required")
    unknown = set(variants) - SUPPORTED_VARIANTS
    if unknown:
        raise ValueError(f"Unsupported variants: {sorted(unknown)}")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    font = discover_hebrew_font(font_path)
    font_id = font.name
    cases = load_stress_cases(cases_path)
    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        cases = cases[:limit]

    pages: list[dict[str, Any]] = []
    for case in cases:
        base_image, fragment = _line_card_base(case, font)
        language = str(case.get("language", "he"))
        track = "bidi_diagnostic" if language == "he" else "hebrew_script_languages"
        for variant in variants:
            page_id = f"diag-{case['id']}-{variant}"
            metadata = _metadata(
                font_id=font_id,
                language=language,
                vocalization=str(case.get("vocalization", "none")),
                document_type="line_card",
                layout_type="single_line",
                template_id=f"diag-{case['id']}",
                degradation=[],
            )
            pages.append(
                _materialize_page(
                    base_image=base_image,
                    page_fragment=fragment,
                    root=root,
                    page_id=page_id,
                    document_id=f"diag-{case['id']}",
                    track=track,
                    split="diagnostic",
                    metadata=metadata,
                    variant=variant,
                    seed=seed,
                )
            )

    if include_structured:
        structured_specs = [
            (
                "table-rtl-logical",
                "structured_documents",
                "table",
                "rtl_table",
                _table_base,
            ),
            (
                "form-mixed-values",
                "structured_documents",
                "form",
                "key_value_form",
                _form_base,
            ),
            (
                "two-columns-rtl",
                "printed_modern",
                "article",
                "two_columns",
                _columns_base,
            ),
        ]
        for case_id, track, document_type, layout_type, renderer in structured_specs:
            base_image, fragment = renderer(font)
            for variant in variants:
                page_id = f"layout-{case_id}-{variant}"
                metadata = _metadata(
                    font_id=font_id,
                    language="he",
                    vocalization="none",
                    document_type=document_type,
                    layout_type=layout_type,
                    template_id=f"layout-{case_id}",
                    degradation=[],
                )
                pages.append(
                    _materialize_page(
                        base_image=base_image,
                        page_fragment=fragment,
                        root=root,
                        page_id=page_id,
                        document_id=f"layout-{case_id}",
                        track=track,
                        split="diagnostic",
                        metadata=metadata,
                        variant=variant,
                        seed=seed,
                    )
                )

    gold_path = root / "gold.jsonl"
    write_jsonl(gold_path, pages)
    cases_source = Path(cases_path) if cases_path is not None else _default_cases_path()
    manifest = {
        "benchmark": "HebOCRBench synthetic diagnostic suite",
        "schema_version": "1.0",
        "seed": seed,
        "variants": list(variants),
        "page_count": len(pages),
        "case_count": len(cases),
        "include_structured": include_structured,
        "font": {
            "basename": font.name,
            "sha256": sha256_file(font),
            "redistributed": False,
        },
        "renderer": {
            "pillow_version": PILLOW_VERSION,
            "raqm_available": bool(ImageFont.core.HAVE_RAQM),
        },
        "cases_sha256": sha256_file(cases_source),
        "gold_sha256": sha256_file(gold_path),
    }
    manifest_path = root / "generation_manifest.json"
    write_json(manifest_path, manifest)
    (root / "README.txt").write_text(
        "Generated by HebOCRBench. Text is stored in Unicode logical order; images are not mirrored.\n"
        f"Font used at generation time: {font.name} (not redistributed).\n",
        encoding="utf-8",
    )
    return GenerationResult(
        root=root,
        gold_path=gold_path,
        manifest_path=manifest_path,
        page_count=len(pages),
        font_path=font,
    )
