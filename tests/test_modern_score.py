from __future__ import annotations

from copy import deepcopy

from hebocrbench.modern_score import combine_modern_track_reports
from hebocrbench.modern_suite import (
    DEFAULT_HEADLINE_TRACKS,
    parse_modern_suite_lock,
    with_suite_fingerprint,
)
from hebocrbench.tracks import load_track


def _suite():
    digest = "a" * 64
    return parse_modern_suite_lock(
        with_suite_fingerprint(
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
    )


def _coverage():
    return {"gold_pages": 10, "matched_prediction_pages": 10, "missing_prediction_pages": 0}


def _metrics(track_id):
    base = {"coverage": _coverage()}
    if track_id == "modern-bidi-v1":
        base.update(
            {
                "conformance": {
                    "status": "conformant",
                    "strict_line_exact_rate": 1.0,
                    "failed_checks": [],
                },
                "bidi": {
                    "ltr_run_exact_rate": 1.0,
                    "numeric_exact_rate": 1.0,
                    "bracket_exact_rate": 1.0,
                    "pairwise_word_order_accuracy": 1.0,
                    "visual_order_failure_rate": 0.0,
                },
            }
        )
    elif track_id == "modern-line-recognition-v1":
        base["recognition"] = {"line_gcer": 0.0, "line_wer": 0.0, "line_exact_rate": 1.0}
    elif track_id == "modern-page-ocr-v1":
        base.update(
            {
                "recognition": {"page_order_gcer": 0.0, "line_gcer": 0.0},
                "layout": {"regions": {"f1": 1.0}, "lines": {"f1": 1.0}},
                "reading_order": {"edge_f1": 1.0, "pairwise_accuracy": 1.0},
            }
        )
    elif track_id == "modern-tables-v1":
        base["tables"] = {
            "gold_tables": 2,
            "table_presence_f1": 1.0,
            "cell_span_f1": 1.0,
            "grid_slot_accuracy": 1.0,
            "cell_text_gcer": 0.0,
        }
    elif track_id == "modern-robustness-v1":
        base.update(
            {
                "recognition": {"line_gcer": 0.0, "line_exact_rate": 1.0},
                "distribution": {"page_line_gcer_p90": 0.0},
            }
        )
    return base


def _reports(suite):
    model = {"name": "perfect-fixture", "version": "1"}
    reports = []
    for track_id in DEFAULT_HEADLINE_TRACKS:
        spec = load_track(track_id)
        entry = suite.tracks[track_id]
        evidence = {
            "suite_version": suite.suite_version,
            "suite_fingerprint": suite.suite_fingerprint,
            "benchmark_version": suite.benchmark_version,
            "profile_id": suite.profile_id,
            "profile_fingerprint": suite.profile_fingerprint,
            "registry_fingerprint": suite.registry_fingerprint,
            "track_id": track_id,
            **entry.to_dict(),
        }
        reports.append(
            {
                "configuration": {
                    "official_track_id": track_id,
                    "official_track_version": spec.version,
                    "official_track_fingerprint": spec.config_fingerprint,
                },
                "metrics": _metrics(track_id),
                "run_manifest": {"benchmark_suite": evidence, "model": model},
            }
        )
    return reports


def test_perfect_locked_reports_receive_rankable_100_score():
    suite = _suite()
    result = combine_modern_track_reports(_reports(suite), suite_lock=suite)

    assert result["status"] == "rankable"
    assert result["headline_score"] == 100.0
    assert set(result["components"]) == {
        "bidi",
        "line_recognition",
        "page_ocr",
        "tables",
        "robustness",
    }


def test_failed_bidi_gate_blocks_official_rank():
    suite = _suite()
    reports = _reports(suite)
    reports[0] = deepcopy(reports[0])
    reports[0]["metrics"]["conformance"]["status"] = "non_conformant"
    reports[0]["metrics"]["conformance"]["failed_checks"] = ["numeric_exact_rate"]

    result = combine_modern_track_reports(reports, suite_lock=suite)

    assert result["status"] == "non_conformant"
    assert result["headline_score"] is None
    assert result["failed_gates"] == ["numeric_exact_rate"]
