"""Deterministic, path-free publication packs for completed Modern baselines.

The complete evaluator output is intentionally much larger than a useful public
summary.  This module verifies every original run artifact, copies the exact
machine-readable score and metric files, and emits a path-free attestation for
the omitted artifacts.  It never copies predictions, page-level reports, error
details, images, gold, or organizer material.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping

from .io import write_json
from .modern_suite import DEFAULT_HEADLINE_TRACKS


class PublicResultsError(ValueError):
    """A source run is incomplete, inconsistent, or unsafe to publish."""


_RESULT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_ABSOLUTE_RE = re.compile(r"[A-Za-z]:[\\/]")
_LOCAL_PATH_MARKERS = ("/Users/", "/home/", "/private/var/", "file://")
_FORBIDDEN_PUBLIC_KEYS = {
    "gold_path",
    "gold_text",
    "hmac_key",
    "id_key",
    "id_map",
    "image_path",
    "organizer_path",
    "prediction_path",
    "raw_prediction",
    "reference_text",
}
_MODEL_IDENTITY_FIELDS = (
    "system_id",
    "family",
    "name",
    "version",
    "artifacts",
    "engine",
    "engine_version",
    "inference_backend",
    "runner",
    "runner_schema_version",
    "server_parallel",
    "server_context_size",
    "server_context_per_slot",
)
_RUNTIME_FIELDS = (
    "python",
    "platform",
    "libraries",
    "unicode_data_version",
    "evaluator_version",
)
_SUITE_COMMON_FIELDS = (
    "benchmark_version",
    "suite_version",
    "suite_fingerprint",
    "profile_id",
    "profile_fingerprint",
    "registry_fingerprint",
)
_REQUIRED_REPORT_ARTIFACTS = {"errors", "html", "metrics", "per_page", "summary"}
_REAL_EXTENSION_TRACKS = (
    "modern-handwriting-v1",
    "historical-hebrew-press-mixed-v1",
    "historical-pinkas-handwriting-v1",
)
_SYNTHETIC_DIAGNOSTIC_TRACKS = (
    "biblical-niqqud-synthetic-diagnostic-v1",
    "rashi-print-synthetic-diagnostic-v1",
)
_ALL_EXTENSION_TRACKS = (*_REAL_EXTENSION_TRACKS, *_SYNTHETIC_DIAGNOSTIC_TRACKS)
_HISTORICAL_PRESS_TRACK = "historical-hebrew-press-mixed-v1"
_EXTENSION_REPORTING_POLICY = {
    "combined_score": None,
    "modern_headline_blending": False,
    "synthetic_diagnostics_rankable": False,
}


def _load_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicResultsError(f"cannot read {label} as UTF-8 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PublicResultsError(f"{label} must be a JSON object: {path}")
    return value


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicResultsError(f"{label} must be an object")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicResultsError(f"{label} must be a non-negative integer")
    return value


def _assert_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _HEX_SHA256_RE.fullmatch(value):
        raise PublicResultsError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _assert_public_value(value: object, location: str = "payload") -> None:
    """Reject local paths and fields that could carry private run material."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text.lower() in _FORBIDDEN_PUBLIC_KEYS:
                raise PublicResultsError(f"forbidden public field at {location}.{key_text}")
            _assert_public_value(child, f"{location}.{key_text}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_public_value(child, f"{location}[{index}]")
        return
    if not isinstance(value, str):
        return
    if value.startswith(("/", "~")) or _WINDOWS_ABSOLUTE_RE.match(value):
        raise PublicResultsError(f"absolute local path at {location}")
    if any(marker in value for marker in _LOCAL_PATH_MARKERS):
        raise PublicResultsError(f"local path marker at {location}")


def _measure_file(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise PublicResultsError(f"required source artifact is missing: {path}")
    digest = hashlib.sha256()
    size = 0
    line_count = 0
    last_byte = b""
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            line_count += chunk.count(b"\n")
            last_byte = chunk[-1:]
    result: dict[str, object] = {"sha256": digest.hexdigest(), "size_bytes": size}
    if path.suffix == ".jsonl":
        result["line_count"] = line_count + int(size > 0 and last_byte != b"\n")
    return result


def _verify_declared_artifact(
    path: Path,
    declared: Mapping[str, Any],
    label: str,
) -> dict[str, object]:
    measured = _measure_file(path)
    expected_sha = _assert_sha256(declared.get("sha256"), f"{label}.sha256")
    expected_size = _integer(declared.get("size_bytes"), f"{label}.size_bytes")
    if measured["sha256"] != expected_sha or measured["size_bytes"] != expected_size:
        raise PublicResultsError(f"source artifact differs from its run manifest: {label}")
    return measured


def _model_identity(model: Mapping[str, Any]) -> dict[str, Any]:
    identity = {field: model[field] for field in _MODEL_IDENTITY_FIELDS if field in model}
    for required in ("system_id", "family", "name", "version"):
        if not isinstance(identity.get(required), str) or not identity[required]:
            raise PublicResultsError(f"run manifest model is missing {required}")
    _assert_public_value(identity, "model")
    return identity


def _runtime_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    runtime = {field: manifest.get(field) for field in _RUNTIME_FIELDS}
    if any(value is None for value in runtime.values()):
        raise PublicResultsError("run manifest is missing runtime identity")
    _assert_public_value(runtime, "runtime")
    return runtime


def _relative_report_artifact_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise PublicResultsError(f"{label}.path must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or len(relative.parts) != 1 or relative.name != value:
        raise PublicResultsError(f"{label}.path must be a report-local filename")
    return relative


def _artifact_record(
    measured: Mapping[str, object],
    *,
    included_as: str | None = None,
) -> dict[str, object]:
    record = dict(measured)
    record["included_in_compact_pack"] = included_as is not None
    if included_as is not None:
        record["included_as"] = included_as
    return record


def _verify_complete_coverage(
    track_id: str,
    track_run: Mapping[str, Any],
    metrics: Mapping[str, Any],
    prediction: Mapping[str, object],
) -> dict[str, object]:
    coverage = _mapping(metrics.get("coverage"), f"{track_id}.metrics.coverage")
    operational = _mapping(metrics.get("operational"), f"{track_id}.metrics.operational")
    selected = _integer(track_run.get("selected_pages"), f"{track_id}.selected_pages")
    source_pages = _integer(
        track_run.get("source_evaluation_pages"), f"{track_id}.source_evaluation_pages"
    )
    failures = _integer(track_run.get("failures"), f"{track_id}.failures")
    api_failures = _integer(track_run.get("api_failures"), f"{track_id}.api_failures")
    submitted = _integer(
        coverage.get("submitted_prediction_pages"),
        f"{track_id}.coverage.submitted_prediction_pages",
    )
    matched = _integer(
        coverage.get("matched_prediction_pages"),
        f"{track_id}.coverage.matched_prediction_pages",
    )
    gold_pages = _integer(coverage.get("gold_pages"), f"{track_id}.coverage.gold_pages")
    missing = _integer(
        coverage.get("missing_prediction_pages"),
        f"{track_id}.coverage.missing_prediction_pages",
    )
    extra = _integer(
        coverage.get("extra_prediction_pages"),
        f"{track_id}.coverage.extra_prediction_pages",
    )
    evaluated = _integer(
        operational.get("evaluated_pages"), f"{track_id}.operational.evaluated_pages"
    )
    operational_api_failures = _integer(
        operational.get("api_failures"), f"{track_id}.operational.api_failures"
    )
    prediction_lines = _integer(prediction.get("line_count"), f"{track_id}.prediction lines")
    expected_equal = {
        "source_evaluation_pages": source_pages,
        "gold_pages": gold_pages,
        "submitted_prediction_pages": submitted,
        "matched_prediction_pages": matched,
        "evaluated_pages": evaluated,
        "prediction_jsonl_records": prediction_lines,
    }
    if any(value != selected for value in expected_equal.values()):
        raise PublicResultsError(
            f"{track_id} is not a complete full-coverage run: "
            f"selected={selected}, observed={expected_equal}"
        )
    if failures or api_failures or operational_api_failures or missing or extra:
        raise PublicResultsError(
            f"{track_id} has failures or incomplete coverage: "
            f"failures={failures}, api_failures={api_failures}, "
            f"operational_api_failures={operational_api_failures}, "
            f"missing={missing}, extra={extra}"
        )
    return {
        "selected_pages": selected,
        "source_evaluation_pages": source_pages,
        "submitted_prediction_pages": submitted,
        "matched_prediction_pages": matched,
        "missing_prediction_pages": missing,
        "extra_prediction_pages": extra,
        "evaluated_pages": evaluated,
        "prediction_jsonl_records": prediction_lines,
        "runner_failures": failures,
        "api_failures": api_failures,
        "operational_api_failures": operational_api_failures,
        "complete": True,
    }


def _build_result(source_root: Path, destination: Path, result_name: str) -> dict[str, Any]:
    baseline_path = source_root / "baseline-run.json"
    score_path = source_root / "modern-score.json"
    baseline = _load_mapping(baseline_path, "baseline-run.json")
    score = _load_mapping(score_path, "modern-score.json")
    if baseline.get("limited_smoke_run") is not False:
        raise PublicResultsError(f"{result_name} is not a full baseline run")
    track_runs = _mapping(baseline.get("tracks"), f"{result_name}.tracks")
    required_tracks = set(DEFAULT_HEADLINE_TRACKS)
    if set(track_runs) != required_tracks:
        raise PublicResultsError(
            f"{result_name} must contain exactly the five Modern headline tracks"
        )
    suite_fingerprint = _assert_sha256(
        baseline.get("suite_fingerprint"), f"{result_name}.suite_fingerprint"
    )
    if score.get("suite_fingerprint") != suite_fingerprint:
        raise PublicResultsError(f"{result_name} score and runner suite fingerprints differ")
    if (
        set(score.get("required_tracks", [])) != required_tracks
        or score.get("missing_tracks") != []
    ):
        raise PublicResultsError(f"{result_name} modern score is missing required tracks")
    admission = _mapping(score.get("score_admission"), f"{result_name}.score_admission")
    expected_admission = {
        "status": "verified_recomputed",
        "artifact_hashes_verified": True,
        "component_roots_verified": True,
        "blind_input_contract_verified": True,
        "metrics_recomputed": True,
        "gold_assistance": False,
        "oracle_layout": False,
    }
    for field, expected in expected_admission.items():
        if admission.get(field) != expected:
            raise PublicResultsError(f"{result_name} score admission {field} must be {expected!r}")
    _assert_public_value(score, f"{result_name}.modern_score")

    source_artifacts: dict[str, dict[str, object]] = {}
    baseline_measured = _measure_file(baseline_path)
    score_measured = _measure_file(score_path)
    source_artifacts["baseline-run.json"] = _artifact_record(baseline_measured)
    public_score_path = f"results/{result_name}/modern-score.json"
    source_artifacts["modern-score.json"] = _artifact_record(
        score_measured, included_as=public_score_path
    )
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(score_path, destination / "modern-score.json")

    common_suite: dict[str, Any] | None = None
    common_model: dict[str, Any] | None = None
    common_runtime: dict[str, Any] | None = None
    public_tracks: dict[str, dict[str, Any]] = {}
    total_pages = 0
    total_failures = 0

    baseline_model = _mapping(baseline.get("model"), f"{result_name}.model")
    for track_id in DEFAULT_HEADLINE_TRACKS:
        track_run = _mapping(track_runs[track_id], f"{result_name}.tracks.{track_id}")
        report_dir = source_root / "reports" / track_id
        manifest_path = report_dir / "run_manifest.json"
        manifest = _load_mapping(manifest_path, f"{track_id} run manifest")
        manifest_model = _mapping(manifest.get("model"), f"{track_id}.model")
        identity = _model_identity(manifest_model)
        for field in ("system_id", "family", "name", "version"):
            if baseline_model.get(field) != identity.get(field):
                raise PublicResultsError(f"{track_id} runner and report model {field} differ")
        if common_model is None:
            common_model = identity
        elif common_model != identity:
            raise PublicResultsError(f"{result_name} model identity differs across tracks")

        runtime = _runtime_identity(manifest)
        if common_runtime is None:
            common_runtime = runtime
        elif common_runtime != runtime:
            raise PublicResultsError(f"{result_name} runtime identity differs across tracks")

        suite_evidence = _mapping(manifest.get("benchmark_suite"), f"{track_id}.benchmark_suite")
        observed_common = {field: suite_evidence.get(field) for field in _SUITE_COMMON_FIELDS}
        for field in ("suite_fingerprint", "profile_fingerprint", "registry_fingerprint"):
            _assert_sha256(observed_common.get(field), f"{track_id}.{field}")
        if observed_common["suite_fingerprint"] != suite_fingerprint:
            raise PublicResultsError(f"{track_id} is bound to a different suite")
        if common_suite is None:
            common_suite = observed_common
        elif common_suite != observed_common:
            raise PublicResultsError(f"{result_name} suite identity differs across tracks")
        if suite_evidence.get("track_id") != track_id:
            raise PublicResultsError(f"{track_id} run manifest declares another track")

        configuration = _mapping(manifest.get("configuration"), f"{track_id}.configuration")
        if configuration.get("official_track_id") != track_id:
            raise PublicResultsError(f"{track_id} report has the wrong official track id")
        if manifest_model.get("gold_assistance") is not False:
            raise PublicResultsError(f"{track_id} used gold assistance")
        if manifest_model.get("oracle_layout") is not False:
            raise PublicResultsError(f"{track_id} used oracle layout")

        declared_artifacts = _mapping(manifest.get("artifacts"), f"{track_id}.artifacts")
        if not _REQUIRED_REPORT_ARTIFACTS.issubset(declared_artifacts):
            missing = sorted(_REQUIRED_REPORT_ARTIFACTS - set(declared_artifacts))
            raise PublicResultsError(f"{track_id} report manifest lacks artifacts: {missing}")
        metrics: dict[str, Any] | None = None
        for artifact_name in sorted(declared_artifacts):
            declaration = _mapping(
                declared_artifacts[artifact_name], f"{track_id}.artifacts.{artifact_name}"
            )
            relative = _relative_report_artifact_path(
                declaration.get("path"), f"{track_id}.artifacts.{artifact_name}"
            )
            artifact_path = report_dir / relative
            measured = _verify_declared_artifact(
                artifact_path, declaration, f"{track_id}.artifacts.{artifact_name}"
            )
            logical_path = f"reports/{track_id}/{relative.as_posix()}"
            included_as = None
            if artifact_name == "metrics":
                metrics = _load_mapping(artifact_path, f"{track_id} metrics")
                _assert_public_value(metrics, f"{result_name}.{track_id}.metrics")
                public_metrics_path = f"results/{result_name}/tracks/{track_id}/metrics.json"
                metrics_destination = destination / "tracks" / track_id / "metrics.json"
                metrics_destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifact_path, metrics_destination)
                included_as = public_metrics_path
            source_artifacts[logical_path] = _artifact_record(measured, included_as=included_as)

        if metrics is None:
            raise PublicResultsError(f"{track_id} has no metrics artifact")
        run_manifest_measured = _measure_file(manifest_path)
        source_artifacts[f"reports/{track_id}/run_manifest.json"] = _artifact_record(
            run_manifest_measured
        )

        prediction_path = source_root / "predictions" / f"{track_id}.jsonl"
        declared_inputs = _mapping(manifest.get("inputs"), f"{track_id}.inputs")
        declared_prediction = _mapping(
            declared_inputs.get("predictions"), f"{track_id}.inputs.predictions"
        )
        prediction_measured = _verify_declared_artifact(
            prediction_path, declared_prediction, f"{track_id}.inputs.predictions"
        )
        runner_prediction = track_run.get("prediction_path")
        if (
            not isinstance(runner_prediction, str)
            or Path(runner_prediction).resolve() != prediction_path.resolve()
        ):
            raise PublicResultsError(f"{track_id} runner points at another prediction artifact")
        source_artifacts[f"predictions/{track_id}.jsonl"] = _artifact_record(prediction_measured)

        coverage = _verify_complete_coverage(track_id, track_run, metrics, prediction_measured)
        total_pages += int(coverage["selected_pages"])
        total_failures += int(coverage["runner_failures"]) + int(coverage["api_failures"])
        public_tracks[track_id] = {
            "track_id": track_id,
            "track_version": configuration.get("official_track_version"),
            "track_fingerprint": configuration.get("official_track_fingerprint"),
            "evaluation_split": track_run.get("evaluation_split"),
            "input_mode": manifest_model.get("input_mode"),
            "oracle_layout": False,
            "gold_assistance": False,
            "dataset_fingerprint": suite_evidence.get("dataset_fingerprint"),
            "dataset_gold_sha256": suite_evidence.get("gold_sha256"),
            "certification_sha256": suite_evidence.get("certification_sha256"),
            "run_created_at_utc": manifest.get("created_at_utc"),
            "coverage_and_failures": coverage,
            "metrics_file": f"tracks/{track_id}/metrics.json",
            "metrics_sha256": source_artifacts[
                f"reports/{track_id}/{declared_artifacts['metrics']['path']}"
            ]["sha256"],
        }
        for field in (
            "track_fingerprint",
            "dataset_fingerprint",
            "dataset_gold_sha256",
            "certification_sha256",
        ):
            _assert_sha256(public_tracks[track_id].get(field), f"{track_id}.{field}")

    assert common_suite is not None
    assert common_model is not None
    assert common_runtime is not None
    if score.get("profile_id") != common_suite["profile_id"]:
        raise PublicResultsError(f"{result_name} score and suite profile ids differ")
    if score.get("profile_fingerprint") != common_suite["profile_fingerprint"]:
        raise PublicResultsError(f"{result_name} score and suite profile fingerprints differ")

    payload = {
        "schema_version": "1.0",
        "pack_type": "HebOCRBench compact public baseline result",
        "result_name": result_name,
        "benchmark": "HebOCRBench Modern Hebrew",
        "compact_not_self_contained": True,
        "verification_boundary": (
            "Exact aggregate metrics and the official Modern score are included. "
            "Raw predictions, page-level/error reports, HTML, CSV summaries, run manifests, "
            "gold, images, and organizer material are omitted. The source artifact attestation "
            "can verify separately obtained complete-run bytes, but this compact pack alone "
            "cannot recompute the metrics."
        ),
        "model": common_model,
        "runtime": {
            **common_runtime,
            "runner_engine": baseline.get("engine"),
            "workers": baseline.get("workers"),
        },
        "suite": common_suite,
        "modern_score": score,
        "modern_score_file": "modern-score.json",
        "modern_score_sha256": score_measured["sha256"],
        "complete_run_summary": {
            "track_count": len(public_tracks),
            "evaluated_items": total_pages,
            "runner_and_api_failures": total_failures,
            "all_tracks_complete": True,
            "score_status": score.get("status"),
            "score_admission_status": admission.get("status"),
        },
        "tracks": public_tracks,
        "source_artifact_attestation": {
            "scope": "all baseline runner, prediction, and evaluator report artifacts",
            "artifact_count": len(source_artifacts),
            "artifacts": dict(sorted(source_artifacts.items())),
        },
    }
    _assert_public_value(payload, f"{result_name}.result")
    write_json(destination / "result.json", payload)
    return payload


def _extension_track_contract(
    track_id: str,
    engine: object,
) -> tuple[str, bool, bool]:
    """Return the only accepted input/oracle/gold-assistance contract."""

    if track_id != _HISTORICAL_PRESS_TRACK:
        return "blind_whole_line_image", False, False
    if engine == "tesseract":
        return "oracle_layout_line_crops", True, True
    if engine == "surya2-llamacpp":
        return "blind_full_page_image", False, False
    raise PublicResultsError(
        "historical Hebrew press extension requires Tesseract oracle crops "
        "or blind full-page Surya OCR 2"
    )


def _consistent_optional_field(
    expected: object,
    label: str,
    *sources: Mapping[str, Any],
) -> None:
    for source in sources:
        if label in source and source[label] != expected:
            raise PublicResultsError(
                f"extension {label} differs across runner/report evidence: "
                f"expected {expected!r}, got {source[label]!r}"
            )


def _build_extension_result(
    source_root: Path,
    destination: Path,
    result_name: str,
) -> dict[str, Any]:
    baseline_path = source_root / "separate-baseline-run.json"
    baseline = _load_mapping(baseline_path, "separate-baseline-run.json")
    if baseline.get("benchmark") != "HebOCRBench separate extensions and diagnostics":
        raise PublicResultsError(f"{result_name} is not an extension baseline run")
    if baseline.get("limited_smoke_run") is not False:
        raise PublicResultsError(f"{result_name} is not a full extension baseline run")
    if baseline.get("reporting_policy") != _EXTENSION_REPORTING_POLICY:
        raise PublicResultsError(
            f"{result_name} extension reporting policy permits ranking or blending"
        )
    groups = _mapping(baseline.get("groups"), f"{result_name}.groups")
    expected_groups = {
        "separate_real_extensions": list(_REAL_EXTENSION_TRACKS),
        "synthetic_diagnostics": list(_SYNTHETIC_DIAGNOSTIC_TRACKS),
    }
    if dict(groups) != expected_groups:
        raise PublicResultsError(
            f"{result_name} must contain exactly the five canonical extension tracks"
        )
    track_runs = _mapping(baseline.get("tracks"), f"{result_name}.tracks")
    if set(track_runs) != set(_ALL_EXTENSION_TRACKS):
        raise PublicResultsError(
            f"{result_name} must contain exactly the five canonical extension tracks"
        )
    engine = baseline.get("engine")
    if engine not in {"tesseract", "surya2-llamacpp"}:
        raise PublicResultsError(f"unsupported extension engine: {engine!r}")
    workers = _integer(baseline.get("workers"), f"{result_name}.workers")
    if workers <= 0:
        raise PublicResultsError(f"{result_name}.workers must be positive")
    baseline_model = _mapping(baseline.get("model"), f"{result_name}.model")

    source_artifacts: dict[str, dict[str, object]] = {
        "separate-baseline-run.json": _artifact_record(_measure_file(baseline_path))
    }
    common_model: dict[str, Any] | None = None
    common_runtime: dict[str, Any] | None = None
    public_tracks: dict[str, dict[str, Any]] = {}
    total_pages = 0
    total_failures = 0
    destination.mkdir(parents=True, exist_ok=True)

    for track_id in _ALL_EXTENSION_TRACKS:
        track_run = _mapping(track_runs[track_id], f"{result_name}.tracks.{track_id}")
        expected_class = (
            "synthetic_diagnostic"
            if track_id in _SYNTHETIC_DIAGNOSTIC_TRACKS
            else "separate_real_extension"
        )
        expected_synthetic = track_id in _SYNTHETIC_DIAGNOSTIC_TRACKS
        expected_input, expected_oracle, expected_gold_assistance = _extension_track_contract(
            track_id, engine
        )

        report_dir = source_root / "reports" / track_id
        manifest_path = report_dir / "run_manifest.json"
        manifest = _load_mapping(manifest_path, f"{track_id} run manifest")
        manifest_model = _mapping(manifest.get("model"), f"{track_id}.model")
        identity = _model_identity(manifest_model)
        for field, expected in baseline_model.items():
            if manifest_model.get(field) != expected:
                raise PublicResultsError(f"{track_id} runner and report model {field} differ")
        if common_model is None:
            common_model = identity
        elif common_model != identity:
            raise PublicResultsError(f"{result_name} model identity differs across tracks")

        runtime = _runtime_identity(manifest)
        if common_runtime is None:
            common_runtime = runtime
        elif common_runtime != runtime:
            raise PublicResultsError(f"{result_name} runtime identity differs across tracks")

        configuration = _mapping(manifest.get("configuration"), f"{track_id}.configuration")
        if configuration.get("official_track_id") != track_id:
            raise PublicResultsError(f"{track_id} report has the wrong official track id")
        if configuration.get("reporting_class") != expected_class:
            raise PublicResultsError(f"{track_id} has the wrong reporting class")
        if configuration.get("synthetic_diagnostic") is not expected_synthetic:
            raise PublicResultsError(f"{track_id} synthetic diagnostic flag differs")
        if configuration.get("modern_headline_eligible") is not False:
            raise PublicResultsError(f"{track_id} cannot be eligible for the Modern headline")
        if configuration.get("baseline_workers") != workers:
            raise PublicResultsError(f"{track_id} report worker count differs")
        if configuration.get("limited_smoke_run") is not False:
            raise PublicResultsError(f"{track_id} report is a smoke run")
        _consistent_optional_field(
            expected_class,
            "reporting_class",
            track_run,
            configuration,
        )
        _consistent_optional_field(
            expected_synthetic,
            "diagnostic_only",
            track_run,
        )
        _consistent_optional_field(
            False,
            "modern_headline_eligible",
            track_run,
            configuration,
        )
        _consistent_optional_field(
            expected_input,
            "input_mode",
            track_run,
            configuration,
            manifest_model,
        )
        _consistent_optional_field(
            expected_oracle,
            "oracle_layout",
            track_run,
            configuration,
            manifest_model,
        )
        _consistent_optional_field(
            expected_gold_assistance,
            "gold_assistance",
            track_run,
            configuration,
            manifest_model,
        )
        adapter = manifest_model.get("adapter")
        if not isinstance(adapter, str) or not adapter:
            raise PublicResultsError(f"{track_id} report model has no adapter identity")
        _consistent_optional_field(adapter, "adapter", track_run)

        for summary_field, configuration_field in (
            ("evaluation_split", "evaluation_split"),
            ("source_evaluation_pages", "source_evaluation_pages"),
            ("selected_pages", "evaluated_evaluation_pages"),
            ("selection_sha256", "evaluation_selection_sha256"),
        ):
            if track_run.get(summary_field) != configuration.get(configuration_field):
                raise PublicResultsError(f"{track_id} runner and report {summary_field} differ")
        selection_sha = _assert_sha256(
            track_run.get("selection_sha256"), f"{track_id}.selection_sha256"
        )
        track_fingerprint = _assert_sha256(
            configuration.get("official_track_fingerprint"),
            f"{track_id}.official_track_fingerprint",
        )

        declared_artifacts = _mapping(manifest.get("artifacts"), f"{track_id}.artifacts")
        if not _REQUIRED_REPORT_ARTIFACTS.issubset(declared_artifacts):
            missing = sorted(_REQUIRED_REPORT_ARTIFACTS - set(declared_artifacts))
            raise PublicResultsError(f"{track_id} report manifest lacks artifacts: {missing}")
        metrics: dict[str, Any] | None = None
        for artifact_name in sorted(declared_artifacts):
            declaration = _mapping(
                declared_artifacts[artifact_name], f"{track_id}.artifacts.{artifact_name}"
            )
            relative = _relative_report_artifact_path(
                declaration.get("path"), f"{track_id}.artifacts.{artifact_name}"
            )
            artifact_path = report_dir / relative
            measured = _verify_declared_artifact(
                artifact_path, declaration, f"{track_id}.artifacts.{artifact_name}"
            )
            logical_path = f"reports/{track_id}/{relative.as_posix()}"
            included_as = None
            if artifact_name == "metrics":
                metrics = _load_mapping(artifact_path, f"{track_id} metrics")
                _assert_public_value(metrics, f"{result_name}.{track_id}.metrics")
                public_metrics_path = (
                    f"extension-results/{result_name}/tracks/{track_id}/metrics.json"
                )
                metrics_destination = destination / "tracks" / track_id / "metrics.json"
                metrics_destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(artifact_path, metrics_destination)
                included_as = public_metrics_path
            source_artifacts[logical_path] = _artifact_record(measured, included_as=included_as)
        if metrics is None:
            raise PublicResultsError(f"{track_id} has no metrics artifact")
        source_artifacts[f"reports/{track_id}/run_manifest.json"] = _artifact_record(
            _measure_file(manifest_path)
        )

        inputs = _mapping(manifest.get("inputs"), f"{track_id}.inputs")
        declared_prediction = _mapping(inputs.get("predictions"), f"{track_id}.inputs.predictions")
        prediction_path = source_root / "predictions" / f"{track_id}.jsonl"
        prediction_measured = _verify_declared_artifact(
            prediction_path, declared_prediction, f"{track_id}.inputs.predictions"
        )
        runner_prediction = track_run.get("prediction_path")
        if (
            not isinstance(runner_prediction, str)
            or Path(runner_prediction).resolve() != prediction_path.resolve()
        ):
            raise PublicResultsError(f"{track_id} runner points at another prediction artifact")
        source_artifacts[f"predictions/{track_id}.jsonl"] = _artifact_record(prediction_measured)

        declared_gold = _mapping(inputs.get("gold"), f"{track_id}.inputs.gold")
        declared_gold_path = declared_gold.get("path")
        if not isinstance(declared_gold_path, str) or not declared_gold_path:
            raise PublicResultsError(f"{track_id}.inputs.gold.path is missing")
        gold_path = Path(declared_gold_path).resolve()
        if gold_path.name != "gold.jsonl":
            raise PublicResultsError(f"{track_id} gold input is not gold.jsonl")
        gold_measured = _verify_declared_artifact(
            gold_path, declared_gold, f"{track_id}.inputs.gold"
        )
        source_artifacts[f"inputs/{track_id}/gold.jsonl"] = _artifact_record(gold_measured)

        coverage = _verify_complete_coverage(track_id, track_run, metrics, prediction_measured)
        total_pages += int(coverage["selected_pages"])
        total_failures += int(coverage["runner_failures"]) + int(coverage["api_failures"])
        public_tracks[track_id] = {
            "track_id": track_id,
            "track_version": configuration.get("official_track_version"),
            "track_fingerprint": track_fingerprint,
            "evaluation_split": track_run.get("evaluation_split"),
            "selection_sha256": selection_sha,
            "reporting_class": expected_class,
            "synthetic_diagnostic": expected_synthetic,
            "diagnostic_only": expected_synthetic,
            "ranked_in_pack": False,
            "included_in_combined_score": False,
            "modern_headline_eligible": False,
            "input_mode": expected_input,
            "oracle_layout": expected_oracle,
            "gold_assistance": expected_gold_assistance,
            "adapter": adapter,
            "dataset_gold_sha256": gold_measured["sha256"],
            "run_created_at_utc": manifest.get("created_at_utc"),
            "coverage_and_failures": coverage,
            "metrics_file": f"tracks/{track_id}/metrics.json",
            "metrics_sha256": source_artifacts[
                f"reports/{track_id}/{declared_artifacts['metrics']['path']}"
            ]["sha256"],
        }

    assert common_model is not None
    assert common_runtime is not None
    payload = {
        "schema_version": "1.0",
        "pack_type": "HebOCRBench compact separate extension and diagnostic result",
        "result_name": result_name,
        "benchmark": "HebOCRBench separate extensions and diagnostics",
        "compact_not_self_contained": True,
        "verification_boundary": (
            "Exact aggregate metrics are included separately per track. Raw predictions, "
            "page-level/error reports, HTML, CSV summaries, run manifests, gold, images, "
            "and organizer material are omitted. Source artifact attestations can verify "
            "separately obtained complete-run bytes; this compact pack cannot recompute metrics."
        ),
        "reporting_policy": dict(_EXTENSION_REPORTING_POLICY),
        "pack_ranking_enabled": False,
        "model": common_model,
        "runtime": {
            **common_runtime,
            "runner_engine": engine,
            "workers": workers,
        },
        "groups": expected_groups,
        "complete_run_summary": {
            "track_count": len(public_tracks),
            "real_extension_track_count": len(_REAL_EXTENSION_TRACKS),
            "synthetic_diagnostic_track_count": len(_SYNTHETIC_DIAGNOSTIC_TRACKS),
            "evaluated_items": total_pages,
            "runner_and_api_failures": total_failures,
            "all_tracks_complete": True,
            "combined_score": None,
            "ranked_in_pack": False,
            "modern_headline_blending": False,
        },
        "tracks": public_tracks,
        "source_artifact_attestation": {
            "scope": (
                "all extension runner, prediction, evaluator report, and hashed gold-input "
                "artifacts"
            ),
            "artifact_count": len(source_artifacts),
            "artifacts": dict(sorted(source_artifacts.items())),
        },
    }
    _assert_public_value(payload, f"{result_name}.extension_result")
    write_json(destination / "result.json", payload)
    return payload


def _readme(
    result_payloads: Mapping[str, Mapping[str, Any]],
    extension_payloads: Mapping[str, Mapping[str, Any]],
) -> str:
    rows = []
    for name, payload in sorted(result_payloads.items()):
        model = _mapping(payload["model"], f"{name}.model")
        summary = _mapping(payload["complete_run_summary"], f"{name}.summary")
        rows.append(
            f"| `{name}` | {model['name']} | `{summary['score_status']}` | "
            f"{summary['evaluated_items']} | {summary['runner_and_api_failures']} |"
        )
    extension_rows = []
    for name, payload in sorted(extension_payloads.items()):
        model = _mapping(payload["model"], f"{name}.model")
        summary = _mapping(payload["complete_run_summary"], f"{name}.summary")
        extension_rows.append(
            f"| `{name}` | {model['name']} | {summary['real_extension_track_count']} | "
            f"{summary['synthetic_diagnostic_track_count']} | {summary['evaluated_items']} | "
            f"{summary['runner_and_api_failures']} |"
        )
    modern_table = (
        "| Result | Model | Status | Evaluated items | Runner/API failures |\n"
        "|---|---|---:|---:|---:|\n" + ("\n".join(rows) if rows else "| _none_ | — | — | — | — |")
    )
    extension_table = (
        "| Result | Model | Real extensions | Synthetic diagnostics | Evaluated items | "
        "Runner/API failures |\n"
        "|---|---|---:|---:|---:|---:|\n"
        + ("\n".join(extension_rows) if extension_rows else "| _none_ | — | — | — | — | — |")
    )
    return (
        "# HebOCRBench v1.0.0 compact baseline results\n\n"
        "This publication contains exact aggregate `metrics.json` files and exact official "
        "`modern-score.json` files from completed, blind Modern Hebrew baseline runs. Every "
        "source run artifact is bound by SHA-256 and byte size in each `result.json`.\n\n"
        + modern_table
        + "\n\n## Separate extensions and diagnostics\n\n"
        "Extension metrics are reported per track only. Synthetic diagnostics are not "
        "rankable, and no extension is blended into the Modern headline or a combined score.\n\n"
        + extension_table
        + "\n\n"
        "## Deliberate compactness boundary\n\n"
        "This pack is **not self-contained**. It does not include raw predictions, page-level "
        "records, error details, HTML reports, CSV summaries, run manifests, source images, "
        "gold annotations, organizer ID maps, or key material. Therefore it cannot by itself "
        "recompute a score. Its attestations can verify the hashes of complete artifacts when "
        "those artifacts are obtained separately.\n\n"
        "No aggregate score combines separate Hebrew families or extension tracks.\n"
    )


def _payload_manifest(
    root: Path,
    result_payloads: Mapping[str, Mapping[str, Any]],
    extension_payloads: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    artifacts: dict[str, dict[str, object]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "PACK-MANIFEST.json":
            continue
        relative = path.relative_to(root).as_posix()
        artifacts[relative] = _measure_file(path)
    result_index = {
        name: {
            "result_file": f"results/{name}/result.json",
            "system_id": _mapping(payload["model"], f"{name}.model")["system_id"],
            "suite_fingerprint": _mapping(payload["suite"], f"{name}.suite")["suite_fingerprint"],
            "profile_fingerprint": _mapping(payload["suite"], f"{name}.suite")[
                "profile_fingerprint"
            ],
            "status": _mapping(payload["complete_run_summary"], f"{name}.summary")["score_status"],
        }
        for name, payload in sorted(result_payloads.items())
    }
    extension_index = {
        name: {
            "result_file": f"extension-results/{name}/result.json",
            "system_id": _mapping(payload["model"], f"{name}.model")["system_id"],
            "track_count": _mapping(payload["complete_run_summary"], f"{name}.summary")[
                "track_count"
            ],
            "evaluated_items": _mapping(payload["complete_run_summary"], f"{name}.summary")[
                "evaluated_items"
            ],
            "combined_score": None,
            "ranked_in_pack": False,
            "modern_headline_blending": False,
        }
        for name, payload in sorted(extension_payloads.items())
    }
    basis: dict[str, Any] = {
        "schema_version": "1.0",
        "pack_type": "HebOCRBench compact public baseline results",
        "benchmark_version": "1.0.0",
        "compact_not_self_contained": True,
        "privacy_boundary": {
            "contains_raw_predictions": False,
            "contains_gold": False,
            "contains_images": False,
            "contains_organizer_maps": False,
            "contains_key_material": False,
            "contains_local_absolute_paths": False,
        },
        "results": result_index,
        "extension_results": extension_index,
        "payload_artifacts": artifacts,
    }
    encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return {**basis, "pack_fingerprint": hashlib.sha256(encoded).hexdigest()}


def verify_public_results_pack(root: str | Path) -> dict[str, Any]:
    """Verify a compact pack without access to the private complete-run directories."""

    pack_root = Path(root).resolve()
    if not pack_root.is_dir():
        raise PublicResultsError(f"public results pack does not exist: {pack_root}")
    for path in pack_root.rglob("*"):
        if path.is_symlink():
            raise PublicResultsError(f"public results pack must not contain symlinks: {path}")
    manifest_path = pack_root / "PACK-MANIFEST.json"
    manifest = _load_mapping(manifest_path, "PACK-MANIFEST.json")
    fingerprint = _assert_sha256(manifest.get("pack_fingerprint"), "pack_fingerprint")
    basis = {key: value for key, value in manifest.items() if key != "pack_fingerprint"}
    encoded = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    if hashlib.sha256(encoded).hexdigest() != fingerprint:
        raise PublicResultsError("public results pack fingerprint is invalid")
    if manifest.get("compact_not_self_contained") is not True:
        raise PublicResultsError("public results pack must disclose that it is not self-contained")
    privacy = _mapping(manifest.get("privacy_boundary"), "privacy_boundary")
    if not privacy or any(value is not False for value in privacy.values()):
        raise PublicResultsError("public results pack privacy boundary must be fail-closed")

    declared_payload = _mapping(manifest.get("payload_artifacts"), "payload_artifacts")
    actual_paths = {
        path.relative_to(pack_root).as_posix()
        for path in pack_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual_paths != set(declared_payload):
        missing = sorted(set(declared_payload) - actual_paths)
        extra = sorted(actual_paths - set(declared_payload))
        raise PublicResultsError(
            f"public results payload file set differs: missing={missing}, extra={extra}"
        )
    for relative, declaration_value in declared_payload.items():
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise PublicResultsError(f"unsafe public payload path: {relative!r}")
        declaration = _mapping(declaration_value, f"payload_artifacts.{relative}")
        measured = _measure_file(pack_root / relative)
        if measured != declaration:
            raise PublicResultsError(f"public payload hash or size differs: {relative}")

    expected_paths = {"README.md"}
    results = _mapping(manifest.get("results"), "results")
    for name, index_value in results.items():
        if not isinstance(name, str) or not _RESULT_NAME_RE.fullmatch(name):
            raise PublicResultsError(f"invalid result index name: {name!r}")
        index = _mapping(index_value, f"results.{name}")
        expected_result_path = f"results/{name}/result.json"
        if index.get("result_file") != expected_result_path:
            raise PublicResultsError(f"result index path differs for {name}")
        result = _load_mapping(pack_root / expected_result_path, f"{name} result")
        if (
            result.get("result_name") != name
            or result.get("compact_not_self_contained") is not True
        ):
            raise PublicResultsError(f"invalid compact result identity for {name}")
        model = _mapping(result.get("model"), f"{name}.model")
        suite = _mapping(result.get("suite"), f"{name}.suite")
        summary = _mapping(result.get("complete_run_summary"), f"{name}.summary")
        expected_index = {
            "result_file": expected_result_path,
            "system_id": model.get("system_id"),
            "suite_fingerprint": suite.get("suite_fingerprint"),
            "profile_fingerprint": suite.get("profile_fingerprint"),
            "status": summary.get("score_status"),
        }
        if dict(index) != expected_index:
            raise PublicResultsError(f"result index metadata differs for {name}")
        expected_paths.add(expected_result_path)

        score_relative = f"results/{name}/{result.get('modern_score_file')}"
        score = _load_mapping(pack_root / score_relative, f"{name} modern score")
        if score != result.get("modern_score"):
            raise PublicResultsError(f"embedded and copied Modern scores differ for {name}")
        if _measure_file(pack_root / score_relative)["sha256"] != result.get("modern_score_sha256"):
            raise PublicResultsError(f"Modern score hash differs for {name}")
        expected_paths.add(score_relative)

        tracks = _mapping(result.get("tracks"), f"{name}.tracks")
        if set(tracks) != set(DEFAULT_HEADLINE_TRACKS):
            raise PublicResultsError(f"compact result lacks Modern tracks for {name}")
        for track_id, track_value in tracks.items():
            track = _mapping(track_value, f"{name}.tracks.{track_id}")
            relative_within_result = track.get("metrics_file")
            if not isinstance(relative_within_result, str):
                raise PublicResultsError(f"metrics path missing for {name}/{track_id}")
            expected_metrics = f"tracks/{track_id}/metrics.json"
            if relative_within_result != expected_metrics:
                raise PublicResultsError(f"metrics path differs for {name}/{track_id}")
            metrics_relative = f"results/{name}/{expected_metrics}"
            if _measure_file(pack_root / metrics_relative)["sha256"] != track.get("metrics_sha256"):
                raise PublicResultsError(f"metrics hash differs for {name}/{track_id}")
            expected_paths.add(metrics_relative)

        attestation = _mapping(
            result.get("source_artifact_attestation"), f"{name}.source_artifact_attestation"
        )
        source_artifacts = _mapping(attestation.get("artifacts"), f"{name}.source_artifacts")
        if attestation.get("artifact_count") != len(source_artifacts):
            raise PublicResultsError(f"source artifact count differs for {name}")
        included_paths: set[str] = set()
        for logical_path, artifact_value in source_artifacts.items():
            if not isinstance(logical_path, str) or Path(logical_path).is_absolute():
                raise PublicResultsError(f"unsafe source artifact path for {name}")
            artifact = _mapping(artifact_value, f"{name}.source_artifacts.{logical_path}")
            _assert_sha256(artifact.get("sha256"), f"{name}.{logical_path}.sha256")
            _integer(artifact.get("size_bytes"), f"{name}.{logical_path}.size_bytes")
            included = artifact.get("included_in_compact_pack")
            if included not in {True, False}:
                raise PublicResultsError(f"invalid inclusion flag for {name}/{logical_path}")
            if included:
                included_as = artifact.get("included_as")
                if not isinstance(included_as, str) or included_as not in declared_payload:
                    raise PublicResultsError(f"included source artifact is absent for {name}")
                declaration = _mapping(declared_payload[included_as], included_as)
                if declaration.get("sha256") != artifact.get("sha256"):
                    raise PublicResultsError(f"included source artifact hash differs for {name}")
                included_paths.add(included_as)
            elif "included_as" in artifact:
                raise PublicResultsError(f"omitted source artifact has a public path for {name}")
        expected_included = {
            score_relative,
            *(f"results/{name}/tracks/{track_id}/metrics.json" for track_id in tracks),
        }
        if included_paths != expected_included:
            raise PublicResultsError(f"source inclusion attestation is incomplete for {name}")

    extension_results = _mapping(manifest.get("extension_results"), "extension_results")
    for name, index_value in extension_results.items():
        if not isinstance(name, str) or not _RESULT_NAME_RE.fullmatch(name):
            raise PublicResultsError(f"invalid extension result index name: {name!r}")
        index = _mapping(index_value, f"extension_results.{name}")
        result_relative = f"extension-results/{name}/result.json"
        result = _load_mapping(pack_root / result_relative, f"{name} extension result")
        if (
            result.get("result_name") != name
            or result.get("compact_not_self_contained") is not True
            or result.get("reporting_policy") != _EXTENSION_REPORTING_POLICY
            or result.get("pack_ranking_enabled") is not False
        ):
            raise PublicResultsError(f"invalid compact extension identity for {name}")
        model = _mapping(result.get("model"), f"extension_results.{name}.model")
        summary = _mapping(result.get("complete_run_summary"), f"extension_results.{name}.summary")
        expected_index = {
            "result_file": result_relative,
            "system_id": model.get("system_id"),
            "track_count": summary.get("track_count"),
            "evaluated_items": summary.get("evaluated_items"),
            "combined_score": None,
            "ranked_in_pack": False,
            "modern_headline_blending": False,
        }
        if dict(index) != expected_index:
            raise PublicResultsError(f"extension result index metadata differs for {name}")
        expected_summary = {
            "track_count": 5,
            "real_extension_track_count": 3,
            "synthetic_diagnostic_track_count": 2,
            "runner_and_api_failures": 0,
            "all_tracks_complete": True,
            "combined_score": None,
            "ranked_in_pack": False,
            "modern_headline_blending": False,
        }
        for field, expected in expected_summary.items():
            if summary.get(field) != expected:
                raise PublicResultsError(
                    f"extension result {name} summary {field} must be {expected!r}"
                )
        expected_paths.add(result_relative)

        groups = _mapping(result.get("groups"), f"extension_results.{name}.groups")
        if dict(groups) != {
            "separate_real_extensions": list(_REAL_EXTENSION_TRACKS),
            "synthetic_diagnostics": list(_SYNTHETIC_DIAGNOSTIC_TRACKS),
        }:
            raise PublicResultsError(f"extension result groups differ for {name}")
        tracks = _mapping(result.get("tracks"), f"extension_results.{name}.tracks")
        if set(tracks) != set(_ALL_EXTENSION_TRACKS):
            raise PublicResultsError(f"extension result lacks canonical tracks for {name}")
        included_metric_paths: set[str] = set()
        evaluated_items = 0
        for track_id, track_value in tracks.items():
            track = _mapping(track_value, f"extension_results.{name}.tracks.{track_id}")
            expected_synthetic = track_id in _SYNTHETIC_DIAGNOSTIC_TRACKS
            expected_class = (
                "synthetic_diagnostic" if expected_synthetic else "separate_real_extension"
            )
            expected_flags = {
                "reporting_class": expected_class,
                "synthetic_diagnostic": expected_synthetic,
                "diagnostic_only": expected_synthetic,
                "ranked_in_pack": False,
                "included_in_combined_score": False,
                "modern_headline_eligible": False,
            }
            for field, expected in expected_flags.items():
                if track.get(field) != expected:
                    raise PublicResultsError(
                        f"extension result {name}/{track_id} {field} must be {expected!r}"
                    )
            runtime = _mapping(result.get("runtime"), f"extension_results.{name}.runtime")
            expected_input, expected_oracle, expected_gold = _extension_track_contract(
                track_id, runtime.get("runner_engine")
            )
            if (
                track.get("input_mode") != expected_input
                or track.get("oracle_layout") is not expected_oracle
                or track.get("gold_assistance") is not expected_gold
            ):
                raise PublicResultsError(
                    f"extension result input/oracle contract differs for {name}/{track_id}"
                )
            metrics_file = track.get("metrics_file")
            expected_metrics = f"tracks/{track_id}/metrics.json"
            if metrics_file != expected_metrics:
                raise PublicResultsError(f"extension metrics path differs for {name}/{track_id}")
            metrics_relative = f"extension-results/{name}/{expected_metrics}"
            if _measure_file(pack_root / metrics_relative)["sha256"] != track.get("metrics_sha256"):
                raise PublicResultsError(f"extension metrics hash differs for {name}/{track_id}")
            expected_paths.add(metrics_relative)
            included_metric_paths.add(metrics_relative)
            coverage = _mapping(
                track.get("coverage_and_failures"),
                f"extension_results.{name}.{track_id}.coverage",
            )
            if coverage.get("complete") is not True:
                raise PublicResultsError(f"extension coverage is incomplete for {name}/{track_id}")
            for failure_field in (
                "missing_prediction_pages",
                "extra_prediction_pages",
                "runner_failures",
                "api_failures",
                "operational_api_failures",
            ):
                if coverage.get(failure_field) != 0:
                    raise PublicResultsError(
                        f"extension coverage has {failure_field} for {name}/{track_id}"
                    )
            evaluated_items += _integer(
                coverage.get("evaluated_pages"),
                f"extension_results.{name}.{track_id}.evaluated_pages",
            )
        if summary.get("evaluated_items") != evaluated_items:
            raise PublicResultsError(f"extension evaluated item total differs for {name}")

        attestation = _mapping(
            result.get("source_artifact_attestation"),
            f"extension_results.{name}.source_artifact_attestation",
        )
        source_artifacts = _mapping(
            attestation.get("artifacts"), f"extension_results.{name}.source_artifacts"
        )
        if attestation.get("artifact_count") != len(source_artifacts):
            raise PublicResultsError(f"extension source artifact count differs for {name}")
        expected_source_artifacts = {"separate-baseline-run.json"}
        for track_id in tracks:
            expected_source_artifacts.update(
                {
                    f"inputs/{track_id}/gold.jsonl",
                    f"predictions/{track_id}.jsonl",
                    f"reports/{track_id}/errors.jsonl",
                    f"reports/{track_id}/metrics.json",
                    f"reports/{track_id}/per_page.jsonl",
                    f"reports/{track_id}/report.html",
                    f"reports/{track_id}/run_manifest.json",
                    f"reports/{track_id}/summary.csv",
                }
            )
        if set(source_artifacts) != expected_source_artifacts:
            raise PublicResultsError(f"extension source artifact set differs for {name}")
        for track_id, track_value in tracks.items():
            track = _mapping(track_value, f"extension_results.{name}.tracks.{track_id}")
            gold_attestation = _mapping(
                source_artifacts[f"inputs/{track_id}/gold.jsonl"],
                f"extension_results.{name}.gold.{track_id}",
            )
            metrics_attestation = _mapping(
                source_artifacts[f"reports/{track_id}/metrics.json"],
                f"extension_results.{name}.metrics.{track_id}",
            )
            if gold_attestation.get("sha256") != track.get("dataset_gold_sha256"):
                raise PublicResultsError(
                    f"extension gold attestation differs for {name}/{track_id}"
                )
            if metrics_attestation.get("sha256") != track.get("metrics_sha256"):
                raise PublicResultsError(
                    f"extension metrics attestation differs for {name}/{track_id}"
                )
        included_paths: set[str] = set()
        for logical_path, artifact_value in source_artifacts.items():
            if not isinstance(logical_path, str) or Path(logical_path).is_absolute():
                raise PublicResultsError(f"unsafe extension source artifact path for {name}")
            artifact = _mapping(
                artifact_value, f"extension_results.{name}.source_artifacts.{logical_path}"
            )
            _assert_sha256(artifact.get("sha256"), f"{name}.{logical_path}.sha256")
            _integer(artifact.get("size_bytes"), f"{name}.{logical_path}.size_bytes")
            included = artifact.get("included_in_compact_pack")
            if included not in {True, False}:
                raise PublicResultsError(
                    f"invalid extension inclusion flag for {name}/{logical_path}"
                )
            if included:
                included_as = artifact.get("included_as")
                if not isinstance(included_as, str) or included_as not in declared_payload:
                    raise PublicResultsError(
                        f"included extension source artifact is absent for {name}"
                    )
                declaration = _mapping(declared_payload[included_as], included_as)
                if declaration.get("sha256") != artifact.get("sha256"):
                    raise PublicResultsError(
                        f"included extension source artifact hash differs for {name}"
                    )
                included_paths.add(included_as)
            elif "included_as" in artifact:
                raise PublicResultsError(
                    f"omitted extension source artifact has a public path for {name}"
                )
        if included_paths != included_metric_paths:
            raise PublicResultsError(
                f"extension source inclusion attestation is incomplete for {name}"
            )

    if actual_paths != expected_paths:
        raise PublicResultsError("public pack contains an unexpected payload file type")
    _assert_public_value(manifest, "PACK-MANIFEST")
    for json_path in pack_root.rglob("*.json"):
        _assert_public_value(_load_mapping(json_path, "public JSON"), json_path.name)
    readme = (pack_root / "README.md").read_text(encoding="utf-8")
    if any(marker in readme for marker in _LOCAL_PATH_MARKERS):
        raise PublicResultsError("README contains a local path marker")
    return manifest


def build_public_results_pack(
    runs: Mapping[str, str | Path],
    output: str | Path,
    *,
    extension_runs: Mapping[str, str | Path] | None = None,
    clean: bool = False,
) -> dict[str, Any]:
    """Verify Modern/extension baseline runs and build a deterministic compact pack."""

    if not runs and not extension_runs:
        raise PublicResultsError("at least one named baseline or extension run is required")
    normalized_runs: dict[str, Path] = {}
    for name, path in runs.items():
        if not _RESULT_NAME_RE.fullmatch(name):
            raise PublicResultsError(f"invalid result name: {name!r}")
        if name in normalized_runs:
            raise PublicResultsError(f"duplicate result name: {name}")
        source = Path(path).resolve()
        if not source.is_dir():
            raise PublicResultsError(f"baseline run directory does not exist: {source}")
        normalized_runs[name] = source

    normalized_extension_runs: dict[str, Path] = {}
    for name, path in (extension_runs or {}).items():
        if not _RESULT_NAME_RE.fullmatch(name):
            raise PublicResultsError(f"invalid extension result name: {name!r}")
        if name in normalized_extension_runs:
            raise PublicResultsError(f"duplicate extension result name: {name}")
        source = Path(path).resolve()
        if not source.is_dir():
            raise PublicResultsError(f"extension run directory does not exist: {source}")
        normalized_extension_runs[name] = source

    destination = Path(output).resolve()
    for name, source in normalized_runs.items():
        if (
            destination == source
            or destination.is_relative_to(source)
            or source.is_relative_to(destination)
        ):
            raise PublicResultsError(f"output must not overlap source run {name}")
    for name, source in normalized_extension_runs.items():
        if (
            destination == source
            or destination.is_relative_to(source)
            or source.is_relative_to(destination)
        ):
            raise PublicResultsError(f"output must not overlap extension source run {name}")
    if destination.exists():
        if not destination.is_dir():
            raise PublicResultsError("output exists and is not a directory")
        if any(destination.iterdir()) and not clean:
            raise PublicResultsError("output is not empty; pass clean=True explicitly")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        results: dict[str, dict[str, Any]] = {}
        for name, source in sorted(normalized_runs.items()):
            results[name] = _build_result(source, staging / "results" / name, name)
        extension_results: dict[str, dict[str, Any]] = {}
        for name, source in sorted(normalized_extension_runs.items()):
            extension_results[name] = _build_extension_result(
                source, staging / "extension-results" / name, name
            )
        (staging / "README.md").write_text(
            _readme(results, extension_results), encoding="utf-8", newline="\n"
        )
        manifest = _payload_manifest(staging, results, extension_results)
        _assert_public_value(manifest, "PACK-MANIFEST")
        write_json(staging / "PACK-MANIFEST.json", manifest)
        verify_public_results_pack(staging)

        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


__all__ = [
    "PublicResultsError",
    "build_public_results_pack",
    "verify_public_results_pack",
]
