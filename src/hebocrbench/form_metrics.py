"""Closed-schema key-value form evaluation."""

from __future__ import annotations

from typing import Mapping, Sequence

from .alignment import align_sequences, error_rate, merge_alignments
from .unicode_utils import graphemes, normalize_strict


def evaluate_form(
    gold_fields: Sequence[Mapping[str, object]],
    prediction_fields: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    gold = {str(field["field_id"]): field for field in gold_fields}
    pred = {str(field["field_id"]): field for field in prediction_fields}
    common = set(gold) & set(pred)
    precision = len(common) / max(1, len(pred))
    recall = len(common) / max(1, len(gold))
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

    alignments = []
    exact = 0
    for field_id, gold_field in gold.items():
        gold_value = normalize_strict(str(gold_field.get("value_text", "")))
        pred_value = normalize_strict(str(pred.get(field_id, {}).get("value_text", "")))
        alignments.append(align_sequences(graphemes(gold_value), graphemes(pred_value)))
        if field_id in pred and gold_value == pred_value:
            exact += 1
    for field_id, pred_field in pred.items():
        if field_id not in gold:
            alignments.append(
                align_sequences([], graphemes(normalize_strict(str(pred_field.get("value_text", "")))))
            )
    merged = merge_alignments(alignments)
    return {
        "gold_fields": len(gold),
        "prediction_fields": len(pred),
        "matched_fields": len(common),
        "missing_fields": len(set(gold) - set(pred)),
        "hallucinated_fields": len(set(pred) - set(gold)),
        "field_presence_precision": precision,
        "field_presence_recall": recall,
        "field_presence_f1": f1,
        "value_exact_rate": exact / max(1, len(gold)),
        "value_gcer": error_rate(merged),
        "value_alignment": merged.to_dict(),
    }
