"""Hebrew niqqud and cantillation metrics."""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz.distance import Levenshtein

from .alignment import AlignmentResult, align_sequences
from .unicode_utils import (
    classify_hebrew_mark,
    graphemes,
    is_hebrew_letter,
    normalize_strict,
    split_base_and_marks,
)

MARK_CATEGORIES = (
    "vowel",
    "dagesh_mapiq",
    "shin_sin_dot",
    "meteg_rafe",
    "cantillation",
    "other_hebrew_mark",
)


def _hebrew_clusters(text: str) -> list[tuple[str, tuple[str, ...]]]:
    result: list[tuple[str, tuple[str, ...]]] = []
    for cluster in graphemes(text):
        base, marks = split_base_and_marks(cluster)
        if len(base) == 1 and is_hebrew_letter(base):
            result.append((base, tuple(mark for mark in marks if classify_hebrew_mark(mark))))
    return result


def _category_stats(alignment: AlignmentResult) -> dict[str, float | int]:
    correct = alignment.correct
    predicted = alignment.correct + alignment.substitutions + alignment.insertions
    reference = alignment.correct + alignment.substitutions + alignment.deletions
    precision = 1.0 if predicted == 0 else correct / predicted
    recall = 1.0 if reference == 0 else correct / reference
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "correct": correct,
        "substitutions": alignment.substitutions,
        "deletions": alignment.deletions,
        "insertions": alignment.insertions,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


@dataclass(slots=True)
class DiacriticEvaluation:
    base_pairs: int = 0
    reference_bases: int = 0
    predicted_bases: int = 0
    reference_marks: int = 0
    predicted_marks: int = 0
    correct_marks: int = 0
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0
    unmarked_reference_bases: int = 0
    hallucinated_unmarked_bases: int = 0
    exact_mark_sets: int = 0
    by_category: dict[str, dict[str, float | int]] = field(default_factory=dict)

    @property
    def mark_precision(self) -> float:
        return 1.0 if self.predicted_marks == 0 else self.correct_marks / self.predicted_marks

    @property
    def mark_recall(self) -> float:
        return 1.0 if self.reference_marks == 0 else self.correct_marks / self.reference_marks

    @property
    def mark_f1(self) -> float:
        p, r = self.mark_precision, self.mark_recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)

    @property
    def mark_error_rate(self) -> float:
        return (self.substitutions + self.deletions + self.insertions) / max(
            1, self.reference_marks
        )

    @property
    def dropped_mark_rate(self) -> float:
        return (self.substitutions + self.deletions) / max(1, self.reference_marks)

    @property
    def hallucinated_mark_rate(self) -> float:
        return self.hallucinated_unmarked_bases / max(1, self.unmarked_reference_bases)

    @property
    def exact_mark_set_rate(self) -> float:
        return self.exact_mark_sets / max(1, self.base_pairs)

    def to_dict(self) -> dict[str, object]:
        return {
            "base_pairs": self.base_pairs,
            "reference_bases": self.reference_bases,
            "predicted_bases": self.predicted_bases,
            "reference_marks": self.reference_marks,
            "predicted_marks": self.predicted_marks,
            "correct_marks": self.correct_marks,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "mark_precision": self.mark_precision,
            "mark_recall": self.mark_recall,
            "mark_f1": self.mark_f1,
            "mark_error_rate": self.mark_error_rate,
            "dropped_mark_rate": self.dropped_mark_rate,
            "hallucinated_mark_rate": self.hallucinated_mark_rate,
            "hallucinated_unmarked_bases": self.hallucinated_unmarked_bases,
            "unmarked_reference_bases": self.unmarked_reference_bases,
            "exact_mark_set_rate": self.exact_mark_set_rate,
            "by_category": self.by_category,
        }


def evaluate_diacritics(reference: str, prediction: str) -> DiacriticEvaluation:
    ref_clusters = _hebrew_clusters(normalize_strict(reference))
    pred_clusters = _hebrew_clusters(normalize_strict(prediction))
    ref_bases = [base for base, _ in ref_clusters]
    pred_bases = [base for base, _ in pred_clusters]

    result = DiacriticEvaluation(
        reference_bases=len(ref_bases),
        predicted_bases=len(pred_bases),
    )
    category_alignments: dict[str, list[AlignmentResult]] = {
        category: [] for category in MARK_CATEGORIES
    }

    for tag, i1, i2, j1, j2 in Levenshtein.opcodes(ref_bases, pred_bases):
        if tag != "equal":
            continue
        for ref_index, pred_index in zip(range(i1, i2), range(j1, j2), strict=True):
            _, ref_marks = ref_clusters[ref_index]
            _, pred_marks = pred_clusters[pred_index]
            result.base_pairs += 1
            result.reference_marks += len(ref_marks)
            result.predicted_marks += len(pred_marks)
            if not ref_marks:
                result.unmarked_reference_bases += 1
                if pred_marks:
                    result.hallucinated_unmarked_bases += 1
            if ref_marks == pred_marks:
                result.exact_mark_sets += 1

            mark_alignment = align_sequences(ref_marks, pred_marks)
            result.correct_marks += mark_alignment.correct
            result.substitutions += mark_alignment.substitutions
            result.deletions += mark_alignment.deletions
            result.insertions += mark_alignment.insertions

            for category in MARK_CATEGORIES:
                category_alignments[category].append(
                    align_sequences(
                        [m for m in ref_marks if classify_hebrew_mark(m) == category],
                        [m for m in pred_marks if classify_hebrew_mark(m) == category],
                    )
                )

    for category, alignments in category_alignments.items():
        merged = AlignmentResult(n_ref=0, n_pred=0)
        for alignment in alignments:
            merged.n_ref += alignment.n_ref
            merged.n_pred += alignment.n_pred
            merged.substitutions += alignment.substitutions
            merged.deletions += alignment.deletions
            merged.insertions += alignment.insertions
            merged.correct += alignment.correct
            merged.confusions.update(alignment.confusions)
        result.by_category[category] = _category_stats(merged)
    return result


def merge_diacritic_evaluations(
    evaluations: list[DiacriticEvaluation],
) -> DiacriticEvaluation:
    """Micro-average multiple line-level diacritic evaluations."""

    merged = DiacriticEvaluation()
    scalar_fields = (
        "base_pairs",
        "reference_bases",
        "predicted_bases",
        "reference_marks",
        "predicted_marks",
        "correct_marks",
        "substitutions",
        "deletions",
        "insertions",
        "unmarked_reference_bases",
        "hallucinated_unmarked_bases",
        "exact_mark_sets",
    )
    for evaluation in evaluations:
        for name in scalar_fields:
            setattr(merged, name, getattr(merged, name) + getattr(evaluation, name))

    for category in MARK_CATEGORIES:
        totals = {
            "correct": 0,
            "substitutions": 0,
            "deletions": 0,
            "insertions": 0,
        }
        for evaluation in evaluations:
            stats = evaluation.by_category.get(category, {})
            for name in totals:
                totals[name] += int(stats.get(name, 0))
        predicted = totals["correct"] + totals["substitutions"] + totals["insertions"]
        reference = totals["correct"] + totals["substitutions"] + totals["deletions"]
        precision = 1.0 if predicted == 0 else totals["correct"] / predicted
        recall = 1.0 if reference == 0 else totals["correct"] / reference
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        merged.by_category[category] = {
            **totals,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return merged
