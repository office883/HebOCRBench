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


def test_packaged_registry_isolates_the_narrow_historical_pinkas_source():
    registry = load_source_registry()

    assert set(registry.sources) == {
        "biblical-niqqud-synthetic-diagnostic-v1",
        "historical-hebrew-press-mixed-v1",
        "historical-pinkas-handwriting-v1",
        "modern_bidi_diagnostic",
        "modern_public_documents",
        "modern_print_lines_development",
        "modern_handwriting_lines",
        "rashi-print-synthetic-diagnostic-v1",
    }
    assert all("yi" not in source.languages for source in registry.sources.values())
    assert registry.sources["modern_public_documents"].task == "page_ocr"
    assert registry.sources["modern_public_documents"].primary_score is True
    assert registry.sources["modern_handwriting_lines"].primary_score is False
    assert registry.sources["modern_handwriting_lines"].expected["human_test"] == 964
    pinkas = registry.sources["historical-pinkas-handwriting-v1"]
    assert pinkas.primary_score is False
    assert pinkas.expected["test_lines"] == 266
    assert pinkas.expected["test_pages"] == 6
    assert pinkas.expected["writer_disjoint"] is False
    assert pinkas.downloads[0].sha256 == (
        "d986a3527d1ddae19cf2f09f3ff5e84458eeb5e1f6f9cb4e2a48d895dfcd5eb6"
    )
    press = registry.sources["historical-hebrew-press-mixed-v1"]
    assert press.primary_score is False
    assert press.expected["test_pages"] == 34
    assert press.expected["test_lines"] == 4016
    assert press.expected["pure_rashi_annotations"] is False
    assert press.downloads[0].sha256 == (
        "775e77227cbd46099487d3294d8cfd449ced7c8b6eeb7865ba41f053fe1b0ea8"
    )
    niqqud = registry.sources["biblical-niqqud-synthetic-diagnostic-v1"]
    rashi = registry.sources["rashi-print-synthetic-diagnostic-v1"]
    assert niqqud.primary_score is rashi.primary_score is False
    assert niqqud.expected["cantillation_marks"] == 0
    assert niqqud.expected["held_out_test"] is True
    assert rashi.expected["held_out_test"] is True


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


def test_historical_pinkas_profile_is_a_separate_single_source_extension():
    registry = load_source_registry()
    profiles = load_profiles()

    resolved = resolve_profile(
        registry,
        profiles["historical-pinkas-handwriting-v1"],
        accepted_source_ids=set(),
    )

    assert [source.source_id for source in resolved] == ["historical-pinkas-handwriting-v1"]


def test_historical_press_profile_is_mixed_and_separate_from_pure_rashi():
    registry = load_source_registry()
    profiles = load_profiles()

    resolved = resolve_profile(
        registry,
        profiles["historical-hebrew-press-mixed-v1"],
        accepted_source_ids=set(),
    )

    assert [source.source_id for source in resolved] == ["historical-hebrew-press-mixed-v1"]
    assert resolved[0].expected["pure_rashi_annotations"] is False


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
