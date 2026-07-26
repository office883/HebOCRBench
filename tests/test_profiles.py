from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hebocrbench.corpus_registry import load_registry
from hebocrbench.profiles import ProfileError, load_profiles, validate_profile_selection


ROOT = Path(__file__).resolve().parents[1]


def test_packaged_profiles_match_authoritative_file_and_reference_only_modern_sources():
    registry = load_registry(ROOT / "corpora" / "registry.yaml")
    profiles = load_profiles(ROOT / "corpora" / "profiles.yaml", registry=registry)
    packaged = load_profiles(None, registry=registry)

    assert profiles.fingerprint == packaged.fingerprint
    assert set(profiles.profiles) == {
        "modern-hebrew-print-v1",
        "modern-hebrew-development-v1",
        "modern-hebrew-handwriting-v1",
    }
    assert profiles.profiles["modern-hebrew-print-v1"].source_ids == (
        "modern-bidi-diagnostic-v1",
        "modern-public-documents-v1",
    )
    assert profiles.profiles["modern-hebrew-development-v1"].source_ids == (
        "modern-bidi-diagnostic-v1",
        "modern-public-documents-v1",
        "modern-print-lines-development-v1",
    )
    assert all("yi" not in registry.sources[source_id].languages for source_id in registry.sources)


def test_profile_selection_requires_exact_sources_for_official_modern_profile():
    registry = load_registry(ROOT / "corpora" / "registry.yaml")
    profiles = load_profiles(ROOT / "corpora" / "profiles.yaml", registry=registry)
    profile = profiles.profiles["modern-hebrew-print-v1"]

    result = validate_profile_selection(
        profile,
        selected_source_ids=profile.source_ids,
        registry=registry,
        accepted_source_ids=[],
    )
    assert result.is_valid

    wrong = validate_profile_selection(
        profile,
        selected_source_ids=profile.source_ids + ("modern-handwriting-lines-v1",),
        registry=registry,
        accepted_source_ids=[],
    )
    assert not wrong.is_valid
    assert "profile_source_mismatch" in {issue.code for issue in wrong.issues}


def test_handwriting_profile_is_separate_from_print_profile():
    registry = load_registry(ROOT / "corpora" / "registry.yaml")
    profiles = load_profiles(ROOT / "corpora" / "profiles.yaml", registry=registry)

    print_profile = profiles.profiles["modern-hebrew-print-v1"]
    handwriting_profile = profiles.profiles["modern-hebrew-handwriting-v1"]
    assert set(print_profile.source_ids).isdisjoint(handwriting_profile.source_ids)
    assert handwriting_profile.score_policy == "separate-extension-no-print-blending"


def test_profile_loader_rejects_unknown_sources(tmp_path):
    payload = {
        "schema_version": "1.0",
        "profiles_version": "1.0.0",
        "profiles": {
            "broken": {
                "title": "Broken",
                "description": "References a source that does not exist.",
                "source_ids": ["missing-source"],
                "allowed_license_tiers": ["bundled"],
                "certification_class": "official",
                "score_policy": "per-source",
            }
        },
    }
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    registry = load_registry(ROOT / "corpora" / "registry.yaml")

    with pytest.raises(ProfileError, match="unknown sources"):
        load_profiles(path, registry=registry)
