"""Tesseract baseline adapter for recognition-only, oracle-layout evaluation.

This adapter intentionally reuses gold polygons and IDs, but never gold text.
It therefore measures recognition in fixed crops; it is not an end-to-end
layout baseline and must be labeled accordingly in any comparison.
"""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from PIL import Image

LanguageMap = {
    "he": "heb",
    "arc": "heb",
    "jrb": "heb",
    "lad": "heb",
}
Recognizer = Callable[[Image.Image, str, int], str]


def tesseract_version(executable: str = "tesseract") -> str:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unavailable"
    return completed.stdout.splitlines()[0].strip() if completed.stdout else "unknown"


def recognize_with_tesseract(
    image: Image.Image,
    language: str,
    psm: int,
    *,
    executable: str = "tesseract",
    timeout_seconds: float = 60.0,
) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    command = [
        executable,
        "stdin",
        "stdout",
        "-l",
        language,
        "--psm",
        str(psm),
        "-c",
        "preserve_interword_spaces=1",
    ]
    try:
        completed = subprocess.run(
            command,
            input=buffer.getvalue(),
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Tesseract executable not found: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Tesseract timed out after {timeout_seconds}s") from exc
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Tesseract failed with exit {completed.returncode}: {message}")
    return completed.stdout.decode("utf-8", errors="replace").rstrip("\r\n\f")


def _crop_polygon(image: Image.Image, polygon: Sequence[Sequence[float]], pad: int) -> Image.Image:
    if not polygon:
        return image.copy()
    xs = [float(point[0]) for point in polygon]
    ys = [float(point[1]) for point in polygon]
    left = max(0, int(min(xs)) - pad)
    top = max(0, int(min(ys)) - pad)
    right = min(image.width, int(max(xs)) + pad)
    bottom = min(image.height, int(max(ys)) + pad)
    if right <= left or bottom <= top:
        raise ValueError("Line polygon has an empty crop")
    return image.crop((left, top, right, bottom))


def run_tesseract_oracle_layout(
    gold_pages: Sequence[Mapping[str, Any]],
    *,
    dataset_root: str | Path,
    executable: str = "tesseract",
    psm: int = 7,
    pad: int = 14,
    recognizer: Recognizer | None = None,
) -> list[dict[str, Any]]:
    """Recognize every gold line crop and return prediction-schema records."""

    root = Path(dataset_root)
    use_default_recognizer = recognizer is None
    if recognizer is None:

        def default_recognizer(image: Image.Image, language: str, mode: int) -> str:
            return recognize_with_tesseract(image, language, mode, executable=executable)

        recognizer = default_recognizer
    predictions: list[dict[str, Any]] = []
    version = tesseract_version(executable) if use_default_recognizer else "custom"
    for page in gold_pages:
        image_meta = page.get("image", {})
        rotation = int(image_meta.get("rotation_degrees", 0))
        if rotation != 0:
            raise ValueError(
                "The oracle-layout Tesseract adapter currently requires rotation_degrees=0"
            )
        image_path = root / str(image_meta.get("path", ""))
        with Image.open(image_path) as source:
            image = source.convert("RGB")
            regions = deepcopy(page.get("regions", []))
            started = time.perf_counter()
            for region in regions:
                for line in region.get("lines", []):
                    crop = _crop_polygon(image, line.get("polygon", []), pad)
                    iso_language = str(line.get("language", "he"))
                    tess_language = LanguageMap.get(iso_language, "heb")
                    line["text"] = recognizer(crop, tess_language, psm)
                    line.pop("uncertain_spans", None)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
        predictions.append(
            {
                "schema_version": "1.0",
                "page_id": str(page["page_id"]),
                "regions": regions,
                "reading_order": deepcopy(page.get("reading_order", {"edges": []})),
                "tables": [],
                "form_fields": [],
                "model": {
                    "name": "Tesseract",
                    "version": version,
                    "adapter": "tesseract_oracle_layout",
                    "oracle_layout": True,
                    "psm": psm,
                },
                "timing_ms": elapsed_ms,
            }
        )
    return predictions
