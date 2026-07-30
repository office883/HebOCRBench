"""Modern-Hebrew PDF text-layer acceptance policy.

The low-level PDF engine is kept separate from this policy so that text-layer
fidelity can evolve without coupling it to geometry extraction. Global page
order is diagnostic; acceptance is based on content, local adjacency, exact
LTR-sensitive tokens, and punctuation coverage.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from rapidfuzz.distance import Levenshtein


def _ngrams(tokens: Sequence[str], size: int) -> tuple[tuple[str, ...], ...]:
    if size <= 0:
        raise ValueError("ngram size must be positive")
    return tuple(
        tuple(tokens[index : index + size])
        for index in range(max(0, len(tokens) - size + 1))
    )


def _is_critical_token(token: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9@]", token)) or token.startswith(
        ("http://", "https://", "www.")
    )


def install(engine: Any) -> None:
    """Install the v1 Modern-Hebrew fidelity policy into the PDF engine."""

    def agreement_components(first: str, second: str) -> dict[str, float]:
        first_tokens = engine._comparison_tokens(first)
        second_tokens = engine._comparison_tokens(second)
        if not first_tokens or not second_tokens:
            return {
                "anchor_order": 0.0,
                "anchor_content": 0.0,
                "local_order": 0.0,
                "critical_content": 0.0,
                "punctuation_content": 0.0,
                "overall": 0.0,
            }

        first_anchors = tuple(token for token in first_tokens if engine._is_anchor_token(token))
        second_anchors = tuple(
            token for token in second_tokens if engine._is_anchor_token(token)
        )
        first_punctuation = tuple(
            token for token in first_tokens if not engine._is_anchor_token(token)
        )
        second_punctuation = tuple(
            token for token in second_tokens if not engine._is_anchor_token(token)
        )
        first_critical = tuple(token for token in first_tokens if _is_critical_token(token))
        second_critical = tuple(token for token in second_tokens if _is_critical_token(token))

        anchor_order = (
            float(Levenshtein.normalized_similarity(first_anchors, second_anchors))
            if first_anchors and second_anchors
            else 0.0
        )
        anchor_content = engine._multiset_f1(first_anchors, second_anchors)
        local_order = (
            anchor_order
            if len(first_anchors) < 2 or len(second_anchors) < 2
            else engine._multiset_f1(_ngrams(first_anchors, 2), _ngrams(second_anchors, 2))
        )
        punctuation_content = engine._multiset_f1(first_punctuation, second_punctuation)
        critical_content = engine._multiset_f1(first_critical, second_critical)
        return {
            "anchor_order": anchor_order,
            "anchor_content": anchor_content,
            "local_order": local_order,
            "critical_content": critical_content,
            "punctuation_content": punctuation_content,
            "overall": anchor_content,
        }

    def text_layer_agreement(
        first: str,
        second: str,
        *,
        minimum: float | None = None,
    ) -> float:
        """Verify content fidelity without conflating it with page-region order."""

        components = agreement_components(first, second)
        score = components["overall"]
        if minimum is not None:
            local_floor = max(0.80, minimum - 0.15)
            punctuation_floor = max(0.90, minimum - 0.04)
            failed = (
                score + 1e-12 < minimum
                or components["local_order"] + 1e-12 < local_floor
                or components["critical_content"] + 1e-12 < 1.0
                or components["punctuation_content"] + 1e-12 < punctuation_floor
            )
            if failed:
                raise engine.ModernPdfError(
                    "independent extractors disagree: "
                    f"agreement={score:.6f}, minimum={minimum:.6f}, "
                    f"anchor_order={components['anchor_order']:.6f}, "
                    f"anchor_content={components['anchor_content']:.6f}, "
                    f"local_order={components['local_order']:.6f}, "
                    f"local_order_minimum={local_floor:.6f}, "
                    f"critical_content={components['critical_content']:.6f}, "
                    f"punctuation_content={components['punctuation_content']:.6f}, "
                    f"punctuation_minimum={punctuation_floor:.6f}"
                )
        return score

    engine._ngrams = _ngrams
    engine._is_critical_token = _is_critical_token
    engine._agreement_components = agreement_components
    engine.text_layer_agreement = text_layer_agreement
