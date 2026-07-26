from __future__ import annotations

import json

import pytest

from hebocrbench.modern_suite import (
    DEFAULT_HEADLINE_TRACKS,
    ModernSuiteError,
    parse_modern_suite_lock,
    validate_modern_suite_contract,
    with_suite_fingerprint,
)


def _payload():
    digest = "a" * 64
    return with_suite_fingerprint(
        {
            "schema_version": "1.0",
            "suite_version": "1.0.0",
            "benchmark": "HebOCRBench Modern Hebrew",
            "benchmark_version": "1.0.0",
            "profile_id": "modern-hebrew-print-v1",
            "profile_fingerprint": "b" * 64,
            "registry_fingerprint": "c" * 64,
            "tracks": {
                track_id: {
                    "maturity": "certified",
                    "headline": True,
                    "dataset_fingerprint": digest,
                    "gold_sha256": digest,
                    "certification_sha256": digest,
                }
                for track_id in DEFAULT_HEADLINE_TRACKS
            },
        }
    )


def test_suite_lock_is_self_verifying_and_contract_bound():
    suite = parse_modern_suite_lock(_payload())
    validate_modern_suite_contract(
        suite,
        expected_benchmark_version="1.0.0",
        expected_registry_fingerprint="c" * 64,
        expected_profile_id="modern-hebrew-print-v1",
        expected_profile_fingerprint="b" * 64,
        allowed_track_ids=set(DEFAULT_HEADLINE_TRACKS),
    )
    assert {track_id for track_id, track in suite.tracks.items() if track.headline} == set(
        DEFAULT_HEADLINE_TRACKS
    )


def test_suite_lock_rejects_tampered_track_evidence():
    payload = json.loads(json.dumps(_payload()))
    payload["tracks"]["modern-page-ocr-v1"]["gold_sha256"] = "d" * 64

    with pytest.raises(ModernSuiteError, match="suite_fingerprint"):
        parse_modern_suite_lock(payload)


def test_suite_contract_rejects_uncertified_headline_track():
    payload = _payload()
    payload["tracks"]["modern-tables-v1"]["maturity"] = "experimental"
    payload = with_suite_fingerprint(payload)
    suite = parse_modern_suite_lock(payload)

    with pytest.raises(ModernSuiteError, match="not certified"):
        validate_modern_suite_contract(
            suite,
            expected_benchmark_version="1.0.0",
            expected_registry_fingerprint="c" * 64,
            expected_profile_id="modern-hebrew-print-v1",
            expected_profile_fingerprint="b" * 64,
            allowed_track_ids=set(DEFAULT_HEADLINE_TRACKS),
        )
