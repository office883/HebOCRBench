from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image
import yaml

from hebocrbench.certification import certify_release
from hebocrbench.cli import main
from hebocrbench.corpus_builder import build_corpus, freeze_corpus
from hebocrbench.corpus_registry import load_registry
from hebocrbench.io import load_jsonl, write_jsonl


def _registry(path: Path, artifact: Path, *, research_nc: bool = False) -> Path:
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    payload = {
        "schema_version": "1.0",
        "registry_version": "cert-fixture",
        "benchmark": "HebOCRBench",
        "sources": {
            "core-page": {
                "title": "Certified PAGE fixture",
                "version": "1",
                "task": "end_to_end_ocr",
                "track": "modern_page_ocr",
                "languages": ["he", "en"],
                "script": "Hebr",
                "status": "core",
                "converter": "pagexml",
                "homepage": "https://example.invalid/core",
                "citation": {"key": "core-page", "text": "Certified fixture"},
                "license": {
                    "spdx": "CC-BY-NC-SA-4.0" if research_nc else "CC-BY-4.0",
                    "tier": "research-nc" if research_nc else "open",
                    "redistribution": "conditional" if research_nc else "allowed",
                    "requires_acceptance": research_nc,
                    "uri": (
                        "https://creativecommons.org/licenses/by-nc-sa/4.0/"
                        if research_nc
                        else "https://creativecommons.org/licenses/by/4.0/"
                    ),
                },
                "artifacts": [
                    {
                        "artifact_id": "official",
                        "url": artifact.resolve().as_uri(),
                        "filename": artifact.name,
                        "archive": "none",
                        "checksum": {"algorithm": "sha256", "value": digest},
                        "size_bytes": artifact.stat().st_size,
                    }
                ],
                "discovery": {
                    "annotation_globs": ["**/*.xml"],
                    "image_roots": ["."],
                    "exclude_globs": [],
                    "split_from_path": True,
                },
                "split": {
                    "strategy": "upstream",
                    "group_fields": ["document_id"],
                    "upstream_map": {"test": "test"},
                },
                "metadata": {
                    "script_style": "modern_square_print",
                    "era": "modern",
                    "document_type": "manuscript",
                    "layout_type": "two_column",
                    "vocalization": "none",
                    "source_type": "scan",
                    "source_collection": "Certification fixture",
                },
            }
        },
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _source_tree(tmp_path: Path, artifact: Path, *, verified: bool = True) -> Path:
    root = tmp_path / "source"
    test = root / "test"
    test.mkdir(parents=True)
    shutil.copy("tests/fixtures/page/sample.xml", test / "sample.xml")
    Image.new("RGB", (1200, 800), "white").save(test / "page.jpg")
    if verified:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        marker = {
            "schema_version": "1.0",
            "source_id": "core-page",
            "verification_status": "verified",
            "artifacts": [
                {
                    "artifact_id": "official",
                    "actual_sha256": digest,
                    "size_bytes": artifact.stat().st_size,
                    "registry_checksum": {"algorithm": "sha256", "value": digest},
                }
            ],
        }
        (root / ".hebocrbench-source.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return root


def _build(
    tmp_path: Path, *, verified: bool = True, research_nc: bool = False, profile: str = "open"
):
    artifact = tmp_path / "official.bin"
    artifact.write_bytes(b"official immutable corpus artifact")
    registry = load_registry(
        _registry(tmp_path / "registry.yaml", artifact, research_nc=research_nc)
    )
    source = _source_tree(tmp_path, artifact, verified=verified)
    build = tmp_path / "build"
    build_corpus(
        registry,
        {"core-page": source},
        build,
        source_ids={"core-page"},
        accepted_source_ids={"core-page"} if research_nc else set(),
        benchmark_version="1.0.0",
        profile=profile,
    )
    freeze_corpus(build)
    return build, registry


def _codes(report):
    return {issue.code for issue in report.errors}


def test_certification_writes_marker_only_after_all_gates_pass(tmp_path):
    build, registry = _build(tmp_path)

    report = certify_release(build, registry, expected_version="1.0.0")

    assert report.is_certified
    assert report.errors == []
    marker = json.loads((build / "CERTIFIED.json").read_text(encoding="utf-8"))
    assert json.loads((build / "CERTIFIED").read_text(encoding="utf-8")) == marker
    assert marker["dataset_fingerprint"] == report.dataset_fingerprint
    assert marker["registry_fingerprint"] == registry.fingerprint


def test_certification_rejects_unverified_core_source(tmp_path):
    build, registry = _build(tmp_path, verified=False)

    report = certify_release(build, registry, expected_version="1.0.0")

    assert not report.is_certified
    assert "core_source_unverified" in _codes(report)
    assert not (build / "CERTIFIED.json").exists()


def test_certification_rejects_noncommercial_source_in_open_profile(tmp_path):
    build, registry = _build(tmp_path, research_nc=True, profile="open")

    report = certify_release(build, registry, expected_version="1.0.0")

    assert "profile_license_violation" in _codes(report)


def test_certification_reruns_leakage_audit_and_detects_tampering(tmp_path):
    build, registry = _build(tmp_path)
    records = load_jsonl(build / "gold.jsonl")
    duplicate = json.loads(json.dumps(records[0]))
    duplicate["page_id"] = "cross-split-copy"
    duplicate["split"] = "train"
    records.append(duplicate)
    write_jsonl(build / "gold.jsonl", records)

    report = certify_release(build, registry, expected_version="1.0.0")

    assert "split_leak_document_id" in _codes(report)
    assert "file_hash_mismatch" in _codes(report)


def test_certification_detects_hash_mismatch_and_missing_required_file(tmp_path):
    build, registry = _build(tmp_path)
    (build / "stats.json").write_text("{}\n", encoding="utf-8")
    (build / "licenses" / "core-page.txt").unlink()

    report = certify_release(build, registry, expected_version="1.0.0")

    assert "file_hash_mismatch" in _codes(report)
    assert "required_file_missing" in _codes(report)


def test_release_certify_cli_emits_machine_readable_success(capsys, tmp_path):
    build, registry = _build(tmp_path)
    registry_path = tmp_path / "registry.yaml"

    assert (
        main(
            [
                "release",
                "certify",
                "--build-root",
                str(build),
                "--registry",
                str(registry_path),
                "--expected-version",
                "1.0.0",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["certified"] is True
    assert payload["checks"]["source_provenance"] is True
    assert (
        payload["dataset_fingerprint"]
        == json.loads((build / "CERTIFIED.json").read_text(encoding="utf-8"))["dataset_fingerprint"]
    )


def test_certification_enforces_exact_official_profile_membership(tmp_path):
    from hebocrbench.profiles import load_profiles

    build, registry = _build(tmp_path, profile="fixture-v1")
    profiles_payload = {
        "schema_version": "1.0",
        "profiles_version": "1.0.0",
        "profiles": {
            "fixture-v1": {
                "title": "Fixture official profile",
                "description": "Exact fixture profile for certification tests.",
                "source_ids": ["core-page"],
                "allowed_license_tiers": ["open"],
                "certification_class": "official-open",
                "score_policy": "per-source",
            }
        },
    }
    profiles_path = tmp_path / "profiles.yaml"
    profiles_path.write_text(
        yaml.safe_dump(profiles_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    profiles = load_profiles(profiles_path, registry=registry)

    assert certify_release(
        build, registry, expected_version="1.0.0", profiles=profiles
    ).is_certified

    manifest_path = build / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_ids"] = []
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = certify_release(
        build,
        registry,
        expected_version="1.0.0",
        profiles=profiles,
    )
    assert "profile_source_mismatch" in _codes(report)
