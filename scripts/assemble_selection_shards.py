#!/usr/bin/env python3
"""Assemble all verified selection shards into one canonical source root."""

from __future__ import annotations

import argparse
import hashlib
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


def _apply_quality_replacements(
    output: Path,
    lock_root: Path,
    *,
    selection_sha256: str,
) -> tuple[list[dict[str, object]], str]:
    path = lock_root / "modern-public-quality-replacements-v1.json"
    if not path.is_file():
        return [], selection_sha256
    payload = _load(path)
    if payload.get("schema_version") != "1.0":
        raise SystemExit("quality replacement lock schema mismatch")
    if payload.get("parent_selection_sha256") != selection_sha256:
        raise SystemExit("quality replacement lock belongs to another selection")
    raw_replacements = payload.get("replacements")
    if not isinstance(raw_replacements, list):
        raise SystemExit("quality replacement lock has no replacements array")
    applied: list[dict[str, object]] = []
    for raw in raw_replacements:
        if not isinstance(raw, dict):
            raise SystemExit("quality replacement entry is not an object")
        document_id = str(raw.get("document_id", ""))
        manifest_path = output / "manifests" / f"{document_id}.json"
        manifest = _load(manifest_path)
        if manifest.get("pdf_sha256") != raw.get("pdf_sha256"):
            raise SystemExit(f"quality replacement PDF mismatch: {document_id}")
        pages = manifest.get("pages")
        if not isinstance(pages, list):
            raise SystemExit(f"quality replacement manifest has no pages: {document_id}")
        remove_page = int(raw.get("remove_page", 0))
        add_page = int(raw.get("add_page", 0))
        if remove_page not in pages or add_page in pages or add_page < 1:
            raise SystemExit(f"invalid quality replacement page set: {document_id}")
        manifest["pages"] = sorted(add_page if page == remove_page else int(page) for page in pages)
        manifest["quality_replacement"] = {
            "lock": path.name,
            "remove_page": remove_page,
            "add_page": add_page,
            "rejected_evidence": raw.get("rejected_evidence"),
            "replacement_evidence": raw.get("replacement_evidence"),
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        applied.append(
            {
                "document_id": document_id,
                "remove_page": remove_page,
                "add_page": add_page,
                "pdf_sha256": raw.get("pdf_sha256"),
            }
        )
    effective_basis = {
        "parent_selection_sha256": selection_sha256,
        "quality_replacement_lock_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "replacements": applied,
    }
    effective_sha256 = hashlib.sha256(
        json.dumps(effective_basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return applied, effective_sha256


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
        expected = documents_for_shard(selection, shard_index=index, shard_count=args.shard_count)
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
    replacements, effective_selection_sha256 = _apply_quality_replacements(
        output,
        Path(args.lock_root).resolve(),
        selection_sha256=str(selection["selection_sha256"]),
    )
    summary = {
        "schema_version": "1.0",
        "selection_sha256": selection["selection_sha256"],
        "effective_selection_sha256": effective_selection_sha256,
        "quality_replacements": replacements,
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
        # These values bind the assembled source to the immutable registry
        # artifact.  The selection hash remains preserved in ``extra`` and must
        # not masquerade as a different registry revision.
        source_version="2026-07-26-v1",
        artifact_id="knesset-open-data-api",
        requested_revision="2026-07-26-v1",
        extra=summary,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
