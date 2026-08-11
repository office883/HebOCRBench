"""Blind, local Surya OCR 2 page adapter backed by llama.cpp.

Surya OCR 2 is a vision-language OCR model, independent of Tesseract.  This
adapter invokes local GGUF weights and their multimodal projector directly;
it never reads gold text or gold geometry.  The exact full-page prompt is the
model's published training-time contract.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import socket
import subprocess
import time
from typing import Any, Literal
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image


FULL_PAGE_PROMPT = (
    "OCR this image to HTML. Each block is a div with data-label and data-bbox "
    "(x0 y0 x1 y1, normalized 0-1000)."
)


@dataclass(frozen=True, slots=True)
class Surya2PageOutput:
    """One raw full-page generation plus the dimensions of its source image."""

    html: str
    image_width: int
    image_height: int


class Surya2InvocationError(RuntimeError):
    """A visible local inference failure with bounded process evidence."""

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


Surya2Runner = Callable[
    [Path, Path, Path, str, int, int, float],
    Surya2PageOutput,
]
Surya2Backend = Literal["cli", "server"]
Surya2HTTPPost = Callable[[str, bytes, float, int], bytes]

_SERVER_RESPONSE_LIMIT_BYTES = 16 * 1024 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _bounded(value: object, limit: int = 4000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[-limit:]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def llama_cpp_version(executable: str = "llama-cli") -> str:
    """Return llama.cpp's stable build line without raising on unavailable hosts."""

    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unavailable"
    combined = "\n".join((completed.stdout or "", completed.stderr or ""))
    for line in combined.splitlines():
        if line.strip().startswith("version:"):
            return line.strip().removeprefix("version:").strip()
    return "unknown"


def _extract_cli_generation(stdout: str, prompt: str) -> str:
    """Isolate one generation from llama-cli's human-oriented stdout."""

    normalized = stdout.replace("\r\n", "\n").replace("\r", "\n")
    marker = f"> {prompt}\n"
    if marker in normalized:
        candidate = normalized.rsplit(marker, 1)[1]
    else:
        html_start = min(
            (index for index in (normalized.find("<div"), normalized.find("<p")) if index >= 0),
            default=-1,
        )
        if html_start < 0:
            raise Surya2InvocationError(
                "llama.cpp completed without a recognizable Surya HTML generation",
                stdout=_bounded(stdout),
            )
        candidate = normalized[html_start:]
    candidate = candidate.split("\n[ Prompt:", 1)[0]
    candidate = candidate.split("\n\nExiting...", 1)[0]
    generation = candidate.strip()
    if not generation:
        raise Surya2InvocationError(
            "llama.cpp returned an empty Surya generation",
            stdout=_bounded(stdout),
        )
    return generation


def invoke_surya2_page(
    image_path: str | Path,
    model_path: str | Path,
    mmproj_path: str | Path,
    prompt: str,
    max_tokens: int,
    image_max_tokens: int,
    timeout_seconds: float,
    *,
    executable: str = "llama-cli",
) -> Surya2PageOutput:
    """Run one deterministic, non-interactive Surya OCR 2 generation."""

    source = Path(image_path)
    model = Path(model_path)
    projector = Path(mmproj_path)
    command = [
        executable,
        "-m",
        str(model),
        "--mmproj",
        str(projector),
        "--image",
        str(source),
        "-p",
        prompt,
        "-n",
        str(max_tokens),
        "--image-max-tokens",
        str(image_max_tokens),
        "--temp",
        "0",
        "--seed",
        "1",
        "--no-display-prompt",
        "--simple-io",
        "--single-turn",
        "--no-warmup",
        "--log-disable",
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
        raise Surya2InvocationError(f"llama.cpp executable not found: {executable}") from exc
    except subprocess.TimeoutExpired as exc:
        raise Surya2InvocationError(
            f"Surya OCR 2 timed out after {timeout_seconds}s",
            stdout=_bounded(exc.stdout),
            stderr=_bounded(exc.stderr),
        ) from exc
    except subprocess.SubprocessError as exc:
        raise Surya2InvocationError(f"llama.cpp process failed: {exc}") from exc
    if completed.returncode != 0:
        raise Surya2InvocationError(
            f"llama.cpp failed with exit {completed.returncode}",
            return_code=completed.returncode,
            stdout=_bounded(completed.stdout),
            stderr=_bounded(completed.stderr),
        )

    generation = _extract_cli_generation(completed.stdout or "", prompt)
    try:
        with Image.open(source) as image:
            width, height = image.size
    except (OSError, ValueError) as exc:
        raise Surya2InvocationError(f"Could not read source image dimensions: {exc}") from exc
    if width <= 0 or height <= 0:
        raise Surya2InvocationError("Source image has invalid dimensions")
    return Surya2PageOutput(generation, width, height)


class _NoRedirect(HTTPRedirectHandler):
    """Prevent a compromised local server from redirecting image bytes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def normalize_surya2_server_url(server_url: str) -> str:
    """Return the exact local chat-completions endpoint, rejecting non-loopback URLs."""

    parsed = urlsplit(server_url)
    if parsed.scheme != "http":
        raise ValueError("Surya server URL must use plain HTTP on loopback")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Surya server URL must not contain credentials")
    if (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        raise ValueError("Surya server URL must use 127.0.0.1, ::1, or localhost")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Surya server URL has an invalid port") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("Surya server URL must include a valid explicit port")
    if parsed.query or parsed.fragment:
        raise ValueError("Surya server URL must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    if path not in {"", "/v1/chat/completions"}:
        raise ValueError("Surya server URL path must be empty or /v1/chat/completions")
    endpoint_path = "/v1/chat/completions"
    return urlunsplit(("http", parsed.netloc, endpoint_path, "", ""))


def _post_local_json(url: str, body: bytes, timeout_seconds: float, limit: int) -> bytes:
    request = Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            declared_length = response.headers.get("Content-Length")
            if declared_length:
                try:
                    declared_size = int(declared_length)
                except ValueError as exc:
                    raise Surya2InvocationError(
                        "llama-server returned an invalid Content-Length"
                    ) from exc
                if declared_size > limit:
                    raise Surya2InvocationError(
                        f"llama-server response exceeds the {limit}-byte safety limit"
                    )
            payload = response.read(limit + 1)
    except HTTPError as exc:
        try:
            evidence = exc.read(4001).decode("utf-8", errors="replace")
        except OSError:
            evidence = ""
        raise Surya2InvocationError(
            f"llama-server HTTP request failed with status {exc.code}",
            return_code=exc.code,
            stderr=_bounded(evidence),
        ) from exc
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise Surya2InvocationError(f"llama-server request failed: {exc}") from exc
    if len(payload) > limit:
        raise Surya2InvocationError(f"llama-server response exceeds the {limit}-byte safety limit")
    return payload


def _server_generation(response: object, expected_model: str | None = None) -> str:
    if not isinstance(response, Mapping):
        raise Surya2InvocationError("llama-server returned a non-object JSON response")
    if expected_model is not None and response.get("model") != expected_model:
        raise Surya2InvocationError(
            "llama-server response model identity does not match the requested artifact hashes"
        )
    choices = response.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
        raise Surya2InvocationError("llama-server response has no completion choices")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise Surya2InvocationError("llama-server returned a malformed completion choice")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise Surya2InvocationError("llama-server completion has no assistant message")
    content = message.get("content")
    if isinstance(content, str):
        generation = content.strip()
    elif isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, Mapping) and part.get("type") in {"text", "output_text"}:
                value = part.get("text")
                if isinstance(value, str):
                    chunks.append(value)
        generation = "".join(chunks).strip()
    else:
        generation = ""
    if not generation:
        raise Surya2InvocationError("llama-server returned an empty Surya generation")
    return generation


def invoke_surya2_server_page(
    image_path: str | Path,
    server_url: str,
    prompt: str,
    max_tokens: int,
    timeout_seconds: float,
    *,
    model_id: str | None = None,
    http_post: Surya2HTTPPost | None = None,
    response_limit_bytes: int = _SERVER_RESPONSE_LIMIT_BYTES,
) -> Surya2PageOutput:
    """Run one deterministic generation through an already-loaded local llama-server."""

    endpoint = normalize_surya2_server_url(server_url)
    if response_limit_bytes <= 0:
        raise ValueError("response_limit_bytes must be positive")
    source = Path(image_path)
    try:
        with Image.open(source) as image:
            width, height = image.size
            image_format = (image.format or "").upper()
    except (OSError, ValueError) as exc:
        raise Surya2InvocationError(f"Could not read source image dimensions: {exc}") from exc
    if width <= 0 or height <= 0:
        raise Surya2InvocationError("Source image has invalid dimensions")
    mime_type = Image.MIME.get(image_format, "application/octet-stream")
    try:
        encoded_image = base64.b64encode(source.read_bytes()).decode("ascii")
    except OSError as exc:
        raise Surya2InvocationError(f"Could not read source image: {exc}") from exc
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded_image}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "seed": 1,
        "stream": False,
    }
    if model_id is not None:
        if not model_id.strip():
            raise ValueError("model_id must not be empty")
        payload["model"] = model_id
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    post = http_post or _post_local_json
    try:
        raw_response = post(endpoint, body, timeout_seconds, response_limit_bytes)
    except Surya2InvocationError:
        raise
    except Exception as exc:
        raise Surya2InvocationError(f"llama-server request failed: {exc}") from exc
    if not isinstance(raw_response, bytes):
        raise Surya2InvocationError("llama-server HTTP transport returned non-byte evidence")
    if len(raw_response) > response_limit_bytes:
        raise Surya2InvocationError(
            f"llama-server response exceeds the {response_limit_bytes}-byte safety limit"
        )
    try:
        decoded = json.loads(raw_response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Surya2InvocationError(
            "llama-server returned invalid JSON",
            stdout=_bounded(raw_response.decode("utf-8", "replace")),
        ) from exc
    return Surya2PageOutput(_server_generation(decoded, model_id), width, height)


@dataclass(frozen=True, slots=True)
class _HTMLCell:
    text: str
    rowspan: int
    colspan: int


@dataclass(frozen=True, slots=True)
class _HTMLTable:
    rows: tuple[tuple[_HTMLCell, ...], ...]
    valid: bool


@dataclass(frozen=True, slots=True)
class _HTMLBlock:
    label: str
    bbox: str
    text: str
    tables: tuple[_HTMLTable, ...] = ()
    table_elements: int = 0


_BREAK_TAGS = frozenset({"br", "p", "pre", "tr", "li", "h1", "h2", "h3", "h4", "h5", "hr"})


def _clean_text(chunks: Sequence[str]) -> str:
    text = "".join(chunks).replace("\u00a0", " ")
    text = re.sub(r"[ \f\v]+", " ", text)
    text = re.sub(r" *\t *", "\t", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"\t+\n", "\n", text)
    return text.strip(" \t\n")


class _SuryaHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_HTMLBlock] = []
        self._label = ""
        self._bbox = ""
        self._chunks: list[str] | None = None
        self._div_depth = 0
        self._tables: list[_HTMLTable] = []
        self._table_elements = 0
        self._table_depth = 0
        self._table_rows: list[list[_HTMLCell]] | None = None
        self._table_row: list[_HTMLCell] | None = None
        self._cell_chunks: list[str] | None = None
        self._cell_tag = ""
        self._cell_rowspan = 1
        self._cell_colspan = 1
        self._table_malformed = False

    def _separator(self, value: str) -> None:
        if self._chunks is not None and self._chunks and self._chunks[-1] != value:
            self._chunks.append(value)

    def _cell_separator(self, value: str) -> None:
        if self._cell_chunks is not None and self._cell_chunks:
            if self._cell_chunks[-1] != value:
                self._cell_chunks.append(value)

    def _captures_semantic_tables(self) -> bool:
        return self._label.strip().lower().replace("-", "_") == "table"

    @staticmethod
    def _positive_span(attributes: Mapping[str, str], name: str) -> int | None:
        raw = attributes.get(name, "1").strip()
        if not re.fullmatch(r"[1-9][0-9]*", raw):
            return None
        value = int(raw)
        return value if value <= 1000 else None

    def _reset_table_capture(self) -> None:
        self._tables = []
        self._table_elements = 0
        self._table_depth = 0
        self._table_rows = None
        self._table_row = None
        self._cell_chunks = None
        self._cell_tag = ""
        self._cell_rowspan = 1
        self._cell_colspan = 1
        self._table_malformed = False

    def _finish_cell(self) -> None:
        if self._cell_chunks is None:
            return
        if self._table_row is None:
            self._table_malformed = True
        else:
            self._table_row.append(
                _HTMLCell(
                    _clean_text(self._cell_chunks),
                    self._cell_rowspan,
                    self._cell_colspan,
                )
            )
        self._cell_chunks = None
        self._cell_tag = ""
        self._cell_rowspan = 1
        self._cell_colspan = 1

    def _finish_row(self) -> None:
        if self._cell_chunks is not None:
            self._table_malformed = True
            self._finish_cell()
        if self._table_row is None:
            self._table_malformed = True
            return
        if not self._table_row:
            self._table_malformed = True
        assert self._table_rows is not None
        self._table_rows.append(self._table_row)
        self._table_row = None

    def _finish_table(self) -> None:
        if self._cell_chunks is not None:
            self._table_malformed = True
            self._finish_cell()
        if self._table_row is not None:
            self._table_malformed = True
            self._finish_row()
        rows = self._table_rows or []
        self._tables.append(
            _HTMLTable(
                tuple(tuple(row) for row in rows),
                bool(rows) and not self._table_malformed,
            )
        )
        self._table_rows = None
        self._table_row = None
        self._cell_chunks = None
        self._cell_tag = ""
        self._table_malformed = False

    def _semantic_starttag(self, tag: str, attributes: Mapping[str, str]) -> None:
        # Surya's real table protocol is explicit: a Table-labelled block with
        # semantic HTML table markup.  Never infer a grid from aligned text.
        if not self._captures_semantic_tables():
            return
        if tag == "table":
            if self._table_depth == 0:
                self._table_elements += 1
                self._table_rows = []
                self._table_row = None
                self._cell_chunks = None
                self._table_malformed = False
            else:
                self._table_malformed = True
            self._table_depth += 1
            return
        if self._table_depth != 1:
            return
        if tag == "tr":
            if self._table_row is not None or self._cell_chunks is not None:
                self._table_malformed = True
                if self._table_row is not None:
                    self._finish_row()
            self._table_row = []
            return
        if tag not in {"td", "th"}:
            return
        if self._table_row is None:
            self._table_malformed = True
            self._table_row = []
        if self._cell_chunks is not None:
            self._table_malformed = True
            self._finish_cell()
        rowspan = self._positive_span(attributes, "rowspan")
        colspan = self._positive_span(attributes, "colspan")
        if rowspan is None or colspan is None:
            self._table_malformed = True
        self._cell_chunks = []
        self._cell_tag = tag
        self._cell_rowspan = rowspan or 1
        self._cell_colspan = colspan or 1

    def _semantic_endtag(self, tag: str) -> None:
        if not self._captures_semantic_tables() or self._table_depth == 0:
            return
        if tag == "table":
            if self._table_depth > 1:
                self._table_depth -= 1
                return
            self._finish_table()
            self._table_depth = 0
            return
        if self._table_depth != 1:
            return
        if tag in {"td", "th"}:
            if self._cell_chunks is None:
                self._table_malformed = True
                return
            if self._cell_tag != tag:
                self._table_malformed = True
            self._finish_cell()
        elif tag == "tr":
            self._finish_row()

    def _append_text(self, value: str) -> None:
        if self._chunks is not None:
            self._chunks.append(value)
        if self._cell_chunks is not None:
            self._cell_chunks.append(value)

    def _finish_block(self) -> None:
        if self._table_depth:
            self._table_malformed = True
            self._finish_table()
        assert self._chunks is not None
        self.blocks.append(
            _HTMLBlock(
                self._label,
                self._bbox,
                _clean_text(self._chunks),
                tuple(self._tables),
                self._table_elements,
            )
        )
        self._label = ""
        self._bbox = ""
        self._chunks = None
        self._reset_table_capture()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if self._chunks is None and lowered == "div":
            if attributes.get("data-bbox") and attributes.get("data-label"):
                self._label = attributes["data-label"]
                self._bbox = attributes["data-bbox"]
                self._chunks = []
                self._div_depth = 1
                self._reset_table_capture()
                return
        elif self._chunks is not None and lowered == "div":
            self._div_depth += 1

        if self._chunks is None:
            return
        self._semantic_starttag(lowered, attributes)
        if lowered in _BREAK_TAGS:
            self._separator("\n")
            self._cell_separator("\n")
        elif lowered in {"td", "th"}:
            self._separator("\t")
        elif lowered == "input" and attributes.get("value"):
            self._append_text(attributes["value"])
        elif lowered == "img" and attributes.get("alt"):
            self._append_text(attributes["alt"])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        self._append_text(data)

    def handle_endtag(self, tag: str) -> None:
        if self._chunks is None:
            return
        lowered = tag.lower()
        self._semantic_endtag(lowered)
        if lowered == "div":
            self._div_depth -= 1
            if self._div_depth == 0:
                self._finish_block()
                return
        if lowered in _BREAK_TAGS:
            self._separator("\n")
            self._cell_separator("\n")
        elif lowered in {"td", "th"}:
            self._separator("\t")

    def finish(self) -> None:
        if self._chunks is not None:
            self._finish_block()


class _PlainHTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _BREAK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _BREAK_TAGS:
            self.chunks.append("\n")


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


def _scaled_polygon(value: str, width: int, height: int) -> list[list[float]] | None:
    parts = value.replace(",", " ").split()
    if len(parts) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(part) for part in parts)
    except ValueError:
        return None
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        return None
    left, right = x0 / 1000 * width, x1 / 1000 * width
    top, bottom = y0 / 1000 * height, y1 / 1000 * height
    return [
        [round(left, 3), round(top, 3)],
        [round(right, 3), round(top, 3)],
        [round(right, 3), round(bottom, 3)],
        [round(left, 3), round(bottom, 3)],
    ]


def _semantic_table_payload(
    source: _HTMLTable,
    *,
    table_id: str,
    region_id: str | None,
    polygon: list[list[float]] | None,
) -> dict[str, Any] | None:
    """Apply the HTML table-placement algorithm without inventing cells or geometry."""

    if not source.valid or not source.rows or len(source.rows) > 1000:
        return None
    occupied: set[tuple[int, int]] = set()
    cells: list[dict[str, Any]] = []
    n_rows = len(source.rows)
    n_cols = 0
    for row_index, row in enumerate(source.rows):
        if not row:
            return None
        col_index = 0
        for cell in row:
            if len(cells) >= 10000:
                return None
            row_end = row_index + cell.rowspan
            if row_end > n_rows:
                return None
            while True:
                col_end = col_index + cell.colspan
                if col_end > 1000:
                    return None
                if all((row_index, column) not in occupied for column in range(col_index, col_end)):
                    break
                col_index += 1
            slots = {
                (row, column)
                for row in range(row_index, row_end)
                for column in range(col_index, col_end)
            }
            if slots & occupied:
                return None
            occupied.update(slots)
            cells.append(
                {
                    "row_start": row_index,
                    "row_end": row_end,
                    "col_start": col_index,
                    "col_end": col_end,
                    "text": cell.text,
                }
            )
            n_cols = max(n_cols, col_end)
            col_index = col_end
    if not cells or n_cols <= 0:
        return None
    payload: dict[str, Any] = {
        "table_id": table_id,
        "region_id": region_id,
        "n_rows": n_rows,
        "n_cols": n_cols,
        "cells": cells,
    }
    if polygon is not None:
        payload["polygon"] = polygon
    return payload


def parse_surya2_html(
    html: str,
    image_width: int,
    image_height: int,
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], dict[str, Any]]:
    """Convert Surya's ordered block HTML into benchmark page structures."""

    parser = _SuryaHTMLParser()
    parser.feed(html)
    parser.close()
    parser.finish()

    raw_blocks = parser.blocks
    protocol_fallback = False
    if not raw_blocks:
        plain = _PlainHTMLTextParser()
        plain.feed(html)
        plain.close()
        text = _clean_text(plain.chunks)
        raw_blocks = [_HTMLBlock("Text", "0 0 1000 1000", text)] if text else []
        protocol_fallback = bool(raw_blocks)

    regions: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    page_parts: list[str] = []
    invalid_boxes = 0
    explicit_table_blocks = 0
    semantic_table_elements = 0
    rejected_semantic_tables = 0
    table_blocks_without_semantic_grid = 0
    for block in raw_blocks:
        if block.text:
            page_parts.append(block.text)
        polygon = _scaled_polygon(block.bbox, image_width, image_height)
        normalized_label = block.label.strip().lower().replace("-", "_") or "text"
        if normalized_label == "table":
            explicit_table_blocks += 1
            semantic_table_elements += block.table_elements
            if block.table_elements == 0:
                table_blocks_without_semantic_grid += 1
        if polygon is None:
            invalid_boxes += 1
            region_id = None
        else:
            position = len(regions)
            region_id = f"pred-surya2-r{position + 1:04d}"
            direction = _base_direction(block.text)
            lines: list[dict[str, Any]] = []
            if block.text:
                lines.append(
                    {
                        "line_id": f"{region_id}-block-text",
                        "polygon": polygon,
                        "text": block.text,
                        "base_direction": direction,
                        "language": _language(block.text),
                        "reading_index": 0,
                        "geometry_level": "block-box-proxy",
                    }
                )
            regions.append(
                {
                    "region_id": region_id,
                    "type": normalized_label,
                    "engine_label": block.label,
                    "polygon": polygon,
                    "base_direction": direction,
                    "reading_index": position,
                    "lines": lines,
                }
            )

        # A single Surya Table block normally contains one semantic table.  If
        # it contains several, their shared block box cannot locate each table,
        # so retain the explicit grids but omit per-table polygons.
        table_polygon = polygon if len(block.tables) == 1 else None
        for source_table in block.tables:
            payload = _semantic_table_payload(
                source_table,
                table_id=f"pred-surya2-table-{len(tables) + 1:04d}",
                region_id=region_id,
                polygon=table_polygon,
            )
            if payload is None:
                rejected_semantic_tables += 1
            else:
                tables.append(payload)

    page_text = "\n".join(page_parts)
    return (
        regions,
        page_text,
        tables,
        {
            "engine_blocks": len(raw_blocks),
            "emitted_regions": len(regions),
            "invalid_block_boxes": invalid_boxes,
            "explicit_table_blocks": explicit_table_blocks,
            "semantic_table_elements": semantic_table_elements,
            "emitted_tables": len(tables),
            "emitted_table_cells": sum(len(table["cells"]) for table in tables),
            "rejected_semantic_tables": rejected_semantic_tables,
            "table_blocks_without_semantic_grid": table_blocks_without_semantic_grid,
            "table_parser_policy": "explicit-table-label-and-semantic-html-grid-v1",
            "protocol_fallback": protocol_fallback,
            "generated_html_characters": len(html),
            "generated_html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        },
    )


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
    if isinstance(exc, Surya2InvocationError):
        if exc.return_code is not None:
            payload["return_code"] = exc.return_code
        if exc.stderr:
            payload["stderr"] = _bounded(exc.stderr)
        if exc.stdout:
            payload["stdout"] = _bounded(exc.stdout)
    return payload


def run_surya2_page_ocr(
    gold_envelopes: Sequence[Mapping[str, Any]],
    *,
    dataset_root: str | Path,
    model_path: str | Path,
    mmproj_path: str | Path,
    executable: str = "llama-cli",
    backend: Surya2Backend = "cli",
    server_url: str | None = None,
    prompt: str = FULL_PAGE_PROMPT,
    max_tokens: int = 4096,
    image_max_tokens: int = 2048,
    timeout_seconds: float = 300.0,
    runner: Surya2Runner | None = None,
    server_http_post: Surya2HTTPPost | None = None,
    model_version: str | None = None,
    model_sha256: str | None = None,
    mmproj_sha256: str | None = None,
    engine_version: str | None = None,
) -> list[dict[str, Any]]:
    """Run local, blind Surya OCR 2 using only ``page_id`` and ``image.path``."""

    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if image_max_tokens <= 0:
        raise ValueError("image_max_tokens must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if backend not in {"cli", "server"}:
        raise ValueError("backend must be cli or server")
    endpoint: str | None = None
    if backend == "server":
        if not server_url:
            raise ValueError("server_url is required for the Surya server backend")
        endpoint = normalize_surya2_server_url(server_url)
    elif server_url is not None:
        raise ValueError("server_url may only be set for the Surya server backend")

    model = Path(model_path).resolve()
    projector = Path(mmproj_path).resolve()
    if not model.is_file():
        raise FileNotFoundError(f"Surya OCR 2 GGUF does not exist: {model}")
    if not projector.is_file():
        raise FileNotFoundError(f"Surya OCR 2 mmproj does not exist: {projector}")
    for label, digest in (
        ("model_sha256", model_sha256),
        ("mmproj_sha256", mmproj_sha256),
    ):
        if digest is not None and (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest.lower())
        ):
            raise ValueError(f"{label} must be a SHA-256 digest")
    model_sha256 = (model_sha256 or _sha256(model)).lower()
    mmproj_sha256 = (mmproj_sha256 or _sha256(projector)).lower()
    system_id = f"surya-ocr-2::{model_sha256}::{mmproj_sha256}"
    custom_runner = runner is not None
    engine_version = engine_version or (
        "custom" if custom_runner else llama_cpp_version(executable)
    )

    if runner is None:

        def default_runner(
            image_path: Path,
            selected_model: Path,
            selected_projector: Path,
            selected_prompt: str,
            selected_max_tokens: int,
            selected_image_max_tokens: int,
            timeout: float,
        ) -> Surya2PageOutput:
            if backend == "server":
                assert endpoint is not None
                return invoke_surya2_server_page(
                    image_path,
                    endpoint,
                    selected_prompt,
                    selected_max_tokens,
                    timeout,
                    model_id=system_id,
                    http_post=server_http_post,
                )
            return invoke_surya2_page(
                image_path,
                selected_model,
                selected_projector,
                selected_prompt,
                selected_max_tokens,
                selected_image_max_tokens,
                timeout,
                executable=executable,
            )

        runner = default_runner

    adapter_name = (
        "surya2_llamacpp_server_page_e2e" if backend == "server" else "surya2_llamacpp_page_e2e"
    )
    configured_model = {
        "system_id": system_id,
        "family": "surya-ocr-2",
        "name": "Surya OCR 2",
        "version": model_version or f"gguf-sha256-{model_sha256}",
        "adapter": adapter_name,
        "oracle_layout": False,
        "gold_assistance": False,
        "input_mode": "blind_full_page_image",
        "engine": "llama.cpp",
        "engine_version": engine_version,
        "inference_backend": backend,
        "model_filename": model.name,
        "model_sha256": model_sha256,
        "mmproj_filename": projector.name,
        "mmproj_sha256": mmproj_sha256,
        "prompt": prompt,
        "temperature": 0,
        "seed": 1,
        "max_tokens": max_tokens,
        "image_max_tokens": image_max_tokens,
        "geometry_level": "block",
        "character_order": "logical-as-generated-by-engine",
        "table_parser": "explicit-table-label-and-semantic-html-grid-v1",
        "timeout_seconds": timeout_seconds,
    }
    if endpoint is not None:
        configured_model["server_url"] = endpoint

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
                model,
                projector,
                prompt,
                max_tokens,
                image_max_tokens,
                timeout_seconds,
            )
            if not isinstance(raw, Surya2PageOutput):
                raise TypeError("runner must return Surya2PageOutput")
            if raw.image_width <= 0 or raw.image_height <= 0:
                raise ValueError("runner returned invalid image dimensions")
            regions, page_text, tables, diagnostics = parse_surya2_html(
                raw.html, raw.image_width, raw.image_height
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            region_ids = [str(region["region_id"]) for region in regions]
            diagnostics.update(
                {
                    "image_width": raw.image_width,
                    "image_height": raw.image_height,
                    "end_to_end_timing_ms": elapsed_ms,
                }
            )
            predictions.append(
                {
                    "schema_version": "1.0",
                    "page_id": page_id,
                    "page_text": page_text,
                    "regions": regions,
                    "reading_order": {
                        "edges": [
                            [region_ids[item], region_ids[item + 1]]
                            for item in range(len(region_ids) - 1)
                        ]
                    },
                    "tables": tables,
                    "form_fields": [],
                    "model": dict(configured_model),
                    "timing_ms": elapsed_ms,
                    "status": "ok",
                    "failure": None,
                    "api_failures": 0,
                    "adapter_diagnostics": diagnostics,
                }
            )
        except Exception as exc:  # retain each failed page in benchmark artifacts
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
                        "engine_blocks": 0,
                        "emitted_regions": 0,
                        "invalid_block_boxes": 0,
                        "explicit_table_blocks": 0,
                        "semantic_table_elements": 0,
                        "emitted_tables": 0,
                        "emitted_table_cells": 0,
                        "rejected_semantic_tables": 0,
                        "table_blocks_without_semantic_grid": 0,
                        "table_parser_policy": ("explicit-table-label-and-semantic-html-grid-v1"),
                        "protocol_fallback": False,
                        "end_to_end_timing_ms": elapsed_ms,
                    },
                }
            )
    return predictions


__all__ = [
    "FULL_PAGE_PROMPT",
    "Surya2InvocationError",
    "Surya2PageOutput",
    "invoke_surya2_page",
    "invoke_surya2_server_page",
    "llama_cpp_version",
    "normalize_surya2_server_url",
    "parse_surya2_html",
    "run_surya2_page_ocr",
]
