"""Bidirectional-text diagnostics that never rescue the strict score."""

from __future__ import annotations

from collections import defaultdict, deque
import re
import unicodedata

from .alignment import align_sequences, error_rate
from .unicode_utils import graphemes, normalize_strict

try:  # Optional: a full UAX #9 implementation when installed.
    from bidi import get_display as _get_display  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    try:
        from bidi.algorithm import get_display as _get_display  # type: ignore
    except Exception:  # pragma: no cover - environment dependent
        _get_display = None

ASCII_RUN_RE = re.compile(
    r"(?:https?://|www\.)[^\s]+|"
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|"
    r"[A-Za-z0-9]+(?:[._:/?&=%+#@\-][A-Za-z0-9]+)+|"
    r"[A-Za-z]+|"
    r"[0-9]+(?:[.,:/\-][0-9]+)*"
)
NUMBER_RE = re.compile(r"[0-9]+(?:[.,:/\-][0-9]+)*")
BRACKETS = frozenset("()[]{}<>")


def _is_hebrew_cluster(cluster: str) -> bool:
    return any(0x0590 <= ord(ch) <= 0x05FF for ch in cluster)


def _fallback_visual_proxy(text: str) -> str:
    """Approximate visual storage order for diagnostics when python-bidi is absent.

    It reverses RTL token order and Hebrew grapheme order while preserving the
    internal order of ASCII/digit runs. This is intentionally not used for
    conformance or scoring; it only detects common visual-order OCR output.
    """

    tokens = re.findall(
        r"[\u0590-\u05FF\uFB1D-\uFB4F]+|[A-Za-z0-9._:/?&=%+#@\-]+|\s+|.",
        text,
    )
    display: list[str] = []
    for token in reversed(tokens):
        if _is_hebrew_cluster(token):
            display.append("".join(reversed(graphemes(token))))
        else:
            display.append(token)
    return "".join(display)


def visual_proxy(text: str) -> str:
    if _get_display is not None:
        try:
            return str(_get_display(text, base_dir="R"))
        except Exception:
            pass
    return _fallback_visual_proxy(text)


def visual_order_diagnostic(
    reference: str,
    prediction: str,
    *,
    min_visual_order_gain: float = 0.0,
    max_visual_order_error_rate: float = 1.0,
) -> dict[str, object]:
    """Detect high-confidence visual-order storage without rescuing the score.

    A prediction is only treated as a conformance failure when it is both
    materially closer to a visual/reversed reference and reasonably close to
    that visual form. This prevents unrelated OCR noise from being mistaken
    for a BiDi storage error.
    """

    for name, value in (
        ("min_visual_order_gain", min_visual_order_gain),
        ("max_visual_order_error_rate", max_visual_order_error_rate),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in [0, 1], got {value}")

    ref = normalize_strict(reference)
    pred = normalize_strict(prediction)
    ref_graphemes = graphemes(ref)
    pred_graphemes = graphemes(pred)
    logical = align_sequences(ref_graphemes, pred_graphemes)
    display_ref = visual_proxy(ref)
    visual = align_sequences(graphemes(display_ref), pred_graphemes)
    reversed_ref = "".join(reversed(ref_graphemes))
    reversed_alignment = align_sequences(graphemes(reversed_ref), pred_graphemes)
    visual_rate = min(error_rate(visual), error_rate(reversed_alignment))
    logical_rate = error_rate(logical)
    gain = logical_rate - visual_rate
    candidate = len(ref_graphemes) >= 2 and gain > 1e-12
    suspected = (
        candidate
        and gain + 1e-12 >= min_visual_order_gain
        and visual_rate <= max_visual_order_error_rate + 1e-12
    )
    return {
        "logical_error_rate": logical_rate,
        "visual_error_rate": visual_rate,
        "visual_order_candidate": candidate,
        "visual_order_suspected": suspected,
        "visual_order_gain": gain,
        "min_visual_order_gain": min_visual_order_gain,
        "max_visual_order_error_rate": max_visual_order_error_rate,
        "visual_proxy": display_ref,
        "strict_score_changed": False,
    }


def _extract_ascii_runs(text: str) -> list[str]:
    runs: list[str] = []
    for match in ASCII_RUN_RE.finditer(text):
        token = match.group(0).rstrip(".,;!?)]}")
        if token:
            runs.append(token)
    return runs


def _sequence_exact_rate(reference: list[str], prediction: list[str]) -> float:
    alignment = align_sequences(reference, prediction)
    return alignment.correct / max(1, alignment.n_ref)


def ltr_run_metrics(reference: str, prediction: str) -> dict[str, object]:
    ref = normalize_strict(reference)
    pred = normalize_strict(prediction)
    ref_runs = _extract_ascii_runs(ref)
    pred_runs = _extract_ascii_runs(pred)
    ref_numbers = NUMBER_RE.findall(ref)
    pred_numbers = NUMBER_RE.findall(pred)
    runs_alignment = align_sequences(ref_runs, pred_runs)
    numbers_alignment = align_sequences(ref_numbers, pred_numbers)
    return {
        "reference_runs": ref_runs,
        "prediction_runs": pred_runs,
        "reference_count": runs_alignment.n_ref,
        "prediction_count": runs_alignment.n_pred,
        "correct_count": runs_alignment.correct,
        "exact_rate": _sequence_exact_rate(ref_runs, pred_runs),
        "error_rate": error_rate(runs_alignment),
        "numeric_reference_runs": ref_numbers,
        "numeric_prediction_runs": pred_numbers,
        "numeric_reference_count": numbers_alignment.n_ref,
        "numeric_prediction_count": numbers_alignment.n_pred,
        "numeric_correct_count": numbers_alignment.correct,
        "numeric_exact_rate": _sequence_exact_rate(ref_numbers, pred_numbers),
        "numeric_error_rate": error_rate(numbers_alignment),
    }


def pairwise_word_order_accuracy(reference: str, prediction: str) -> dict[str, float | int]:
    ref_words = normalize_strict(reference).split()
    pred_words = normalize_strict(prediction).split()
    positions: dict[str, deque[int]] = defaultdict(deque)
    for index, word in enumerate(pred_words):
        positions[word].append(index)

    matched_positions: list[int] = []
    for word in ref_words:
        if positions[word]:
            matched_positions.append(positions[word].popleft())

    comparable = 0
    concordant = 0
    for i in range(len(matched_positions)):
        for j in range(i + 1, len(matched_positions)):
            comparable += 1
            if matched_positions[i] < matched_positions[j]:
                concordant += 1
    accuracy = 1.0 if comparable == 0 else concordant / comparable
    return {
        "accuracy": accuracy,
        "coverage": len(matched_positions) / max(1, len(ref_words)),
        "matched_words": len(matched_positions),
        "comparable_pairs": comparable,
        "concordant_pairs": concordant,
    }


def bracket_metrics(reference: str, prediction: str) -> dict[str, object]:
    ref = [ch for ch in normalize_strict(reference) if ch in BRACKETS]
    pred = [ch for ch in normalize_strict(prediction) if ch in BRACKETS]
    alignment = align_sequences(ref, pred)
    return {
        "reference": ref,
        "prediction": pred,
        "exact": ref == pred,
        "error_rate": error_rate(alignment),
        "alignment": alignment.to_dict(),
    }


def first_strong_direction(text: str) -> str:
    for ch in text:
        bidi = unicodedata.bidirectional(ch)
        if bidi in {"R", "AL"}:
            return "rtl"
        if bidi == "L":
            return "ltr"
    return "neutral"
