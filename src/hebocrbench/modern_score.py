"""Guarded headline scoring for the official Modern Hebrew benchmark tracks.

The public headline is deliberately assembled from separate locked track reports.
This prevents a single easy slice from hiding failures in page order, tables,
forms, robustness or Hebrew/Latin bidirectional text.  Modern handwriting is
reported beside the printed-document score and never blended into it.
"""

from __future__ import annotations

from math import prod
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .io import sha256_file
from .modern_suite import DEFAULT_HEADLINE_TRACKS, ModernSuiteSpec, coerce_modern_suite_lock
from .tracks import TrackError, load_track

Report = Mapping[str, Any]

REQUIRED_PRINT_TRACKS: tuple[str, ...] = DEFAULT_HEADLINE_TRACKS
FORMS_TRACK = "modern-forms-v1"
HANDWRITING_TRACK = "modern-handwriting-v1"

_COMPONENT_WEIGHTS: Mapping[str, float] = {
    "bidi": 0.12,
    "line_recognition": 0.20,
    "page_ocr": 0.34,
    "tables": 0.17,
    "robustness": 0.17,
}


class ModernScoreError(ValueError):
    """A report bundle cannot be interpreted as an official score input."""


def _mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModernScoreError(f"{location} must be a mapping")
    return value


def _number(value: object, location: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModernScoreError(f"{location} must be numeric")
    return float(value)


def _metric(root: Mapping[str, Any], path: str) -> float:
    current: object = root
    for part in path.split("."):
        current = _mapping(current, path).get(part)
    return _number(current, path)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _quality_from_error(value: float) -> float:
    return 1.0 - _clamp(value)


def _weighted_geometric(values: Sequence[tuple[float, float]]) -> float:
    if not values:
        raise ModernScoreError("weighted geometric mean requires values")
    total_weight = sum(weight for _, weight in values)
    if total_weight <= 0:
        raise ModernScoreError("weighted geometric mean requires positive weights")
    normalized = [(_clamp(value), weight / total_weight) for value, weight in values]
    if any(value == 0.0 and weight > 0 for value, weight in normalized):
        return 0.0
    return prod(value**weight for value, weight in normalized)


def _component(
    values: Sequence[tuple[str, float, float]],
) -> dict[str, object]:
    normalized = _weighted_geometric([(value, weight) for _, value, weight in values])
    return {
        "score": round(100.0 * normalized, 6),
        "normalized_score": round(normalized, 8),
        "inputs": {
            name: {"value": round(value, 8), "weight": weight} for name, value, weight in values
        },
    }


def _index_reports(reports: Sequence[Report]) -> dict[str, Report]:
    indexed: dict[str, Report] = {}
    for position, report in enumerate(reports):
        configuration = _mapping(report.get("configuration"), f"reports[{position}].configuration")
        track_id = configuration.get("official_track_id")
        if not isinstance(track_id, str) or not track_id:
            raise ModernScoreError(
                f"reports[{position}].configuration.official_track_id must be a non-empty string"
            )
        if track_id in indexed:
            raise ModernScoreError(f"duplicate report for official track {track_id}")
        indexed[track_id] = report
    return indexed


def _suite_failures(
    indexed: Mapping[str, Report],
    suite: ModernSuiteSpec,
) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for track_id in REQUIRED_PRINT_TRACKS:
        entry = suite.tracks.get(track_id)
        if entry is None:
            failures.append({"track_id": track_id, "reason": "track missing from suite lock"})
            continue
        if not entry.headline:
            failures.append(
                {"track_id": track_id, "reason": "required track is not marked headline"}
            )
        if entry.maturity != "certified":
            failures.append(
                {
                    "track_id": track_id,
                    "reason": f"headline track maturity is {entry.maturity}, expected certified",
                }
            )

    submitted = set(indexed) & {FORMS_TRACK, HANDWRITING_TRACK, *REQUIRED_PRINT_TRACKS}
    common_fields = (
        "suite_version",
        "suite_fingerprint",
        "benchmark_version",
        "profile_id",
        "profile_fingerprint",
        "registry_fingerprint",
    )
    for track_id in sorted(submitted):
        report = indexed[track_id]
        manifest = _mapping(report.get("run_manifest"), f"{track_id}.run_manifest")
        evidence = manifest.get("benchmark_suite")
        if not isinstance(evidence, Mapping):
            failures.append({"track_id": track_id, "reason": "missing benchmark_suite evidence"})
            continue
        expected_entry = suite.tracks.get(track_id)
        if expected_entry is None:
            failures.append(
                {"track_id": track_id, "reason": "submitted track is absent from suite lock"}
            )
            continue
        expected_common = {
            "suite_version": suite.suite_version,
            "suite_fingerprint": suite.suite_fingerprint,
            "benchmark_version": suite.benchmark_version,
            "profile_id": suite.profile_id,
            "profile_fingerprint": suite.profile_fingerprint,
            "registry_fingerprint": suite.registry_fingerprint,
        }
        for field in common_fields:
            if evidence.get(field) != expected_common[field]:
                failures.append(
                    {
                        "track_id": track_id,
                        "field": field,
                        "reason": f"expected {expected_common[field]}, got {evidence.get(field)!r}",
                    }
                )
        expected_track = {"track_id": track_id, **expected_entry.to_dict()}
        for field, expected in expected_track.items():
            if evidence.get(field) != expected:
                failures.append(
                    {
                        "track_id": track_id,
                        "field": field,
                        "reason": f"expected {expected!r}, got {evidence.get(field)!r}",
                    }
                )
    return failures


def _contract_failures(indexed: Mapping[str, Report]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for track_id in (*REQUIRED_PRINT_TRACKS, HANDWRITING_TRACK):
        report = indexed.get(track_id)
        if report is None:
            continue
        configuration = _mapping(report.get("configuration"), f"{track_id}.configuration")
        try:
            spec = load_track(track_id)
        except TrackError as exc:
            failures.append({"track_id": track_id, "field": "track_contract", "reason": str(exc)})
            continue
        observed_version = str(configuration.get("official_track_version", ""))
        observed_fingerprint = str(configuration.get("official_track_fingerprint", ""))
        if observed_version != spec.version:
            failures.append(
                {
                    "track_id": track_id,
                    "field": "official_track_version",
                    "reason": f"expected {spec.version}, got {observed_version or '<missing>'}",
                }
            )
        if observed_fingerprint != spec.config_fingerprint:
            failures.append(
                {
                    "track_id": track_id,
                    "field": "official_track_fingerprint",
                    "reason": (
                        f"expected {spec.config_fingerprint}, "
                        f"got {observed_fingerprint or '<missing>'}"
                    ),
                }
            )
    return failures


def _model_failures(indexed: Mapping[str, Report]) -> list[dict[str, object]]:
    manifests = {
        track_id: report.get("run_manifest")
        for track_id, report in indexed.items()
        if track_id in REQUIRED_PRINT_TRACKS
    }
    if not any(isinstance(value, Mapping) for value in manifests.values()):
        return []
    identities: dict[str, object] = {}
    missing: list[str] = []
    for track_id in REQUIRED_PRINT_TRACKS:
        manifest = manifests.get(track_id)
        if not isinstance(manifest, Mapping):
            missing.append(track_id)
            continue
        model = manifest.get("model")
        if not isinstance(model, Mapping) or not model:
            missing.append(track_id)
            continue
        stable_keys = ("system_id", "family", "name", "version", "artifacts")
        identity = {key: model[key] for key in stable_keys if key in model}
        if not identity:
            missing.append(track_id)
            continue
        identities[track_id] = identity
    failures: list[dict[str, object]] = []
    if missing:
        failures.append(
            {
                "reason": "missing model identity",
                "track_ids": sorted(missing),
            }
        )
    canonical = {
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for value in identities.values()
    }
    if len(canonical) > 1:
        failures.append(
            {
                "reason": "track reports were produced by different model identities",
                "models": identities,
            }
        )
    return failures


def _verification_failures(
    indexed: Mapping[str, Report], suite: ModernSuiteSpec
) -> list[dict[str, object]]:
    """Require runtime-only evidence emitted by the recomputing scorer.

    ``load_modern_report_bundle`` never trusts or imports this marker from a
    report directory.  It is attached in memory only after the official scorer
    has verified the certified roots, prediction bytes, artifact hashes, blind
    input contract and a fresh evaluator recomputation.
    """

    failures: list[dict[str, object]] = []
    for track_id in REQUIRED_PRINT_TRACKS:
        evidence = indexed[track_id].get("verified_evidence")
        if not isinstance(evidence, Mapping):
            failures.append(
                {
                    "track_id": track_id,
                    "reason": "missing official recomputation evidence",
                }
            )
            continue
        expected = {
            "schema_version": "1.0",
            "status": "recomputed_from_locked_inputs",
            "suite_fingerprint": suite.suite_fingerprint,
            "track_id": track_id,
            "dataset_fingerprint": suite.tracks[track_id].dataset_fingerprint,
            "gold_sha256": suite.tracks[track_id].gold_sha256,
            "gold_assistance": False,
            "oracle_layout": False,
        }
        for field, value in expected.items():
            if evidence.get(field) != value:
                failures.append(
                    {
                        "track_id": track_id,
                        "field": field,
                        "reason": f"expected {value!r}, got {evidence.get(field)!r}",
                    }
                )
        for field in ("predictions_sha256", "metrics_sha256", "input_mode"):
            if not isinstance(evidence.get(field), str) or not evidence.get(field):
                failures.append(
                    {
                        "track_id": track_id,
                        "field": field,
                        "reason": "official recomputation evidence is incomplete",
                    }
                )
    return failures


def _coverage_failures(indexed: Mapping[str, Report]) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    for track_id in REQUIRED_PRINT_TRACKS:
        report = indexed[track_id]
        metrics = _mapping(report.get("metrics"), f"{track_id}.metrics")
        coverage = _mapping(metrics.get("coverage"), f"{track_id}.metrics.coverage")
        gold_pages = int(_number(coverage.get("gold_pages"), f"{track_id}.coverage.gold_pages"))
        missing = int(
            _number(
                coverage.get("missing_prediction_pages"),
                f"{track_id}.coverage.missing_prediction_pages",
            )
        )
        matched = int(
            _number(
                coverage.get("matched_prediction_pages"),
                f"{track_id}.coverage.matched_prediction_pages",
            )
        )
        if gold_pages <= 0:
            failures.append({"track_id": track_id, "reason": "gold_pages must be positive"})
        if missing > 0 or matched != gold_pages:
            failures.append({"track_id": track_id, "missing_prediction_pages": missing})
    return failures


def _bidi_component(metrics: Mapping[str, Any]) -> dict[str, object]:
    conformance = _mapping(metrics.get("conformance"), "bidi.conformance")
    bidi = _mapping(metrics.get("bidi"), "bidi.metrics")
    return _component(
        (
            (
                "strict_line_exact_rate",
                _metric(conformance, "strict_line_exact_rate"),
                0.25,
            ),
            ("ltr_run_exact_rate", _metric(bidi, "ltr_run_exact_rate"), 0.25),
            ("numeric_exact_rate", _metric(bidi, "numeric_exact_rate"), 0.20),
            ("bracket_exact_rate", _metric(bidi, "bracket_exact_rate"), 0.15),
            (
                "pairwise_word_order_accuracy",
                _metric(bidi, "pairwise_word_order_accuracy"),
                0.10,
            ),
            (
                "visual_order_integrity",
                _quality_from_error(_metric(bidi, "visual_order_failure_rate")),
                0.05,
            ),
        )
    )


def _line_component(metrics: Mapping[str, Any]) -> dict[str, object]:
    recognition = _mapping(metrics.get("recognition"), "line.recognition")
    return _component(
        (
            (
                "line_gcer_quality",
                _quality_from_error(_metric(recognition, "line_gcer")),
                0.55,
            ),
            (
                "line_wer_quality",
                _quality_from_error(_metric(recognition, "line_wer")),
                0.20,
            ),
            ("line_exact_rate", _metric(recognition, "line_exact_rate"), 0.25),
        )
    )


def _page_component(metrics: Mapping[str, Any]) -> dict[str, object]:
    recognition = _mapping(metrics.get("recognition"), "page.recognition")
    layout = _mapping(metrics.get("layout"), "page.layout")
    regions = _mapping(layout.get("regions"), "page.layout.regions")
    lines = _mapping(layout.get("lines"), "page.layout.lines")
    reading = _mapping(metrics.get("reading_order"), "page.reading_order")
    return _component(
        (
            (
                "page_order_gcer_quality",
                _quality_from_error(_metric(recognition, "page_order_gcer")),
                0.35,
            ),
            (
                "line_gcer_quality",
                _quality_from_error(_metric(recognition, "line_gcer")),
                0.15,
            ),
            ("region_f1", _metric(regions, "f1"), 0.125),
            ("line_f1", _metric(lines, "f1"), 0.125),
            ("reading_order_edge_f1", _metric(reading, "edge_f1"), 0.125),
            (
                "reading_order_pairwise_accuracy",
                _metric(reading, "pairwise_accuracy"),
                0.125,
            ),
        )
    )


def _table_component(metrics: Mapping[str, Any]) -> dict[str, object]:
    tables = _mapping(metrics.get("tables"), "tables")
    if _metric(tables, "gold_tables") <= 0:
        raise ModernScoreError("modern-tables-v1 contains no gold tables")
    return _component(
        (
            ("table_presence_f1", _metric(tables, "table_presence_f1"), 0.25),
            ("cell_span_f1", _metric(tables, "cell_span_f1"), 0.25),
            ("grid_slot_accuracy", _metric(tables, "grid_slot_accuracy"), 0.25),
            (
                "cell_text_gcer_quality",
                _quality_from_error(_metric(tables, "cell_text_gcer")),
                0.25,
            ),
        )
    )


def _form_component(metrics: Mapping[str, Any]) -> dict[str, object]:
    forms = _mapping(metrics.get("forms"), "forms")
    if _metric(forms, "gold_fields") <= 0:
        raise ModernScoreError("modern-forms-v1 contains no gold form fields")
    return _component(
        (
            ("field_presence_f1", _metric(forms, "field_presence_f1"), 0.30),
            (
                "value_gcer_quality",
                _quality_from_error(_metric(forms, "value_gcer")),
                0.40,
            ),
            ("value_exact_rate", _metric(forms, "value_exact_rate"), 0.30),
        )
    )


def _robustness_component(metrics: Mapping[str, Any]) -> dict[str, object]:
    recognition = _mapping(metrics.get("recognition"), "robustness.recognition")
    distribution = _mapping(metrics.get("distribution"), "robustness.distribution")
    paired = _mapping(metrics.get("robustness_pairs"), "robustness.robustness_pairs")
    coverage = _mapping(paired.get("coverage"), "robustness.robustness_pairs.coverage")
    if _metric(coverage, "pair_coverage") != 1.0:
        raise ModernScoreError("modern-robustness-v1 has incomplete clean/degraded pair coverage")
    macro = _mapping(
        _mapping(paired.get("summary"), "robustness.robustness_pairs.summary").get("macro"),
        "robustness.robustness_pairs.summary.macro",
    )
    paired_metrics = _mapping(
        macro.get("metrics"), "robustness.robustness_pairs.summary.macro.metrics"
    )
    line_delta = _mapping(
        paired_metrics.get("line_gcer"),
        "robustness.robustness_pairs.summary.macro.metrics.line_gcer",
    )
    page_delta = _mapping(
        paired_metrics.get("page_order_gcer"),
        "robustness.robustness_pairs.summary.macro.metrics.page_order_gcer",
    )
    return _component(
        (
            (
                "overall_line_gcer_quality",
                _quality_from_error(_metric(recognition, "line_gcer")),
                0.30,
            ),
            (
                "p90_page_gcer_quality",
                _quality_from_error(_metric(distribution, "page_line_gcer_p90")),
                0.20,
            ),
            ("line_exact_rate", _metric(recognition, "line_exact_rate"), 0.15),
            (
                "mean_line_gcer_delta_quality",
                _quality_from_error(max(0.0, _metric(line_delta, "mean_delta"))),
                0.20,
            ),
            (
                "p90_page_order_gcer_delta_quality",
                _quality_from_error(max(0.0, _metric(page_delta, "p90_delta"))),
                0.15,
            ),
        )
    )


def _handwriting_summary(report: Report | None, maturity: str | None = None) -> dict[str, object]:
    if report is None:
        return {"status": "not_submitted", "score": None}
    metrics = _mapping(report.get("metrics"), "modern-handwriting-v1.metrics")
    coverage = _mapping(metrics.get("coverage"), "modern-handwriting-v1.coverage")
    missing = int(
        _number(
            coverage.get("missing_prediction_pages"),
            "modern-handwriting-v1.coverage.missing_prediction_pages",
        )
    )
    component = _line_component(metrics)
    return {
        "status": (
            "certified_separate_track"
            if missing == 0 and maturity == "certified"
            else "diagnostic_separate_track"
            if missing == 0
            else "incomplete_submission"
        ),
        "maturity": maturity,
        "score": component["score"] if missing == 0 else None,
        "component": component,
        "missing_prediction_pages": missing,
    }


def _forms_summary(report: Report | None, maturity: str | None = None) -> dict[str, object]:
    if report is None:
        return {"status": "not_submitted", "maturity": maturity, "score": None}
    try:
        component = _form_component(_mapping(report.get("metrics"), f"{FORMS_TRACK}.metrics"))
    except ModernScoreError as exc:
        return {
            "status": "invalid_extension",
            "maturity": maturity,
            "score": None,
            "reason": str(exc),
        }
    return {
        "status": "certified_extension" if maturity == "certified" else "diagnostic_extension",
        "maturity": maturity,
        "score": component["score"],
        "component": component,
    }


def combine_modern_track_reports(
    reports: Sequence[Report],
    *,
    suite_lock: ModernSuiteSpec | Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Combine locked official reports into the guarded Modern Hebrew headline.

    The function does not average raw metrics across incompatible tasks.  Each
    task first becomes a bounded component score, then the headline uses a
    weighted geometric mean.  A zero component therefore makes the headline
    zero, and a failed BiDi gate makes the submission non-rankable.
    """

    indexed = _index_reports(reports)
    missing = sorted(set(REQUIRED_PRINT_TRACKS) - set(indexed))
    suite = coerce_modern_suite_lock(suite_lock) if suite_lock is not None else None
    forms_maturity = (
        suite.tracks.get(FORMS_TRACK).maturity if suite and suite.tracks.get(FORMS_TRACK) else None
    )
    handwriting_maturity = (
        suite.tracks.get(HANDWRITING_TRACK).maturity
        if suite and suite.tracks.get(HANDWRITING_TRACK)
        else None
    )
    base: dict[str, object] = {
        "benchmark": "HebOCRBench Modern Hebrew",
        "score_schema_version": "1.1",
        "required_tracks": list(REQUIRED_PRINT_TRACKS),
        "missing_tracks": missing,
        "forms_in_headline": False,
        "handwriting_in_headline": False,
        "component_weights": dict(_COMPONENT_WEIGHTS),
        "headline_formula": "weighted geometric mean after mandatory suite, BiDi and coverage gates",
        "suite_fingerprint": suite.suite_fingerprint if suite else None,
        "profile_id": suite.profile_id if suite else None,
        "profile_fingerprint": suite.profile_fingerprint if suite else None,
        "forms": _forms_summary(indexed.get(FORMS_TRACK), forms_maturity),
        "handwriting": _handwriting_summary(indexed.get(HANDWRITING_TRACK), handwriting_maturity),
    }
    if missing:
        return {**base, "status": "incomplete", "headline_score": None, "components": {}}
    if suite is None:
        return {
            **base,
            "status": "invalid_evidence",
            "headline_score": None,
            "suite_failures": [{"reason": "Modern Hebrew suite lock is required"}],
            "components": {},
        }

    suite_failures = _suite_failures(indexed, suite)
    evidence_failures = _contract_failures(indexed)
    model_failures = _model_failures(indexed)
    verification_failures = _verification_failures(indexed, suite)
    if suite_failures or evidence_failures or model_failures or verification_failures:
        return {
            **base,
            "status": "invalid_evidence",
            "headline_score": None,
            "suite_failures": suite_failures,
            "contract_failures": evidence_failures,
            "model_failures": model_failures,
            "verification_failures": verification_failures,
            "components": {},
        }

    coverage_failures = _coverage_failures(indexed)
    if coverage_failures:
        return {
            **base,
            "status": "incomplete_submission",
            "headline_score": None,
            "coverage_failures": coverage_failures,
            "components": {},
        }

    bidi_metrics = _mapping(indexed["modern-bidi-v1"].get("metrics"), "modern-bidi-v1.metrics")
    conformance = _mapping(bidi_metrics.get("conformance"), "modern-bidi-v1.conformance")
    quality_status = str(conformance.get("quality_status", "unknown"))
    raw_quality_warnings = conformance.get(
        "quality_failed_checks",
        conformance.get("failed_quality_checks", []),
    )
    quality_warnings = [str(item) for item in raw_quality_warnings]
    base.update(
        {
            "bidi_quality_status": quality_status,
            "quality_warnings": quality_warnings,
        }
    )
    if conformance.get("status") != "conformant":
        return {
            **base,
            "status": "non_conformant",
            "headline_score": None,
            "failed_gates": list(conformance.get("failed_checks", [])),
            "components": {},
        }

    try:
        components = {
            "bidi": _bidi_component(bidi_metrics),
            "line_recognition": _line_component(
                _mapping(
                    indexed["modern-line-recognition-v1"].get("metrics"),
                    "modern-line-recognition-v1.metrics",
                )
            ),
            "page_ocr": _page_component(
                _mapping(
                    indexed["modern-page-ocr-v1"].get("metrics"),
                    "modern-page-ocr-v1.metrics",
                )
            ),
            "tables": _table_component(
                _mapping(
                    indexed["modern-tables-v1"].get("metrics"),
                    "modern-tables-v1.metrics",
                )
            ),
            "robustness": _robustness_component(
                _mapping(
                    indexed["modern-robustness-v1"].get("metrics"),
                    "modern-robustness-v1.metrics",
                )
            ),
        }
    except ModernScoreError as exc:
        return {
            **base,
            "status": "invalid_metrics",
            "headline_score": None,
            "reason": str(exc),
            "components": {},
        }

    normalized = _weighted_geometric(
        [
            (float(component["normalized_score"]), _COMPONENT_WEIGHTS[name])
            for name, component in components.items()
        ]
    )
    return {
        **base,
        "status": "rankable",
        "headline_score": round(100.0 * normalized, 6),
        "normalized_headline_score": round(normalized, 8),
        "components": components,
        "failed_gates": [],
        "coverage_failures": [],
        "suite_failures": [],
        "contract_failures": [],
        "model_failures": [],
        "verification_failures": [],
    }


def load_modern_report_bundle(path: str | Path) -> dict[str, object]:
    """Load one canonical evaluation directory into the score-combiner shape."""

    root = Path(path)
    if not root.is_dir():
        raise ModernScoreError(f"report bundle is not a directory: {root}")
    metrics_path = root / "metrics.json"
    manifest_path = root / "run_manifest.json"
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModernScoreError(f"cannot read report bundle {root}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ModernScoreError(f"invalid JSON in report bundle {root}: {exc}") from exc
    metrics = dict(_mapping(metrics, f"{root}/metrics.json"))
    manifest = dict(_mapping(manifest, f"{root}/run_manifest.json"))
    artifacts = _mapping(manifest.get("artifacts"), f"{root}/run_manifest.artifacts")
    metrics_evidence = _mapping(artifacts.get("metrics"), f"{root}/artifacts.metrics")
    if metrics_evidence.get("path") != "metrics.json":
        raise ModernScoreError(f"{root}: metrics artifact path is not canonical")
    if metrics_evidence.get("size_bytes") != metrics_path.stat().st_size:
        raise ModernScoreError(f"{root}: metrics.json size differs from run manifest")
    if metrics_evidence.get("sha256") != sha256_file(metrics_path):
        raise ModernScoreError(f"{root}: metrics.json hash differs from run manifest")
    configuration = dict(
        _mapping(manifest.get("configuration"), f"{root}/run_manifest.configuration")
    )
    return {
        "schema_version": "1.0",
        "configuration": configuration,
        "metrics": metrics,
        "run_manifest": manifest,
    }


def load_modern_report_root(path: str | Path) -> list[dict[str, object]]:
    """Load every immediate canonical report directory under ``path``."""

    root = Path(path)
    if not root.is_dir():
        raise ModernScoreError(f"reports root is not a directory: {root}")
    bundles = [
        load_modern_report_bundle(child)
        for child in sorted(root.iterdir())
        if child.is_dir()
        and (child / "metrics.json").is_file()
        and (child / "run_manifest.json").is_file()
    ]
    if not bundles:
        raise ModernScoreError(f"no canonical report bundles found under {root}")
    return bundles


__all__ = [
    "FORMS_TRACK",
    "HANDWRITING_TRACK",
    "ModernScoreError",
    "REQUIRED_PRINT_TRACKS",
    "combine_modern_track_reports",
    "load_modern_report_bundle",
    "load_modern_report_root",
]
