"""Recognition metrics for strict Hebrew OCR transcription."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import unicodedata

from .alignment import AlignmentResult, align_sequences, error_rate, normalized_error_rate
from .unicode_utils import (
    HEBREW_FINAL_TO_MEDIAL,
    graphemes,
    normalize_strict,
    strip_hebrew_marks,
)


def whitespace_tokens(text: str) -> list[str]:
    return text.split()


def punctuation_units(text: str) -> list[str]:
    return [ch for ch in text if unicodedata.category(ch).startswith("P")]


def bag_of_words_error(reference: str, prediction: str) -> float:
    ref = Counter(whitespace_tokens(reference))
    pred = Counter(whitespace_tokens(prediction))
    denominator = sum(ref.values()) + sum(pred.values())
    if denominator == 0:
        return 0.0
    return sum(abs(ref[token] - pred[token]) for token in ref.keys() | pred.keys()) / denominator


@dataclass(slots=True)
class TextEvaluation:
    reference: str
    prediction: str
    exact: bool
    codepoint: AlignmentResult
    grapheme: AlignmentResult
    word: AlignmentResult
    base_letter: AlignmentResult
    punctuation: AlignmentResult
    bag_of_words_error: float
    final_letter_confusions: int

    @property
    def codepoint_rate(self) -> float:
        return error_rate(self.codepoint)

    @property
    def grapheme_rate(self) -> float:
        return error_rate(self.grapheme)

    @property
    def word_rate(self) -> float:
        return error_rate(self.word)

    def to_dict(self) -> dict[str, object]:
        return {
            "reference": self.reference,
            "prediction": self.prediction,
            "exact": self.exact,
            "codepoint": self.codepoint.to_dict(),
            "grapheme": self.grapheme.to_dict(),
            "word": self.word.to_dict(),
            "base_letter": self.base_letter.to_dict(),
            "punctuation": self.punctuation.to_dict(),
            "bag_of_words_error": self.bag_of_words_error,
            "final_letter_confusions": self.final_letter_confusions,
            "rates": {
                "cer": self.codepoint_rate,
                "normalized_cer": normalized_error_rate(self.codepoint),
                "gcer": self.grapheme_rate,
                "normalized_gcer": normalized_error_rate(self.grapheme),
                "wer": self.word_rate,
                "base_letter_cer": error_rate(self.base_letter),
                "punctuation_error_rate": error_rate(self.punctuation),
            },
        }


def _count_final_letter_confusions(alignment: AlignmentResult) -> int:
    count = 0
    for (reference, prediction), frequency in alignment.confusions.items():
        if not isinstance(reference, str) or not isinstance(prediction, str):
            continue
        if HEBREW_FINAL_TO_MEDIAL.get(reference) == prediction:
            count += frequency
        elif HEBREW_FINAL_TO_MEDIAL.get(prediction) == reference:
            count += frequency
    return count


def evaluate_text(reference: str, prediction: str) -> TextEvaluation:
    ref = normalize_strict(reference)
    pred = normalize_strict(prediction)
    codepoint = align_sequences(list(ref), list(pred))
    grapheme = align_sequences(graphemes(ref), graphemes(pred))
    word = align_sequences(whitespace_tokens(ref), whitespace_tokens(pred))
    base_letter = align_sequences(
        list(strip_hebrew_marks(ref)), list(strip_hebrew_marks(pred))
    )
    punctuation = align_sequences(punctuation_units(ref), punctuation_units(pred))
    return TextEvaluation(
        reference=ref,
        prediction=pred,
        exact=ref == pred,
        codepoint=codepoint,
        grapheme=grapheme,
        word=word,
        base_letter=base_letter,
        punctuation=punctuation,
        bag_of_words_error=bag_of_words_error(ref, pred),
        final_letter_confusions=_count_final_letter_confusions(codepoint),
    )
