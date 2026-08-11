from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hebocrbench.corpus_registry import RegistryError, load_registry


def _registry_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "registry_version": "1.0.0",
        "benchmark": "HebOCRBench",
        "sources": {
            "open-page": {
                "title": "Open PAGE corpus",
                "version": "1",
                "task": "end_to_end_ocr",
                "track": "modern_page_ocr",
                "languages": ["he"],
                "script": "Hebr",
                "status": "core",
                "converter": "pagexml",
                "homepage": "https://example.org/open",
                "citation": {"key": "open-page", "text": "Example citation"},
                "license": {
                    "spdx": "CC-BY-4.0",
                    "tier": "open",
                    "redistribution": "allowed",
                    "requires_acceptance": False,
                    "uri": "https://creativecommons.org/licenses/by/4.0/",
                },
                "artifacts": [
                    {
                        "artifact_id": "archive",
                        "url": "file:///tmp/open.zip",
                        "filename": "open.zip",
                        "archive": "zip",
                        "checksum": {
                            "algorithm": "sha256",
                            "value": "a" * 64,
                        },
                    }
                ],
                "discovery": {
                    "annotation_globs": ["**/*.xml"],
                    "image_roots": ["images"],
                },
                "split": {
                    "strategy": "hash_group",
                    "group_fields": ["document_id"],
                    "ratios": {"train": 0.7, "dev": 0.15, "test": 0.15},
                    "seed": 20260723,
                },
                "metadata": {
                    "script_style": "modern_square_print",
                    "era": "modern",
                    "document_type": "public_document",
                    "layout_type": "complex",
                    "vocalization": "mixed",
                    "source_type": "scan",
                },
            },
            "research-alto": {
                "title": "Research ALTO corpus",
                "version": "2",
                "task": "recognition_and_layout",
                "track": "modern_page_ocr",
                "languages": ["he"],
                "script": "Hebr",
                "status": "core",
                "converter": "alto",
                "homepage": "https://example.org/research",
                "citation": {"key": "research-alto", "text": "Research citation"},
                "license": {
                    "spdx": "CC-BY-NC-SA-4.0",
                    "tier": "research-nc",
                    "redistribution": "conditional",
                    "requires_acceptance": True,
                    "uri": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                },
                "artifacts": [],
                "discovery": {"annotation_globs": ["**/*alto*.xml"], "image_roots": ["."]},
                "split": {"strategy": "upstream", "group_fields": ["document_id"]},
                "metadata": {
                    "script_style": "modern_square_print",
                    "era": "modern",
                    "document_type": "public_document",
                    "layout_type": "mixed",
                    "vocalization": "mixed",
                    "source_type": "scan",
                },
            },
        },
    }


def _write_registry(tmp_path: Path, payload: dict[str, object]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_registry_loads_typed_sources_and_selects_license_tiers(tmp_path):
    registry = load_registry(_write_registry(tmp_path, _registry_payload()))

    assert registry.registry_version == "1.0.0"
    assert registry.sources["open-page"].is_open is True
    assert registry.sources["open-page"].acceptance_required is False
    assert registry.sources["research-alto"].is_open is False
    assert registry.sources["research-alto"].acceptance_required is True
    assert [source.source_id for source in registry.select(tiers={"open"})] == ["open-page"]
    assert [source.source_id for source in registry.select(source_ids={"research-alto"})] == [
        "research-alto"
    ]


def test_registry_fingerprint_is_independent_of_yaml_key_order(tmp_path):
    payload = _registry_payload()
    path_a = _write_registry(tmp_path / "a", payload)
    reversed_sources = dict(reversed(list(payload["sources"].items())))  # type: ignore[index]
    payload_b = dict(payload)
    payload_b["sources"] = reversed_sources
    path_b = _write_registry(tmp_path / "b", payload_b)

    assert load_registry(path_a).fingerprint == load_registry(path_b).fingerprint


def test_registry_rejects_noncommercial_license_marked_open(tmp_path):
    payload = _registry_payload()
    payload["sources"]["research-alto"]["license"]["tier"] = "open"  # type: ignore[index]

    with pytest.raises(RegistryError, match="non-commercial"):
        load_registry(_write_registry(tmp_path, payload))


def test_registry_rejects_missing_checksum_for_remote_core_artifact(tmp_path):
    payload = _registry_payload()
    del payload["sources"]["open-page"]["artifacts"][0]["checksum"]  # type: ignore[index]

    with pytest.raises(RegistryError, match="checksum"):
        load_registry(_write_registry(tmp_path, payload))


def test_registry_rejects_unknown_source_selection(tmp_path):
    registry = load_registry(_write_registry(tmp_path, _registry_payload()))

    with pytest.raises(RegistryError, match="Unknown source"):
        registry.select(source_ids={"missing"})


def test_packaged_v1_registry_keeps_historical_pinkas_as_a_separate_locked_extension():
    registry = load_registry(Path("corpora/registry.yaml"))

    assert registry.registry_version == "1.0.0"
    assert set(registry.sources) == {
        "biblical-niqqud-synthetic-diagnostic-v1",
        "historical-hebrew-press-mixed-v1",
        "historical-pinkas-handwriting-v1",
        "modern-bidi-diagnostic-v1",
        "modern-public-documents-v1",
        "modern-print-lines-development-v1",
        "modern-handwriting-lines-v1",
        "rashi-print-synthetic-diagnostic-v1",
    }
    modern_source_ids = {
        "modern-bidi-diagnostic-v1",
        "modern-public-documents-v1",
        "modern-print-lines-development-v1",
        "modern-handwriting-lines-v1",
    }
    modern_sources = {
        source_id: source
        for source_id, source in registry.sources.items()
        if source_id in modern_source_ids
    }
    for source in modern_sources.values():
        assert source.metadata.get("era") == "modern"
        assert "yi" not in source.languages
    pinkas = registry.sources["historical-pinkas-handwriting-v1"]
    assert pinkas.metadata["era"] == "historical"
    assert pinkas.metadata["benchmark_data_status"] == "real-public-fixed"
    assert pinkas.metadata["coverage_scope"] == "narrow-single-collection"
    assert pinkas.metadata["writer_disjoint"] is False
    assert pinkas.converter == "pinkas-webdataset"
    assert pinkas.artifacts[0].checksum is not None
    assert (
        pinkas.artifacts[0].checksum.value
        == "d986a3527d1ddae19cf2f09f3ff5e84458eeb5e1f6f9cb4e2a48d895dfcd5eb6"
    )
    press = registry.sources["historical-hebrew-press-mixed-v1"]
    assert press.converter == "historical-press-pagealto"
    assert press.metadata["expected_pages"] == 34
    assert press.metadata["expected_lines"] == 4016
    assert press.metadata["pure_rashi_claim"] is False
    assert press.artifacts[0].ignored_archive_members == ("/log.txt",)
    assert press.artifacts[0].checksum is not None
    assert press.artifacts[0].checksum.value == (
        "775e77227cbd46099487d3294d8cfd449ced7c8b6eeb7865ba41f053fe1b0ea8"
    )
    for source_id in (
        "historical-hebrew-press-mixed-v1",
        "historical-pinkas-handwriting-v1",
        "modern-public-documents-v1",
        "modern-print-lines-development-v1",
        "modern-handwriting-lines-v1",
    ):
        artifact = registry.sources[source_id].artifacts[0]
        assert artifact.revision

    niqqud = registry.sources["biblical-niqqud-synthetic-diagnostic-v1"]
    rashi = registry.sources["rashi-print-synthetic-diagnostic-v1"]
    assert niqqud.status == rashi.status == "diagnostic"
    assert niqqud.converter == rashi.converter == "foundation-webdataset"
    assert niqqud.metadata["benchmark_data_status"] == "synthetic-public-fixed"
    assert niqqud.metadata["expected_cantillation_marks"] == 0
    assert niqqud.metadata["headline_eligible"] is False
    assert rashi.metadata["headline_eligible"] is False
    assert (
        niqqud.artifacts[0].checksum.value
        == rashi.artifacts[0].checksum.value
        == ("12886b77eefb54f73ed2ea9ba9ddf4766de60ed2635126248344739626608927")
    )
