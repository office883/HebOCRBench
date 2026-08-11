"""Blind, end-to-end Tesseract page OCR adapter.

Unlike :mod:`hebocrbench.adapters.tesseract`, this adapter never crops or copies
gold regions.  A benchmark record is used only as an envelope containing the
public ``page_id`` and image path.  Tesseract must discover all text and layout
from the page image itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import csv
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any
import unicodedata

from .tesseract import tesseract_version


@dataclass(frozen=True, slots=True)
class TesseractPageOutput:
    """Raw outputs produced by one Tesseract page invocation."""

    text: str
    tsv: str


class TesseractPageInvocationError(RuntimeError):
    """A visible per-page engine failure, including available process evidence."""

    def __init__(
        self,
        message: str,
        *,
        return_code: int | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stderr = stderr


PageRunner = Callable[[Path, str, int, float], TesseractPageOutput]


def invoke_tesseract_page(
    image_path: str | Path,
    language: str,
    psm: int,
    timeout_seconds: float,
    *,
    executable: str = "tesseract",
) -> TesseractPageOutput:
    """Run Tesseract once and require both its plain-text and TSV artifacts."""

    source = Path(image_path)
    with tempfile.TemporaryDirectory(prefix="hebocrbench-tesseract-page-") as temporary:
        output_base = Path(temporary) / "page"
        command = [
            executable,
            str(source),
            str(output_base),
            "-l",
            language,
            "--psm",
            str(psm),
            "-c",
            "preserve_interword_spaces=1",
            "txt",
            "tsv",
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
            raise TesseractPageInvocationError(
                f"Tesseract executable not found: {executable}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TesseractPageInvocationError(
                f"Tesseract timed out after {timeout_seconds}s"
            ) from exc
        except subprocess.SubprocessError as exc:
            raise TesseractPageInvocationError(f"Tesseract process failed: {exc}") from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            raise TesseractPageInvocationError(
                f"Tesseract failed with exit {completed.returncode}: {stderr}",
                return_code=completed.returncode,
                stderr=stderr,
            )

        text_path = Path(f"{output_base}.txt")
        tsv_path = Path(f"{output_base}.tsv")
        missing = [path.name for path in (text_path, tsv_path) if not path.is_file()]
        if missing:
            raise TesseractPageInvocationError(
                f"Tesseract did not create required output: {', '.join(missing)}",
                return_code=completed.returncode,
                stderr=(completed.stderr or "").strip(),
            )
        return TesseractPageOutput(
            text=text_path.read_text(encoding="utf-8", errors="replace"),
            tsv=tsv_path.read_text(encoding="utf-8", errors="replace"),
        )


def _integer(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _confidence(value: object) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _polygon(left: int, top: int, right: int, bottom: int) -> list[list[int]]:
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def _bounds(items: Sequence[Mapping[str, Any]]) -> list[list[int]]:
    return _polygon(
        min(int(item["left"]) for item in items),
        min(int(item["top"]) for item in items),
        max(int(item["right"]) for item in items),
        max(int(item["bottom"]) for item in items),
    )


def _base_direction(text: str) -> str:
    for character in text:
        direction = unicodedata.bidirectional(character)
        if direction in {"R", "AL"}:
            return "rtl"
        if direction == "L":
            return "ltr"
    return "auto"


def _line_language(text: str) -> str:
    if any("HEBREW" in unicodedata.name(character, "") for character in text):
        return "he"
    if any("LATIN" in unicodedata.name(character, "") for character in text):
        return "en"
    return "und"


def parse_tesseract_tsv(tsv: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build predicted regions and lines solely from Tesseract word boxes."""

    reader = csv.DictReader(StringIO(tsv), delimiter="\t")
    line_words: dict[tuple[int, int, int, int], list[dict[str, Any]]] = {}
    parsed_rows = 0
    for row in reader:
        parsed_rows += 1
        if _integer(row.get("level")) != 5:
            continue
        text = str(row.get("text", "")).strip()
        left = _integer(row.get("left"))
        top = _integer(row.get("top"))
        width = _integer(row.get("width"))
        height = _integer(row.get("height"))
        page_number = _integer(row.get("page_num"))
        block_number = _integer(row.get("block_num"))
        paragraph_number = _integer(row.get("par_num"))
        line_number = _integer(row.get("line_num"))
        numeric = (
            left,
            top,
            width,
            height,
            page_number,
            block_number,
            paragraph_number,
            line_number,
        )
        if not text or any(value is None for value in numeric):
            continue
        assert left is not None and top is not None
        assert width is not None and height is not None
        if left < 0 or top < 0 or width <= 0 or height <= 0:
            continue
        assert page_number is not None and block_number is not None
        assert paragraph_number is not None and line_number is not None
        key = (page_number, block_number, paragraph_number, line_number)
        line_words.setdefault(key, []).append(
            {
                "text": text,
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "confidence": _confidence(row.get("conf")),
            }
        )

    block_lines: dict[
        tuple[int, int], list[tuple[tuple[int, int, int, int], list[dict[str, Any]]]]
    ] = {}
    for key, words in line_words.items():
        block_lines.setdefault(key[:2], []).append((key, words))

    regions: list[dict[str, Any]] = []
    recognized_words = 0
    recognized_lines = 0
    for region_index, (_block_key, lines_and_words) in enumerate(block_lines.items()):
        region_id = f"pred-r{region_index + 1:04d}"
        lines: list[dict[str, Any]] = []
        all_words: list[dict[str, Any]] = []
        for line_index, (_line_key, words) in enumerate(lines_and_words):
            line_text = " ".join(str(word["text"]) for word in words)
            confidences = [
                float(word["confidence"]) for word in words if word.get("confidence") is not None
            ]
            line: dict[str, Any] = {
                "line_id": f"{region_id}-l{line_index + 1:04d}",
                "polygon": _bounds(words),
                "text": line_text,
                "base_direction": _base_direction(line_text),
                "language": _line_language(line_text),
                "reading_index": line_index,
            }
            if confidences:
                line["confidence"] = sum(confidences) / len(confidences)
            lines.append(line)
            all_words.extend(words)
            recognized_words += len(words)
            recognized_lines += 1

        region_text = "\n".join(str(line["text"]) for line in lines)
        regions.append(
            {
                "region_id": region_id,
                "type": "text",
                "polygon": _bounds(all_words),
                "base_direction": _base_direction(region_text),
                "reading_index": region_index,
                "lines": lines,
            }
        )

    return regions, {
        "tsv_rows": parsed_rows,
        "recognized_words": recognized_words,
        "recognized_lines": recognized_lines,
        "recognized_regions": len(regions),
    }


def _resolve_image(root: Path, value: object) -> Path:
    relative = Path(str(value or ""))
    if not str(value or ""):
        raise ValueError("Gold envelope is missing image.path")
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
    payload: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
    if isinstance(exc, TesseractPageInvocationError):
        if exc.return_code is not None:
            payload["return_code"] = exc.return_code
        if exc.stderr:
            payload["stderr"] = exc.stderr
    return payload


def run_tesseract_page_ocr(
    gold_envelopes: Sequence[Mapping[str, Any]],
    *,
    dataset_root: str | Path,
    executable: str = "tesseract",
    language: str = "heb+eng",
    psm: int = 3,
    timeout_seconds: float = 120.0,
    runner: PageRunner | None = None,
    model_version: str | None = None,
) -> list[dict[str, Any]]:
    """Run blind page OCR while reading only ``page_id`` and ``image.path``.

    Engine and per-page I/O failures are represented as schema-valid prediction
    records.  Structurally invalid envelopes without a page ID are rejected,
    because no valid prediction can identify such a page.
    """

    if psm < 0 or psm > 13:
        raise ValueError("psm must be between 0 and 13")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if not language.strip():
        raise ValueError("language must not be empty")

    root = Path(dataset_root)
    custom_runner = runner is not None
    if runner is None:

        def default_runner(
            image_path: Path, selected_language: str, selected_psm: int, timeout: float
        ) -> TesseractPageOutput:
            return invoke_tesseract_page(
                image_path,
                selected_language,
                selected_psm,
                timeout,
                executable=executable,
            )

        runner = default_runner

    version = model_version
    if version is None:
        version = "custom" if custom_runner else tesseract_version(executable)
    model = {
        "system_id": f"tesseract::{version}",
        "family": "tesseract",
        "name": "Tesseract",
        "version": version,
        "adapter": "tesseract_page_e2e",
        "oracle_layout": False,
        "executable": executable,
        "language": language,
        "psm": psm,
        "output_formats": ["txt", "tsv"],
        "timeout_seconds": timeout_seconds,
    }

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
            raw = runner(image_path, language, psm, timeout_seconds)
            if not isinstance(raw, TesseractPageOutput):
                raise TypeError("runner must return TesseractPageOutput")
            regions, diagnostics = parse_tesseract_tsv(raw.tsv)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            region_ids = [str(region["region_id"]) for region in regions]
            predictions.append(
                {
                    "schema_version": "1.0",
                    "page_id": page_id,
                    "page_text": raw.text.rstrip("\r\n\f"),
                    "regions": regions,
                    "reading_order": {
                        "edges": [
                            [region_ids[position], region_ids[position + 1]]
                            for position in range(len(region_ids) - 1)
                        ]
                    },
                    "tables": [],
                    "form_fields": [],
                    "model": dict(model),
                    "timing_ms": elapsed_ms,
                    "status": "ok",
                    "failure": None,
                    "api_failures": 0,
                    "adapter_diagnostics": diagnostics,
                }
            )
        except Exception as exc:  # one bad page must remain observable in the run artifact
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
                    "model": dict(model),
                    "timing_ms": elapsed_ms,
                    "status": "failed",
                    "failure": _failure_payload(exc),
                    "api_failures": 1,
                    "adapter_diagnostics": {
                        "tsv_rows": 0,
                        "recognized_words": 0,
                        "recognized_lines": 0,
                        "recognized_regions": 0,
                    },
                }
            )
    return predictions
