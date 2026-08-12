from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hebocrbench.modern_suite import DEFAULT_HEADLINE_TRACKS
from hebocrbench.public_results import (
    PublicResultsError,
    build_public_results_pack,
    verify_public_results_pack,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _artifact(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _make_run(root: Path, *, system_id: str = "fixture::1") -> Path:
    suite_sha = "1" * 64
    profile_sha = "2" * 64
    registry_sha = "3" * 64
    model = {
        "system_id": system_id,
        "family": "fixture",
        "name": "Fixture OCR",
        "version": "1.0",
    }
    tracks: dict[str, object] = {}
    for index, track_id in enumerate(DEFAULT_HEADLINE_TRACKS, start=1):
        prediction = root / "predictions" / f"{track_id}.jsonl"
        prediction.parent.mkdir(parents=True, exist_ok=True)
        prediction.write_text('{"page_id":"public-page"}\n', encoding="utf-8")
        report = root / "reports" / track_id
        report.mkdir(parents=True, exist_ok=True)
        metrics = {
            "coverage": {
                "gold_pages": 1,
                "submitted_prediction_pages": 1,
                "matched_prediction_pages": 1,
                "missing_prediction_pages": 0,
                "extra_prediction_pages": 0,
                "missing_page_ids": [],
                "extra_page_ids": [],
            },
            "operational": {"evaluated_pages": 1, "latency_ms_p50": float(index)},
            "recognition": {"line_gcer": index / 10},
        }
        _write_json(report / "metrics.json", metrics)
        (report / "errors.jsonl").write_text("", encoding="utf-8")
        (report / "per_page.jsonl").write_text(
            '{"page_id":"public-page","line_gcer":0.1}\n', encoding="utf-8"
        )
        (report / "report.html").write_text("<p>aggregate report</p>\n", encoding="utf-8")
        (report / "summary.csv").write_text("metric,value\nline_gcer,0.1\n", encoding="utf-8")
        artifacts = {
            name: _artifact(report / filename)
            for name, filename in {
                "errors": "errors.jsonl",
                "html": "report.html",
                "metrics": "metrics.json",
                "per_page": "per_page.jsonl",
                "summary": "summary.csv",
            }.items()
        }
        input_mode = (
            "blind_whole_line_image"
            if track_id in {"modern-bidi-v1", "modern-line-recognition-v1"}
            else "blind_full_page_image"
        )
        run_manifest = {
            "benchmark": "HebOCRBench Modern Hebrew",
            "benchmark_suite": {
                "benchmark_version": "1.0.0",
                "suite_version": "1.0.0",
                "suite_fingerprint": suite_sha,
                "profile_id": "modern-hebrew-print-v1",
                "profile_fingerprint": profile_sha,
                "registry_fingerprint": registry_sha,
                "track_id": track_id,
                "headline": True,
                "maturity": "certified",
                "dataset_fingerprint": _sha(f"dataset-{index}"),
                "gold_sha256": _sha(f"gold-{index}"),
                "certification_sha256": _sha(f"certification-{index}"),
            },
            "configuration": {
                "official_track_id": track_id,
                "official_track_version": "1.0.0",
                "official_track_fingerprint": _sha(f"track-{index}"),
            },
            "model": {
                **model,
                "runner": "fixture.runner",
                "runner_schema_version": "1.0",
                "input_mode": input_mode,
                "gold_assistance": False,
                "oracle_layout": False,
            },
            "created_at_utc": f"2026-08-12T00:00:0{index}+00:00",
            "evaluator_version": "1.0.0",
            "unicode_data_version": "15.0.0",
            "python": "3.12 fixture",
            "platform": "fixture-arm64",
            "libraries": {"fixture": "1.0"},
            "inputs": {
                "predictions": {
                    "path": str(prediction),
                    "sha256": hashlib.sha256(prediction.read_bytes()).hexdigest(),
                    "size_bytes": prediction.stat().st_size,
                }
            },
            "artifacts": artifacts,
        }
        _write_json(report / "run_manifest.json", run_manifest)
        tracks[track_id] = {
            "track_id": track_id,
            "evaluation_split": "diagnostic" if track_id == "modern-bidi-v1" else "test",
            "selected_pages": 1,
            "source_evaluation_pages": 1,
            "failures": 0,
            "api_failures": 0,
            "prediction_path": str(prediction),
            "model": model,
        }

    baseline = {
        "runner_schema_version": "1.0",
        "benchmark": "HebOCRBench Modern Hebrew",
        "engine": "fixture",
        "workers": 2,
        "limited_smoke_run": False,
        "suite_fingerprint": suite_sha,
        "model": model,
        "tracks": tracks,
    }
    _write_json(root / "baseline-run.json", baseline)
    score = {
        "score_schema_version": "1.1",
        "benchmark": "HebOCRBench Modern Hebrew",
        "status": "non_conformant",
        "headline_score": None,
        "suite_fingerprint": suite_sha,
        "profile_id": "modern-hebrew-print-v1",
        "profile_fingerprint": profile_sha,
        "required_tracks": list(DEFAULT_HEADLINE_TRACKS),
        "missing_tracks": [],
        "failed_gates": ["fixture_gate"],
        "score_admission": {
            "schema_version": "1.0",
            "status": "verified_recomputed",
            "artifact_hashes_verified": True,
            "component_roots_verified": True,
            "blind_input_contract_verified": True,
            "metrics_recomputed": True,
            "gold_assistance": False,
            "oracle_layout": False,
        },
    }
    _write_json(root / "modern-score.json", score)
    return root


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_public_results_pack_is_compact_path_free_and_deterministic(tmp_path):
    source = _make_run(tmp_path / "private-run")
    first = tmp_path / "public-one"
    second = tmp_path / "public-two"

    first_manifest = build_public_results_pack({"fixture": source}, first)
    second_manifest = build_public_results_pack({"fixture": source}, second)

    assert verify_public_results_pack(first) == first_manifest
    assert first_manifest == second_manifest
    assert _tree_hashes(first) == _tree_hashes(second)
    assert first_manifest["compact_not_self_contained"] is True
    assert first_manifest["privacy_boundary"] == {
        "contains_raw_predictions": False,
        "contains_gold": False,
        "contains_images": False,
        "contains_organizer_maps": False,
        "contains_key_material": False,
        "contains_local_absolute_paths": False,
    }
    result = json.loads((first / "results" / "fixture" / "result.json").read_text())
    assert result["complete_run_summary"] == {
        "track_count": 5,
        "evaluated_items": 5,
        "runner_and_api_failures": 0,
        "all_tracks_complete": True,
        "score_status": "non_conformant",
        "score_admission_status": "verified_recomputed",
    }
    assert result["source_artifact_attestation"]["artifact_count"] == 37
    predictions = result["source_artifact_attestation"]["artifacts"][
        "predictions/modern-line-recognition-v1.jsonl"
    ]
    assert predictions["included_in_compact_pack"] is False
    assert predictions["line_count"] == 1
    assert not list(first.rglob("*.jsonl"))
    assert not list(first.rglob("*.png"))
    all_public_bytes = b"".join(path.read_bytes() for path in first.rglob("*") if path.is_file())
    assert str(tmp_path).encode() not in all_public_bytes
    assert "not self-contained" in (first / "README.md").read_text(encoding="utf-8")


def test_public_results_pack_rejects_tampered_complete_run_artifact(tmp_path):
    source = _make_run(tmp_path / "private-run")
    (source / "reports" / "modern-tables-v1" / "summary.csv").write_text(
        "tampered\n", encoding="utf-8"
    )

    with pytest.raises(PublicResultsError, match="differs from its run manifest"):
        build_public_results_pack({"fixture": source}, tmp_path / "public")


def test_public_results_pack_rejects_local_path_leaked_by_metrics(tmp_path):
    source = _make_run(tmp_path / "private-run")
    report = source / "reports" / "modern-bidi-v1"
    metrics_path = report / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["debug"] = {"cache": "/Users/example/private-cache"}
    _write_json(metrics_path, metrics)
    manifest_path = report / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["metrics"] = _artifact(metrics_path)
    _write_json(manifest_path, manifest)

    with pytest.raises(PublicResultsError, match="absolute local path"):
        build_public_results_pack({"fixture": source}, tmp_path / "public")


def test_public_results_verifier_rejects_tampered_compact_payload(tmp_path):
    source = _make_run(tmp_path / "private-run")
    public = tmp_path / "public"
    build_public_results_pack({"fixture": source}, public)
    score_path = public / "results" / "fixture" / "modern-score.json"
    score_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(PublicResultsError, match="payload hash or size differs"):
        verify_public_results_pack(public)
