"""Resumable, test-only runners for the five official Modern Hebrew tracks.

The runner is deliberately stricter than a convenience benchmark script:

* only held-out records are submitted: ``test`` everywhere, plus the locked
  ``diagnostic`` split for the BiDi conformance track whose corpus has no
  records literally named ``test``;
* page engines receive a new envelope containing only ``page_id`` and
  ``image.path``;
* the recognition track operates on the already-derived line image and never
  consumes a gold polygon, line ID, or transcription;
* Tesseract's historical-press oracle-layout extension receives only layout
  IDs, polygons and reading indexes in addition to ``page_id``/``image.path``;
  Surya OCR 2 receives the blind full-page envelope instead;
* every page, including an engine failure, has a schema-valid prediction;
* cache keys bind the exact image bytes, engine configuration and track; and
* a headline score is attempted only for a complete (non-limited) run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import tempfile
import time
from typing import Any, Literal
import unicodedata

from PIL import Image

from .adapters.apple_vision import run_apple_vision_page_ocr
from .adapters.surya2_llamacpp import (
    llama_cpp_version,
    normalize_surya2_server_url,
    run_surya2_page_ocr,
)
from .adapters.tesseract import recognize_with_tesseract, tesseract_version
from .adapters.tesseract_page import run_tesseract_page_ocr
from .evaluator import evaluate_dataset
from .io import load_jsonl, sha256_file, write_json, write_jsonl
from .modern_suite import (
    DEFAULT_HEADLINE_TRACKS,
    ModernSuiteSpec,
    load_modern_suite_lock,
    suite_evidence_for_track,
)
from .official_score import verify_and_combine_modern_reports
from .report import write_evaluation_artifacts
from .tracks import load_track
from .validator import validate_gold_records, validate_prediction_records

Engine = Literal["tesseract", "apple-vision", "surya2-llamacpp"]
Prediction = dict[str, Any]
Predictor = Callable[[str, Mapping[str, Any], Path, "BaselineSettings"], Prediction]

RUNNER_SCHEMA_VERSION = "1.0"
LINE_TRACK = "modern-line-recognition-v1"
BIDI_TRACK = "modern-bidi-v1"
MODERN_HANDWRITING_TRACK = "modern-handwriting-v1"
PINKAS_HANDWRITING_TRACK = "historical-pinkas-handwriting-v1"
HISTORICAL_PRESS_TRACK = "historical-hebrew-press-mixed-v1"
BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK = "biblical-niqqud-synthetic-diagnostic-v1"
RASHI_PRINT_DIAGNOSTIC_TRACK = "rashi-print-synthetic-diagnostic-v1"

REAL_EXTENSION_TRACKS = (
    MODERN_HANDWRITING_TRACK,
    HISTORICAL_PRESS_TRACK,
    PINKAS_HANDWRITING_TRACK,
)
SYNTHETIC_DIAGNOSTIC_TRACKS = (
    BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK,
    RASHI_PRINT_DIAGNOSTIC_TRACK,
)
SEPARATE_REPORT_TRACKS = REAL_EXTENSION_TRACKS + SYNTHETIC_DIAGNOSTIC_TRACKS
LINE_IMAGE_TRACKS = frozenset(
    (
        LINE_TRACK,
        *(track_id for track_id in SEPARATE_REPORT_TRACKS if track_id != HISTORICAL_PRESS_TRACK),
    )
)
DIAGNOSTIC_SPLIT_TRACKS = frozenset((BIDI_TRACK, *SYNTHETIC_DIAGNOSTIC_TRACKS))
SUPPORTED_BASELINE_TRACKS = frozenset((*DEFAULT_HEADLINE_TRACKS, *SEPARATE_REPORT_TRACKS))


class BaselineRunnerError(ValueError):
    """A baseline run cannot be completed without invalidating its evidence."""


@dataclass(frozen=True, slots=True)
class BaselineSettings:
    """Engine settings that participate in every per-page cache key."""

    engine: Engine
    model_version: str | None = None
    timeout_seconds: float = 120.0
    tesseract_executable: str = "tesseract"
    tesseract_language: str = "heb+eng"
    tesseract_page_psm: int = 3
    tesseract_line_psm: int = 7
    tesseract_oracle_pad: int = 14
    apple_languages: tuple[str, ...] = ("he-IL", "en-US")
    apple_recognition_level: str = "accurate"
    apple_language_correction: bool = True
    apple_revision: int | None = None
    apple_executable: str | None = None
    surya_model_path: str | None = None
    surya_mmproj_path: str | None = None
    surya_backend: Literal["cli", "server"] = "cli"
    surya_server_url: str | None = None
    surya_server_parallel: int | None = None
    surya_server_context_size: int | None = None
    surya_executable: str = "llama-cli"
    surya_server_executable: str = "llama-server"
    surya_max_tokens: int = 4096
    surya_image_max_tokens: int = 2048

    def validate(self) -> None:
        if self.engine not in {"tesseract", "apple-vision", "surya2-llamacpp"}:
            raise BaselineRunnerError(f"unsupported baseline engine: {self.engine}")
        if self.timeout_seconds <= 0:
            raise BaselineRunnerError("timeout_seconds must be positive")
        for label, value in (
            ("tesseract_page_psm", self.tesseract_page_psm),
            ("tesseract_line_psm", self.tesseract_line_psm),
        ):
            if value < 0 or value > 13:
                raise BaselineRunnerError(f"{label} must be between 0 and 13")
        if self.tesseract_oracle_pad < 0:
            raise BaselineRunnerError("tesseract_oracle_pad must not be negative")
        if not self.tesseract_language.strip():
            raise BaselineRunnerError("tesseract_language must not be empty")
        if not self.apple_languages:
            raise BaselineRunnerError("apple_languages must not be empty")
        if self.apple_recognition_level not in {"accurate", "fast"}:
            raise BaselineRunnerError("apple_recognition_level must be accurate or fast")
        if self.apple_revision is not None and self.apple_revision <= 0:
            raise BaselineRunnerError("apple_revision must be positive")
        if self.engine == "surya2-llamacpp" and (
            not self.surya_model_path or not self.surya_mmproj_path
        ):
            raise BaselineRunnerError("Surya OCR 2 requires surya_model_path and surya_mmproj_path")
        if self.surya_backend not in {"cli", "server"}:
            raise BaselineRunnerError("surya_backend must be cli or server")
        if self.surya_backend == "server":
            if not self.surya_server_url:
                raise BaselineRunnerError("Surya server backend requires surya_server_url")
            try:
                normalize_surya2_server_url(self.surya_server_url)
            except ValueError as exc:
                raise BaselineRunnerError(str(exc)) from exc
            if self.surya_server_parallel is None or self.surya_server_parallel <= 0:
                raise BaselineRunnerError(
                    "Surya server backend requires a positive surya_server_parallel"
                )
            if self.surya_server_context_size is None or self.surya_server_context_size <= 0:
                raise BaselineRunnerError(
                    "Surya server backend requires a positive surya_server_context_size"
                )
            context_per_slot = self.surya_server_context_size // self.surya_server_parallel
            required_context = self.surya_max_tokens + self.surya_image_max_tokens
            if context_per_slot < required_context:
                raise BaselineRunnerError(
                    "Surya server context per slot is smaller than generation + image token limits"
                )
        elif self.surya_server_url is not None:
            raise BaselineRunnerError("surya_server_url may only be set for server backend")
        elif self.surya_server_parallel is not None or self.surya_server_context_size is not None:
            raise BaselineRunnerError(
                "Surya server parallel/context settings may only be set for server backend"
            )
        if self.surya_max_tokens <= 0 or self.surya_image_max_tokens <= 0:
            raise BaselineRunnerError("Surya OCR 2 token limits must be positive")


@dataclass(frozen=True, slots=True)
class TrackPredictionResult:
    track_id: str
    prediction_path: Path
    selected_pages: int
    source_evaluation_pages: int
    evaluation_split: str
    selection_sha256: str
    cache_hits: int
    cache_misses: int
    failures: int
    latency_ms_mean: float | None
    latency_ms_p50: float | None
    latency_ms_p95: float | None
    model: Mapping[str, Any]

    def to_dict(self) -> dict[str, object]:
        return {
            "track_id": self.track_id,
            "prediction_path": str(self.prediction_path),
            "selected_pages": self.selected_pages,
            "source_evaluation_pages": self.source_evaluation_pages,
            "evaluation_split": self.evaluation_split,
            "selection_sha256": self.selection_sha256,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "failures": self.failures,
            "latency_ms_mean": self.latency_ms_mean,
            "latency_ms_p50": self.latency_ms_p50,
            "latency_ms_p95": self.latency_ms_p95,
            "model": dict(self.model),
        }


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _model_identity(settings: BaselineSettings) -> dict[str, object]:
    if settings.engine == "tesseract":
        version = settings.model_version or tesseract_version(settings.tesseract_executable)
        return {
            "system_id": f"tesseract::{version}",
            "family": "tesseract",
            "name": "Tesseract",
            "version": version,
        }
    if settings.engine == "apple-vision":
        macos = platform.mac_ver()[0] or "non-macos"
        requested_revision = settings.apple_revision or "default"
        version = settings.model_version or f"Vision-macOS-{macos}-revision-{requested_revision}"
        return {
            "system_id": f"apple-vision::{version}",
            "family": "apple-vision",
            "name": "Apple Vision",
            "version": version,
        }
    model = Path(str(settings.surya_model_path)).resolve()
    projector = Path(str(settings.surya_mmproj_path)).resolve()
    if not model.is_file() or not projector.is_file():
        raise BaselineRunnerError("Surya OCR 2 model or multimodal projector is missing")
    model_sha256 = sha256_file(model)
    mmproj_sha256 = sha256_file(projector)
    version = settings.model_version or f"gguf-sha256-{model_sha256}"
    backend = settings.surya_backend
    engine_executable = (
        settings.surya_server_executable if backend == "server" else settings.surya_executable
    )
    identity = {
        "system_id": f"surya-ocr-2::{model_sha256}::{mmproj_sha256}",
        "family": "surya-ocr-2",
        "name": "Surya OCR 2",
        "version": version,
        "artifacts": {
            "model_sha256": model_sha256,
            "mmproj_sha256": mmproj_sha256,
        },
        "engine": "llama.cpp",
        "engine_version": llama_cpp_version(engine_executable),
        "inference_backend": backend,
    }
    if backend == "server":
        assert settings.surya_server_url is not None
        identity["server_url"] = normalize_surya2_server_url(settings.surya_server_url)
        identity["server_parallel"] = settings.surya_server_parallel
        identity["server_context_size"] = settings.surya_server_context_size
        assert settings.surya_server_parallel is not None
        assert settings.surya_server_context_size is not None
        identity["server_context_per_slot"] = (
            settings.surya_server_context_size // settings.surya_server_parallel
        )
    return identity


def _safe_image_path(root: Path, relative_value: object) -> Path:
    value = str(relative_value or "")
    if not value:
        raise BaselineRunnerError("gold image.path is missing")
    relative = Path(value)
    if relative.is_absolute():
        raise BaselineRunnerError("gold image.path must be relative")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise BaselineRunnerError("gold image.path escapes the dataset root")
    if not resolved.is_file():
        raise BaselineRunnerError(f"gold image is missing: {relative}")
    return resolved


def _evaluation_records(
    track_id: str, records: Sequence[Mapping[str, Any]]
) -> tuple[str, list[Mapping[str, Any]]]:
    split = "test"
    selected = [record for record in records if str(record.get("split", "")) == split]
    if not selected and track_id in DIAGNOSTIC_SPLIT_TRACKS:
        # Only these explicitly enumerated diagnostic tracks may use this
        # evaluation-only partition.  This is never a general fallback and it
        # can never admit train/dev records.
        split = "diagnostic"
        selected = [record for record in records if str(record.get("split", "")) == split]
    selected.sort(key=lambda item: str(item.get("page_id", "")))
    if not selected:
        raise BaselineRunnerError(
            f"track has no held-out evaluation records (required split=test): {track_id}"
        )
    ids = [str(record.get("page_id", "")) for record in selected]
    if any(not page_id for page_id in ids):
        raise BaselineRunnerError("test record is missing page_id")
    if len(set(ids)) != len(ids):
        raise BaselineRunnerError("test split contains duplicate page_id values")
    return split, selected


def _limited_evaluation_records(
    track_id: str,
    records: Sequence[Mapping[str, Any]],
    max_pages: int | None,
) -> list[Mapping[str, Any]]:
    if max_pages is None:
        return list(records)
    if track_id != "modern-robustness-v1":
        return list(records[:max_pages])
    # Robustness is defined over one clean control plus every degradation of
    # that same parent.  A smoke limit therefore counts parent pages, never
    # individual variants; slicing variants would make the metric undefined.
    selected_parents: list[str] = []
    for record in records:
        metadata = record.get("metadata")
        parent_id = str(metadata.get("parent_page_id", "")) if isinstance(metadata, Mapping) else ""
        if not parent_id:
            raise BaselineRunnerError("robustness record is missing metadata.parent_page_id")
        if parent_id not in selected_parents:
            selected_parents.append(parent_id)
            if len(selected_parents) == max_pages:
                break
    allowed = set(selected_parents)
    return [
        record
        for record in records
        if isinstance(record.get("metadata"), Mapping)
        and str(record["metadata"].get("parent_page_id", "")) in allowed
    ]


def _selection_hash(records: Sequence[Mapping[str, Any]]) -> str:
    return _canonical_hash(
        [
            {
                "page_id": str(record["page_id"]),
                "image_sha256": str(
                    record.get("image", {}).get("sha256", "")
                    if isinstance(record.get("image"), Mapping)
                    else ""
                ),
            }
            for record in records
        ]
    )


def _envelope(record: Mapping[str, Any]) -> dict[str, object]:
    image = record.get("image")
    if not isinstance(image, Mapping):
        raise BaselineRunnerError(f"page {record.get('page_id')} is missing image metadata")
    path = image.get("path")
    if not isinstance(path, str) or not path:
        raise BaselineRunnerError(f"page {record.get('page_id')} is missing image.path")
    # Keep this exact allowlist.  Adapter tests rely on the fact that no gold
    # transcription, geometry, IDs, table cells or reading order crosses it.
    return {"page_id": str(record["page_id"]), "image": {"path": path}}


def _project_polygon(value: object, *, label: str) -> list[list[float | int]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise BaselineRunnerError(f"{label} is not a polygon")
    projected: list[list[float | int]] = []
    for point in value:
        if (
            not isinstance(point, Sequence)
            or isinstance(point, (str, bytes))
            or len(point) < 2
            or not isinstance(point[0], (int, float))
            or not isinstance(point[1], (int, float))
        ):
            raise BaselineRunnerError(f"{label} contains an invalid point")
        projected.append([point[0], point[1]])
    return projected


def _layout_projection(record: Mapping[str, Any]) -> list[dict[str, object]]:
    """Project oracle layout without ever copying transcription or semantic labels."""

    projected_regions: list[dict[str, object]] = []
    regions = record.get("regions")
    if not isinstance(regions, Sequence) or isinstance(regions, (str, bytes)):
        raise BaselineRunnerError(f"page {record.get('page_id')} has no region layout")
    for region_index, raw_region in enumerate(regions):
        if not isinstance(raw_region, Mapping):
            raise BaselineRunnerError(
                f"page {record.get('page_id')} region {region_index} is not an object"
            )
        region_id = str(raw_region.get("region_id", ""))
        if not region_id:
            raise BaselineRunnerError(
                f"page {record.get('page_id')} region {region_index} lacks ID or polygon"
            )
        projected_region: dict[str, object] = {
            "region_id": region_id,
            "polygon": _project_polygon(
                raw_region.get("polygon"), label=f"region {region_id} polygon"
            ),
            "lines": [],
        }
        if "reading_index" in raw_region:
            projected_region["reading_index"] = raw_region.get("reading_index")
        raw_lines = raw_region.get("lines")
        if not isinstance(raw_lines, Sequence) or isinstance(raw_lines, (str, bytes)):
            raise BaselineRunnerError(
                f"page {record.get('page_id')} region {region_id} has no line layout"
            )
        projected_lines: list[dict[str, object]] = []
        for line_index, raw_line in enumerate(raw_lines):
            if not isinstance(raw_line, Mapping):
                raise BaselineRunnerError(
                    f"page {record.get('page_id')} line {line_index} is not an object"
                )
            line_id = str(raw_line.get("line_id", ""))
            if not line_id:
                raise BaselineRunnerError(
                    f"page {record.get('page_id')} line {line_index} lacks ID or polygon"
                )
            projected_line: dict[str, object] = {
                "line_id": line_id,
                "polygon": _project_polygon(
                    raw_line.get("polygon"), label=f"line {line_id} polygon"
                ),
            }
            if "reading_index" in raw_line:
                projected_line["reading_index"] = raw_line.get("reading_index")
            projected_lines.append(projected_line)
        projected_region["lines"] = projected_lines
        projected_regions.append(projected_region)
    return projected_regions


def _oracle_layout_envelope(record: Mapping[str, Any]) -> dict[str, object]:
    """Return the historical-press model envelope under a strict field allowlist."""

    envelope = _envelope(record)
    envelope["regions"] = _layout_projection(record)
    return envelope


def _uses_oracle_layout(track_id: str, engine: Engine) -> bool:
    return track_id == HISTORICAL_PRESS_TRACK and engine == "tesseract"


def _input_mode(track_id: str, engine: Engine) -> str:
    if _uses_oracle_layout(track_id, engine):
        return "oracle_layout_line_crops"
    if track_id in LINE_IMAGE_TRACKS:
        return "blind_whole_line_image"
    return "blind_full_page_image"


def _model_envelope(
    track_id: str,
    record: Mapping[str, Any],
    engine: Engine,
) -> dict[str, object]:
    if _uses_oracle_layout(track_id, engine):
        return _oracle_layout_envelope(record)
    return _envelope(record)


def _direction(text: str) -> str:
    for character in text:
        value = unicodedata.bidirectional(character)
        if value in {"R", "AL"}:
            return "rtl"
        if value == "L":
            return "ltr"
    return "auto"


def _line_language(text: str) -> str:
    if any("HEBREW" in unicodedata.name(character, "") for character in text):
        return "he"
    if any("LATIN" in unicodedata.name(character, "") for character in text):
        return "en"
    return "und"


def _failure_prediction(
    page_id: str,
    model: Mapping[str, Any],
    exc: Exception,
    elapsed_ms: float,
    *,
    adapter: str,
    unsupported: bool = False,
) -> Prediction:
    failure: dict[str, object] = {
        "error_type": type(exc).__name__,
        "message": str(exc),
        "category": "unsupported_platform_or_runtime" if unsupported else "engine_failure",
    }
    for field in ("return_code", "stderr", "stdout"):
        value = getattr(exc, field, None)
        if value not in (None, ""):
            failure[field] = value
    return {
        "schema_version": "1.0",
        "page_id": page_id,
        "page_text": "",
        "regions": [],
        "reading_order": {"edges": []},
        "tables": [],
        "form_fields": [],
        "model": {**dict(model), "adapter": adapter, "oracle_layout": False},
        "timing_ms": elapsed_ms,
        "status": "failed",
        "failure": failure,
        "api_failures": 1,
        "adapter_diagnostics": {"input_contract": ["page_id", "image.path"]},
    }


def _apple_failure_is_unsupported(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "only on macos",
            "unsupported",
            "helper not found",
            "xcrun",
            "vision framework",
        )
    )


def _tesseract_line_image(
    envelope: Mapping[str, Any],
    root: Path,
    settings: BaselineSettings,
    model: Mapping[str, Any],
) -> Prediction:
    """Recognize one derived line image without reading its gold polygon or ID."""

    page_id = str(envelope["page_id"])
    started = time.perf_counter()
    try:
        image_meta = envelope.get("image")
        if not isinstance(image_meta, Mapping):
            raise BaselineRunnerError("line envelope is missing image")
        path = _safe_image_path(root, image_meta.get("path"))
        with Image.open(path) as source:
            image = source.convert("RGB")
            width, height = image.size
            text = recognize_with_tesseract(
                image,
                settings.tesseract_language,
                settings.tesseract_line_psm,
                executable=settings.tesseract_executable,
                timeout_seconds=settings.timeout_seconds,
            ).rstrip("\r\n\f")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        polygon = [[0, 0], [width, 0], [width, height], [0, height]]
        token = hashlib.sha256(page_id.encode("utf-8")).hexdigest()[:16]
        region_id = f"pred-line-region-{token}"
        return {
            "schema_version": "1.0",
            "page_id": page_id,
            "page_text": text,
            "regions": [
                {
                    "region_id": region_id,
                    "type": "text_line",
                    "polygon": polygon,
                    "base_direction": _direction(text),
                    "reading_index": 0,
                    "lines": [
                        {
                            "line_id": f"pred-line-{token}",
                            "polygon": polygon,
                            "text": text,
                            "base_direction": _direction(text),
                            "language": _line_language(text),
                            "reading_index": 0,
                        }
                    ],
                }
            ],
            "reading_order": {"edges": []},
            "tables": [],
            "form_fields": [],
            "model": {
                **dict(model),
                "adapter": "tesseract_line_image",
                "oracle_layout": False,
                "language": settings.tesseract_language,
                "psm": settings.tesseract_line_psm,
            },
            "timing_ms": elapsed_ms,
            "status": "ok",
            "failure": None,
            "api_failures": 0,
            "adapter_diagnostics": {
                "input_contract": ["page_id", "image.path"],
                "whole_line_image": True,
            },
        }
    except Exception as exc:
        return _failure_prediction(
            page_id,
            model,
            exc,
            (time.perf_counter() - started) * 1000.0,
            adapter="tesseract_line_image",
        )


def _crop_oracle_polygon(
    image: Image.Image, polygon: Sequence[Sequence[float]], *, pad: int
) -> Image.Image:
    if not polygon:
        raise BaselineRunnerError("oracle-layout line polygon is empty")
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    left = max(0, math.floor(min(xs)) - pad)
    top = max(0, math.floor(min(ys)) - pad)
    right = min(image.width, math.ceil(max(xs)) + pad)
    bottom = min(image.height, math.ceil(max(ys)) + pad)
    if right <= left or bottom <= top:
        raise BaselineRunnerError("oracle-layout line polygon produces an empty crop")
    return image.crop((left, top, right, bottom))


def _oracle_layout_input_contract() -> list[str]:
    return [
        "page_id",
        "image.path",
        "regions[].region_id",
        "regions[].polygon",
        "regions[].reading_index",
        "regions[].lines[].line_id",
        "regions[].lines[].polygon",
        "regions[].lines[].reading_index",
    ]


def _oracle_prediction_regions(
    layout_regions: Sequence[Mapping[str, Any]],
    texts: Mapping[str, str],
) -> list[dict[str, object]]:
    prediction_regions: list[dict[str, object]] = []
    for region in layout_regions:
        prediction_lines: list[dict[str, object]] = []
        for line in region.get("lines", []):
            if not isinstance(line, Mapping):
                raise BaselineRunnerError("oracle-layout envelope contains a malformed line")
            line_id = str(line["line_id"])
            text = str(texts.get(line_id, ""))
            prediction_line: dict[str, object] = {
                "line_id": line_id,
                "polygon": line["polygon"],
                "text": text,
                "base_direction": _direction(text),
                "language": _line_language(text),
            }
            if "reading_index" in line:
                prediction_line["reading_index"] = line.get("reading_index")
            prediction_lines.append(prediction_line)
        region_text = "\n".join(str(line["text"]) for line in prediction_lines)
        prediction_region: dict[str, object] = {
            "region_id": str(region["region_id"]),
            "type": "text",
            "polygon": region["polygon"],
            "base_direction": _direction(region_text),
            "lines": prediction_lines,
        }
        if "reading_index" in region:
            prediction_region["reading_index"] = region.get("reading_index")
        prediction_regions.append(prediction_region)
    return prediction_regions


def _tesseract_oracle_layout_page(
    envelope: Mapping[str, Any],
    root: Path,
    settings: BaselineSettings,
    model: Mapping[str, Any],
) -> Prediction:
    """Run Tesseract on every supplied line polygon without receiving gold text."""

    page_id = str(envelope["page_id"])
    raw_regions = envelope.get("regions")
    if not isinstance(raw_regions, Sequence) or isinstance(raw_regions, (str, bytes)):
        raise BaselineRunnerError("oracle-layout envelope is missing regions")
    layout_regions = [region for region in raw_regions if isinstance(region, Mapping)]
    started = time.perf_counter()
    texts: dict[str, str] = {}
    try:
        image_meta = envelope.get("image")
        if not isinstance(image_meta, Mapping):
            raise BaselineRunnerError("oracle-layout envelope is missing image")
        path = _safe_image_path(root, image_meta.get("path"))
        with Image.open(path) as source:
            image = source.convert("RGB")
            for region in layout_regions:
                lines = region.get("lines")
                if not isinstance(lines, Sequence) or isinstance(lines, (str, bytes)):
                    raise BaselineRunnerError("oracle-layout region is missing lines")
                for line in lines:
                    if not isinstance(line, Mapping):
                        raise BaselineRunnerError("oracle-layout region has a malformed line")
                    line_id = str(line["line_id"])
                    polygon = line.get("polygon")
                    if not isinstance(polygon, Sequence) or isinstance(polygon, (str, bytes)):
                        raise BaselineRunnerError(f"oracle-layout line {line_id} lacks a polygon")
                    crop = _crop_oracle_polygon(
                        image,
                        polygon,
                        pad=settings.tesseract_oracle_pad,
                    )
                    texts[line_id] = recognize_with_tesseract(
                        crop,
                        settings.tesseract_language,
                        settings.tesseract_line_psm,
                        executable=settings.tesseract_executable,
                        timeout_seconds=settings.timeout_seconds,
                    ).rstrip("\r\n\f")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "schema_version": "1.0",
            "page_id": page_id,
            "regions": _oracle_prediction_regions(layout_regions, texts),
            "reading_order": {"edges": []},
            "tables": [],
            "form_fields": [],
            "model": {
                **dict(model),
                "adapter": "tesseract_oracle_layout_line_crops",
                "oracle_layout": True,
                "input_mode": "oracle_layout_line_crops",
                "language": settings.tesseract_language,
                "psm": settings.tesseract_line_psm,
                "crop_pad_pixels": settings.tesseract_oracle_pad,
            },
            "timing_ms": elapsed_ms,
            "status": "ok",
            "failure": None,
            "api_failures": 0,
            "adapter_diagnostics": {
                "input_contract": _oracle_layout_input_contract(),
                "input_mode": "oracle_layout_line_crops",
                "line_crops": len(texts),
                "gold_text_exposed": False,
            },
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "schema_version": "1.0",
            "page_id": page_id,
            "regions": _oracle_prediction_regions(layout_regions, {}),
            "reading_order": {"edges": []},
            "tables": [],
            "form_fields": [],
            "model": {
                **dict(model),
                "adapter": "tesseract_oracle_layout_line_crops",
                "oracle_layout": True,
                "input_mode": "oracle_layout_line_crops",
                "language": settings.tesseract_language,
                "psm": settings.tesseract_line_psm,
                "crop_pad_pixels": settings.tesseract_oracle_pad,
            },
            "timing_ms": elapsed_ms,
            "status": "failed",
            "failure": {
                "error_type": type(exc).__name__,
                "message": str(exc),
                "category": "engine_failure",
            },
            "api_failures": 1,
            "adapter_diagnostics": {
                "input_contract": _oracle_layout_input_contract(),
                "input_mode": "oracle_layout_line_crops",
                "line_crops_completed_before_failure": len(texts),
                "gold_text_exposed": False,
            },
        }


def _normalize_prediction(
    prediction: Mapping[str, Any],
    *,
    track_id: str,
    page_id: str,
    model: Mapping[str, Any],
    engine: Engine,
) -> Prediction:
    result = dict(prediction)
    if str(result.get("page_id", "")) != page_id:
        raise BaselineRunnerError(
            f"adapter returned page_id={result.get('page_id')!r}, expected {page_id!r}"
        )
    adapter_model = result.get("model")
    merged_model = dict(adapter_model) if isinstance(adapter_model, Mapping) else {}
    merged_model.update(model)
    oracle_layout = _uses_oracle_layout(track_id, engine)
    merged_model["input_mode"] = _input_mode(track_id, engine)
    merged_model["oracle_layout"] = oracle_layout
    merged_model["gold_assistance"] = oracle_layout
    result["model"] = merged_model
    result.setdefault("timing_ms", 0.0)
    result.setdefault("status", "ok")
    result.setdefault("failure", None)
    result.setdefault("api_failures", 1 if result["status"] == "failed" else 0)
    if result["status"] == "failed" and engine == "apple-vision":
        failure = result.get("failure")
        if isinstance(failure, Mapping):
            normalized_failure = dict(failure)
            message = str(normalized_failure.get("message", "")).lower()
            unsupported = any(
                marker in message
                for marker in ("only on macos", "unsupported", "helper not found", "xcrun")
            )
            normalized_failure.setdefault(
                "category",
                "unsupported_platform_or_runtime" if unsupported else "engine_failure",
            )
            result["failure"] = normalized_failure
    validation = validate_prediction_records([result])
    if not validation.is_valid:
        messages = "; ".join(f"{issue.code}: {issue.message}" for issue in validation.errors[:10])
        raise BaselineRunnerError(
            f"adapter emitted an invalid prediction for {page_id}: {messages}"
        )
    return result


def _default_predictor(
    track_id: str,
    envelope: Mapping[str, Any],
    root: Path,
    settings: BaselineSettings,
    model: Mapping[str, Any],
) -> Prediction:
    if settings.engine == "tesseract" and track_id == HISTORICAL_PRESS_TRACK:
        return _tesseract_oracle_layout_page(envelope, root, settings, model)
    if settings.engine == "tesseract" and track_id in LINE_IMAGE_TRACKS:
        return _tesseract_line_image(envelope, root, settings, model)

    page_id = str(envelope["page_id"])
    started = time.perf_counter()
    try:
        if settings.engine == "tesseract":
            predictions = run_tesseract_page_ocr(
                [envelope],
                dataset_root=root,
                executable=settings.tesseract_executable,
                language=settings.tesseract_language,
                psm=settings.tesseract_page_psm,
                timeout_seconds=settings.timeout_seconds,
                model_version=str(model["version"]),
            )
        elif settings.engine == "apple-vision":
            predictions = run_apple_vision_page_ocr(
                [envelope],
                dataset_root=root,
                executable=settings.apple_executable,
                languages=settings.apple_languages,
                recognition_level=settings.apple_recognition_level,
                uses_language_correction=settings.apple_language_correction,
                revision=settings.apple_revision,
                timeout_seconds=settings.timeout_seconds,
                model_version=str(model["version"]),
            )
        else:
            artifacts = model.get("artifacts")
            if not isinstance(artifacts, Mapping):
                raise BaselineRunnerError("Surya OCR 2 model identity has no artifact hashes")
            predictions = run_surya2_page_ocr(
                [envelope],
                dataset_root=root,
                model_path=str(settings.surya_model_path),
                mmproj_path=str(settings.surya_mmproj_path),
                executable=settings.surya_executable,
                backend=settings.surya_backend,
                server_url=settings.surya_server_url,
                max_tokens=settings.surya_max_tokens,
                image_max_tokens=settings.surya_image_max_tokens,
                timeout_seconds=settings.timeout_seconds,
                model_version=str(model["version"]),
                model_sha256=str(artifacts["model_sha256"]),
                mmproj_sha256=str(artifacts["mmproj_sha256"]),
                engine_version=str(model["engine_version"]),
            )
        if len(predictions) != 1:
            raise BaselineRunnerError("adapter must return exactly one prediction per page")
        return dict(predictions[0])
    except Exception as exc:
        unsupported = settings.engine == "apple-vision"
        return _failure_prediction(
            page_id,
            model,
            exc,
            (time.perf_counter() - started) * 1000.0,
            adapter=(
                "apple_vision_page_e2e"
                if settings.engine == "apple-vision"
                else "surya2_llamacpp_server_page_e2e"
                if settings.engine == "surya2-llamacpp" and settings.surya_backend == "server"
                else "surya2_llamacpp_page_e2e"
                if settings.engine == "surya2-llamacpp"
                else "tesseract_page_e2e"
            ),
            unsupported=_apple_failure_is_unsupported(exc) if unsupported else False,
        )


def _cache_key(
    *,
    track_id: str,
    record: Mapping[str, Any],
    root: Path,
    settings: BaselineSettings,
    model: Mapping[str, Any],
) -> str:
    image = record.get("image")
    if not isinstance(image, Mapping):
        raise BaselineRunnerError(f"page {record.get('page_id')} has no image mapping")
    image_path = _safe_image_path(root, image.get("path"))
    actual_sha256 = sha256_file(image_path)
    declared_sha256 = str(image.get("sha256", ""))
    if declared_sha256 and declared_sha256 != actual_sha256:
        raise BaselineRunnerError(
            f"image SHA-256 mismatch for page {record.get('page_id')}: "
            f"expected {declared_sha256}, got {actual_sha256}"
        )
    cache_settings = asdict(settings)
    if settings.engine != "surya2-llamacpp" or settings.surya_backend != "server":
        # Server-only audit fields must not invalidate unrelated Tesseract,
        # Apple Vision, or llama-cli cache entries.
        cache_settings.pop("surya_server_parallel", None)
        cache_settings.pop("surya_server_context_size", None)
    return _canonical_hash(
        {
            "runner_schema_version": RUNNER_SCHEMA_VERSION,
            "track_id": track_id,
            "page_id": str(record["page_id"]),
            "image_path": str(image["path"]),
            "image_sha256": actual_sha256,
            "settings": cache_settings,
            "model": dict(model),
            # Only Tesseract's historical-press mode receives oracle layout.
            # Its cache identity binds exactly the geometry/IDs visible to the
            # model.  Surya's blind full-page cache depends only on the image,
            # settings and hash-bound model identity above.
            "layout_projection": (
                _layout_projection(record)
                if _uses_oracle_layout(track_id, settings.engine)
                else None
            ),
        }
    )


def _read_cache(path: Path, key: str, page_id: str) -> Prediction:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineRunnerError(f"cannot read baseline cache entry {path}: {exc}") from exc
    if not isinstance(value, Mapping) or value.get("cache_key") != key:
        raise BaselineRunnerError(f"baseline cache key mismatch: {path}")
    prediction = value.get("prediction")
    if not isinstance(prediction, Mapping) or str(prediction.get("page_id", "")) != page_id:
        raise BaselineRunnerError(f"baseline cache prediction is malformed: {path}")
    return dict(prediction)


def _write_cache(path: Path, key: str, track_id: str, prediction: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        write_json(
            temporary,
            {
                "cache_schema_version": RUNNER_SCHEMA_VERSION,
                "cache_key": key,
                "track_id": track_id,
                "page_id": prediction["page_id"],
                "prediction": dict(prediction),
            },
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_baseline_track(
    track_id: str,
    dataset_root: str | Path,
    output_path: str | Path,
    *,
    cache_root: str | Path,
    settings: BaselineSettings,
    max_pages: int | None = None,
    retry_failures: bool = False,
    workers: int = 1,
    predictor: Predictor | None = None,
) -> TrackPredictionResult:
    """Run or resume one track and emit a deterministic prediction JSONL.

    ``predictor`` is an injection seam for tests and external adapters.  It is
    passed the same minimal envelope as built-in adapters.
    """

    settings.validate()
    if track_id not in SUPPORTED_BASELINE_TRACKS:
        raise BaselineRunnerError(f"unsupported baseline track: {track_id}")
    if track_id == HISTORICAL_PRESS_TRACK and settings.engine not in {
        "tesseract",
        "surya2-llamacpp",
    }:
        raise BaselineRunnerError(
            "historical Hebrew press requires Tesseract oracle-layout crops or "
            "blind full-page Surya OCR 2"
        )
    if max_pages is not None and max_pages <= 0:
        raise BaselineRunnerError("max_pages must be positive")
    if workers <= 0:
        raise BaselineRunnerError("workers must be positive")

    root = Path(dataset_root).resolve()
    gold_path = root / "gold.jsonl"
    if not gold_path.is_file():
        raise BaselineRunnerError(f"track root has no gold.jsonl: {root}")
    all_records = load_jsonl(gold_path)
    evaluation_split, evaluation_records = _evaluation_records(track_id, all_records)
    selected = _limited_evaluation_records(track_id, evaluation_records, max_pages)
    validation = validate_gold_records(selected, dataset_root=root)
    if not validation.is_valid:
        messages = "; ".join(f"{issue.code}: {issue.message}" for issue in validation.errors[:10])
        raise BaselineRunnerError(f"selected test gold is invalid: {messages}")

    model = _model_identity(settings)
    cache_directory = Path(cache_root) / RUNNER_SCHEMA_VERSION / settings.engine / track_id

    def process_record(record: Mapping[str, Any]) -> tuple[Prediction, bool]:
        page_id = str(record["page_id"])
        key = _cache_key(
            track_id=track_id,
            record=record,
            root=root,
            settings=settings,
            model=model,
        )
        cache_path = cache_directory / f"{key}.json"
        prediction: Prediction | None = None
        if cache_path.is_file():
            cached = _read_cache(cache_path, key, page_id)
            if not (retry_failures and cached.get("status") == "failed"):
                prediction = cached
                return (
                    _normalize_prediction(
                        prediction,
                        track_id=track_id,
                        page_id=page_id,
                        model=model,
                        engine=settings.engine,
                    ),
                    True,
                )
        if prediction is None:
            envelope = _model_envelope(track_id, record, settings.engine)
            if predictor is None:
                raw = _default_predictor(track_id, envelope, root, settings, model)
            else:
                started = time.perf_counter()
                try:
                    raw = predictor(track_id, envelope, root, settings)
                except Exception as exc:
                    raw = _failure_prediction(
                        page_id,
                        model,
                        exc,
                        (time.perf_counter() - started) * 1000.0,
                        adapter="custom_baseline_predictor",
                        unsupported=(
                            _apple_failure_is_unsupported(exc)
                            if settings.engine == "apple-vision"
                            else False
                        ),
                    )
            prediction = _normalize_prediction(
                raw,
                track_id=track_id,
                page_id=page_id,
                model=model,
                engine=settings.engine,
            )
            _write_cache(cache_path, key, track_id, prediction)
        return prediction, False

    if workers == 1:
        completed = [process_record(record) for record in selected]
    else:
        # executor.map yields in input order.  Runtime completion order can
        # therefore never alter predictions.jsonl or its evidence hash.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hebocr-baseline") as pool:
            completed = list(pool.map(process_record, selected))
    predictions = [prediction for prediction, _ in completed]
    cache_hits = sum(was_cached for _, was_cached in completed)
    cache_misses = len(completed) - cache_hits

    prediction_validation = validate_prediction_records(predictions)
    if not prediction_validation.is_valid:
        messages = "; ".join(
            f"{issue.code}: {issue.message}" for issue in prediction_validation.errors[:10]
        )
        raise BaselineRunnerError(f"prediction JSONL would be invalid: {messages}")
    destination = Path(output_path)
    write_jsonl(destination, predictions)
    timings = [
        float(prediction["timing_ms"])
        for prediction in predictions
        if isinstance(prediction.get("timing_ms"), (int, float))
    ]
    return TrackPredictionResult(
        track_id=track_id,
        prediction_path=destination,
        selected_pages=len(selected),
        source_evaluation_pages=len(evaluation_records),
        evaluation_split=evaluation_split,
        selection_sha256=_selection_hash(selected),
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        failures=sum(prediction.get("status") == "failed" for prediction in predictions),
        latency_ms_mean=statistics.mean(timings) if timings else None,
        latency_ms_p50=_quantile(timings, 0.50),
        latency_ms_p95=_quantile(timings, 0.95),
        model=model,
    )


def _coerce_suite(value: str | Path | ModernSuiteSpec) -> ModernSuiteSpec:
    return value if isinstance(value, ModernSuiteSpec) else load_modern_suite_lock(value)


def _modern_input_mode(track_id: str) -> str:
    return _input_mode(track_id, "tesseract")


def _extension_input_mode(track_id: str, engine: Engine) -> str:
    if track_id not in SEPARATE_REPORT_TRACKS:
        raise BaselineRunnerError(f"not a separate-report track: {track_id}")
    return _input_mode(track_id, engine)


def _extension_adapter(track_id: str, settings: BaselineSettings) -> str:
    if _uses_oracle_layout(track_id, settings.engine):
        return "tesseract_oracle_layout_line_crops"
    if settings.engine == "tesseract":
        return "tesseract_line_image"
    if settings.engine == "surya2-llamacpp" and settings.surya_backend == "server":
        return "surya2_llamacpp_server_page_e2e"
    if settings.engine == "surya2-llamacpp":
        return "surya2_llamacpp_page_e2e"
    raise BaselineRunnerError("separate extension baselines support only Tesseract or Surya OCR 2")


def run_modern_baseline_suite(
    track_roots: Mapping[str, str | Path],
    suite_lock: str | Path | ModernSuiteSpec,
    output_root: str | Path,
    *,
    settings: BaselineSettings,
    max_pages: int | None = None,
    retry_failures: bool = False,
    workers: int = 1,
) -> dict[str, object]:
    """Run, evaluate and (when complete) score all five Modern tracks."""

    settings.validate()
    missing = sorted(set(DEFAULT_HEADLINE_TRACKS) - set(track_roots))
    if missing:
        raise BaselineRunnerError("missing track roots: " + ", ".join(missing))
    suite = _coerce_suite(suite_lock)
    output = Path(output_root)
    predictions_root = output / "predictions"
    reports_root = output / "reports"
    cache_root = output / "cache"
    official = max_pages is None
    track_summaries: dict[str, object] = {}

    for track_id in DEFAULT_HEADLINE_TRACKS:
        root = Path(track_roots[track_id]).resolve()
        gold_path = root / "gold.jsonl"
        # This binds the full frozen corpus before test selection.  Reports use
        # the same evidence only for complete runs; limited smoke runs are never
        # eligible for a headline score.
        suite_evidence = suite_evidence_for_track(suite, track_id, gold_path)
        result = run_baseline_track(
            track_id,
            root,
            predictions_root / f"{track_id}.jsonl",
            cache_root=cache_root,
            settings=settings,
            max_pages=max_pages,
            retry_failures=retry_failures,
            workers=workers,
        )
        evaluation_split, gold = _evaluation_records(track_id, load_jsonl(gold_path))
        selected_gold = _limited_evaluation_records(track_id, gold, max_pages)
        predictions = load_jsonl(result.prediction_path)
        spec = load_track(track_id)
        unexpected = sorted(
            {str(page.get("track", "")) for page in selected_gold} - set(spec.accepted_gold_tracks)
        )
        if unexpected:
            raise BaselineRunnerError(
                f"{track_id} gold is outside its contract: {', '.join(unexpected)}"
            )
        run = evaluate_dataset(selected_gold, predictions, config=spec.benchmark_config)
        input_mode = _modern_input_mode(track_id)
        run.configuration.update(
            {
                "official_track_id": spec.track_id,
                "official_track_version": spec.version,
                "official_track_fingerprint": spec.config_fingerprint,
                "evaluation_split": evaluation_split,
                "source_evaluation_pages": result.source_evaluation_pages,
                "evaluated_evaluation_pages": result.selected_pages,
                "evaluation_selection_sha256": result.selection_sha256,
                "baseline_runner_schema_version": RUNNER_SCHEMA_VERSION,
                "baseline_workers": workers,
                "limited_smoke_run": not official,
                "input_mode": input_mode,
                "gold_assistance": False,
                "oracle_layout": False,
            }
        )
        model_manifest = {
            **dict(result.model),
            "runner": "hebocrbench.baseline_runner",
            "runner_schema_version": RUNNER_SCHEMA_VERSION,
            "input_mode": input_mode,
            "gold_assistance": False,
            "oracle_layout": False,
        }
        report_dir = reports_root / track_id
        artifacts = write_evaluation_artifacts(
            run,
            report_dir,
            gold_path=gold_path,
            predictions_path=result.prediction_path,
            model_manifest=model_manifest,
            suite_evidence=suite_evidence if official else None,
        )
        track_summaries[track_id] = {
            **result.to_dict(),
            "report": {key: str(path) for key, path in artifacts.items()},
            "line_gcer": run.metrics["recognition"]["line_gcer"],
            "page_order_gcer": run.metrics["recognition"]["page_order_gcer"],
            "api_failures": run.metrics["operational"]["api_failures"],
        }
    if official:
        score = verify_and_combine_modern_reports(reports_root, track_roots, suite)
    else:
        score = {
            "benchmark": "HebOCRBench Modern Hebrew",
            "score_schema_version": "1.1",
            "status": "smoke_only",
            "headline_score": None,
            "reason": "max_pages was set; limited runs are never rankable",
            "suite_fingerprint": suite.suite_fingerprint,
        }
    write_json(output / "modern-score.json", score)
    summary = {
        "benchmark": "HebOCRBench Modern Hebrew",
        "runner_schema_version": RUNNER_SCHEMA_VERSION,
        "engine": settings.engine,
        "model": _model_identity(settings),
        "settings": asdict(settings),
        "workers": workers,
        "suite_fingerprint": suite.suite_fingerprint,
        "evaluation_splits": {
            track_id: summary["evaluation_split"]
            for track_id, summary in track_summaries.items()
            if isinstance(summary, Mapping)
        },
        "limited_smoke_run": not official,
        "tracks": track_summaries,
        "modern_score": score,
    }
    write_json(output / "baseline-run.json", summary)
    return summary


def run_extension_baseline_suite(
    track_roots: Mapping[str, str | Path],
    output_root: str | Path,
    *,
    settings: BaselineSettings,
    max_pages: int | None = None,
    retry_failures: bool = False,
    workers: int = 1,
) -> dict[str, object]:
    """Run separately reported real extensions and synthetic diagnostics.

    These results are deliberately outside ``run_modern_baseline_suite`` and
    are never passed to the Modern headline score combiner.  Each supplied
    track is evaluated on its own contract and receives its own report bundle.
    """

    settings.validate()
    if settings.engine not in {"tesseract", "surya2-llamacpp"}:
        raise BaselineRunnerError(
            "separate extension baselines support only Tesseract or Surya OCR 2"
        )
    if workers <= 0:
        raise BaselineRunnerError("workers must be positive")
    if not track_roots:
        raise BaselineRunnerError("at least one separate extension track root is required")
    unexpected_roots = sorted(set(track_roots) - set(SEPARATE_REPORT_TRACKS))
    if unexpected_roots:
        raise BaselineRunnerError(
            "track is not a separate extension or synthetic diagnostic: "
            + ", ".join(unexpected_roots)
        )

    output = Path(output_root)
    predictions_root = output / "predictions"
    reports_root = output / "reports"
    cache_root = output / "cache"
    complete_run = max_pages is None
    track_summaries: dict[str, object] = {}

    # The fixed order is part of deterministic artifact generation and is
    # independent of the caller's mapping insertion order.
    for track_id in SEPARATE_REPORT_TRACKS:
        if track_id not in track_roots:
            continue
        root = Path(track_roots[track_id]).resolve()
        gold_path = root / "gold.jsonl"
        result = run_baseline_track(
            track_id,
            root,
            predictions_root / f"{track_id}.jsonl",
            cache_root=cache_root,
            settings=settings,
            max_pages=max_pages,
            retry_failures=retry_failures,
            workers=workers,
        )
        evaluation_split, gold = _evaluation_records(track_id, load_jsonl(gold_path))
        selected_gold = gold[:max_pages] if max_pages is not None else gold
        predictions = load_jsonl(result.prediction_path)
        spec = load_track(track_id)
        unexpected_gold = sorted(
            {str(page.get("track", "")) for page in selected_gold} - set(spec.accepted_gold_tracks)
        )
        if unexpected_gold:
            raise BaselineRunnerError(
                f"{track_id} gold is outside its contract: {', '.join(unexpected_gold)}"
            )
        synthetic_diagnostic = track_id in SYNTHETIC_DIAGNOSTIC_TRACKS
        reporting_class = (
            "synthetic_diagnostic" if synthetic_diagnostic else "separate_real_extension"
        )
        input_mode = _extension_input_mode(track_id, settings.engine)
        oracle_layout = _uses_oracle_layout(track_id, settings.engine)
        adapter = _extension_adapter(track_id, settings)
        run = evaluate_dataset(selected_gold, predictions, config=spec.benchmark_config)
        run.configuration.update(
            {
                "official_track_id": spec.track_id,
                "official_track_version": spec.version,
                "official_track_fingerprint": spec.config_fingerprint,
                "evaluation_split": evaluation_split,
                "source_evaluation_pages": result.source_evaluation_pages,
                "evaluated_evaluation_pages": result.selected_pages,
                "evaluation_selection_sha256": result.selection_sha256,
                "baseline_runner_schema_version": RUNNER_SCHEMA_VERSION,
                "baseline_workers": workers,
                "limited_smoke_run": not complete_run,
                "reporting_class": reporting_class,
                "modern_headline_eligible": False,
                "synthetic_diagnostic": synthetic_diagnostic,
                "input_mode": input_mode,
                "gold_assistance": oracle_layout,
                "oracle_layout": oracle_layout,
            }
        )
        model_manifest = {
            **dict(result.model),
            "runner": "hebocrbench.baseline_runner",
            "runner_schema_version": RUNNER_SCHEMA_VERSION,
            "input_mode": input_mode,
            "gold_assistance": oracle_layout,
            "oracle_layout": oracle_layout,
            "adapter": adapter,
        }
        report_dir = reports_root / track_id
        artifacts = write_evaluation_artifacts(
            run,
            report_dir,
            gold_path=gold_path,
            predictions_path=result.prediction_path,
            model_manifest=model_manifest,
        )
        track_summaries[track_id] = {
            **result.to_dict(),
            "report": {key: str(path) for key, path in artifacts.items()},
            "reporting_class": reporting_class,
            "modern_headline_eligible": False,
            "diagnostic_only": synthetic_diagnostic,
            "input_mode": input_mode,
            "gold_assistance": oracle_layout,
            "oracle_layout": oracle_layout,
            "adapter": adapter,
            "line_gcer": run.metrics["recognition"]["line_gcer"],
            "line_cer": run.metrics["recognition"]["line_cer"],
            "line_wer": run.metrics["recognition"]["line_wer"],
            "line_exact_rate": run.metrics["recognition"]["line_exact_rate"],
            "api_failures": run.metrics["operational"]["api_failures"],
        }

    real_ids = [track_id for track_id in REAL_EXTENSION_TRACKS if track_id in track_summaries]
    diagnostic_ids = [
        track_id for track_id in SYNTHETIC_DIAGNOSTIC_TRACKS if track_id in track_summaries
    ]
    summary = {
        "benchmark": "HebOCRBench separate extensions and diagnostics",
        "runner_schema_version": RUNNER_SCHEMA_VERSION,
        "engine": settings.engine,
        "model": _model_identity(settings),
        "settings": asdict(settings),
        "workers": workers,
        "limited_smoke_run": not complete_run,
        "reporting_policy": {
            "modern_headline_blending": False,
            "combined_score": None,
            "synthetic_diagnostics_rankable": False,
        },
        "groups": {
            "separate_real_extensions": real_ids,
            "synthetic_diagnostics": diagnostic_ids,
        },
        "tracks": track_summaries,
    }
    write_json(output / "separate-baseline-run.json", summary)
    return summary


__all__ = [
    "BaselineRunnerError",
    "BaselineSettings",
    "BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK",
    "DIAGNOSTIC_SPLIT_TRACKS",
    "HISTORICAL_PRESS_TRACK",
    "PINKAS_HANDWRITING_TRACK",
    "RASHI_PRINT_DIAGNOSTIC_TRACK",
    "REAL_EXTENSION_TRACKS",
    "RUNNER_SCHEMA_VERSION",
    "SEPARATE_REPORT_TRACKS",
    "SYNTHETIC_DIAGNOSTIC_TRACKS",
    "TrackPredictionResult",
    "run_baseline_track",
    "run_extension_baseline_suite",
    "run_modern_baseline_suite",
]
