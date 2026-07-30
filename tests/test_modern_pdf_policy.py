from __future__ import annotations

import pytest

from hebocrbench.converters.modern_pdf import ModernPdfError, text_layer_agreement


def test_policy_accepts_reordered_regions_but_preserves_local_text() -> None:
    right = "סכום 1,250 ש״ח בתאריך 26.07.2026. הערה שומרת סדר מילים מקומי."
    left = "כתובת qa@example.com ודגם OCR-v2.1. הטקסט נשאר זהה בכל אזור."

    assert text_layer_agreement(
        right + "\n" + left,
        left + "\n" + right,
        minimum=0.98,
    ) >= 0.98


def test_policy_ignores_directional_controls_and_embedded_bom() -> None:
    text = "הדוח לשנת 2026 נשלח לכתובת qa@example.com."
    decorated = "\u202b" + text + "\u202c\ufeff"

    assert text_layer_agreement(text, decorated, minimum=0.98) == 1.0


def test_policy_rejects_reversed_local_word_order() -> None:
    with pytest.raises(ModernPdfError, match="local_order"):
        text_layer_agreement(
            "אחד שני שלושה ארבעה חמישה שישה שבעה שמונה",
            "שמונה שבעה שישה חמישה ארבעה שלושה שני אחד",
            minimum=0.98,
        )


def test_policy_rejects_changed_critical_ltr_token() -> None:
    with pytest.raises(ModernPdfError, match="critical_content"):
        text_layer_agreement(
            "הדוח לשנת 2026 נשלח לכתובת qa@example.com",
            "הדוח לשנת 6202 נשלח לכתובת qa@example.com",
            minimum=0.98,
        )
