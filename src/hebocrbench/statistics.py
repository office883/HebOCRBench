"""Document-level bootstrap and deterministic distribution summaries."""

from __future__ import annotations

from collections import defaultdict
import math
import random
from typing import Any, Sequence

from .alignment import error_rate, merge_alignments


def quantile(values: Sequence[float], probability: float) -> float:
    """Linear-interpolated quantile compatible with common type-7 defaults."""

    if not values:
        return 0.0
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _sample_metrics(pages: Sequence[Any]) -> dict[str, float]:
    line_codepoints = [evaluation.codepoint for page in pages for evaluation in page._line_text]
    line_graphemes = [evaluation.grapheme for page in pages for evaluation in page._line_text]
    page_graphemes = [page._page_text.grapheme for page in pages if page._page_text is not None]
    return {
        "line_cer": error_rate(merge_alignments(line_codepoints)),
        "line_gcer": error_rate(merge_alignments(line_graphemes)),
        "page_order_gcer": error_rate(merge_alignments(page_graphemes)),
    }


def bootstrap_document_intervals(
    pages: Sequence[Any],
    *,
    samples: int,
    seed: int = 20260722,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Paired-ready confidence intervals using the document as sampling unit."""

    if samples <= 0:
        return {
            "sampling_unit": "document",
            "samples": 0,
            "confidence": confidence,
        }
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    by_document: dict[str, list[Any]] = defaultdict(list)
    for page in pages:
        by_document[str(page.document_id)].append(page)
    document_ids = sorted(by_document)
    if not document_ids:
        return {
            "sampling_unit": "document",
            "samples": samples,
            "confidence": confidence,
            "line_cer": {"lower": 0.0, "median": 0.0, "upper": 0.0},
            "line_gcer": {"lower": 0.0, "median": 0.0, "upper": 0.0},
            "page_order_gcer": {"lower": 0.0, "median": 0.0, "upper": 0.0},
        }
    rng = random.Random(seed)
    draws: dict[str, list[float]] = defaultdict(list)
    for _ in range(samples):
        sampled_pages: list[Any] = []
        for _index in range(len(document_ids)):
            sampled_id = rng.choice(document_ids)
            sampled_pages.extend(by_document[sampled_id])
        for metric, value in _sample_metrics(sampled_pages).items():
            draws[metric].append(value)
    alpha = (1.0 - confidence) / 2.0
    result: dict[str, Any] = {
        "sampling_unit": "document",
        "samples": samples,
        "confidence": confidence,
        "seed": seed,
        "documents": len(document_ids),
    }
    for metric, values in draws.items():
        result[metric] = {
            "lower": quantile(values, alpha),
            "median": quantile(values, 0.5),
            "upper": quantile(values, 1.0 - alpha),
        }
    return result
