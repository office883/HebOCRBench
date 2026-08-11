from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from hebocrbench.cli import main
from hebocrbench.full_suite import (
    MODERN_HEADLINE_COMPONENTS,
    REAL_COVERAGE_TARGETS,
    FullSuiteError,
    build_full_suite_lock,
    parse_full_suite_lock,
    validate_full_suite_contract,
    verify_full_suite_roots,
    with_full_suite_fingerprint,
)
from hebocrbench.io import sha256_file


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_FINGERPRINT = "a" * 64
PROFILES_FINGERPRINT = "b" * 64
DATASET_FINGERPRINT = "c" * 64


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _certified_root(
    root: Path,
    *,
    track_id: str = "modern-handwriting-v1",
    profile_id: str = "modern-hebrew-handwriting-v1",
) -> Path:
    root.mkdir()
    gold = root / "gold.jsonl"
    stats = root / "stats.json"
    dataset_lock = root / "dataset.lock.json"
    gold.write_text('{"page_id":"page-1"}\n', encoding="utf-8")
    _write_json(stats, {"page_count": 1})
    _write_json(
        dataset_lock,
        {
            "dataset_fingerprint": DATASET_FINGERPRINT,
            "records_sha256": sha256_file(gold),
            "stats_sha256": sha256_file(stats),
        },
    )
    inventory = [
        {
            "path": path.name,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in (dataset_lock, gold, stats)
    ]
    manifest = root / "manifest.json"
    _write_json(
        manifest,
        {
            "benchmark": "HebOCRBench",
            "benchmark_version": "1.0.0",
            "profile": profile_id,
            "profile_scope": "track-component",
            "track_id": track_id,
            "dataset_fingerprint": DATASET_FINGERPRINT,
            "registry_fingerprint": REGISTRY_FINGERPRINT,
            "page_count": 1,
            "source_ids": ["fixture-source-v1"],
            "files": inventory,
        },
    )
    certification = root / "certification.json"
    _write_json(certification, {"valid": True})
    _write_json(
        root / "FROZEN.json",
        {
            "dataset_fingerprint": DATASET_FINGERPRINT,
            "manifest_sha256": sha256_file(manifest),
        },
    )
    _write_json(
        root / "CERTIFIED.json",
        {
            "benchmark_version": "1.0.0",
            "certification_sha256": sha256_file(certification),
            "certified": True,
            "dataset_fingerprint": DATASET_FINGERPRINT,
            "registry_fingerprint": REGISTRY_FINGERPRINT,
        },
    )
    return root


def test_unified_suite_keeps_profiles_separate_and_gaps_explicit(tmp_path: Path):
    handwriting = _certified_root(tmp_path / "handwriting")
    payload = build_full_suite_lock(
        {"modern-handwriting-v1": handwriting},
        registry_fingerprint=REGISTRY_FINGERPRINT,
        profiles_fingerprint=PROFILES_FINGERPRINT,
    )
    suite = parse_full_suite_lock(payload)
    validate_full_suite_contract(
        suite,
        expected_benchmark_version="1.0.0",
        expected_registry_fingerprint=REGISTRY_FINGERPRINT,
        expected_profiles_fingerprint=PROFILES_FINGERPRINT,
    )

    assert suite.reporting_policy["cross_family_score"] == "forbidden"
    assert suite.components["modern-handwriting-v1"].status == "certified"
    assert all(
        suite.components[track_id].status == "missing" for track_id in MODERN_HEADLINE_COMPONENTS
    )
    assert suite.components["historical-pinkas-handwriting-v1"].status == "missing"
    assert all(suite.components[target].status == "missing" for target in REAL_COVERAGE_TARGETS)
    assert suite.coverage["declared_real_target_coverage"] == "incomplete"
    assert suite.coverage["modern_forms_status"] == "missing-real-gold"
    forms = suite.components["modern-forms-v1"]
    assert forms.status == "missing"
    assert forms.evidence_class == "missing-real-gold"
    assert forms.reporting_role == "experimental-non-rankable"


def test_full_suite_schema_accepts_missing_and_certified_components(tmp_path: Path):
    payload = build_full_suite_lock(
        {"modern-handwriting-v1": _certified_root(tmp_path / "handwriting")},
        registry_fingerprint=REGISTRY_FINGERPRINT,
        profiles_fingerprint=PROFILES_FINGERPRINT,
    )
    schema = json.loads(
        (ROOT / "schemas" / "full-suite-lock.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema).validate(payload)


def test_full_suite_reports_mixed_historical_press_without_closing_pure_rashi_gap(
    tmp_path: Path,
):
    press = _certified_root(
        tmp_path / "press",
        track_id="historical-hebrew-press-mixed-v1",
        profile_id="historical-hebrew-press-mixed-v1",
    )

    suite = parse_full_suite_lock(
        build_full_suite_lock(
            {"historical-hebrew-press-mixed-v1": press},
            registry_fingerprint=REGISTRY_FINGERPRINT,
            profiles_fingerprint=PROFILES_FINGERPRINT,
        )
    )

    assert suite.components["historical-hebrew-press-mixed-v1"].status == "certified"
    assert suite.components["rashi-pure-print-real-v1"].status == "missing"
    assert (
        "historical-hebrew-press-mixed-v1"
        in suite.coverage["real_public_fixed_extensions_available"]
    )
    assert "rashi-pure-print-real-v1" in suite.coverage["unresolved_real_coverage_gaps"]


def test_full_suite_rejects_digest_tampering_and_rehashed_policy_tampering():
    payload = build_full_suite_lock(
        {},
        registry_fingerprint=REGISTRY_FINGERPRINT,
        profiles_fingerprint=PROFILES_FINGERPRINT,
    )
    tampered = json.loads(json.dumps(payload))
    tampered["components"]["biblical-cantillation-real-v1"]["missing_reason"] = "done"
    with pytest.raises(FullSuiteError, match="suite_fingerprint"):
        parse_full_suite_lock(tampered)

    tampered = json.loads(json.dumps(payload))
    tampered["reporting_policy"]["cross_family_score"] = "allowed"
    tampered = with_full_suite_fingerprint(tampered)
    with pytest.raises(FullSuiteError, match="forbid cross-family"):
        parse_full_suite_lock(tampered)


def test_full_suite_detects_root_byte_tampering(tmp_path: Path):
    handwriting = _certified_root(tmp_path / "handwriting")
    suite = parse_full_suite_lock(
        build_full_suite_lock(
            {"modern-handwriting-v1": handwriting},
            registry_fingerprint=REGISTRY_FINGERPRINT,
            profiles_fingerprint=PROFILES_FINGERPRINT,
        )
    )
    report = verify_full_suite_roots(
        suite,
        {"modern-handwriting-v1": handwriting},
    )
    assert report["valid"] is True

    with (handwriting / "gold.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(FullSuiteError, match="manifest size is stale"):
        verify_full_suite_roots(suite, {"modern-handwriting-v1": handwriting})


def test_full_suite_refuses_to_certify_forms_without_real_field_gold(tmp_path: Path):
    forms = _certified_root(
        tmp_path / "forms",
        track_id="modern-forms-v1",
        profile_id="modern-hebrew-print-v1",
    )

    with pytest.raises(FullSuiteError, match="real gold contract"):
        build_full_suite_lock(
            {"modern-forms-v1": forms},
            registry_fingerprint=REGISTRY_FINGERPRINT,
            profiles_fingerprint=PROFILES_FINGERPRINT,
        )

    payload = build_full_suite_lock(
        {"modern-handwriting-v1": _certified_root(tmp_path / "handwriting")},
        registry_fingerprint=REGISTRY_FINGERPRINT,
        profiles_fingerprint=PROFILES_FINGERPRINT,
    )
    forms_entry = payload["components"]["modern-forms-v1"]
    forms_entry.pop("missing_reason")
    forms_entry["status"] = "certified"
    forms_entry["evidence"] = payload["components"]["modern-handwriting-v1"]["evidence"]
    payload = with_full_suite_fingerprint(payload)
    with pytest.raises(FullSuiteError, match="real gold contract"):
        parse_full_suite_lock(payload)


def test_full_suite_cli_can_publish_an_honest_all_missing_manifest(tmp_path: Path):
    output = tmp_path / "full-suite.lock.json"
    assert (
        main(
            [
                "full-suite",
                "build",
                "--registry",
                str(ROOT / "corpora" / "registry.yaml"),
                "--profiles",
                str(ROOT / "corpora" / "profiles.yaml"),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["reporting_policy"]["cross_family_score"] == "forbidden"
    assert payload["coverage"]["modern_headline_status"] == "incomplete"
    assert payload["coverage"]["declared_real_target_coverage"] == "incomplete"
    assert (
        main(
            [
                "full-suite",
                "verify",
                "--registry",
                str(ROOT / "corpora" / "registry.yaml"),
                "--profiles",
                str(ROOT / "corpora" / "profiles.yaml"),
                "--lock",
                str(output),
            ]
        )
        == 0
    )
