"""Frozen Modern-Hebrew public-document selection contracts."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlparse

EXPECTED_SELECTION_SHA256 = "4c0ebc58784d565f225b8a412ffa93146df4c417de479464f1d9f283ecf7af52"


class SelectionLockError(ValueError):
    """A selection lock is absent, corrupt, or internally inconsistent."""


def _parts(root: Path, stem: str) -> list[Path]:
    parts = sorted(root.glob(f"{stem}.part*"))
    if not parts:
        raise SelectionLockError(f"no selection-lock parts found for {stem!r}")
    expected = [f"{stem}.part{index:02d}" for index in range(len(parts))]
    actual = [path.name for path in parts]
    if actual != expected:
        raise SelectionLockError(f"selection-lock parts are not contiguous: {actual}")
    return parts


def load_selection_lock(
    root: str | Path,
    *,
    stem: str = "modern-public-selection-v1.json.gz.b64",
    expected_sha256: str = EXPECTED_SELECTION_SHA256,
) -> dict[str, object]:
    directory = Path(root)
    encoded = "".join(path.read_text(encoding="ascii").strip() for path in _parts(directory, stem))
    try:
        compressed = base64.b64decode(encoded, validate=True)
        raw = gzip.decompress(compressed)
        value = json.loads(raw)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise SelectionLockError(f"cannot decode selection lock: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise SelectionLockError(
            f"selection-lock SHA-256 mismatch: expected {expected_sha256}, got {digest}"
        )
    if not isinstance(value, dict):
        raise SelectionLockError("selection lock must contain a JSON object")
    _validate(value)
    value["selection_sha256"] = digest
    return value


def _positive_pages(value: object, document_id: str) -> list[int]:
    if not isinstance(value, list) or not value:
        raise SelectionLockError(f"{document_id}: selected_pages must be non-empty")
    pages: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise SelectionLockError(f"{document_id}: selected_pages must be positive integers")
        pages.append(item)
    if pages != sorted(set(pages)):
        raise SelectionLockError(f"{document_id}: selected_pages must be sorted and unique")
    return pages


def _validate(value: Mapping[str, object]) -> None:
    if value.get("schema_version") != "1.0":
        raise SelectionLockError("selection schema_version must be '1.0'")
    raw_documents = value.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise SelectionLockError("selection lock requires documents")
    ids: set[str] = set()
    hashes: set[str] = set()
    templates: set[str] = set()
    computed = {"docs": 0, "pages": 0, "tables": 0, "forms": 0, "mixed": 0}
    for raw in raw_documents:
        if not isinstance(raw, Mapping):
            raise SelectionLockError("every selected document must be an object")
        document_id = str(raw.get("document_id", "")).strip()
        if not document_id or document_id in ids:
            raise SelectionLockError(f"duplicate or empty document_id: {document_id!r}")
        ids.add(document_id)
        digest = str(raw.get("pdf_sha256", "")).lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise SelectionLockError(f"{document_id}: invalid pdf_sha256")
        if digest in hashes:
            raise SelectionLockError(f"{document_id}: duplicate PDF bytes")
        hashes.add(digest)
        url = urlparse(str(raw.get("pdf_url", "")))
        if url.scheme != "https" or url.hostname != "fs.knesset.gov.il" or not url.path.endswith(".pdf"):
            raise SelectionLockError(f"{document_id}: non-canonical Knesset PDF URL")
        pages = _positive_pages(raw.get("selected_pages"), document_id)
        page_count = int(raw.get("pdf_page_count", 0) or 0)
        if page_count <= 0 or pages[-1] > page_count:
            raise SelectionLockError(f"{document_id}: selected page exceeds PDF page count")
        template = str(raw.get("template_family", "")).strip()
        if not template:
            raise SelectionLockError(f"{document_id}: template_family is required")
        templates.add(template)
        computed["docs"] += 1
        computed["pages"] += len(pages)
        computed["tables"] += int(raw.get("table_pages", 0) or 0)
        computed["forms"] += int(raw.get("form_pages", 0) or 0)
        computed["mixed"] += int(raw.get("mixed_bidi_pages", 0) or 0)
    computed["template_families"] = len(templates)
    coverage = value.get("coverage")
    if not isinstance(coverage, Mapping):
        raise SelectionLockError("selection lock requires coverage")
    declared = {name: int(coverage.get(name, -1)) for name in computed}
    if declared != computed:
        raise SelectionLockError(f"coverage mismatch: declared={declared}, computed={computed}")
    targets = value.get("targets")
    if not isinstance(targets, Mapping):
        raise SelectionLockError("selection lock requires targets")
    names = {
        "documents": "docs",
        "pages": "pages",
        "template_families": "template_families",
        "table_pages": "tables",
        "form_pages": "forms",
        "mixed_bidi_pages": "mixed",
    }
    for target_name, computed_name in names.items():
        if computed[computed_name] < int(targets.get(target_name, 0)):
            raise SelectionLockError(f"coverage does not meet target {target_name}")


def documents_for_shard(
    selection: Mapping[str, object], *, shard_index: int, shard_count: int
) -> list[dict[str, object]]:
    if shard_count <= 0 or not 0 <= shard_index < shard_count:
        raise SelectionLockError("invalid shard index/count")
    documents = selection.get("documents")
    if not isinstance(documents, Sequence):
        raise SelectionLockError("selection lock has no document sequence")
    chosen = []
    for raw in documents:
        if not isinstance(raw, Mapping):
            continue
        document_id = str(raw["document_id"])
        bucket = int.from_bytes(hashlib.sha256(document_id.encode()).digest()[:8], "big") % shard_count
        if bucket == shard_index:
            chosen.append(dict(raw))
    return sorted(chosen, key=lambda item: str(item["document_id"]))


def verify_pdf_bytes(document: Mapping[str, object], payload: bytes) -> None:
    document_id = str(document.get("document_id", "<unknown>"))
    if not payload.startswith(b"%PDF"):
        raise SelectionLockError(f"{document_id}: downloaded bytes are not a PDF")
    expected_size = int(document.get("pdf_size_bytes", 0) or 0)
    if expected_size and len(payload) != expected_size:
        raise SelectionLockError(
            f"{document_id}: PDF size mismatch: expected {expected_size}, got {len(payload)}"
        )
    expected = str(document.get("pdf_sha256", "")).lower()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise SelectionLockError(
            f"{document_id}: PDF SHA-256 mismatch: expected {expected}, got {actual}"
        )
