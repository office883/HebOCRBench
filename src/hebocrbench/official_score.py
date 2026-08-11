"""Fail-closed admission for official Modern-Hebrew headline scores.

``metrics.json`` is a useful report artifact, but it is not evidence by itself:
both it and ``run_manifest.json`` are editable files.  This module admits a
headline submission only after rebuilding the suite lock from the certified
roots, re-hashing every report input/artifact, validating the blind prediction
contract, and recomputing every metric from the locked gold and prediction
JSONL bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
from typing import Any

from . import __version__
from .corpus_registry import load_registry
from .evaluator import evaluate_dataset
from .io import load_jsonl, sha256_file
from .modern_score import (
    ModernScoreError,
    combine_modern_track_reports,
    load_modern_report_bundle,
)
from .modern_suite import (
    DEFAULT_HEADLINE_TRACKS,
    ModernSuiteSpec,
    build_modern_suite_lock,
    coerce_modern_suite_lock,
    validate_modern_suite_contract,
)
from .profiles import load_profiles, profile_fingerprint
from .tracks import list_official_tracks, load_track
from .validator import validate_gold_records, validate_prediction_records

OFFICIAL_VERIFICATION_SCHEMA_VERSION = "1.0"
_LINE_TRACK = "modern-line-recognition-v1"
_BIDI_TRACK = "modern-bidi-v1"
_RUNNER = "hebocrbench.baseline_runner"
_RUNNER_SCHEMA_VERSION = "1.0"


class OfficialScoreError(ModernScoreError):
    """A report cannot be admitted to the official Modern headline."""


def _mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OfficialScoreError(f"{location} must be a mapping")
    return value


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OfficialScoreError(f"cannot read {label} at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OfficialScoreError(f"invalid JSON in {label} at {path}: {exc}") from exc
    return _mapping(value, label)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evaluation_records(
    track_id: str, records: Sequence[Mapping[str, Any]]
) -> tuple[str, list[Mapping[str, Any]]]:
    expected_split = "diagnostic" if track_id == _BIDI_TRACK else "test"
    forbidden = sorted(
        {
            str(record.get("split", ""))
            for record in records
            if str(record.get("split", "")) != expected_split
        }
    )
    if forbidden:
        raise OfficialScoreError(
            f"{track_id} root contains non-evaluation splits: {', '.join(forbidden)}"
        )
    selected = sorted(records, key=lambda record: str(record.get("page_id", "")))
    if not selected:
        raise OfficialScoreError(f"{track_id} root has no {expected_split} records")
    return expected_split, selected


def _selection_hash(records: Sequence[Mapping[str, Any]]) -> str:
    return _canonical_hash(
        [
            {
                "page_id": str(record.get("page_id", "")),
                "image_sha256": str(
                    record.get("image", {}).get("sha256", "")
                    if isinstance(record.get("image"), Mapping)
                    else ""
                ),
            }
            for record in records
        ]
    )


def _identifier_values(record: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    regions = record.get("regions")
    if isinstance(regions, Sequence) and not isinstance(regions, (str, bytes)):
        for region in regions:
            if not isinstance(region, Mapping):
                continue
            region_id = region.get("region_id")
            if isinstance(region_id, str) and region_id:
                values.add(region_id)
            lines = region.get("lines")
            if isinstance(lines, Sequence) and not isinstance(lines, (str, bytes)):
                for line in lines:
                    if isinstance(line, Mapping):
                        line_id = line.get("line_id")
                        if isinstance(line_id, str) and line_id:
                            values.add(line_id)
    tables = record.get("tables")
    if isinstance(tables, Sequence) and not isinstance(tables, (str, bytes)):
        for table in tables:
            if not isinstance(table, Mapping):
                continue
            for key in ("table_id", "region_id"):
                value = table.get(key)
                if isinstance(value, str) and value:
                    values.add(value)
            cells = table.get("cells")
            if isinstance(cells, Sequence) and not isinstance(cells, (str, bytes)):
                for cell in cells:
                    if isinstance(cell, Mapping):
                        value = cell.get("cell_id")
                        if isinstance(value, str) and value:
                            values.add(value)
    fields = record.get("form_fields")
    if isinstance(fields, Sequence) and not isinstance(fields, (str, bytes)):
        for field in fields:
            if not isinstance(field, Mapping):
                continue
            for key in ("field_id", "label_region_id", "value_region_id"):
                value = field.get(key)
                if isinstance(value, str) and value:
                    values.add(value)
    return values


def _verify_artifacts(report_root: Path, manifest: Mapping[str, Any]) -> None:
    artifacts = _mapping(manifest.get("artifacts"), f"{report_root}/run_manifest.artifacts")
    required = {"metrics", "per_page", "errors", "summary", "html"}
    missing = sorted(required - set(str(key) for key in artifacts))
    if missing:
        raise OfficialScoreError(
            f"{report_root} is missing canonical artifact evidence: {', '.join(missing)}"
        )
    for label in sorted(required):
        evidence = _mapping(artifacts.get(label), f"{report_root}/artifacts.{label}")
        relative = evidence.get("path")
        if not isinstance(relative, str) or not relative or Path(relative).name != relative:
            raise OfficialScoreError(f"{report_root}: {label} artifact path is not canonical")
        path = report_root / relative
        if not path.is_file():
            raise OfficialScoreError(f"{report_root}: {label} artifact is missing")
        if evidence.get("size_bytes") != path.stat().st_size:
            raise OfficialScoreError(f"{report_root}: {label} artifact size differs")
        if evidence.get("sha256") != sha256_file(path):
            raise OfficialScoreError(f"{report_root}: {label} artifact hash differs")


def _input_path(report_root: Path, evidence: Mapping[str, Any], label: str) -> Path:
    raw_path = evidence.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise OfficialScoreError(f"{report_root}: {label} input path is missing")
    path = Path(raw_path)
    if not path.is_absolute():
        path = report_root / path
    path = path.resolve()
    if not path.is_file():
        raise OfficialScoreError(f"{report_root}: {label} input is missing: {path}")
    if evidence.get("size_bytes") != path.stat().st_size:
        raise OfficialScoreError(f"{report_root}: {label} input size differs")
    if evidence.get("sha256") != sha256_file(path):
        raise OfficialScoreError(f"{report_root}: {label} input hash differs")
    return path


def _verify_blind_contract(
    track_id: str,
    gold: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    configuration: Mapping[str, Any],
) -> str:
    if manifest.get("model") is None:
        raise OfficialScoreError(f"{track_id}: model manifest is missing")
    model = _mapping(manifest.get("model"), f"{track_id}.run_manifest.model")
    if (
        model.get("runner") != _RUNNER
        or model.get("runner_schema_version") != _RUNNER_SCHEMA_VERSION
    ):
        raise OfficialScoreError(
            f"{track_id}: run was not produced by the official baseline runner"
        )
    expected_mode = "blind_whole_line_image" if track_id == _LINE_TRACK else "blind_full_page_image"
    if configuration.get("input_mode") != expected_mode:
        raise OfficialScoreError(
            f"{track_id}: expected input_mode={expected_mode}, got {configuration.get('input_mode')!r}"
        )
    if configuration.get("gold_assistance") is not False:
        raise OfficialScoreError(f"{track_id}: gold_assistance must be explicitly false")
    if configuration.get("oracle_layout") is not False or model.get("oracle_layout") is not False:
        raise OfficialScoreError(f"{track_id}: oracle layout is forbidden for the Modern headline")
    if configuration.get("limited_smoke_run") is not False:
        raise OfficialScoreError(f"{track_id}: limited/smoke runs are not rankable")

    stable_keys = ("system_id", "family", "name", "version", "artifacts")
    expected_identity = {key: model.get(key) for key in stable_keys if key in model}
    if not expected_identity:
        raise OfficialScoreError(f"{track_id}: stable model identity is missing")
    if len(gold) != len(predictions):
        raise OfficialScoreError(f"{track_id}: prediction coverage is incomplete")
    gold_ids = {str(record.get("page_id", "")) for record in gold}
    prediction_ids = {str(record.get("page_id", "")) for record in predictions}
    if gold_ids != prediction_ids or len(prediction_ids) != len(predictions):
        raise OfficialScoreError(f"{track_id}: prediction page IDs do not exactly match gold")

    gold_by_id = {str(record["page_id"]): record for record in gold}
    for prediction in predictions:
        page_id = str(prediction.get("page_id", ""))
        prediction_model = _mapping(prediction.get("model"), f"{track_id}.{page_id}.model")
        observed_identity = {
            key: prediction_model.get(key) for key in stable_keys if key in prediction_model
        }
        if observed_identity != expected_identity:
            raise OfficialScoreError(f"{track_id}.{page_id}: model identity differs from manifest")
        if prediction_model.get("oracle_layout") is not False:
            raise OfficialScoreError(f"{track_id}.{page_id}: oracle_layout must be false")
        shared = _identifier_values(gold_by_id[page_id]) & _identifier_values(prediction)
        if shared:
            sample = ", ".join(sorted(shared)[:5])
            raise OfficialScoreError(
                f"{track_id}.{page_id}: prediction reuses gold layout identifiers: {sample}"
            )
    return expected_mode


def _verify_one_report(
    track_id: str,
    report_root: Path,
    track_root: Path,
    suite: ModernSuiteSpec,
) -> dict[str, object]:
    bundle = load_modern_report_bundle(report_root)
    configuration = _mapping(bundle.get("configuration"), f"{track_id}.configuration")
    if configuration.get("official_track_id") != track_id:
        raise OfficialScoreError(f"{report_root}: report track ID differs from {track_id}")
    manifest = _mapping(bundle.get("run_manifest"), f"{track_id}.run_manifest")
    _verify_artifacts(report_root, manifest)

    inputs = _mapping(manifest.get("inputs"), f"{track_id}.run_manifest.inputs")
    gold_evidence = _mapping(inputs.get("gold"), f"{track_id}.inputs.gold")
    prediction_evidence = _mapping(inputs.get("predictions"), f"{track_id}.inputs.predictions")
    root_gold_path = (track_root / "gold.jsonl").resolve()
    if gold_evidence.get("sha256") != sha256_file(root_gold_path):
        raise OfficialScoreError(f"{track_id}: report gold hash differs from certified root")
    if gold_evidence.get("size_bytes") != root_gold_path.stat().st_size:
        raise OfficialScoreError(f"{track_id}: report gold size differs from certified root")
    prediction_path = _input_path(report_root, prediction_evidence, "predictions")

    gold_raw = load_jsonl(root_gold_path)
    prediction_raw = load_jsonl(prediction_path)
    split, gold = _evaluation_records(track_id, gold_raw)
    predictions = [dict(_mapping(item, f"{track_id}.prediction")) for item in prediction_raw]
    gold_validation = validate_gold_records(gold, dataset_root=track_root)
    prediction_validation = validate_prediction_records(predictions)
    if not gold_validation.is_valid:
        raise OfficialScoreError(f"{track_id}: certified root gold failed runtime validation")
    if not prediction_validation.is_valid:
        raise OfficialScoreError(f"{track_id}: predictions failed schema validation")

    spec = load_track(track_id)
    unexpected = sorted(
        {str(page.get("track", "")) for page in gold} - set(spec.accepted_gold_tracks)
    )
    if unexpected:
        raise OfficialScoreError(f"{track_id}: gold is outside the track contract")
    input_mode = _verify_blind_contract(track_id, gold, predictions, manifest, configuration)

    expected_configuration = {
        "official_track_id": spec.track_id,
        "official_track_version": spec.version,
        "official_track_fingerprint": spec.config_fingerprint,
        "evaluation_split": split,
        "source_evaluation_pages": len(gold),
        "evaluated_evaluation_pages": len(gold),
        "evaluation_selection_sha256": _selection_hash(gold),
        "baseline_runner_schema_version": _RUNNER_SCHEMA_VERSION,
        "limited_smoke_run": False,
        "input_mode": input_mode,
        "gold_assistance": False,
        "oracle_layout": False,
    }
    for field, expected in expected_configuration.items():
        if configuration.get(field) != expected:
            raise OfficialScoreError(
                f"{track_id}: configuration.{field} expected {expected!r}, "
                f"got {configuration.get(field)!r}"
            )

    recomputed = evaluate_dataset(gold, predictions, config=spec.benchmark_config)
    stored_metrics = _mapping(bundle.get("metrics"), f"{track_id}.metrics")
    if recomputed.metrics != stored_metrics:
        raise OfficialScoreError(f"{track_id}: stored metrics differ from evaluator recomputation")

    report = dict(bundle)
    report["verified_evidence"] = {
        "schema_version": OFFICIAL_VERIFICATION_SCHEMA_VERSION,
        "status": "recomputed_from_locked_inputs",
        "suite_fingerprint": suite.suite_fingerprint,
        "track_id": track_id,
        "dataset_fingerprint": suite.tracks[track_id].dataset_fingerprint,
        "gold_sha256": suite.tracks[track_id].gold_sha256,
        "predictions_sha256": sha256_file(prediction_path),
        "metrics_sha256": _canonical_hash(recomputed.metrics),
        "input_mode": input_mode,
        "gold_assistance": False,
        "oracle_layout": False,
    }
    return report


def verify_and_combine_modern_reports(
    reports_root: str | Path,
    track_roots: Mapping[str, str | Path],
    suite_lock: ModernSuiteSpec | Mapping[str, Any],
) -> dict[str, object]:
    """Recompute and combine all five official Modern-Hebrew reports."""

    suite = coerce_modern_suite_lock(suite_lock)
    registry = load_registry()
    profiles = load_profiles(registry=registry)
    profile = profiles.profiles.get(suite.profile_id)
    if profile is None:
        raise OfficialScoreError(f"suite profile is not canonical: {suite.profile_id}")
    validate_modern_suite_contract(
        suite,
        expected_benchmark_version=__version__,
        expected_registry_fingerprint=registry.fingerprint,
        expected_profile_id=profile.profile_id,
        expected_profile_fingerprint=profile_fingerprint(profile),
        allowed_track_ids={spec.track_id for spec in list_official_tracks()},
    )
    normalized_roots = {
        str(track_id): Path(root).resolve() for track_id, root in track_roots.items()
    }
    expected = set(DEFAULT_HEADLINE_TRACKS)
    if set(normalized_roots) != expected:
        missing = sorted(expected - set(normalized_roots))
        extra = sorted(set(normalized_roots) - expected)
        raise OfficialScoreError(
            f"track roots must be exactly the headline tracks; missing={missing}, extra={extra}"
        )

    rebuilt = build_modern_suite_lock(
        normalized_roots,
        profile_id=suite.profile_id,
        profile_fingerprint=suite.profile_fingerprint,
        registry_fingerprint=suite.registry_fingerprint,
        benchmark_version=suite.benchmark_version,
        suite_version=suite.suite_version,
        maturity={track_id: suite.tracks[track_id].maturity for track_id in expected},
        headline_tracks=DEFAULT_HEADLINE_TRACKS,
    )
    if rebuilt != suite.to_dict():
        raise OfficialScoreError("suite lock differs from independently verified component roots")

    report_base = Path(reports_root).resolve()
    reports: list[dict[str, object]] = []
    for track_id in DEFAULT_HEADLINE_TRACKS:
        report_root = report_base / track_id
        if not report_root.is_dir():
            raise OfficialScoreError(f"missing canonical report directory: {report_root}")
        reports.append(_verify_one_report(track_id, report_root, normalized_roots[track_id], suite))
    result = combine_modern_track_reports(reports, suite_lock=suite)
    result["score_admission"] = {
        "schema_version": OFFICIAL_VERIFICATION_SCHEMA_VERSION,
        "status": "verified_recomputed",
        "component_roots_verified": True,
        "artifact_hashes_verified": True,
        "metrics_recomputed": True,
        "blind_input_contract_verified": True,
        "oracle_layout": False,
        "gold_assistance": False,
    }
    return result


__all__ = [
    "OFFICIAL_VERIFICATION_SCHEMA_VERSION",
    "OfficialScoreError",
    "verify_and_combine_modern_reports",
]
