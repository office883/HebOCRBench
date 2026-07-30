"""Public corpus-builder API with canonical prebuilt-gold support."""

from __future__ import annotations

from . import _corpus_builder_engine as _engine
from ._prebuilt_gold import install as _install_prebuilt_gold

_install_prebuilt_gold(_engine)

for _name in dir(_engine):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_engine, _name))


def _sync_facade_overrides() -> None:
    # Tests and integrations historically monkeypatch this public module.
    _engine.convert_modern_pdf_manifest = globals()["convert_modern_pdf_manifest"]


def build_corpus(*args, **kwargs):
    _sync_facade_overrides()
    return _engine.build_corpus(*args, **kwargs)


freeze_corpus = _engine.freeze_corpus
BuildError = _engine.BuildError
CorpusBuildResult = _engine.CorpusBuildResult

__all__ = ["BuildError", "CorpusBuildResult", "build_corpus", "freeze_corpus"]
