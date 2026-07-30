from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from hebocrbench.selection_lock import (
    SelectionLockError,
    documents_for_shard,
    load_selection_lock,
    verify_pdf_bytes,
)


def _document(name: str, payload: bytes, template: str) -> dict[str, object]:
    return {
        "document_id": name,
        "pdf_url": f"https://fs.knesset.gov.il/25/{name}.pdf",
        "pdf_sha256": hashlib.sha256(payload).hexdigest(),
        "pdf_size_bytes": len(payload),
        "pdf_page_count": 2,
        "selected_pages": [1, 2],
        "template_family": template,
        "table_pages": 1,
        "form_pages": 0,
        "mixed_bidi_pages": 2,
    }


def _write_lock(root: Path, value: dict[str, object], *, parts: int = 2) -> str:
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    encoded = base64.b64encode(gzip.compress(raw, mtime=0)).decode()
    size = (len(encoded) + parts - 1) // parts
    for index in range(parts):
        (root / f"lock.part{index:02d}").write_text(
            encoded[index * size : (index + 1) * size], encoding="ascii"
        )
    return hashlib.sha256(raw).hexdigest()


def test_loads_and_shards_a_valid_lock(tmp_path: Path) -> None:
    first = _document("doc-a", b"%PDF-a", "template-a")
    second = _document("doc-b", b"%PDF-b", "template-b")
    value = {
        "schema_version": "1.0",
        "targets": {
            "documents": 2,
            "pages": 4,
            "template_families": 2,
            "table_pages": 2,
            "form_pages": 0,
            "mixed_bidi_pages": 4,
        },
        "coverage": {
            "docs": 2,
            "pages": 4,
            "tables": 2,
            "forms": 0,
            "mixed": 4,
            "template_families": 2,
        },
        "documents": [first, second],
    }
    digest = _write_lock(tmp_path, value)
    loaded = load_selection_lock(tmp_path, stem="lock", expected_sha256=digest)
    shards = [documents_for_shard(loaded, shard_index=i, shard_count=3) for i in range(3)]
    assert sorted(item["document_id"] for shard in shards for item in shard) == ["doc-a", "doc-b"]
    verify_pdf_bytes(first, b"%PDF-a")


def test_rejects_tampering_and_missing_parts(tmp_path: Path) -> None:
    document = _document("doc-a", b"%PDF-a", "template-a")
    value = {
        "schema_version": "1.0",
        "targets": {
            "documents": 1,
            "pages": 2,
            "template_families": 1,
            "table_pages": 1,
            "form_pages": 0,
            "mixed_bidi_pages": 2,
        },
        "coverage": {
            "docs": 1,
            "pages": 2,
            "tables": 1,
            "forms": 0,
            "mixed": 2,
            "template_families": 1,
        },
        "documents": [document],
    }
    digest = _write_lock(tmp_path, value, parts=3)
    (tmp_path / "lock.part01").unlink()
    with pytest.raises(SelectionLockError, match="not contiguous"):
        load_selection_lock(tmp_path, stem="lock", expected_sha256=digest)


def test_rejects_wrong_pdf_bytes(tmp_path: Path) -> None:
    document = _document("doc-a", b"%PDF-a", "template-a")
    with pytest.raises(SelectionLockError, match="mismatch"):
        verify_pdf_bytes(document, b"%PDF-b")
