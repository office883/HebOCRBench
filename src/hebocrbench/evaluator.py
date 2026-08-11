"""End-to-end Hebrew OCR benchmark evaluation.

The strict score is always computed on NFC logical-order text.  Visual-order
proxies, punctuation folding, base-letter profiles, and order-tolerant views
are diagnostics only; none can rescue the primary score.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import gc
from statistics import mean
from typing import Any, Mapping, Sequence

from .alignment import AlignmentResult, error_rate, merge_alignments
from .bidi_metrics import (
    bracket_metrics,
    ltr_run_metrics,
    pairwise_word_order_accuracy,
    visual_order_diagnostic,
)
from .config import BenchmarkConfig
from .diacritics import (
    DiacriticEvaluation,
    evaluate_diacritics,
    merge_diacritic_evaluations,
)
from .form_metrics import evaluate_form
from .matching import match_units, remap_prediction_graph
from .reading_order import ReadingOrderCycleError, reading_order_metrics, topological_order
from .robustness_metrics import compute_paired_robustness
from .statistics import bootstrap_document_intervals, quantile
from .table_metrics import evaluate_table, match_tables
from .text_metrics import TextEvaluation, evaluate_text
from .unicode_utils import bidi_hygiene, codepoint_view

JsonObject = Mapping[str, Any]


def _coerce_config(config: BenchmarkConfig | Mapping[str, Any] | None) -> BenchmarkConfig:
    if config is None:
        return BenchmarkConfig()
    if isinstance(config, BenchmarkConfig):
        config.validate()
        return config
    return BenchmarkConfig.from_mapping(config)


@dataclass(slots=True)
class PageEvaluation:
    page_id: str
    document_id: str
    track: str
    metrics: dict[str, Any]
    details: dict[str, Any] = field(default_factory=dict)
    _line_text: list[TextEvaluation] = field(default_factory=list, repr=False)
    _page_text: TextEvaluation | None = field(default=None, repr=False)
    _diacritics: list[DiacriticEvaluation] = field(default_factory=list, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "document_id": self.document_id,
            "track": self.track,
            "metrics": self.metrics,
            "details": self.details,
        }


@dataclass(slots=True)
class EvaluationRun:
    metrics: dict[str, Any]
    pages: list[PageEvaluation]
    configuration: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": "HebOCRBench",
            "schema_version": "1.0",
            "configuration": self.configuration,
            "metrics": self.metrics,
            "pages": [page.to_dict() for page in self.pages],
        }


def _flatten_lines(page: JsonObject) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for region_index, region in enumerate(page.get("regions", [])):
        region_id = str(region.get("region_id", f"region-{region_index}"))
        for line_index, line in enumerate(region.get("lines", [])):
            item = dict(line)
            item["_region_id"] = region_id
            item["_region_index"] = region_index
            item["_line_index"] = line_index
            flattened.append(item)
    return flattened


def _line_pairs(
    gold_lines: Sequence[Mapping[str, Any]],
    prediction_lines: Sequence[Mapping[str, Any]],
    *,
    use_shared_ids: bool = True,
    iou_threshold: float = 0.30,
) -> list[tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, str]]:
    """Match lines under the configured oracle/blind assignment policy."""

    assignment = match_units(
        gold_lines,
        prediction_lines,
        id_key="line_id",
        use_shared_ids=use_shared_ids,
        iou_threshold=iou_threshold,
    )
    pairs: list[tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, str]] = []
    for match in assignment.matches:
        pairs.append(
            (
                gold_lines[match.gold_index],
                prediction_lines[match.prediction_index],
                match.method,
            )
        )
    for index in assignment.unmatched_gold_indices:
        pairs.append((gold_lines[index], None, "missing"))
    for index in assignment.unmatched_prediction_indices:
        pairs.append((None, prediction_lines[index], "extra"))
    return pairs


def _region_sort_key(region: Mapping[str, Any], original_index: int) -> tuple[int, int]:
    value = region.get("reading_index")
    return (int(value) if value is not None else 10**9, original_index)


def _ordered_regions(page: JsonObject) -> list[Mapping[str, Any]]:
    regions: list[Mapping[str, Any]] = list(page.get("regions", []))
    ids = [str(region.get("region_id", f"region-{i}")) for i, region in enumerate(regions)]
    by_id = dict(zip(ids, regions, strict=True))
    index = {region_id: i for i, region_id in enumerate(ids)}
    edges = page.get("reading_order", {}).get("edges", [])

    def fallback(region_id: str) -> tuple[int, int]:
        return _region_sort_key(by_id[region_id], index[region_id])

    try:
        order = topological_order(ids, edges, fallback_key=fallback)
    except (ReadingOrderCycleError, ValueError):
        order = sorted(ids, key=fallback)
    return [by_id[region_id] for region_id in order]


def _ordered_lines(region: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    lines: list[Mapping[str, Any]] = list(region.get("lines", []))

    def key(item: tuple[int, Mapping[str, Any]]) -> tuple[int, float, int]:
        original_index, line = item
        reading_index = line.get("reading_index")
        polygon = line.get("polygon", [])
        y = min((float(point[1]) for point in polygon), default=float(original_index))
        return (
            int(reading_index) if reading_index is not None else 10**9,
            y,
            original_index,
        )

    return [line for _, line in sorted(enumerate(lines), key=key)]


def page_logical_text(page: JsonObject) -> str:
    """Serialize a page in its declared reading order without changing BiDi text."""

    if "page_text" in page:
        return str(page.get("page_text", ""))
    region_texts: list[str] = []
    for region in _ordered_regions(page):
        line_texts = [str(line.get("text", "")) for line in _ordered_lines(region)]
        region_texts.append("\n".join(line_texts))
    return "\n\n".join(region_texts)


def _alignment_summary(evaluations: Sequence[TextEvaluation]) -> dict[str, AlignmentResult]:
    if not evaluations:
        empty = AlignmentResult(n_ref=0, n_pred=0)
        return {
            "codepoint": empty,
            "grapheme": AlignmentResult(n_ref=0, n_pred=0),
            "word": AlignmentResult(n_ref=0, n_pred=0),
            "base_letter": AlignmentResult(n_ref=0, n_pred=0),
            "punctuation": AlignmentResult(n_ref=0, n_pred=0),
        }
    return {
        "codepoint": merge_alignments([item.codepoint for item in evaluations]),
        "grapheme": merge_alignments([item.grapheme for item in evaluations]),
        "word": merge_alignments([item.word for item in evaluations]),
        "base_letter": merge_alignments([item.base_letter for item in evaluations]),
        "punctuation": merge_alignments([item.punctuation for item in evaluations]),
    }


def _safe_rate(correct: int, reference: int) -> float:
    return 1.0 if reference == 0 else correct / reference


def _micro_average_scalar(items: Sequence[Mapping[str, Any]], key: str) -> float:
    return 0.0 if not items else mean(float(item.get(key, 0.0)) for item in items)


def _combine_geometry(page_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    gold = sum(int(item.get("gold_count", 0)) for item in page_results)
    pred = sum(int(item.get("prediction_count", 0)) for item in page_results)
    matched = sum(int(item.get("matched", 0)) for item in page_results)
    precision = 1.0 if gold == pred == 0 else matched / max(1, pred)
    recall = 1.0 if gold == pred == 0 else matched / max(1, gold)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    weighted_iou_num = sum(
        float(item.get("mean_iou", 0.0)) * int(item.get("matched", 0)) for item in page_results
    )
    return {
        "gold_count": gold,
        "prediction_count": pred,
        "matched": matched,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_iou": weighted_iou_num / max(1, matched),
        "split_gold_items": sum(int(item.get("split_gold_items", 0)) for item in page_results),
        "merged_prediction_items": sum(
            int(item.get("merged_prediction_items", 0)) for item in page_results
        ),
        "type_correct": sum(int(item.get("type_correct", 0)) for item in page_results),
        "type_accuracy": sum(int(item.get("type_correct", 0)) for item in page_results)
        / max(1, matched),
    }


def _evaluate_tables(gold: JsonObject, prediction: JsonObject) -> dict[str, Any]:
    gold_tables: list[Mapping[str, Any]] = list(gold.get("tables", []))
    pred_tables: list[Mapping[str, Any]] = list(prediction.get("tables", []))
    assignment = match_tables(gold_tables, pred_tables)
    per_table: list[dict[str, Any]] = []

    for match in assignment.matches:
        gold_table = gold_tables[match.gold_index]
        pred_table = pred_tables[match.prediction_index]
        result = evaluate_table(gold_table, pred_table)
        result.update(
            {
                "gold_table_id": gold_table.get("table_id"),
                "prediction_table_id": pred_table.get("table_id"),
                "match_score": match.score,
                "matched_by": match.method,
                "match_location_iou": match.location_iou,
                "match_structure_score": match.structure_score,
                "presence_status": "matched",
            }
        )
        per_table.append(result)
    for index in assignment.unmatched_gold_indices:
        gold_table = gold_tables[index]
        result = evaluate_table(
            gold_table,
            {"n_rows": 0, "n_cols": 0, "cells": []},
        )
        result.update(
            {
                "gold_table_id": gold_table.get("table_id"),
                "prediction_table_id": None,
                "match_score": 0.0,
                "matched_by": "missing",
                "presence_status": "missing",
            }
        )
        per_table.append(result)
    for index in assignment.unmatched_prediction_indices:
        pred_table = pred_tables[index]
        result = evaluate_table(
            {"n_rows": 0, "n_cols": 0, "cells": []},
            pred_table,
        )
        result.update(
            {
                "gold_table_id": None,
                "prediction_table_id": pred_table.get("table_id"),
                "match_score": 0.0,
                "matched_by": "extra",
                "presence_status": "extra",
            }
        )
        per_table.append(result)

    alignments = [_alignment_from_dict(item["text_alignment"]) for item in per_table]
    merged = merge_alignments(alignments)
    return {
        "metric_family": "HebGrid-1.0",
        "gold_tables": len(gold_tables),
        "prediction_tables": len(pred_tables),
        "matched_tables": len(assignment.matches),
        "missing_tables": len(assignment.unmatched_gold_indices),
        "hallucinated_tables": len(assignment.unmatched_prediction_indices),
        "table_presence_precision": assignment.precision,
        "table_presence_recall": assignment.recall,
        "table_presence_f1": assignment.f1,
        "cell_text_gcer": error_rate(merged),
        "cell_span_f1": (
            mean(float(item["cell_span_f1"]) for item in per_table) if per_table else 1.0
        ),
        "grid_slot_accuracy": (
            mean(float(item["grid_slot_accuracy"]) for item in per_table) if per_table else 1.0
        ),
        "per_table": per_table,
    }


def _alignment_from_dict(value: Mapping[str, Any]) -> AlignmentResult:
    return AlignmentResult(
        n_ref=int(value.get("n_ref", 0)),
        n_pred=int(value.get("n_pred", 0)),
        substitutions=int(value.get("substitutions", 0)),
        deletions=int(value.get("deletions", 0)),
        insertions=int(value.get("insertions", 0)),
        correct=int(value.get("correct", 0)),
    )


def evaluate_page(
    gold: JsonObject,
    prediction: JsonObject,
    *,
    config: BenchmarkConfig | Mapping[str, Any] | None = None,
    include_line_details: bool = True,
) -> PageEvaluation:
    """Evaluate one page, including recognition, order, layout and structure."""

    effective_config = _coerce_config(config)
    page_id = str(gold["page_id"])
    if str(prediction.get("page_id", page_id)) != page_id:
        raise ValueError(
            f"Prediction page_id {prediction.get('page_id')!r} does not match {page_id!r}"
        )

    gold_lines = _flatten_lines(gold)
    pred_lines = _flatten_lines(prediction)
    pairs = _line_pairs(
        gold_lines,
        pred_lines,
        use_shared_ids=effective_config.matching.use_shared_ids,
        iou_threshold=effective_config.matching.line_iou_threshold,
    )
    line_evaluations: list[TextEvaluation] = []
    diacritic_evaluations: list[DiacriticEvaluation] = []
    line_details: list[dict[str, Any]] = []

    visual_suspected = 0
    bidi_candidate_lines = 0
    ltr_ref = ltr_correct = numeric_ref = numeric_correct = 0
    bracket_ref = bracket_correct = 0
    word_pairs = word_correct = 0
    hygiene_totals: defaultdict[str, int] = defaultdict(int)

    for gold_line, pred_line, matched_by in pairs:
        reference = "" if gold_line is None else str(gold_line.get("text", ""))
        predicted = "" if pred_line is None else str(pred_line.get("text", ""))
        text_result = evaluate_text(reference, predicted)
        line_evaluations.append(text_result)
        diacritics = evaluate_diacritics(reference, predicted)
        diacritic_evaluations.append(diacritics)

        visual = visual_order_diagnostic(reference, predicted)
        ltr = ltr_run_metrics(reference, predicted)
        brackets = bracket_metrics(reference, predicted)
        words = pairwise_word_order_accuracy(reference, predicted)
        hygiene = bidi_hygiene(predicted)

        if reference:
            bidi_candidate_lines += 1
            visual_suspected += int(bool(visual["visual_order_suspected"]))
        ltr_ref += int(ltr["reference_count"])
        ltr_correct += int(ltr["correct_count"])
        numeric_ref += int(ltr["numeric_reference_count"])
        numeric_correct += int(ltr["numeric_correct_count"])
        bracket_alignment = brackets["alignment"]
        bracket_ref += int(bracket_alignment["n_ref"])
        bracket_correct += int(bracket_alignment["correct"])
        word_pairs += int(words["comparable_pairs"])
        word_correct += int(words["concordant_pairs"])
        for key in (
            "bidi_control_count",
            "unbalanced_embeddings",
            "unbalanced_isolates",
            "zero_width_count",
            "replacement_character_count",
            "private_use_count",
            "presentation_form_count",
        ):
            hygiene_totals[key] += int(hygiene.get(key, 0))

        if include_line_details:
            line_details.append(
                {
                    "gold_line_id": None if gold_line is None else gold_line.get("line_id"),
                    "prediction_line_id": None if pred_line is None else pred_line.get("line_id"),
                    "matched_by": matched_by,
                    "reference": reference,
                    "prediction": predicted,
                    "reference_codepoints": codepoint_view(reference),
                    "prediction_codepoints": codepoint_view(predicted),
                    "text": text_result.to_dict(),
                    "diacritics": diacritics.to_dict(),
                    "bidi": {
                        "visual_order": visual,
                        "ltr_runs": ltr,
                        "brackets": brackets,
                        "word_order": words,
                        "hygiene": hygiene,
                    },
                }
            )

    alignments = _alignment_summary(line_evaluations)
    exact_lines = sum(item.exact for item in line_evaluations)
    page_text_result = evaluate_text(page_logical_text(gold), page_logical_text(prediction))
    merged_diacritics = merge_diacritic_evaluations(diacritic_evaluations)

    gold_regions = list(gold.get("regions", []))
    pred_regions = list(prediction.get("regions", []))
    region_assignment = match_units(
        gold_regions,
        pred_regions,
        id_key="region_id",
        use_shared_ids=effective_config.matching.use_shared_ids,
        iou_threshold=effective_config.matching.region_iou_threshold,
    )
    line_assignment = match_units(
        gold_lines,
        pred_lines,
        id_key="line_id",
        use_shared_ids=effective_config.matching.use_shared_ids,
        iou_threshold=effective_config.matching.line_iou_threshold,
    )
    region_type_correct = sum(
        1
        for match in region_assignment.matches
        if str(gold_regions[match.gold_index].get("type", ""))
        == str(pred_regions[match.prediction_index].get("type", ""))
    )
    region_layout_dict = region_assignment.to_dict()
    region_layout_dict["type_correct"] = region_type_correct
    region_layout_dict["type_accuracy"] = region_type_correct / max(
        1, len(region_assignment.matches)
    )

    gold_region_ids = [str(region.get("region_id", "")) for region in gold_regions]
    pred_region_ids = [str(region.get("region_id", "")) for region in pred_regions]
    gold_edges = gold.get("reading_order", {}).get("edges", [])
    pred_edges = prediction.get("reading_order", {}).get("edges", [])
    if not pred_edges and len(pred_region_ids) > 1:
        pred_ordered_ids = [
            str(region.get("region_id", "")) for region in _ordered_regions(prediction)
        ]
        pred_edges = [
            [pred_ordered_ids[i], pred_ordered_ids[i + 1]] for i in range(len(pred_ordered_ids) - 1)
        ]
    remapped_nodes, remapped_edges, remap_evidence = remap_prediction_graph(
        prediction_nodes=pred_region_ids,
        prediction_edges=pred_edges,
        assignment=region_assignment,
    )
    order = reading_order_metrics(
        gold_nodes=gold_region_ids,
        gold_edges=gold_edges,
        prediction_nodes=remapped_nodes,
        prediction_edges=remapped_edges,
    )
    order["assignment"] = region_assignment.to_dict()
    order["remap"] = remap_evidence

    table_result = _evaluate_tables(gold, prediction)
    form_result = evaluate_form(
        list(gold.get("form_fields", [])), list(prediction.get("form_fields", []))
    )

    recognition = {
        "gold_lines": len(gold_lines),
        "prediction_lines": len(pred_lines),
        "matched_or_accounted_line_pairs": len(line_evaluations),
        "exact_lines": exact_lines,
        "line_cer": error_rate(alignments["codepoint"]),
        "line_gcer": error_rate(alignments["grapheme"]),
        "line_gcer_normalized": (
            alignments["grapheme"].distance
            / max(1, alignments["grapheme"].distance + alignments["grapheme"].correct)
        ),
        "grapheme_substitution_rate": alignments["grapheme"].substitutions
        / max(1, alignments["grapheme"].n_ref),
        "grapheme_deletion_rate": alignments["grapheme"].deletions
        / max(1, alignments["grapheme"].n_ref),
        "grapheme_insertion_rate": alignments["grapheme"].insertions
        / max(1, alignments["grapheme"].n_ref),
        "line_wer": error_rate(alignments["word"]),
        "base_letter_cer": error_rate(alignments["base_letter"]),
        "punctuation_error_rate": error_rate(alignments["punctuation"]),
        "line_exact_rate": exact_lines / max(1, len(line_evaluations)),
        "page_order_cer": page_text_result.codepoint_rate,
        "page_order_gcer": page_text_result.grapheme_rate,
        "reading_order_penalty_gcer": page_text_result.grapheme_rate
        - error_rate(alignments["grapheme"]),
        "page_order_wer": page_text_result.word_rate,
        "page_exact": page_text_result.exact,
        "final_letter_confusions": sum(item.final_letter_confusions for item in line_evaluations),
        "alignments": {name: value.to_dict() for name, value in alignments.items()},
        "page_alignment": page_text_result.to_dict(),
    }
    bidi = {
        "visual_order_failure_count": visual_suspected,
        "visual_order_failure_rate": visual_suspected / max(1, bidi_candidate_lines),
        "ltr_run_reference_count": ltr_ref,
        "ltr_run_exact_count": ltr_correct,
        "ltr_run_exact_rate": _safe_rate(ltr_correct, ltr_ref),
        "numeric_reference_count": numeric_ref,
        "numeric_exact_count": numeric_correct,
        "numeric_exact_rate": _safe_rate(numeric_correct, numeric_ref),
        "bracket_reference_count": bracket_ref,
        "bracket_exact_count": bracket_correct,
        "bracket_exact_rate": _safe_rate(bracket_correct, bracket_ref),
        "word_order_comparable_pairs": word_pairs,
        "word_order_correct_pairs": word_correct,
        "pairwise_word_order_accuracy": _safe_rate(word_correct, word_pairs),
        **dict(hygiene_totals),
    }

    metrics: dict[str, Any] = {
        "recognition": recognition,
        "diacritics": merged_diacritics.to_dict(),
        "bidi": bidi,
        "layout": {
            "regions": region_layout_dict,
            "lines": line_assignment.to_dict(),
        },
        "reading_order": order,
        "tables": table_result,
        "forms": form_result,
        "operational": {
            "timing_ms": float(prediction["timing_ms"])
            if isinstance(prediction.get("timing_ms"), (int, float))
            else None,
            "cost_usd": float(prediction["cost_usd"])
            if isinstance(prediction.get("cost_usd"), (int, float))
            else None,
            "retries": int(prediction.get("retries", 0) or 0),
            "api_failures": int(prediction.get("api_failures", 0) or 0),
        },
    }
    return PageEvaluation(
        page_id=page_id,
        document_id=str(gold.get("document_id", page_id)),
        track=str(gold.get("track", "unknown")),
        metrics=metrics,
        details=(
            {
                "line_results": line_details,
                "gold_page_text": page_logical_text(gold),
                "prediction_page_text": page_logical_text(prediction),
            }
            if include_line_details
            else {"line_results": [], "line_details_compacted": True}
        ),
        # Dataset aggregation consumes the already-computed alignment counters
        # in ``metrics``.  Retaining every line TextEvaluation duplicated long
        # strings and confusion maps across the 4,900-page robustness track.
        _line_text=[],
        _page_text=None,
        _diacritics=[merged_diacritics],
    )


def _aggregate_page_group(pages: Sequence[PageEvaluation]) -> dict[str, Any]:
    line_alignments = {
        name: merge_alignments(
            [
                _alignment_from_dict(page.metrics["recognition"]["alignments"][name])
                for page in pages
            ]
        )
        for name in ("codepoint", "grapheme", "word", "base_letter", "punctuation")
    }
    exact = sum(int(page.metrics["recognition"]["exact_lines"]) for page in pages)
    line_pairs = sum(
        int(page.metrics["recognition"]["matched_or_accounted_line_pairs"]) for page in pages
    )
    page_graphemes = merge_alignments(
        [
            _alignment_from_dict(page.metrics["recognition"]["page_alignment"]["grapheme"])
            for page in pages
        ]
    )
    return {
        "pages": len(pages),
        "documents": len({page.document_id for page in pages}),
        "gold_lines": sum(int(page.metrics["recognition"]["gold_lines"]) for page in pages),
        "line_cer": error_rate(line_alignments["codepoint"]),
        "line_gcer": error_rate(line_alignments["grapheme"]),
        "line_wer": error_rate(line_alignments["word"]),
        "base_letter_cer": error_rate(line_alignments["base_letter"]),
        "line_exact_rate": exact / max(1, line_pairs),
        "page_order_gcer": error_rate(page_graphemes),
    }


def _aggregate_tables(pages: Sequence[PageEvaluation]) -> dict[str, Any]:
    per_table = [table for page in pages for table in page.metrics["tables"].get("per_table", [])]
    gold = sum(int(page.metrics["tables"].get("gold_tables", 0)) for page in pages)
    pred = sum(int(page.metrics["tables"].get("prediction_tables", 0)) for page in pages)
    matched = sum(int(page.metrics["tables"].get("matched_tables", 0)) for page in pages)
    missing = sum(int(page.metrics["tables"].get("missing_tables", 0)) for page in pages)
    hallucinated = sum(int(page.metrics["tables"].get("hallucinated_tables", 0)) for page in pages)
    precision = 1.0 if gold == pred == 0 else matched / max(1, pred)
    recall = 1.0 if gold == pred == 0 else matched / max(1, gold)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    if not per_table:
        return {
            "metric_family": "HebGrid-1.0",
            "gold_tables": gold,
            "prediction_tables": pred,
            "matched_tables": matched,
            "missing_tables": missing,
            "hallucinated_tables": hallucinated,
            "table_presence_precision": precision,
            "table_presence_recall": recall,
            "table_presence_f1": f1,
            "cell_text_gcer": 0.0,
            "cell_span_f1": 1.0,
            "grid_slot_accuracy": 1.0,
        }
    merged = merge_alignments([_alignment_from_dict(item["text_alignment"]) for item in per_table])
    return {
        "metric_family": "HebGrid-1.0",
        "gold_tables": gold,
        "prediction_tables": pred,
        "matched_tables": matched,
        "missing_tables": missing,
        "hallucinated_tables": hallucinated,
        "table_presence_precision": precision,
        "table_presence_recall": recall,
        "table_presence_f1": f1,
        "cell_text_gcer": error_rate(merged),
        "cell_span_f1": mean(float(item["cell_span_f1"]) for item in per_table),
        "grid_slot_accuracy": mean(float(item["grid_slot_accuracy"]) for item in per_table),
    }


def _aggregate_forms(pages: Sequence[PageEvaluation]) -> dict[str, Any]:
    gold = sum(int(page.metrics["forms"]["gold_fields"]) for page in pages)
    pred = sum(int(page.metrics["forms"]["prediction_fields"]) for page in pages)
    matched = sum(int(page.metrics["forms"]["matched_fields"]) for page in pages)
    exact_num = sum(
        float(page.metrics["forms"]["value_exact_rate"]) * int(page.metrics["forms"]["gold_fields"])
        for page in pages
    )
    alignments = [_alignment_from_dict(page.metrics["forms"]["value_alignment"]) for page in pages]
    merged = merge_alignments(alignments)
    precision = 1.0 if gold == pred == 0 else matched / max(1, pred)
    recall = 1.0 if gold == pred == 0 else matched / max(1, gold)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "gold_fields": gold,
        "prediction_fields": pred,
        "matched_fields": matched,
        "field_presence_precision": precision,
        "field_presence_recall": recall,
        "field_presence_f1": f1,
        "value_exact_rate": exact_num / max(1, gold),
        "value_gcer": error_rate(merged),
    }


def _conformance_gate(pages: Sequence[PageEvaluation], config: BenchmarkConfig) -> dict[str, Any]:
    threshold = config.conformance
    diagnostic = [page for page in pages if page.track == threshold.diagnostic_track]
    if not diagnostic:
        return {
            "status": "not_evaluated",
            "reason": f"No pages in track={threshold.diagnostic_track}",
            "failed_checks": [],
        }
    group = _aggregate_page_group(diagnostic)
    bidi_ref = sum(int(page.metrics["bidi"]["ltr_run_reference_count"]) for page in diagnostic)
    bidi_ok = sum(int(page.metrics["bidi"]["ltr_run_exact_count"]) for page in diagnostic)
    numeric_ref = sum(int(page.metrics["bidi"]["numeric_reference_count"]) for page in diagnostic)
    numeric_ok = sum(int(page.metrics["bidi"]["numeric_exact_count"]) for page in diagnostic)
    bracket_ref = sum(int(page.metrics["bidi"]["bracket_reference_count"]) for page in diagnostic)
    bracket_ok = sum(int(page.metrics["bidi"]["bracket_exact_count"]) for page in diagnostic)
    visual_failures = sum(
        int(page.metrics["bidi"]["visual_order_failure_count"]) for page in diagnostic
    )
    controls = sum(int(page.metrics["bidi"]["bidi_control_count"]) for page in diagnostic)
    unbalanced = sum(
        int(page.metrics["bidi"]["unbalanced_embeddings"])
        + int(page.metrics["bidi"]["unbalanced_isolates"])
        for page in diagnostic
    )

    ltr_rate = _safe_rate(bidi_ok, bidi_ref)
    numeric_rate = _safe_rate(numeric_ok, numeric_ref)
    bracket_rate = _safe_rate(bracket_ok, bracket_ref)
    checks = {
        f"strict_line_exact_rate>={threshold.min_exact_line_rate}": (
            group["line_exact_rate"] >= threshold.min_exact_line_rate
        ),
        f"ltr_run_exact_rate>={threshold.min_ltr_run_exact_rate}": (
            ltr_rate >= threshold.min_ltr_run_exact_rate
        ),
        f"numeric_exact_rate>={threshold.min_numeric_exact_rate}": (
            numeric_rate >= threshold.min_numeric_exact_rate
        ),
        f"bracket_exact_rate>={threshold.min_bracket_exact_rate}": (
            bracket_rate >= threshold.min_bracket_exact_rate
        ),
        f"visual_order_failure_count<={threshold.max_visual_order_failure_count}": (
            visual_failures <= threshold.max_visual_order_failure_count
        ),
        f"bidi_control_count<={threshold.max_bidi_control_count}": (
            controls <= threshold.max_bidi_control_count
        ),
        f"unbalanced_bidi_controls<={threshold.max_unbalanced_bidi_controls}": (
            unbalanced <= threshold.max_unbalanced_bidi_controls
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "status": "conformant" if not failed else "non_conformant",
        "failed_checks": failed,
        "checks": checks,
        "diagnostic_track": threshold.diagnostic_track,
        "diagnostic_pages": len(diagnostic),
        "strict_line_exact_rate": group["line_exact_rate"],
        "ltr_run_exact_rate": ltr_rate,
        "numeric_exact_rate": numeric_rate,
        "bracket_exact_rate": bracket_rate,
        "visual_order_failure_count": visual_failures,
        "bidi_control_count": controls,
        "unbalanced_bidi_controls": unbalanced,
        "thresholds": {
            "min_exact_line_rate": threshold.min_exact_line_rate,
            "min_ltr_run_exact_rate": threshold.min_ltr_run_exact_rate,
            "min_numeric_exact_rate": threshold.min_numeric_exact_rate,
            "min_bracket_exact_rate": threshold.min_bracket_exact_rate,
            "max_visual_order_failure_count": threshold.max_visual_order_failure_count,
            "max_bidi_control_count": threshold.max_bidi_control_count,
            "max_unbalanced_bidi_controls": threshold.max_unbalanced_bidi_controls,
        },
    }


def _aggregate_operational(pages: Sequence[PageEvaluation]) -> dict[str, Any]:
    timings = [
        float(page.metrics["operational"]["timing_ms"])
        for page in pages
        if page.metrics["operational"].get("timing_ms") is not None
    ]
    costs = [
        float(page.metrics["operational"]["cost_usd"])
        for page in pages
        if page.metrics["operational"].get("cost_usd") is not None
    ]
    total_ms = sum(timings)
    return {
        "evaluated_pages": len(pages),
        "timed_pages": len(timings),
        "latency_ms_p50": quantile(timings, 0.50) if timings else None,
        "latency_ms_p95": quantile(timings, 0.95) if timings else None,
        "latency_ms_mean": mean(timings) if timings else None,
        "throughput_pages_per_minute": (
            len(timings) / (total_ms / 60000.0) if total_ms > 0 else None
        ),
        "costed_pages": len(costs),
        "total_cost_usd": sum(costs) if costs else None,
        "mean_cost_usd_per_page": mean(costs) if costs else None,
        "retries": sum(int(page.metrics["operational"].get("retries", 0)) for page in pages),
        "api_failures": sum(
            int(page.metrics["operational"].get("api_failures", 0)) for page in pages
        ),
    }


def evaluate_dataset(
    gold_pages: Sequence[JsonObject],
    prediction_pages: Sequence[JsonObject],
    *,
    config: BenchmarkConfig | Mapping[str, Any] | None = None,
    slice_fields: Sequence[str] | None = None,
) -> EvaluationRun:
    """Evaluate a dataset without repeated cyclic-GC scans of the page graph."""

    gc_was_enabled = gc.isenabled()
    if gc_was_enabled:
        gc.disable()
    try:
        return _evaluate_dataset_impl(
            gold_pages,
            prediction_pages,
            config=config,
            slice_fields=slice_fields,
        )
    finally:
        if gc_was_enabled:
            gc.enable()


def _evaluate_dataset_impl(
    gold_pages: Sequence[JsonObject],
    prediction_pages: Sequence[JsonObject],
    *,
    config: BenchmarkConfig | Mapping[str, Any] | None = None,
    slice_fields: Sequence[str] | None = None,
) -> EvaluationRun:
    """Evaluate a dataset and publish micro, macro, slice and uncertainty views."""

    effective_config = _coerce_config(config)
    active_slice_fields = tuple(slice_fields or effective_config.slice_fields)
    gold_tracks = {str(page.get("track", "")) for page in gold_pages}
    compact_line_details = gold_tracks == {"modern_robustness"}
    gold_index = {str(page["page_id"]): page for page in gold_pages}
    prediction_index = {str(page["page_id"]): page for page in prediction_pages}
    missing_ids = sorted(set(gold_index) - set(prediction_index))
    extra_ids = sorted(set(prediction_index) - set(gold_index))

    pages: list[PageEvaluation] = []
    for page_id, gold in gold_index.items():
        prediction = prediction_index.get(
            page_id,
            {
                "schema_version": "1.0",
                "page_id": page_id,
                "regions": [],
                "reading_order": {"edges": []},
                "tables": [],
                "form_fields": [],
                "model": {"name": "missing-prediction"},
            },
        )
        pages.append(
            evaluate_page(
                gold,
                prediction,
                config=effective_config,
                include_line_details=not compact_line_details,
            )
        )

    alignments = {
        name: merge_alignments(
            [
                _alignment_from_dict(page.metrics["recognition"]["alignments"][name])
                for page in pages
            ]
        )
        for name in ("codepoint", "grapheme", "word", "base_letter", "punctuation")
    }
    page_codepoint = merge_alignments(
        [
            _alignment_from_dict(page.metrics["recognition"]["page_alignment"]["codepoint"])
            for page in pages
        ]
    )
    page_grapheme = merge_alignments(
        [
            _alignment_from_dict(page.metrics["recognition"]["page_alignment"]["grapheme"])
            for page in pages
        ]
    )
    page_word = merge_alignments(
        [
            _alignment_from_dict(page.metrics["recognition"]["page_alignment"]["word"])
            for page in pages
        ]
    )
    exact_lines = sum(int(page.metrics["recognition"]["exact_lines"]) for page in pages)
    line_pairs = sum(
        int(page.metrics["recognition"]["matched_or_accounted_line_pairs"]) for page in pages
    )

    per_document: dict[str, list[PageEvaluation]] = defaultdict(list)
    for page in pages:
        per_document[page.document_id].append(page)
    macro_page = (
        mean(float(page.metrics["recognition"]["line_gcer"]) for page in pages) if pages else 0.0
    )
    macro_document = (
        mean(
            _aggregate_page_group(document_pages)["line_gcer"]
            for document_pages in per_document.values()
        )
        if per_document
        else 0.0
    )
    grapheme_alignment = alignments["grapheme"]
    recognition = {
        "gold_pages": len(gold_pages),
        "evaluated_pages": len(pages),
        "gold_lines": sum(int(page.metrics["recognition"]["gold_lines"]) for page in pages),
        "prediction_lines": sum(
            int(page.metrics["recognition"]["prediction_lines"]) for page in pages
        ),
        "line_cer": error_rate(alignments["codepoint"]),
        "line_gcer": error_rate(grapheme_alignment),
        "line_gcer_normalized": grapheme_alignment.distance
        / max(1, grapheme_alignment.distance + grapheme_alignment.correct),
        "line_wer": error_rate(alignments["word"]),
        "base_letter_cer": error_rate(alignments["base_letter"]),
        "punctuation_error_rate": error_rate(alignments["punctuation"]),
        "line_exact_rate": exact_lines / max(1, line_pairs),
        "page_order_cer": error_rate(page_codepoint),
        "page_order_gcer": error_rate(page_grapheme),
        "page_order_wer": error_rate(page_word),
        "reading_order_penalty_gcer": error_rate(page_grapheme) - error_rate(grapheme_alignment),
        "page_exact_rate": sum(
            int(bool(page.metrics["recognition"]["page_exact"])) for page in pages
        )
        / max(1, len(pages)),
        "macro_page_line_gcer": macro_page,
        "macro_document_line_gcer": macro_document,
        "grapheme_substitution_rate": grapheme_alignment.substitutions
        / max(1, grapheme_alignment.n_ref),
        "grapheme_deletion_rate": grapheme_alignment.deletions / max(1, grapheme_alignment.n_ref),
        "grapheme_insertion_rate": grapheme_alignment.insertions / max(1, grapheme_alignment.n_ref),
        "final_letter_confusions": sum(
            int(page.metrics["recognition"]["final_letter_confusions"]) for page in pages
        ),
        "alignments": {name: value.to_dict() for name, value in alignments.items()},
    }

    all_diacritics = [item for page in pages for item in page._diacritics]
    diacritics = merge_diacritic_evaluations(all_diacritics).to_dict()

    bidi_sums: defaultdict[str, int] = defaultdict(int)
    for page in pages:
        for key in (
            "visual_order_failure_count",
            "ltr_run_reference_count",
            "ltr_run_exact_count",
            "numeric_reference_count",
            "numeric_exact_count",
            "bracket_reference_count",
            "bracket_exact_count",
            "word_order_comparable_pairs",
            "word_order_correct_pairs",
            "bidi_control_count",
            "unbalanced_embeddings",
            "unbalanced_isolates",
            "zero_width_count",
            "replacement_character_count",
            "private_use_count",
            "presentation_form_count",
        ):
            bidi_sums[key] += int(page.metrics["bidi"].get(key, 0))
    bidi_line_count = sum(
        1 for page in pages for detail in page.details["line_results"] if detail["reference"]
    )
    bidi = {
        **dict(bidi_sums),
        "visual_order_failure_rate": bidi_sums["visual_order_failure_count"]
        / max(1, bidi_line_count),
        "ltr_run_exact_rate": _safe_rate(
            bidi_sums["ltr_run_exact_count"], bidi_sums["ltr_run_reference_count"]
        ),
        "numeric_exact_rate": _safe_rate(
            bidi_sums["numeric_exact_count"], bidi_sums["numeric_reference_count"]
        ),
        "bracket_exact_rate": _safe_rate(
            bidi_sums["bracket_exact_count"], bidi_sums["bracket_reference_count"]
        ),
        "pairwise_word_order_accuracy": _safe_rate(
            bidi_sums["word_order_correct_pairs"],
            bidi_sums["word_order_comparable_pairs"],
        ),
    }

    region_layouts = [page.metrics["layout"]["regions"] for page in pages]
    line_layouts = [page.metrics["layout"]["lines"] for page in pages]
    layout = {
        "regions": _combine_geometry(region_layouts),
        "lines": _combine_geometry(line_layouts),
    }

    gold_edges = sum(int(page.metrics["reading_order"]["gold_edges"]) for page in pages)
    pred_edges = sum(int(page.metrics["reading_order"]["prediction_edges"]) for page in pages)
    edge_correct = sum(
        round(
            float(page.metrics["reading_order"]["edge_recall"])
            * int(page.metrics["reading_order"]["gold_edges"])
        )
        for page in pages
    )
    comparable = sum(int(page.metrics["reading_order"]["comparable_pairs"]) for page in pages)
    correct_pairs = sum(int(page.metrics["reading_order"]["correct_pairs"]) for page in pages)
    edge_precision = edge_correct / max(1, pred_edges)
    edge_recall = edge_correct / max(1, gold_edges)
    reading = {
        "gold_edges": gold_edges,
        "prediction_edges": pred_edges,
        "edge_precision": edge_precision,
        "edge_recall": edge_recall,
        "edge_f1": 0.0
        if edge_precision + edge_recall == 0
        else 2 * edge_precision * edge_recall / (edge_precision + edge_recall),
        "comparable_pairs": comparable,
        "correct_pairs": correct_pairs,
        "pairwise_accuracy": _safe_rate(correct_pairs, comparable),
    }

    page_by_id = {page.page_id: page for page in pages}
    slices: dict[str, Any] = {}
    for field_name in active_slice_fields:
        groups: dict[str, list[PageEvaluation]] = defaultdict(list)
        for gold in gold_pages:
            if field_name == "track":
                value = gold.get("track")
            elif field_name.startswith("metadata."):
                value = gold.get("metadata", {}).get(field_name.split(".", 1)[1])
            else:
                value = gold.get(field_name)
            if value is None:
                continue
            groups[str(value)].append(page_by_id[str(gold["page_id"])])
        label = field_name.split(".")[-1]
        for value, group_pages in groups.items():
            slices[f"{label}={value}"] = _aggregate_page_group(group_pages)

    page_scores = [
        {
            "page_id": page.page_id,
            "document_id": page.document_id,
            "track": page.track,
            "line_gcer": float(page.metrics["recognition"]["line_gcer"]),
            "page_order_gcer": float(page.metrics["recognition"]["page_order_gcer"]),
        }
        for page in pages
    ]
    line_page_values = [float(item["line_gcer"]) for item in page_scores]
    distribution = {
        "page_line_gcer_p50": quantile(line_page_values, 0.50),
        "page_line_gcer_p90": quantile(line_page_values, 0.90),
        "page_line_gcer_p95": quantile(line_page_values, 0.95),
        "page_line_gcer_max": max(line_page_values, default=0.0),
        "worst_pages": sorted(
            page_scores,
            key=lambda item: (item["line_gcer"], item["page_order_gcer"]),
            reverse=True,
        )[: effective_config.statistics.worst_page_count],
        "worst_slices": [
            {"slice": name, **values}
            for name, values in sorted(
                (
                    (name, values)
                    for name, values in slices.items()
                    if int(values.get("pages", 0))
                    >= effective_config.statistics.worst_slice_min_pages
                ),
                key=lambda item: float(item[1].get("line_gcer", 0.0)),
                reverse=True,
            )[:20]
        ],
    }

    intervals = bootstrap_document_intervals(
        pages,
        samples=effective_config.statistics.bootstrap_samples,
        seed=effective_config.statistics.seed,
        confidence=effective_config.statistics.confidence,
    )
    metrics = {
        "coverage": {
            "gold_pages": len(gold_index),
            "submitted_prediction_pages": len(prediction_index),
            "matched_prediction_pages": len(set(gold_index) & set(prediction_index)),
            "missing_prediction_pages": len(missing_ids),
            "extra_prediction_pages": len(extra_ids),
            "missing_page_ids": missing_ids,
            "extra_page_ids": extra_ids,
        },
        "recognition": recognition,
        "diacritics": diacritics,
        "bidi": bidi,
        "layout": layout,
        "reading_order": reading,
        "tables": _aggregate_tables(pages),
        "forms": _aggregate_forms(pages),
        "operational": _aggregate_operational(pages),
        "distribution": distribution,
        "confidence_intervals": intervals,
        "slices": dict(sorted(slices.items())),
        "conformance": _conformance_gate(pages, effective_config),
    }
    if gold_tracks == {"modern_robustness"}:
        metrics["robustness_pairs"] = compute_paired_robustness(
            gold_pages,
            [page.to_dict() for page in pages],
        )
    configuration = effective_config.to_dict()
    configuration.update(
        {
            "strict_text_normalization": (
                "NFC + normalized newlines; BiDi controls scored separately"
            ),
            "text_order": "Unicode logical order",
            "visual_order_rescue": False,
            "active_slice_fields": list(active_slice_fields),
            "line_error_details_compacted": compact_line_details,
        }
    )
    return EvaluationRun(metrics=metrics, pages=pages, configuration=configuration)
