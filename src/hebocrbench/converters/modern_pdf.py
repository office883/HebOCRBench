"""Public Modern-Hebrew PDF converter API.

The extraction engine and the scientific acceptance policy are separated so
reading-order evaluation is not accidentally folded into text-layer fidelity.
"""

from __future__ import annotations

from . import _modern_pdf_engine as _engine
from ._modern_pdf_policy import install as _install_policy

_install_policy(_engine)

ModernPdfError = _engine.ModernPdfError
text_layer_agreement = _engine.text_layer_agreement
convert_modern_pdf_page = _engine.convert_modern_pdf_page
convert_modern_pdf_manifest = _engine.convert_modern_pdf_manifest

__all__ = [
    "ModernPdfError",
    "convert_modern_pdf_manifest",
    "convert_modern_pdf_page",
    "text_layer_agreement",
]
