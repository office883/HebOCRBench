"""Fast sequence alignment with explicit OCR error accounting."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Hashable, Sequence, TypeVar

from rapidfuzz.distance import Levenshtein

T = TypeVar("T", bound=Hashable)
EMPTY = "∅"


@dataclass(slots=True)
class AlignmentResult:
    n_ref: int
    n_pred: int
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0
    correct: int = 0
    confusions: Counter[tuple[Hashable, Hashable]] = field(default_factory=Counter)

    @property
    def distance(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    def to_dict(self) -> dict[str, object]:
        return {
            "n_ref": self.n_ref,
            "n_pred": self.n_pred,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
            "correct": self.correct,
            "distance": self.distance,
            "error_rate": error_rate(self),
            "normalized_error_rate": normalized_error_rate(self),
            "confusions": [
                {"reference": str(a), "prediction": str(b), "count": count}
                for (a, b), count in self.confusions.most_common()
            ],
        }


def align_sequences(reference: Sequence[T], prediction: Sequence[T]) -> AlignmentResult:
    """Align a gold sequence to a prediction.

    Operation names are interpreted from reference to prediction: a RapidFuzz
    ``delete`` is an OCR deletion and ``insert`` is an OCR insertion.
    """

    ref = list(reference)
    pred = list(prediction)
    result = AlignmentResult(n_ref=len(ref), n_pred=len(pred))
    for op in Levenshtein.editops(ref, pred):
        tag, src_pos, dest_pos = tuple(op)
        if tag == "replace":
            result.substitutions += 1
            result.confusions[(ref[src_pos], pred[dest_pos])] += 1
        elif tag == "delete":
            result.deletions += 1
            result.confusions[(ref[src_pos], EMPTY)] += 1
        elif tag == "insert":
            result.insertions += 1
            result.confusions[(EMPTY, pred[dest_pos])] += 1
        else:  # pragma: no cover - defensive against upstream API changes
            raise ValueError(f"Unsupported edit operation: {tag}")
    result.correct = result.n_ref - result.substitutions - result.deletions
    return result


def error_rate(result: AlignmentResult) -> float:
    return result.distance / max(1, result.n_ref)


def normalized_error_rate(result: AlignmentResult) -> float:
    denominator = result.distance + result.correct
    return 0.0 if denominator == 0 else result.distance / denominator


def merge_alignments(results: Sequence[AlignmentResult]) -> AlignmentResult:
    merged = AlignmentResult(
        n_ref=sum(r.n_ref for r in results),
        n_pred=sum(r.n_pred for r in results),
        substitutions=sum(r.substitutions for r in results),
        deletions=sum(r.deletions for r in results),
        insertions=sum(r.insertions for r in results),
        correct=sum(r.correct for r in results),
    )
    for result in results:
        merged.confusions.update(result.confusions)
    return merged
