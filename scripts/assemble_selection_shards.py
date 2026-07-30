#!/usr/bin/env python3
"""Assemble all verified selection shards into one canonical source root."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from hebocrbench.modern_public import write_source_evidence
from hebocrbench.selection_lock import (
    documents_for_shard,
    load_selection_lock,
    verify_pdf_bytes,
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-root", default="data/locks")
    parser.add_argument("--shards", required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    selection = load_selection_lock(args.lock_root)
    shards = Path(args.shards).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        if not args.overwrite:
            raise SystemExit(f"output exists: {output}")
        shutil.rmtree(output)
    (output / "pdfs").mkdir(parents=True)
    (output / "manifests").mkdir()
    seen: set[str] = set()
    page_count = 0
    for index in range(args.shard_count):
        expected = documents_for_shard(
            selection, shard_index=index, shard_count=args.shard_count
        )
        root = shards / f"shard-{index:03d}"
        evidence = _load(root / "SHARD.json")
        if (
            evidence.get("selection_sha256") != selection["selection_sha256"]
            or evidence.get("shard_index") != index
            or evidence.get("shard_count") != args.shard_count
        ):
            raise SystemExit(f"shard {index} identity mismatch")
        expected_ids = [str(item["document_id"]) for item in expected]
        raw_evidence_documents = evidence.get("documents")
        if not isinstance(raw_evidence_documents, list):
            raise SystemExit(f"shard {index} has no document evidence")
        actual_ids = [str(item["document_id"]) for item in raw_evidence_documents]
        if actual_ids != expected_ids:
            raise SystemExit(f"shard {index} document list mismatch")
        for document in expected:
            document_id = str(document["document_id"])
            if document_id in seen:
                raise SystemExit(f"duplicate document: {document_id}")
            seen.add(document_id)
            source_pdf = root / "pdfs" / f"{document_id}.pdf"
            payload = source_pdf.read_bytes()
            verify_pdf_bytes(document, payload)
            (output / "pdfs" / source_pdf.name).write_bytes(payload)
            source_manifest = root / "manifests" / f"{document_id}.json"
            manifest = _load(source_manifest)
            if (
                manifest.get("document_id") != document_id
                or manifest.get("pdf_sha256") != document["pdf_sha256"]
                or manifest.get("pages") != document["selected_pages"]
            ):
                raise SystemExit(f"manifest mismatch: {document_id}")
            shutil.copyfile(source_manifest, output / "manifests" / source_manifest.name)
            page_count += len(document["selected_pages"])
    raw_documents = selection["documents"]
    assert isinstance(raw_documents, list)
    expected_all = {str(item["document_id"]) for item in raw_documents}
    if seen != expected_all:
        raise SystemExit(
            f"assembled set mismatch: missing={sorted(expected_all - seen)}, "
            f"extras={sorted(seen - expected_all)}"
        )
    summary = {
        "schema_version": "1.0",
        "selection_sha256": selection["selection_sha256"],
        "document_count": len(seen),
        "selected_page_count": page_count,
        "coverage": selection["coverage"],
        "targets": selection["targets"],
        "target_reached": True,
    }
    (output / "evidence").mkdir()
    (output / "evidence" / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_source_evidence(
        output,
        source_id="modern-public-documents-v1",
        source_version="2026-07-30-selection-v1",
        artifact_id="knesset-open-data-selection-v1",
        requested_revision=str(selection["selection_sha256"]),
        extra=summary,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
