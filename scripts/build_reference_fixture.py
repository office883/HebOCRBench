#!/usr/bin/env python3
"""Build a tiny certified PAGE+ALTO integration fixture for release QA.

This artifact proves the data lifecycle. It is intentionally *not* benchmark
corpus data and must never be used for model ranking.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Sequence
import zipfile

from PIL import Image
import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hebocrbench.certification import certify_release  # noqa: E402
from hebocrbench.corpus_builder import build_corpus, freeze_corpus  # noqa: E402
from hebocrbench.corpus_registry import load_registry  # noqa: E402


VERSION = "1.0.0"
ZIP_TIMESTAMP = (2026, 7, 23, 0, 0, 0)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_entry(
    *,
    title: str,
    converter: str,
    annotation_glob: str,
    checksum: str,
    size_bytes: int,
    split_name: str,
) -> dict[str, object]:
    return {
        "title": title,
        "version": "1",
        "task": "end_to_end_ocr",
        "track": "integration_fixture",
        "languages": ["he", "en"],
        "script": "Hebr",
        "status": "core",
        "converter": converter,
        "homepage": "https://example.invalid/hebocrbench-fixture",
        "citation": {
            "key": title.lower().replace(" ", "-"),
            "text": "HebOCRBench 1.0 local integration fixture; not benchmark data.",
        },
        "license": {
            "spdx": "CC0-1.0",
            "tier": "open",
            "redistribution": "allowed",
            "requires_acceptance": False,
            "uri": "https://creativecommons.org/publicdomain/zero/1.0/",
        },
        "artifacts": [
            {
                "artifact_id": "snapshot",
                "url": "file:///fixture/source.snapshot",
                "filename": "source.snapshot",
                "archive": "none",
                "size_bytes": size_bytes,
                "checksum": {"algorithm": "sha256", "value": checksum},
            }
        ],
        "discovery": {
            "annotation_globs": [annotation_glob],
            "image_roots": ["."],
            "exclude_globs": [],
            "split_from_path": True,
        },
        "split": {
            "strategy": "upstream",
            "group_fields": ["document_id"],
            "upstream_map": {split_name: split_name},
        },
        "metadata": {
            "script_style": "fixture",
            "era": "fixture",
            "document_type": "integration_fixture",
            "layout_type": "mixed",
            "vocalization": "none",
            "source_type": "generated_fixture",
            "source_collection": "HebOCRBench QA",
        },
    }


def _marker(source_id: str, snapshot: Path) -> None:
    digest = _sha256(snapshot)
    payload = {
        "schema_version": "1.0",
        "source_id": source_id,
        "source_version": "1",
        "verification_status": "verified",
        "license": "CC0-1.0",
        "artifacts": [
            {
                "artifact_id": "snapshot",
                "actual_sha256": digest,
                "size_bytes": snapshot.stat().st_size,
                "registry_checksum": {"algorithm": "sha256", "value": digest},
            }
        ],
    }
    (snapshot.parent / ".hebocrbench-source.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_zip(source_root: Path, destination: Path) -> None:
    prefix = f"HebOCRBench-v{VERSION}-reference-fixture"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
            relative = path.relative_to(source_root).as_posix()
            info = zipfile.ZipInfo(f"{prefix}/{relative}", ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    temporary.replace(destination)


def build_reference_fixture(output_zip: Path) -> Path:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hebocrbench-reference-") as temporary_name:
        temporary = Path(temporary_name)
        page_root = temporary / "sources" / "fixture-page"
        alto_root = temporary / "sources" / "fixture-alto"
        (page_root / "test").mkdir(parents=True)
        (alto_root / "train").mkdir(parents=True)

        shutil.copyfile(
            ROOT / "tests" / "fixtures" / "page" / "sample.xml", page_root / "test" / "sample.xml"
        )
        shutil.copyfile(
            ROOT / "tests" / "fixtures" / "alto" / "sample.xml", alto_root / "train" / "sample.xml"
        )
        Image.new("RGB", (1200, 800), "white").save(page_root / "test" / "page.jpg")
        Image.new("RGB", (1000, 600), "white").save(alto_root / "train" / "alto-page.png")

        page_snapshot = page_root / "source.snapshot"
        alto_snapshot = alto_root / "source.snapshot"
        page_snapshot.write_text("PAGE fixture snapshot v1\n", encoding="utf-8")
        alto_snapshot.write_text("ALTO fixture snapshot v1\n", encoding="utf-8")
        _marker("fixture-page", page_snapshot)
        _marker("fixture-alto", alto_snapshot)

        registry_payload = {
            "schema_version": "1.0",
            "registry_version": "1.0.0-fixture",
            "benchmark": "HebOCRBench",
            "sources": {
                "fixture-page": _source_entry(
                    title="PAGE integration fixture",
                    converter="pagexml",
                    annotation_glob="**/*.xml",
                    checksum=_sha256(page_snapshot),
                    size_bytes=page_snapshot.stat().st_size,
                    split_name="test",
                ),
                "fixture-alto": _source_entry(
                    title="ALTO integration fixture",
                    converter="alto",
                    annotation_glob="**/*.xml",
                    checksum=_sha256(alto_snapshot),
                    size_bytes=alto_snapshot.stat().st_size,
                    split_name="train",
                ),
            },
        }
        registry_path = temporary / "fixture-registry.yaml"
        registry_path.write_text(
            yaml.safe_dump(registry_payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        registry = load_registry(registry_path)
        build_root = temporary / "package" / "build"
        build_corpus(
            registry,
            {"fixture-page": page_root, "fixture-alto": alto_root},
            build_root,
            source_ids={"fixture-page", "fixture-alto"},
            accepted_source_ids=set(),
            benchmark_version=VERSION,
            profile="open-integration-fixture",
        )
        freeze_corpus(build_root)
        report = certify_release(build_root, registry, expected_version=VERSION)
        if not report.certified:
            raise RuntimeError(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

        package_root = temporary / "package"
        shutil.copyfile(registry_path, package_root / "fixture-registry.yaml")
        (package_root / "README.md").write_text(
            "# HebOCRBench 1.0 reference integration fixture\n\n"
            "This archive proves PAGE+ALTO conversion, provenance, split, freeze and "
            "certification. Its two blank rendered pages and fixture annotations are "
            "test data only. They are not a representative Hebrew OCR corpus and must "
            "not be used for model ranking.\n",
            encoding="utf-8",
        )
        _write_zip(package_root, output_zip)
    return output_zip


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    path = build_reference_fixture(args.output.resolve())
    print(json.dumps({"reference_fixture": str(path), "sha256": _sha256(path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
