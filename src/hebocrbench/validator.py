"""Schema, semantic and split-leakage validation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
from importlib import resources
import json
from pathlib import Path
from typing import Mapping, Sequence
import unicodedata

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from shapely.geometry import Polygon

from .io import sha256_file
from .reading_order import ReadingOrderCycleError, topological_order
from .unicode_utils import (
    BIDI_CONTROLS,
    dangling_combining_mark_indices,
    has_hebrew_presentation_forms,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    page_id: str | None = None
    location: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "page_id": self.page_id,
            "location": self.location,
        }


@dataclass(slots=True)
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        page_id: str | None = None,
        location: str | None = None,
    ) -> None:
        self.issues.append(ValidationIssue(severity, code, message, page_id, location))

    def extend(self, other: "ValidationReport") -> None:
        self.issues.extend(other.issues)

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _schema_documents() -> tuple[dict[str, object], dict[str, object]]:
    schema_dir = resources.files("hebocrbench").joinpath("schemas")
    gold = json.loads(schema_dir.joinpath("gold-page.schema.json").read_text(encoding="utf-8"))
    pred = json.loads(
        schema_dir.joinpath("prediction-page.schema.json").read_text(encoding="utf-8")
    )
    return gold, pred


def _schema_validate(
    records: Sequence[Mapping[str, object]], *, prediction: bool
) -> ValidationReport:
    gold_schema, pred_schema = _schema_documents()
    schema = pred_schema if prediction else gold_schema
    registry = Registry()
    for uri, document in (
        (str(gold_schema["$id"]), gold_schema),
        (str(pred_schema["$id"]), pred_schema),
    ):
        registry = registry.with_resource(uri, Resource.from_contents(document))
    validator = Draft202012Validator(schema, registry=registry)
    report = ValidationReport()
    for record_index, record in enumerate(records):
        page_id = str(record.get("page_id", "")) or None
        for error in sorted(validator.iter_errors(record), key=lambda e: list(e.absolute_path)):
            location = "/".join(map(str, error.absolute_path)) or f"record[{record_index}]"
            report.add(
                "error",
                "schema",
                error.message,
                page_id=page_id,
                location=location,
            )
    return report


def _validate_polygon(
    report: ValidationReport,
    polygon: object,
    *,
    width: int,
    height: int,
    page_id: str,
    location: str,
) -> None:
    if not isinstance(polygon, list):
        return
    try:
        points = [(float(point[0]), float(point[1])) for point in polygon]
    except (TypeError, ValueError, IndexError):
        return
    if any(x < 0 or y < 0 or x > width or y > height for x, y in points):
        report.add(
            "error",
            "polygon_out_of_bounds",
            f"Polygon exceeds image bounds {width}×{height}",
            page_id=page_id,
            location=location,
        )
    poly = Polygon(points) if len(points) >= 3 else Polygon()
    if poly.is_empty or poly.area <= 0 or not poly.is_valid:
        report.add(
            "error",
            "invalid_polygon",
            "Polygon is empty, zero-area or self-intersecting",
            page_id=page_id,
            location=location,
        )


def _validate_text(
    report: ValidationReport,
    text: str,
    *,
    page_id: str,
    location: str,
    gold: bool,
) -> None:
    if not unicodedata.is_normalized("NFC", text):
        report.add(
            "error" if gold else "warning",
            "non_nfc_text",
            "Text is not NFC-normalized",
            page_id=page_id,
            location=location,
        )
    controls = [f"U+{ord(ch):04X}" for ch in text if ch in BIDI_CONTROLS]
    if controls:
        report.add(
            "error" if gold else "warning",
            "bidi_control_in_gold" if gold else "bidi_control_in_prediction",
            f"Directional formatting controls found: {', '.join(controls)}",
            page_id=page_id,
            location=location,
        )
    dangling = dangling_combining_mark_indices(text)
    if dangling:
        report.add(
            "error" if gold else "warning",
            "dangling_combining_mark",
            f"Combining mark without a base at indices {dangling}",
            page_id=page_id,
            location=location,
        )
    if has_hebrew_presentation_forms(text):
        report.add(
            "warning",
            "hebrew_presentation_form",
            "Hebrew Alphabetic Presentation Form found; preferred encoding uses base letters and marks",
            page_id=page_id,
            location=location,
        )


def _semantic_validate(
    records: Sequence[Mapping[str, object]],
    *,
    gold: bool,
    dataset_root: str | Path | None = None,
) -> ValidationReport:
    report = ValidationReport()
    seen_pages: set[str] = set()
    root = Path(dataset_root) if dataset_root is not None else None
    for record in records:
        page_id = str(record.get("page_id", ""))
        if page_id in seen_pages:
            report.add(
                "error", "duplicate_page_id", f"Duplicate page_id {page_id}", page_id=page_id
            )
        seen_pages.add(page_id)
        image = record.get("image", {}) if gold else {}
        width = int(image.get("width", 0)) if isinstance(image, Mapping) else 0
        height = int(image.get("height", 0)) if isinstance(image, Mapping) else 0
        if gold and root is not None and isinstance(image, Mapping):
            image_path = root / str(image.get("path", ""))
            if not image_path.exists():
                report.add(
                    "error",
                    "missing_image",
                    f"Image does not exist: {image_path}",
                    page_id=page_id,
                    location="image/path",
                )
            elif image.get("sha256") and sha256_file(image_path) != str(image["sha256"]).lower():
                report.add(
                    "error",
                    "image_hash_mismatch",
                    "Image SHA-256 does not match manifest",
                    page_id=page_id,
                    location="image/sha256",
                )

        region_ids: set[str] = set()
        line_ids: set[str] = set()
        regions = record.get("regions", [])
        if not isinstance(regions, list):
            continue
        for region_index, region in enumerate(regions):
            if not isinstance(region, Mapping):
                continue
            region_id = str(region.get("region_id", ""))
            if region_id in region_ids:
                report.add(
                    "error",
                    "duplicate_region_id",
                    f"Duplicate region_id {region_id}",
                    page_id=page_id,
                    location=f"regions/{region_index}",
                )
            region_ids.add(region_id)
            if gold:
                _validate_polygon(
                    report,
                    region.get("polygon"),
                    width=width,
                    height=height,
                    page_id=page_id,
                    location=f"regions/{region_index}/polygon",
                )
            lines = region.get("lines", [])
            if not isinstance(lines, list):
                continue
            for line_index, line in enumerate(lines):
                if not isinstance(line, Mapping):
                    continue
                line_id = str(line.get("line_id", ""))
                if line_id in line_ids:
                    report.add(
                        "error",
                        "duplicate_line_id",
                        f"Duplicate line_id {line_id}",
                        page_id=page_id,
                        location=f"regions/{region_index}/lines/{line_index}",
                    )
                line_ids.add(line_id)
                if gold:
                    _validate_polygon(
                        report,
                        line.get("polygon"),
                        width=width,
                        height=height,
                        page_id=page_id,
                        location=f"regions/{region_index}/lines/{line_index}/polygon",
                    )
                text = line.get("text", "")
                if isinstance(text, str):
                    _validate_text(
                        report,
                        text,
                        page_id=page_id,
                        location=f"regions/{region_index}/lines/{line_index}/text",
                        gold=gold,
                    )
                if gold and line.get("language") == "he" and line.get("base_direction") != "rtl":
                    report.add(
                        "warning",
                        "hebrew_line_not_rtl",
                        "Hebrew line does not declare base_direction=rtl",
                        page_id=page_id,
                        location=f"regions/{region_index}/lines/{line_index}/base_direction",
                    )

        reading = record.get("reading_order", {})
        if isinstance(reading, Mapping):
            edges = reading.get("edges", [])
            try:
                topological_order(list(region_ids), edges if isinstance(edges, list) else [])
            except ReadingOrderCycleError:
                report.add(
                    "error",
                    "reading_order_cycle",
                    "Reading-order graph contains a cycle",
                    page_id=page_id,
                    location="reading_order/edges",
                )
            except ValueError as exc:
                report.add(
                    "error",
                    "reading_order_unknown_node",
                    str(exc),
                    page_id=page_id,
                    location="reading_order/edges",
                )

        for table_index, table in enumerate(record.get("tables", []) or []):
            if not isinstance(table, Mapping):
                continue
            rows, cols = int(table.get("n_rows", 0)), int(table.get("n_cols", 0))
            occupied: set[tuple[int, int]] = set()
            for cell_index, cell in enumerate(table.get("cells", []) or []):
                if not isinstance(cell, Mapping):
                    continue
                r0, r1 = int(cell.get("row_start", 0)), int(cell.get("row_end", 0))
                c0, c1 = int(cell.get("col_start", 0)), int(cell.get("col_end", 0))
                if not (0 <= r0 < r1 <= rows and 0 <= c0 < c1 <= cols):
                    report.add(
                        "error",
                        "table_cell_out_of_bounds",
                        "Cell span is outside the declared logical grid",
                        page_id=page_id,
                        location=f"tables/{table_index}/cells/{cell_index}",
                    )
                for slot in ((r, c) for r in range(r0, r1) for c in range(c0, c1)):
                    if slot in occupied:
                        report.add(
                            "error",
                            "overlapping_table_cells",
                            f"Multiple cells occupy logical slot {slot}",
                            page_id=page_id,
                            location=f"tables/{table_index}/cells/{cell_index}",
                        )
                    occupied.add(slot)
                if isinstance(cell.get("text"), str):
                    _validate_text(
                        report,
                        str(cell["text"]),
                        page_id=page_id,
                        location=f"tables/{table_index}/cells/{cell_index}/text",
                        gold=gold,
                    )
    return report


def validate_gold_records(
    records: Sequence[Mapping[str, object]], *, dataset_root: str | Path | None = None
) -> ValidationReport:
    report = _schema_validate(records, prediction=False)
    report.extend(_semantic_validate(records, gold=True, dataset_root=dataset_root))
    return report


def validate_prediction_records(records: Sequence[Mapping[str, object]]) -> ValidationReport:
    report = _schema_validate(records, prediction=True)
    report.extend(_semantic_validate(records, gold=False))
    return report


def _nested_value(record: Mapping[str, object], path: str) -> object:
    value: object = record
    for part in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def audit_split_leakage(
    records: Sequence[Mapping[str, object]],
    *,
    group_fields: Sequence[str] = (
        "document_id",
        "metadata.writer_id",
        "metadata.scribe_id",
        "metadata.template_id",
    ),
) -> ValidationReport:
    report = ValidationReport()
    for field_path in group_fields:
        values: dict[str, set[str]] = defaultdict(set)
        pages: dict[str, list[str]] = defaultdict(list)
        for record in records:
            value = _nested_value(record, field_path)
            if value is None or value == "":
                continue
            key = str(value)
            values[key].add(str(record.get("split", "")))
            pages[key].append(str(record.get("page_id", "")))
        for value, splits in values.items():
            if len(splits) > 1:
                report.add(
                    "error",
                    f"split_leak_{field_path.split('.')[-1]}",
                    f"{field_path}={value!r} appears in splits {sorted(splits)} on pages {pages[value]}",
                )

    hashes: dict[str, set[str]] = defaultdict(set)
    hash_pages: dict[str, list[str]] = defaultdict(list)
    for record in records:
        image = record.get("image", {})
        if not isinstance(image, Mapping) or not image.get("sha256"):
            continue
        digest = str(image["sha256"])
        hashes[digest].add(str(record.get("split", "")))
        hash_pages[digest].append(str(record.get("page_id", "")))
    for digest, splits in hashes.items():
        if len(splits) > 1:
            report.add(
                "error",
                "split_leak_image_hash",
                f"Image hash {digest} appears in splits {sorted(splits)} on pages {hash_pages[digest]}",
            )

    text_hashes: dict[str, set[str]] = defaultdict(set)
    for record in records:
        text = "\n".join(
            str(line.get("text", ""))
            for region in record.get("regions", []) or []
            if isinstance(region, Mapping)
            for line in region.get("lines", []) or []
            if isinstance(line, Mapping)
        )
        if text:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            text_hashes[digest].add(str(record.get("split", "")))
    for digest, splits in text_hashes.items():
        if len(splits) > 1:
            report.add(
                "warning",
                "split_duplicate_text",
                f"Exact page text hash {digest} appears in splits {sorted(splits)}",
            )
    return report
