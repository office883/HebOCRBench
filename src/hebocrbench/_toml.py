"""TOML parser compatibility for the supported Python 3.10+ runtime range."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Callable


def _select_backend(importer: Callable[[str], ModuleType] = import_module) -> ModuleType:
    """Return stdlib ``tomllib`` or the ``tomli`` backport on Python 3.10.

    Only a genuinely missing ``tomllib`` module triggers the fallback.  Import
    failures raised from inside a backend remain visible rather than being
    misreported as a missing standard-library module.
    """

    try:
        return importer("tomllib")
    except ModuleNotFoundError as exc:
        if exc.name != "tomllib":
            raise
        return importer("tomli")


_BACKEND = _select_backend()
TOMLDecodeError = _BACKEND.TOMLDecodeError
load = _BACKEND.load
loads = _BACKEND.loads

__all__ = ["TOMLDecodeError", "load", "loads"]
