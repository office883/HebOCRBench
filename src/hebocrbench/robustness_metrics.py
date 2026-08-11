"""Paired robustness metrics for clean/degraded Hebrew OCR pages.

Every degraded page is compared with the one clean control that shares its
``metadata.parent_page_id``.  Deltas are defined as ``degraded - clean`` so a
positive value always means that degradation made OCR worse.  Missing or
malformed evaluation rows remain visible as failed pairs and therefore cannot
silently improve the aggregate.
"""

from __future__ import annotations

from collections import defaultdict
import math
from numbers import Real
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .statistics import quantile

ROBUSTNESS_METRIC_NAMES = ("page_order_gcer", "line_gcer")

_ANCESTRY_TOP_LEVEL_FIELDS = ("document_id", "track")
_ANCESTRY_METADATA_FIELDS = (
    "source_id",
    "source_version",
    "source_page_id",
    "source_document_id",
    "parent_image_sha256",
    "document_type",
    "template_family",
    "source_collection",
    "ancestry",
)
_MISSING = object()


class RobustnessMetricsError(ValueError):
    """The paired robustness gold or evaluation rows are ambiguous."""


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RobustnessMetricsError(f"{label} must be a non-empty string")
    return value


def _metadata(record: Mapping[str, Any], page_id: str) -> Mapping[str, Any]:
    value = record.get("metadata")
    if not isinstance(value, Mapping):
        raise RobustnessMetricsError(f"gold page {page_id!r} must have object metadata")
    return value


def _lineage(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, object]:
    lineage: dict[str, object] = {
        field: record.get(field, _MISSING) for field in _ANCESTRY_TOP_LEVEL_FIELDS
    }
    lineage.update(
        {f"metadata.{field}": metadata.get(field, _MISSING) for field in _ANCESTRY_METADATA_FIELDS}
    )
    return lineage


def _validate_gold(
    gold_records: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, list[Mapping[str, Any]]]]:
    by_page_id: dict[str, Mapping[str, Any]] = {}
    by_parent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for index, record in enumerate(gold_records):
        if not isinstance(record, Mapping):
            raise RobustnessMetricsError(f"gold record {index} must be an object")
        page_id = _required_text(record.get("page_id"), f"gold record {index} page_id")
        if page_id in by_page_id:
            raise RobustnessMetricsError(f"duplicate gold page_id: {page_id}")
        metadata = _metadata(record, page_id)
        parent_id = _required_text(
            metadata.get("parent_page_id"), f"gold page {page_id!r} parent_page_id"
        )
        _required_text(
            metadata.get("degradation_family"),
            f"gold page {page_id!r} degradation_family",
        )
        _required_text(
            metadata.get("degradation_level"),
            f"gold page {page_id!r} degradation_level",
        )
        if not isinstance(metadata.get("degradation_is_control"), bool):
            raise RobustnessMetricsError(
                f"gold page {page_id!r} degradation_is_control must be boolean"
            )
        _required_text(record.get("split"), f"gold page {page_id!r} split")
        _required_text(record.get("document_id"), f"gold page {page_id!r} document_id")
        by_page_id[page_id] = record
        by_parent[parent_id].append(record)

    if not by_page_id:
        raise RobustnessMetricsError("paired robustness gold is empty")

    for parent_id in sorted(by_parent):
        records = by_parent[parent_id]
        controls = [
            record for record in records if record["metadata"]["degradation_is_control"] is True
        ]
        if len(controls) != 1:
            raise RobustnessMetricsError(
                f"parent {parent_id!r} must have exactly one clean control; found {len(controls)}"
            )
        control = controls[0]
        control_id = str(control["page_id"])
        control_metadata = _metadata(control, control_id)
        if control_metadata["degradation_family"] != "clean":
            raise RobustnessMetricsError(
                f"control {control_id!r} degradation_family must be 'clean'"
            )
        if control_metadata["degradation_level"] != "control":
            raise RobustnessMetricsError(
                f"control {control_id!r} degradation_level must be 'control'"
            )
        children = [record for record in records if record is not control]
        if not children:
            raise RobustnessMetricsError(
                f"parent {parent_id!r} must have at least one degraded child"
            )

        control_split = control["split"]
        control_lineage = _lineage(control, control_metadata)
        for child in sorted(children, key=lambda item: str(item["page_id"])):
            child_id = str(child["page_id"])
            child_metadata = _metadata(child, child_id)
            if child_metadata["degradation_family"] == "clean":
                raise RobustnessMetricsError(
                    f"non-control child {child_id!r} cannot use degradation_family 'clean'"
                )
            if child_metadata["degradation_level"] == "control":
                raise RobustnessMetricsError(
                    f"non-control child {child_id!r} cannot use degradation_level 'control'"
                )
            if child["split"] != control_split:
                raise RobustnessMetricsError(
                    f"child {child_id!r} and control {control_id!r} must share split"
                )
            child_lineage = _lineage(child, child_metadata)
            mismatches = [
                field for field in control_lineage if child_lineage[field] != control_lineage[field]
            ]
            if mismatches:
                raise RobustnessMetricsError(
                    f"child {child_id!r} and control {control_id!r} have different ancestry: "
                    + ", ".join(mismatches)
                )
    return by_page_id, by_parent


def _metric_containers(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    containers = [row]
    recognition = row.get("recognition")
    if isinstance(recognition, Mapping):
        containers.append(recognition)
    metrics = row.get("metrics")
    if isinstance(metrics, Mapping):
        nested_recognition = metrics.get("recognition")
        if isinstance(nested_recognition, Mapping):
            containers.append(nested_recognition)
        containers.append(metrics)
    return containers


def _metric_value(row: Mapping[str, Any], name: str) -> tuple[float | None, str | None]:
    raw: object = _MISSING
    for container in _metric_containers(row):
        if name in container:
            raw = container[name]
            break
    if raw is _MISSING:
        return None, f"missing_metric:{name}"
    if isinstance(raw, bool) or not isinstance(raw, Real):
        return None, f"invalid_metric:{name}"
    value = float(raw)
    if not math.isfinite(value) or value < 0.0:
        return None, f"invalid_metric:{name}"
    return value, None


def _index_evaluations(
    page_evaluations: Iterable[Mapping[str, Any]],
    metric_names: Sequence[str],
) -> tuple[dict[str, dict[str, float | None]], dict[str, dict[str, str]]]:
    values: dict[str, dict[str, float | None]] = {}
    errors: dict[str, dict[str, str]] = {}
    for index, row in enumerate(page_evaluations):
        if not isinstance(row, Mapping):
            raise RobustnessMetricsError(f"evaluation row {index} must be an object")
        page_id = _required_text(row.get("page_id"), f"evaluation row {index} page_id")
        if page_id in values:
            raise RobustnessMetricsError(f"duplicate evaluation page_id: {page_id}")
        page_values: dict[str, float | None] = {}
        page_errors: dict[str, str] = {}
        for name in metric_names:
            value, error = _metric_value(row, name)
            page_values[name] = value
            if error is not None:
                page_errors[name] = error
        values[page_id] = page_values
        errors[page_id] = page_errors
    return values, errors


def _delta_summary(values: Sequence[float]) -> dict[str, int | float | None]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean_delta": None, "median_delta": None, "p90_delta": None}
    return {
        "count": len(ordered),
        "mean_delta": math.fsum(ordered) / len(ordered),
        "median_delta": median(ordered),
        "p90_delta": quantile(ordered, 0.90),
    }


def _group_summary(
    pairs: Sequence[Mapping[str, Any]], metric_names: Sequence[str]
) -> dict[str, Any]:
    scored = [pair for pair in pairs if pair["status"] == "scored"]
    return {
        "expected_pairs": len(pairs),
        "scored_pairs": len(scored),
        "failed_pairs": len(pairs) - len(scored),
        "metrics": {
            name: _delta_summary([float(pair["metrics"][name]["delta"]) for pair in scored])
            for name in metric_names
        },
    }


def compute_paired_robustness(
    gold_records: Iterable[Mapping[str, Any]],
    page_evaluations: Iterable[Mapping[str, Any]],
    *,
    metric_names: Sequence[str] = ROBUSTNESS_METRIC_NAMES,
) -> dict[str, Any]:
    """Compute deterministic degraded-minus-clean deltas for a robustness track.

    Evaluation rows may be flat (the metric names next to ``page_id``), or use
    the evaluator's serialized shape under ``metrics.recognition``.  Structural
    ambiguity in the gold or duplicate evaluation IDs raises
    :class:`RobustnessMetricsError`.  Missing/invalid OCR results are emitted as
    failed pairs and excluded from summaries while remaining in the coverage
    denominator.
    """

    names = tuple(metric_names)
    if not names or any(not isinstance(name, str) or not name for name in names):
        raise RobustnessMetricsError("metric_names must contain non-empty strings")
    if len(set(names)) != len(names):
        raise RobustnessMetricsError("metric_names must be unique")

    gold_by_id, gold_by_parent = _validate_gold(gold_records)
    evaluation_values, evaluation_errors = _index_evaluations(page_evaluations, names)

    pairs: list[dict[str, Any]] = []
    for parent_id in sorted(gold_by_parent):
        records = gold_by_parent[parent_id]
        control = next(
            record for record in records if record["metadata"]["degradation_is_control"] is True
        )
        control_id = str(control["page_id"])
        children = sorted(
            (record for record in records if record is not control),
            key=lambda record: (
                str(record["metadata"]["degradation_family"]),
                str(record["metadata"]["degradation_level"]),
                str(record["page_id"]),
            ),
        )
        for child in children:
            child_id = str(child["page_id"])
            reasons: list[str] = []
            for role, page_id in (("control", control_id), ("child", child_id)):
                if page_id not in evaluation_values:
                    reasons.append(f"{role}:missing_evaluation")
                    continue
                reasons.extend(
                    f"{role}:{evaluation_errors[page_id][name]}"
                    for name in names
                    if name in evaluation_errors[page_id]
                )

            metric_results: dict[str, dict[str, float | None]] = {}
            for name in names:
                control_value = evaluation_values.get(control_id, {}).get(name)
                child_value = evaluation_values.get(child_id, {}).get(name)
                delta = None
                if not reasons and control_value is not None and child_value is not None:
                    delta = 0.0 if child_value == control_value else child_value - control_value
                metric_results[name] = {
                    "control": control_value,
                    "child": child_value,
                    "delta": delta,
                }

            metadata = child["metadata"]
            pairs.append(
                {
                    "parent_page_id": parent_id,
                    "control_page_id": control_id,
                    "child_page_id": child_id,
                    "document_id": str(child["document_id"]),
                    "split": str(child["split"]),
                    "degradation_family": str(metadata["degradation_family"]),
                    "degradation_level": str(metadata["degradation_level"]),
                    "status": "failed" if reasons else "scored",
                    "failure_reasons": reasons,
                    "metrics": metric_results,
                }
            )

    pairs.sort(
        key=lambda pair: (
            pair["parent_page_id"],
            pair["degradation_family"],
            pair["degradation_level"],
            pair["child_page_id"],
        )
    )
    failures = [
        {
            "parent_page_id": pair["parent_page_id"],
            "control_page_id": pair["control_page_id"],
            "child_page_id": pair["child_page_id"],
            "reasons": list(pair["failure_reasons"]),
        }
        for pair in pairs
        if pair["status"] == "failed"
    ]

    by_family: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_level: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for pair in pairs:
        by_family[str(pair["degradation_family"])].append(pair)
        by_level[str(pair["degradation_level"])].append(pair)

    gold_ids = set(gold_by_id)
    evaluation_ids = set(evaluation_values)
    matched_ids = gold_ids & evaluation_ids
    invalid_ids = sorted(page_id for page_id in matched_ids if evaluation_errors[page_id])
    scored_pairs = sum(pair["status"] == "scored" for pair in pairs)
    parents_fully_scored = sum(
        all(pair["status"] == "scored" for pair in pairs if pair["parent_page_id"] == parent_id)
        for parent_id in gold_by_parent
    )
    return {
        "schema_version": "1.0",
        "metric_family": "HebRobustnessPairs-1.0",
        "delta_definition": "degraded_minus_clean",
        "positive_delta_means": "degradation_increased_error",
        "metric_names": list(names),
        "coverage": {
            "parents": len(gold_by_parent),
            "parents_fully_scored": parents_fully_scored,
            "gold_pages": len(gold_ids),
            "control_pages": len(gold_by_parent),
            "child_pages": len(pairs),
            "submitted_metric_pages": len(evaluation_ids),
            "matched_metric_pages": len(matched_ids),
            "valid_metric_pages": sum(not evaluation_errors[page_id] for page_id in matched_ids),
            "missing_metric_pages": len(gold_ids - evaluation_ids),
            "missing_metric_page_ids": sorted(gold_ids - evaluation_ids),
            "invalid_metric_pages": len(invalid_ids),
            "invalid_metric_page_ids": invalid_ids,
            "extra_metric_pages": len(evaluation_ids - gold_ids),
            "extra_metric_page_ids": sorted(evaluation_ids - gold_ids),
            "expected_pairs": len(pairs),
            "scored_pairs": scored_pairs,
            "failed_pairs": len(pairs) - scored_pairs,
            "pair_coverage": scored_pairs / len(pairs),
        },
        "failures": {"count": len(failures), "pairs": failures},
        "summary": {
            "aggregation_unit": "child_control_pair",
            "macro": _group_summary(pairs, names),
            "by_family": {
                family: _group_summary(by_family[family], names) for family in sorted(by_family)
            },
            "by_level": {
                level: _group_summary(by_level[level], names) for level in sorted(by_level)
            },
        },
        "pairs": pairs,
    }


__all__ = [
    "ROBUSTNESS_METRIC_NAMES",
    "RobustnessMetricsError",
    "compute_paired_robustness",
]
