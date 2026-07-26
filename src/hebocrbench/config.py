"""Typed benchmark configuration with conservative Hebrew OCR defaults."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True, slots=True)
class MatchingConfig:
    use_shared_ids: bool = False
    line_iou_threshold: float = 0.30
    region_iou_threshold: float = 0.50
    table_iou_threshold: float = 0.30
    table_topology_threshold: float = 0.75


@dataclass(frozen=True, slots=True)
class ConformanceConfig:
    diagnostic_track: str = "bidi_diagnostic"
    min_exact_line_rate: float = 0.95
    min_ltr_run_exact_rate: float = 0.98
    min_numeric_exact_rate: float = 0.99
    min_bracket_exact_rate: float = 0.99
    max_visual_order_failure_count: int = 0
    max_bidi_control_count: int = 0
    max_unbalanced_bidi_controls: int = 0


@dataclass(frozen=True, slots=True)
class StatisticsConfig:
    bootstrap_samples: int = 0
    confidence: float = 0.95
    seed: int = 20260722
    worst_page_count: int = 20
    worst_slice_min_pages: int = 1


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    schema_version: str = "1.0"
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    conformance: ConformanceConfig = field(default_factory=ConformanceConfig)
    statistics: StatisticsConfig = field(default_factory=StatisticsConfig)
    slice_fields: tuple[str, ...] = (
        "track",
        "metadata.vocalization",
        "metadata.document_type",
        "metadata.layout_type",
        "metadata.script_style",
        "metadata.source_type",
    )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None = None) -> "BenchmarkConfig":
        raw = dict(value or {})
        matching_raw = raw.get("matching", {}) or {}
        conformance_raw = raw.get("conformance", {}) or {}
        statistics_raw = raw.get("statistics", {}) or {}
        slices = raw.get("slice_fields", cls().slice_fields)
        if not isinstance(slices, (list, tuple)):
            raise ValueError("slice_fields must be a list or tuple")
        config = cls(
            schema_version=str(raw.get("schema_version", "1.0")),
            matching=MatchingConfig(**dict(matching_raw)),
            conformance=ConformanceConfig(**dict(conformance_raw)),
            statistics=StatisticsConfig(**dict(statistics_raw)),
            slice_fields=tuple(str(item) for item in slices),
        )
        config.validate()
        return config

    def validate(self) -> None:
        for name, value in (
            ("line_iou_threshold", self.matching.line_iou_threshold),
            ("region_iou_threshold", self.matching.region_iou_threshold),
            ("table_iou_threshold", self.matching.table_iou_threshold),
            ("table_topology_threshold", self.matching.table_topology_threshold),
            ("min_exact_line_rate", self.conformance.min_exact_line_rate),
            ("min_ltr_run_exact_rate", self.conformance.min_ltr_run_exact_rate),
            ("min_numeric_exact_rate", self.conformance.min_numeric_exact_rate),
            ("min_bracket_exact_rate", self.conformance.min_bracket_exact_rate),
            ("confidence", self.statistics.confidence),
        ):
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")
        if self.statistics.bootstrap_samples < 0:
            raise ValueError("bootstrap_samples must be non-negative")
        if self.statistics.worst_page_count < 0:
            raise ValueError("worst_page_count must be non-negative")
        if self.statistics.worst_slice_min_pages < 1:
            raise ValueError("worst_slice_min_pages must be at least 1")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["slice_fields"] = list(self.slice_fields)
        return result


def load_benchmark_config(path: str | Path) -> BenchmarkConfig:
    source = Path(path)
    value = yaml.safe_load(source.read_text(encoding="utf-8"))
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError(f"Benchmark config {source} must contain a YAML mapping")
    return BenchmarkConfig.from_mapping(value)
