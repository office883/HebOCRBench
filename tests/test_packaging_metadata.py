from __future__ import annotations

from importlib import resources
from pathlib import Path
import tomllib

from hebocrbench import __version__
from hebocrbench.corpus_registry import load_registry


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_1_0_0_everywhere():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["version"] == "1.0.0"
    assert __version__ == "1.0.0"


def test_authoritative_registry_matches_packaged_registry():
    authoritative = (ROOT / "corpora" / "registry.yaml").read_bytes()
    packaged = resources.files("hebocrbench").joinpath("data/corpus-registry.yaml").read_bytes()

    assert packaged == authoritative
    assert load_registry(None).fingerprint == load_registry(ROOT / "corpora" / "registry.yaml").fingerprint


def test_default_benchmark_config_is_packaged_verbatim():
    authoritative = (ROOT / "benchmark.yaml").read_bytes()
    packaged = resources.files("hebocrbench").joinpath("data/benchmark.yaml").read_bytes()

    assert packaged == authoritative


def test_all_runtime_schemas_are_packaged():
    expected = {path.name for path in (ROOT / "schemas").glob("*.json")}
    packaged_root = resources.files("hebocrbench").joinpath("schemas")
    packaged = {entry.name for entry in packaged_root.iterdir() if entry.name.endswith(".json")}

    assert expected <= packaged


def test_registry_lock_matches_authoritative_registry():
    import json

    registry = load_registry(ROOT / "corpora" / "registry.yaml")
    lock = json.loads((ROOT / "corpora" / "registry.lock.json").read_text(encoding="utf-8"))
    packaged = json.loads(
        resources.files("hebocrbench").joinpath("data/corpus-registry.lock.json").read_text(encoding="utf-8")
    )

    assert lock == packaged
    assert lock["schema_version"] == "1.0"
    assert lock["registry_version"] == registry.registry_version
    assert lock["registry_fingerprint"] == registry.fingerprint
    assert set(lock["sources"]) == set(registry.sources)


def test_package_data_includes_registry_lock_and_default_config():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = set(project["tool"]["setuptools"]["package-data"]["hebocrbench"])

    assert "data/*.yaml" in patterns
    assert "data/*.json" in patterns


def test_registry_and_profile_locks_are_generated_from_runtime_metadata():
    import json

    from hebocrbench.profiles import load_profiles
    from hebocrbench.release_metadata import profile_lock_payload, registry_lock_payload

    registry = load_registry(ROOT / "corpora" / "registry.yaml")
    profiles = load_profiles(ROOT / "corpora" / "profiles.yaml", registry=registry)
    registry_lock = json.loads(
        (ROOT / "corpora" / "registry.lock.json").read_text(encoding="utf-8")
    )
    profile_lock = json.loads(
        (ROOT / "corpora" / "profiles.lock.json").read_text(encoding="utf-8")
    )

    assert registry_lock_payload(registry, benchmark_version="1.0.0") == registry_lock
    assert profile_lock_payload(profiles, registry_fingerprint=registry.fingerprint) == profile_lock
    source = registry_lock["sources"]["modern-public-documents-v1"]
    assert source["license"]["authority"].startswith("Knesset Open Data")
    assert source["citation"]["key"] == "hebocrbench-modern-public-documents-v1"
    assert source["metadata"]["era"] == "modern"
    assert "discovery" in source
    profile = profile_lock["profiles"]["modern-hebrew-print-v1"]
    assert profile["title"].startswith("HebOCRBench 1.0")
    assert profile["description"]


def test_authoritative_profiles_and_lock_match_packaged_resources():
    authoritative_profiles = (ROOT / "corpora" / "profiles.yaml").read_bytes()
    packaged_profiles = resources.files("hebocrbench").joinpath(
        "data/corpus-profiles.yaml"
    ).read_bytes()
    authoritative_lock = (ROOT / "corpora" / "profiles.lock.json").read_bytes()
    packaged_lock = resources.files("hebocrbench").joinpath(
        "data/corpus-profiles.lock.json"
    ).read_bytes()

    assert packaged_profiles == authoritative_profiles
    assert packaged_lock == authoritative_lock


def test_modern_release_tree_contains_no_historical_source_materializers():
    forbidden = [
        ROOT / "scripts" / "materialize_v1_sources.py",
        ROOT / "src" / "hebocrbench" / "converters" / "wikisource.py",
        ROOT / "tests" / "test_wikisource_converter.py",
        ROOT / "tests" / "test_materializer_v1.py",
    ]
    assert not [path.relative_to(ROOT).as_posix() for path in forbidden if path.exists()]


def test_base_runtime_declares_modern_pdf_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = set(project["dependencies"])

    assert any(item.startswith("PyMuPDF>=") for item in dependencies)
