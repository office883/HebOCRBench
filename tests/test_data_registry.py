from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hebocrbench.data_registry import (
    LicensePolicyError,
    load_profiles,
    load_source_registry,
    resolve_profile,
)


def test_packaged_registry_contains_only_modern_hebrew_source_families():
    registry = load_source_registry()

    assert set(registry.sources) == {
        "modern_bidi_diagnostic",
        "modern_public_documents",
        "modern_print_lines_development",
        "modern_handwriting_lines",
    }
    assert all("yi" not in source.languages for source in registry.sources.values())
    assert registry.sources["modern_public_documents"].task == "page_ocr"
    assert registry.sources["modern_public_documents"].primary_score is True
    assert registry.sources["modern_handwriting_lines"].primary_score is False
    assert registry.sources["modern_handwriting_lines"].expected["human_test"] == 964


def test_modern_print_profile_excludes_handwriting_and_development_sources():
    registry = load_source_registry()
    profiles = load_profiles()

    resolved = resolve_profile(
        registry,
        profiles["modern-print"],
        accepted_source_ids=set(),
    )
    assert {source.source_id for source in resolved} == {
        "modern_bidi_diagnostic",
        "modern_public_documents",
    }


def test_modern_development_profile_retains_non_headline_line_source():
    registry = load_source_registry()
    profiles = load_profiles()

    resolved = resolve_profile(
        registry,
        profiles["modern-development"],
        accepted_source_ids=set(),
    )
    assert {source.source_id for source in resolved} == {
        "modern_bidi_diagnostic",
        "modern_public_documents",
        "modern_print_lines_development",
    }


def test_profile_policy_rejects_source_outside_allowed_license_classes(tmp_path: Path):
    registry = load_source_registry()
    profiles_path = tmp_path / "profiles.yaml"
    profiles_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "profiles": {
                    "bad": {
                        "description": "bad profile",
                        "source_ids": ["modern_public_documents"],
                        "allowed_license_classes": ["bundled"],
                        "require_acceptance": False,
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    profile = load_profiles(profiles_path)["bad"]

    with pytest.raises(LicensePolicyError, match="modern_public_documents"):
        resolve_profile(registry, profile, accepted_source_ids=set())


def test_registry_schema_rejects_missing_authoritative_license(tmp_path: Path):
    registry_path = tmp_path / "sources.yaml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.0",
                "sources": {
                    "broken": {
                        "name": "Broken",
                        "version": "1",
                        "task": "page_ocr",
                        "languages": ["he"],
                        "script": "Hebr",
                        "role": "core",
                        "status": "available",
                        "format": "PDF",
                        "primary_score": True,
                        "landing_page": "https://example.invalid",
                        "downloads": [],
                        "expected": {},
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source registry schema"):
        load_source_registry(registry_path)
