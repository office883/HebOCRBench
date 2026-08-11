"""Blind, end-to-end Apple Vision page OCR baseline.

The adapter uses a small, auditable Swift helper around
``VNRecognizeTextRequest``.  Benchmark records are treated only as image
envelopes: the adapter reads ``page_id`` and ``image.path`` and never consumes
gold text, regions, geometry, tables, forms, or reading order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any
import unicodedata


@dataclass(frozen=True, slots=True)
class AppleVisionObservation:
    """One recognized text observation in Vision normalized coordinates."""

    text: str
    confidence: float
    bounding_box: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class AppleVisionPageOutput:
    """Typed response returned by the Swift helper for one page."""

    observations: tuple[AppleVisionObservation, ...]
    image_width: int
    image_height: int
    request_revision: int
    operating_system_version: str
    inference_timing_ms: float
    framework: str = "Vision"


class AppleVisionInvocationError(RuntimeError):
    """A visible helper compilation or per-page inference failure."""

    def __init__(
        self,
        message: str,
        *,
        return_code: int | None = None,
        stderr: str | None = None,
        stdout: str | None = None,
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stderr = stderr
        self.stdout = stdout


VisionRunner = Callable[
    [Path, tuple[str, ...], str, bool, int | None, float],
    AppleVisionPageOutput,
]


def default_helper_source() -> Path:
    """Return the repository Swift helper used by source checkouts."""

    return Path(__file__).resolve().parents[3] / "scripts" / "apple_vision_ocr.swift"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compile_apple_vision_helper(
    *,
    source_path: str | Path | None = None,
    output_path: str | Path | None = None,
    xcrun_executable: str = "xcrun",
    timeout_seconds: float = 180.0,
) -> Path:
    """Compile the auditable Swift helper, caching it by source SHA-256."""

    if sys.platform != "darwin":
        raise AppleVisionInvocationError("Apple Vision is available only on macOS")
    source = Path(source_path) if source_path is not None else default_helper_source()
    if not source.is_file():
        raise AppleVisionInvocationError(f"Apple Vision Swift helper not found: {source}")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    source_digest = _sha256(source)
    if output_path is None:
        output = (
            Path(tempfile.gettempdir())
            / "hebocrbench-apple-vision"
            / f"apple-vision-ocr-{source_digest[:16]}"
        )
    else:
        output = Path(output_path)
    if output.is_file():
        return output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    module_cache = output.parent / "swift-module-cache"
    module_cache.mkdir(parents=True, exist_ok=True)

    temporary = output.with_name(f".{output.name}.{source_digest[:8]}.tmp")
    command = [
        xcrun_executable,
        "swiftc",
        "-O",
        "-module-cache-path",
        str(module_cache),
        "-framework",
        "Vision",
        "-framework",
        "ImageIO",
        "-framework",
        "CoreGraphics",
        str(source),
        "-o",
        str(temporary),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise AppleVisionInvocationError(
            f"Swift compiler launcher not found: {xcrun_executable}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AppleVisionInvocationError(
            f"Apple Vision helper compilation timed out after {timeout_seconds}s"
        ) from exc
    except subprocess.SubprocessError as exc:
        raise AppleVisionInvocationError(f"Swift helper compilation failed: {exc}") from exc
    if completed.returncode != 0:
        raise AppleVisionInvocationError(
            f"Swift helper compilation failed with exit {completed.returncode}",
            return_code=completed.returncode,
            stderr=(completed.stderr or "").strip(),
            stdout=(completed.stdout or "").strip(),
        )
    try:
        temporary.chmod(0o755)
        temporary.replace(output)
    except OSError as exc:
        raise AppleVisionInvocationError(f"Could not install compiled Swift helper: {exc}") from exc
    return output.resolve()


def _parse_helper_payload(payload: object) -> AppleVisionPageOutput:
    if not isinstance(payload, Mapping):
        raise AppleVisionInvocationError("Apple Vision helper returned a non-object JSON value")
    try:
        width = int(payload["image_width"])
        height = int(payload["image_height"])
        revision = int(payload["request_revision"])
        os_version = str(payload["operating_system_version"])
        framework = str(payload["framework"])
        inference_timing_ms = float(payload["inference_timing_ms"])
        raw_observations = payload["observations"]
    except (KeyError, TypeError, ValueError) as exc:
        raise AppleVisionInvocationError(
            f"Apple Vision helper response is missing or has invalid fields: {exc}"
        ) from exc
    if width <= 0 or height <= 0 or revision <= 0 or inference_timing_ms < 0:
        raise AppleVisionInvocationError(
            "Apple Vision helper returned invalid dimensions or timing"
        )
    if framework != "Vision":
        raise AppleVisionInvocationError(f"Unexpected Apple OCR framework: {framework}")
    if not isinstance(raw_observations, list):
        raise AppleVisionInvocationError("Apple Vision observations must be an array")

    observations: list[AppleVisionObservation] = []
    for index, item in enumerate(raw_observations):
        if not isinstance(item, Mapping):
            raise AppleVisionInvocationError(f"Observation {index} is not an object")
        box = item.get("bounding_box")
        if not isinstance(box, Mapping):
            raise AppleVisionInvocationError(f"Observation {index} has no bounding_box")
        try:
            bounding_box = (
                float(box["x"]),
                float(box["y"]),
                float(box["width"]),
                float(box["height"]),
            )
            confidence = float(item["confidence"])
            text = str(item["text"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppleVisionInvocationError(
                f"Observation {index} has invalid fields: {exc}"
            ) from exc
        if not all(value == value for value in (*bounding_box, confidence)):
            raise AppleVisionInvocationError(f"Observation {index} contains NaN")
        observations.append(
            AppleVisionObservation(
                text=text,
                confidence=confidence,
                bounding_box=bounding_box,
            )
        )
    return AppleVisionPageOutput(
        observations=tuple(observations),
        image_width=width,
        image_height=height,
        request_revision=revision,
        operating_system_version=os_version,
        inference_timing_ms=inference_timing_ms,
        framework=framework,
    )


def invoke_apple_vision_page(
    image_path: str | Path,
    languages: Sequence[str],
    recognition_level: str,
    uses_language_correction: bool,
    revision: int | None,
    timeout_seconds: float,
    *,
    executable: str | Path,
) -> AppleVisionPageOutput:
    """Run one compiled Apple Vision helper invocation and parse its JSON."""

    command = [
        str(executable),
        "--image",
        str(Path(image_path)),
        "--recognition-level",
        recognition_level,
        "--languages",
        ",".join(languages),
        "--language-correction",
        "true" if uses_language_correction else "false",
    ]
    if revision is not None:
        command.extend(["--revision", str(revision)])
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise AppleVisionInvocationError(f"Apple Vision helper not found: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppleVisionInvocationError(
            f"Apple Vision timed out after {timeout_seconds}s"
        ) from exc
    except subprocess.SubprocessError as exc:
        raise AppleVisionInvocationError(f"Apple Vision helper process failed: {exc}") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise AppleVisionInvocationError(
            f"Apple Vision helper failed with exit {completed.returncode}: {stderr}",
            return_code=completed.returncode,
            stderr=stderr,
            stdout=(completed.stdout or "").strip(),
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AppleVisionInvocationError(
            f"Apple Vision helper returned invalid JSON: {exc}",
            return_code=completed.returncode,
            stderr=(completed.stderr or "").strip(),
            stdout=(completed.stdout or "").strip(),
        ) from exc
    return _parse_helper_payload(payload)


def _base_direction(text: str) -> str:
    for character in text:
        direction = unicodedata.bidirectional(character)
        if direction in {"R", "AL"}:
            return "rtl"
        if direction == "L":
            return "ltr"
    return "auto"


def _language(text: str) -> str:
    if any("HEBREW" in unicodedata.name(character, "") for character in text):
        return "he"
    if any("LATIN" in unicodedata.name(character, "") for character in text):
        return "en"
    return "und"


def _polygon(
    observation: AppleVisionObservation, image_width: int, image_height: int
) -> list[list[float]] | None:
    x, y, width, height = observation.bounding_box
    left = max(0.0, min(float(image_width), x * image_width))
    right = max(0.0, min(float(image_width), (x + width) * image_width))
    top = max(0.0, min(float(image_height), (1.0 - y - height) * image_height))
    bottom = max(0.0, min(float(image_height), (1.0 - y) * image_height))
    if right <= left or bottom <= top:
        return None
    return [
        [round(left, 3), round(top, 3)],
        [round(right, 3), round(top, 3)],
        [round(right, 3), round(bottom, 3)],
        [round(left, 3), round(bottom, 3)],
    ]


def _ordered_observations(
    output: AppleVisionPageOutput,
) -> tuple[list[tuple[AppleVisionObservation, list[list[float]]]], int]:
    """Approximate logical order with top-to-bottom bands and RTL tie breaks.

    Vision already returns each candidate string in logical character order.
    This function never reverses text; it only makes the page-level sequence
    deterministic when observations share a visual row.
    """

    prepared: list[tuple[AppleVisionObservation, list[list[float]]]] = []
    discarded = 0
    for observation in output.observations:
        polygon = _polygon(observation, output.image_width, output.image_height)
        if polygon is None or not observation.text:
            discarded += 1
            continue
        prepared.append((observation, polygon))
    if not prepared:
        return [], discarded

    heights = [polygon[2][1] - polygon[0][1] for _, polygon in prepared]
    tolerance = max(2.0, statistics.median(heights) * 0.35)
    prepared.sort(key=lambda item: (item[1][0][1], item[1][0][0]))
    bands: list[list[tuple[AppleVisionObservation, list[list[float]]]]] = []
    band_centers: list[float] = []
    for item in prepared:
        center = (item[1][0][1] + item[1][2][1]) / 2.0
        if bands and abs(center - band_centers[-1]) <= tolerance:
            bands[-1].append(item)
            band_centers[-1] = sum(
                (candidate[1][0][1] + candidate[1][2][1]) / 2.0 for candidate in bands[-1]
            ) / len(bands[-1])
        else:
            bands.append([item])
            band_centers.append(center)

    ordered: list[tuple[AppleVisionObservation, list[list[float]]]] = []
    for band in bands:
        rtl = sum(_base_direction(item[0].text) == "rtl" for item in band)
        ltr = sum(_base_direction(item[0].text) == "ltr" for item in band)
        if rtl >= ltr and rtl > 0:
            band.sort(key=lambda item: (-item[1][1][0], item[1][0][1]))
        else:
            band.sort(key=lambda item: (item[1][0][0], item[1][0][1]))
        ordered.extend(band)
    return ordered, discarded


def _resolve_image(root: Path, value: object) -> Path:
    if not str(value or ""):
        raise ValueError("Gold envelope is missing image.path")
    relative = Path(str(value))
    if relative.is_absolute():
        raise ValueError("image.path must be relative to dataset_root")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("image.path escapes dataset_root")
    if not resolved.is_file():
        raise FileNotFoundError(f"Page image does not exist: {relative}")
    return resolved


def _failure_payload(exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {"error_type": type(exc).__name__, "message": str(exc)}
    if isinstance(exc, AppleVisionInvocationError):
        if exc.return_code is not None:
            payload["return_code"] = exc.return_code
        if exc.stderr:
            payload["stderr"] = exc.stderr
        if exc.stdout:
            payload["stdout"] = exc.stdout
    return payload


def run_apple_vision_page_ocr(
    gold_envelopes: Sequence[Mapping[str, Any]],
    *,
    dataset_root: str | Path,
    executable: str | Path | None = None,
    languages: Sequence[str] = ("he-IL", "en-US"),
    recognition_level: str = "accurate",
    uses_language_correction: bool = True,
    revision: int | None = None,
    timeout_seconds: float = 120.0,
    runner: VisionRunner | None = None,
    model_version: str | None = None,
) -> list[dict[str, Any]]:
    """Run blind Apple Vision OCR using only ``page_id`` and ``image.path``."""

    selected_languages = tuple(str(item).strip() for item in languages if str(item).strip())
    if not selected_languages:
        raise ValueError("languages must not be empty")
    if recognition_level not in {"accurate", "fast"}:
        raise ValueError("recognition_level must be accurate or fast")
    if revision is not None and revision <= 0:
        raise ValueError("revision must be a positive integer")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    helper_path: Path | None = Path(executable).resolve() if executable is not None else None
    helper_digest: str | None = None
    custom_runner = runner is not None
    if runner is None:
        if helper_path is None:
            helper_path = compile_apple_vision_helper()
        if not helper_path.is_file():
            raise AppleVisionInvocationError(f"Apple Vision helper not found: {helper_path}")
        helper_digest = _sha256(helper_path)

        def default_runner(
            image_path: Path,
            selected: tuple[str, ...],
            level: str,
            correction: bool,
            selected_revision: int | None,
            timeout: float,
        ) -> AppleVisionPageOutput:
            assert helper_path is not None
            return invoke_apple_vision_page(
                image_path,
                selected,
                level,
                correction,
                selected_revision,
                timeout,
                executable=helper_path,
            )

        runner = default_runner

    configured_model = {
        "name": "Apple Vision",
        "family": "apple-vision",
        "version": model_version or ("custom" if custom_runner else "resolved-per-page"),
        "adapter": "apple_vision_page_e2e",
        "framework": "Vision",
        "engine": "VNRecognizeTextRequest",
        "oracle_layout": False,
        "recognition_level": recognition_level,
        "recognition_languages": list(selected_languages),
        "uses_language_correction": uses_language_correction,
        "requested_revision": revision,
        "confidence_scale": "0_to_1",
        "character_order": "logical-as-returned-by-engine",
        "page_order_heuristic": "top-to-bottom-row-bands-rtl-tiebreak-v1",
        "timeout_seconds": timeout_seconds,
    }
    if helper_digest is not None:
        configured_model["helper_sha256"] = helper_digest

    root = Path(dataset_root)
    predictions: list[dict[str, Any]] = []
    for index, envelope in enumerate(gold_envelopes):
        page_id = str(envelope.get("page_id", ""))
        if not page_id:
            raise ValueError(f"Gold envelope at index {index} is missing page_id")
        started = time.perf_counter()
        try:
            image = envelope.get("image")
            if not isinstance(image, Mapping):
                raise ValueError("Gold envelope is missing image")
            image_path = _resolve_image(root, image.get("path"))
            raw = runner(
                image_path,
                selected_languages,
                recognition_level,
                uses_language_correction,
                revision,
                timeout_seconds,
            )
            if not isinstance(raw, AppleVisionPageOutput):
                raise TypeError("runner must return AppleVisionPageOutput")
            ordered, discarded = _ordered_observations(raw)
            regions: list[dict[str, Any]] = []
            page_lines: list[str] = []
            for position, (observation, polygon) in enumerate(ordered):
                region_id = f"pred-av-r{position + 1:04d}"
                line_id = f"{region_id}-l0001"
                direction = _base_direction(observation.text)
                line = {
                    "line_id": line_id,
                    "polygon": polygon,
                    "text": observation.text,
                    "base_direction": direction,
                    "language": _language(observation.text),
                    "reading_index": 0,
                    "confidence": observation.confidence,
                }
                regions.append(
                    {
                        "region_id": region_id,
                        "type": "text",
                        "polygon": polygon,
                        "base_direction": direction,
                        "reading_index": position,
                        "lines": [line],
                    }
                )
                page_lines.append(observation.text)

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            region_ids = [str(region["region_id"]) for region in regions]
            model = dict(configured_model)
            model["request_revision"] = raw.request_revision
            model["operating_system_version"] = raw.operating_system_version
            if model_version is None:
                model["version"] = (
                    f"vision-revision-{raw.request_revision}-macos-{raw.operating_system_version}"
                )
            predictions.append(
                {
                    "schema_version": "1.0",
                    "page_id": page_id,
                    "page_text": "\n".join(page_lines),
                    "regions": regions,
                    "reading_order": {
                        "edges": [
                            [region_ids[item], region_ids[item + 1]]
                            for item in range(len(region_ids) - 1)
                        ]
                    },
                    "tables": [],
                    "form_fields": [],
                    "model": model,
                    "timing_ms": elapsed_ms,
                    "status": "ok",
                    "failure": None,
                    "api_failures": 0,
                    "adapter_diagnostics": {
                        "engine_observations": len(raw.observations),
                        "emitted_regions": len(regions),
                        "discarded_observations": discarded,
                        "image_width": raw.image_width,
                        "image_height": raw.image_height,
                        "inference_timing_ms": raw.inference_timing_ms,
                        "end_to_end_timing_ms": elapsed_ms,
                    },
                }
            )
        except Exception as exc:  # retain every failed page in benchmark artifacts
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            predictions.append(
                {
                    "schema_version": "1.0",
                    "page_id": page_id,
                    "page_text": "",
                    "regions": [],
                    "reading_order": {"edges": []},
                    "tables": [],
                    "form_fields": [],
                    "model": dict(configured_model),
                    "timing_ms": elapsed_ms,
                    "status": "failed",
                    "failure": _failure_payload(exc),
                    "api_failures": 1,
                    "adapter_diagnostics": {
                        "engine_observations": 0,
                        "emitted_regions": 0,
                        "discarded_observations": 0,
                        "inference_timing_ms": None,
                        "end_to_end_timing_ms": elapsed_ms,
                    },
                }
            )
    return predictions


__all__ = [
    "AppleVisionInvocationError",
    "AppleVisionObservation",
    "AppleVisionPageOutput",
    "compile_apple_vision_helper",
    "default_helper_source",
    "invoke_apple_vision_page",
    "run_apple_vision_page_ocr",
]
