#!/usr/bin/env python3
"""Download one deterministic shard of the frozen Modern-Hebrew selection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

from hebocrbench.selection_lock import (
    documents_for_shard,
    load_selection_lock,
    verify_pdf_bytes,
)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _download(url: str, timeout: int, attempts: int) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "HebOCRBench/1.0"})
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - locked HTTPS URLs
                return response.read()
        except Exception as exc:  # noqa: BLE001 - preserve final network cause
            last = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-root", default="data/locks")
    parser.add_argument("--output", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)

    selection = load_selection_lock(args.lock_root)
    documents = documents_for_shard(
        selection, shard_index=args.shard_index, shard_count=args.shard_count
    )
    output = Path(args.output).resolve()
    (output / "pdfs").mkdir(parents=True, exist_ok=True)
    (output / "manifests").mkdir(exist_ok=True)
    completed = []
    targets = selection["targets"]
    assert isinstance(targets, dict)
    for document in documents:
        document_id = str(document["document_id"])
        pdf_path = output / "pdfs" / f"{document_id}.pdf"
        if pdf_path.exists() and args.resume:
            payload = pdf_path.read_bytes()
            verify_pdf_bytes(document, payload)
        else:
            payload = _download(str(document["pdf_url"]), args.timeout, args.attempts)
            verify_pdf_bytes(document, payload)
            pdf_path.write_bytes(payload)
        manifest = {
            "schema_version": "1.0",
            "document_id": document_id,
            "pdf_path": f"pdfs/{document_id}.pdf",
            "pdf_sha256": document["pdf_sha256"],
            "pages": document["selected_pages"],
            "dpi": int(targets["dpi"]),
            "minimum_text_layer_agreement": float(targets["minimum_agreement"]),
            "metadata": {
                key: document.get(key)
                for key in (
                    "title",
                    "document_type",
                    "template_family",
                    "catalog_record_id",
                    "group_type_id",
                    "group_type_description",
                    "catalog_last_updated",
                    "knesset_number",
                    "pdf_url",
                    "table_pages",
                    "form_pages",
                    "mixed_bidi_pages",
                )
            },
        }
        _write(output / "manifests" / f"{document_id}.json", manifest)
        completed.append(
            {
                "document_id": document_id,
                "pdf_sha256": document["pdf_sha256"],
                "selected_pages": document["selected_pages"],
            }
        )
    evidence = {
        "schema_version": "1.0",
        "selection_sha256": selection["selection_sha256"],
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "document_count": len(completed),
        "selected_page_count": sum(len(item["selected_pages"]) for item in completed),
        "documents": completed,
    }
    _write(output / "SHARD.json", evidence)
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
