"""Deterministic utilities for the Modern Hebrew public-document source."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from .io import sha256_file, write_json


def rank_page_candidates(
    pages: Sequence[Mapping[str, object]], *, maximum: int
) -> list[int]:
    """Rank usable pages for structural diversity before final verification."""

    if maximum < 0:
        raise ValueError("maximum must be non-negative")
    usable = [page for page in pages if bool(page.get("usable", True))]

    def key(page: Mapping[str, object]) -> tuple[int, int, int, int, int]:
        table_count = int(page.get("table_count", 0) or 0)
        form_signal = int(page.get("form_signal", 0) or 0)
        mixed = int(bool(page.get("mixed_bidi", False)))
        hebrew = int(page.get("hebrew_letters", 0) or 0)
        number = int(page.get("page_number", 0) or 0)
        # Prefer structurally difficult pages, then stronger Hebrew content.
        return (int(table_count > 0), int(form_signal > 0), mixed, table_count + form_signal, hebrew, -number)

    ordered = sorted(usable, key=key, reverse=True)
    return [int(page["page_number"]) for page in ordered[:maximum]]


def template_family_id(
    *,
    catalog_table: str,
    group_type_id: object,
    width: float,
    height: float,
    fonts: Sequence[str],
    header_text: str,
) -> str:
    """Create a stable template-family ID without document-specific numbers."""

    normalized_table = re.sub(r"^KNS_", "", catalog_table).lower()
    normalized_table = re.sub(r"[^a-z0-9]+", "-", normalized_table).strip("-") or "document"
    normalized_header = re.sub(r"\d+", "#", " ".join(header_text.casefold().split()))
    basis = {
        "catalog_table": catalog_table,
        "group_type_id": str(group_type_id),
        "page_size": [round(float(width), 1), round(float(height), 1)],
        "fonts": sorted({str(font).strip() for font in fonts if str(font).strip()}),
        "header_tokens": normalized_header.split()[:20],
    }
    digest = hashlib.sha256(
        json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"knesset-{normalized_table}-{group_type_id}-{digest}"


def _inventory(root: Path) -> dict[str, object]:
    excluded = {".hebocrbench-source.json", "SOURCE_INVENTORY.json"}
    files: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and not item.is_symlink()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    payload = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "files": files,
        "file_count": len(files),
        "size_bytes": sum(int(item["size_bytes"]) for item in files),
        "tree_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def write_source_evidence(
    root: str | Path,
    *,
    source_id: str,
    source_version: str,
    artifact_id: str,
    requested_revision: str,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Write acquisition evidence, inventory and the source verification marker."""

    source_root = Path(root)
    source_root.mkdir(parents=True, exist_ok=True)
    evidence = {
        "schema_version": "1.0",
        "source_id": source_id,
        "source_version": source_version,
        "artifact_id": artifact_id,
        "requested_revision": requested_revision,
        "extra": dict(extra or {}),
    }
    write_json(source_root / "SOURCE_EVIDENCE.json", evidence)
    inventory = _inventory(source_root)
    write_json(source_root / "SOURCE_INVENTORY.json", inventory)
    marker = {
        "schema_version": "1.0",
        "source_id": source_id,
        "source_version": source_version,
        "verification_status": "verified",
        "artifacts": [
            {
                "artifact_id": artifact_id,
                "requested_revision": requested_revision,
                "registry_checksum": None,
                "actual_sha256": inventory["tree_sha256"],
                "size_bytes": inventory["size_bytes"],
            }
        ],
        "tree_sha256": inventory["tree_sha256"],
        "file_count": inventory["file_count"],
        "size_bytes": inventory["size_bytes"],
    }
    write_json(source_root / ".hebocrbench-source.json", marker)
    return {"evidence": evidence, "inventory": inventory, "marker": marker}


__all__ = ["rank_page_candidates", "template_family_id", "write_source_evidence"]
