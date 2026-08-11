from __future__ import annotations

import pytest

from hebocrbench.converters import _modern_pdf_engine as engine
from hebocrbench.converters.modern_pdf import ModernPdfError, text_layer_agreement


def test_policy_accepts_reordered_regions_but_preserves_local_text() -> None:
    right = "סכום 1,250 ש״ח בתאריך 26.07.2026. הערה שומרת סדר מילים מקומי."
    left = "כתובת qa@example.com ודגם OCR-v2.1. הטקסט נשאר זהה בכל אזור."

    assert (
        text_layer_agreement(
            right + "\n" + left,
            left + "\n" + right,
            minimum=0.98,
        )
        >= 0.98
    )


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


def test_verified_pdf_cmap_repairs_are_narrow_and_auditable() -> None:
    repaired, evidence = engine._repair_modern_pdf_text(
        "הצעה ראשו ð ית וההתגוððות \uf0b7 טלפון \uf027 \ufeff íslenska orð"
    )

    assert repaired == "הצעה ראשו נ ית וההתגוננות • טלפון ☎  íslenska orð"
    assert [(item["source"], item["count"]) for item in evidence] == [
        ("U+FEFF", 1),
        ("U+F027", 1),
        ("U+F0B7", 1),
        ("U+00F0", 3),
    ]


def test_page_capability_evidence_distinguishes_slices_from_form_gold() -> None:
    evidence = engine._page_capability_evidence(
        "שם מלא: ________ בשנת 2026 נשלח מסמך OCR-v2",
        table_count=2,
    )

    assert evidence == {
        "mixed_bidi": True,
        "table_count": 2,
        "form_signal_count": 2,
        "form_signals": ["name_label", "underscore_run"],
        "form_ground_truth_available": False,
    }
