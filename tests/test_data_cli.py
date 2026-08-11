from __future__ import annotations

import json
from pathlib import Path
import shutil

from PIL import Image
import yaml

from hebocrbench.cli import main


def _registry(path: Path, *, restricted: bool = False) -> Path:
    license_value = {
        "spdx": "CC-BY-NC-SA-4.0" if restricted else "CC-BY-4.0",
        "tier": "research-nc" if restricted else "open",
        "redistribution": "conditional" if restricted else "allowed",
        "requires_acceptance": restricted,
        "uri": (
            "https://creativecommons.org/licenses/by-nc-sa/4.0/"
            if restricted
            else "https://creativecommons.org/licenses/by/4.0/"
        ),
    }
    payload = {
        "schema_version": "1.0",
        "registry_version": "cli-fixture",
        "benchmark": "HebOCRBench",
        "sources": {
            "page-source": {
                "title": "CLI PAGE fixture",
                "version": "1",
                "task": "end_to_end_ocr",
                "track": "modern_page_ocr",
                "languages": ["he", "en"],
                "script": "Hebr",
                "status": "core",
                "converter": "pagexml",
                "homepage": "https://example.invalid/page",
                "citation": {"key": "cli-page", "text": "CLI fixture citation"},
                "license": license_value,
                "artifacts": [],
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
                    "document_type": "public_document",
                    "layout_type": "two_column",
                    "vocalization": "none",
                    "source_type": "scan",
                    "source_collection": "CLI fixture",
                },
            }
        },
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _source_root(tmp_path: Path) -> Path:
    root = tmp_path / "source" / "test"
    root.mkdir(parents=True)
    shutil.copy("tests/fixtures/page/sample.xml", root / "sample.xml")
    Image.new("RGB", (1200, 800), "white").save(root / "page.jpg")
    return root.parent


def test_data_list_and_licenses_emit_machine_readable_registry(capsys, tmp_path):
    registry = _registry(tmp_path / "registry.yaml")

    assert main(["data", "list", "--registry", str(registry)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["registry_version"] == "cli-fixture"
    assert payload["sources"][0]["source_id"] == "page-source"
    assert payload["sources"][0]["license_tier"] == "open"

    assert main(["data", "licenses", "--registry", str(registry)]) == 0
    licenses = json.loads(capsys.readouterr().out)
    assert licenses["sources"][0]["spdx"] == "CC-BY-4.0"
    assert licenses["sources"][0]["requires_acceptance"] is False


def test_data_build_stats_audit_and_freeze_work_end_to_end(capsys, tmp_path):
    registry = _registry(tmp_path / "registry.yaml")
    source = _source_root(tmp_path)
    output = tmp_path / "build"

    assert (
        main(
            [
                "data",
                "build",
                "--registry",
                str(registry),
                "--source",
                "page-source",
                "--source-root",
                f"page-source={source}",
                "--output",
                str(output),
                "--profile",
                "cli-fixture",
            ]
        )
        == 0
    )
    build_payload = json.loads(capsys.readouterr().out)
    assert build_payload["page_count"] == 1
    assert len(build_payload["dataset_fingerprint"]) == 64

    stats_out = tmp_path / "stats-copy.json"
    assert (
        main(["data", "stats", "--gold", str(output / "gold.jsonl"), "--output", str(stats_out)])
        == 0
    )
    capsys.readouterr()
    assert json.loads(stats_out.read_text(encoding="utf-8"))["pages"] == 1

    audit_out = tmp_path / "audit-copy.json"
    assert (
        main(
            [
                "data",
                "audit",
                "--gold",
                str(output / "gold.jsonl"),
                "--dataset-root",
                str(output),
                "--output",
                str(audit_out),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert json.loads(audit_out.read_text(encoding="utf-8"))["is_valid"] is True

    assert main(["data", "freeze", "--build-root", str(output)]) == 0
    freeze_payload = json.loads(capsys.readouterr().out)
    assert freeze_payload["dataset_fingerprint"] == build_payload["dataset_fingerprint"]
    assert (output / "FROZEN.json").exists()


def test_data_build_rejects_unaccepted_research_source(capsys, tmp_path):
    registry = _registry(tmp_path / "registry.yaml", restricted=True)
    source = _source_root(tmp_path)

    assert (
        main(
            [
                "data",
                "build",
                "--registry",
                str(registry),
                "--source",
                "page-source",
                "--source-root",
                f"page-source={source}",
                "--output",
                str(tmp_path / "build"),
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert "acceptance" in payload["error"].lower()


def test_data_list_uses_packaged_v1_registry_when_registry_is_omitted(capsys):
    assert main(["data", "list", "--source", "modern-public-documents-v1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["registry_version"] == "1.0.0"
    assert [item["source_id"] for item in payload["sources"]] == ["modern-public-documents-v1"]


def test_data_profiles_lists_official_machine_readable_profiles(capsys):
    assert main(["data", "profiles"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["profiles_version"] == "1.0.0"
    assert [item["profile_id"] for item in payload["profiles"]] == [
        "biblical-niqqud-synthetic-diagnostic-v1",
        "historical-hebrew-press-mixed-v1",
        "historical-pinkas-handwriting-v1",
        "modern-hebrew-development-v1",
        "modern-hebrew-handwriting-v1",
        "modern-hebrew-print-v1",
        "rashi-print-synthetic-diagnostic-v1",
    ]
    assert {item["profile_id"]: item["source_ids"] for item in payload["profiles"]}[
        "modern-hebrew-print-v1"
    ] == [
        "modern-bidi-diagnostic-v1",
        "modern-public-documents-v1",
    ]


def test_data_build_rejects_source_selection_that_does_not_match_named_profile(capsys, tmp_path):
    registry_path = _registry(tmp_path / "registry.yaml")
    registry_payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    registry_payload["sources"]["extra-source"] = dict(registry_payload["sources"]["page-source"])
    registry_payload["sources"]["extra-source"]["title"] = "Extra CLI PAGE fixture"
    registry_path.write_text(
        yaml.safe_dump(registry_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    profiles_path = tmp_path / "profiles.yaml"
    profiles_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "profiles_version": "1.0.0",
                "profiles": {
                    "fixture-v1": {
                        "title": "Fixture profile",
                        "description": "Requires exactly one fixture source.",
                        "source_ids": ["page-source"],
                        "allowed_license_tiers": ["open"],
                        "certification_class": "official-open",
                        "score_policy": "per-source",
                    }
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    source = _source_root(tmp_path)

    assert (
        main(
            [
                "data",
                "build",
                "--registry",
                str(registry_path),
                "--profiles",
                str(profiles_path),
                "--profile",
                "fixture-v1",
                "--source",
                "page-source",
                "--source",
                "extra-source",
                "--source-root",
                f"page-source={source}",
                "--source-root",
                f"extra-source={source}",
                "--output",
                str(tmp_path / "build"),
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "ProfileError"
    assert "profile_source_mismatch" in payload["error"]
