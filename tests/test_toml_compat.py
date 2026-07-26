from __future__ import annotations

from types import SimpleNamespace

import pytest

from hebocrbench._toml import _select_backend, loads


def test_toml_compat_loads_project_metadata():
    assert loads('[project]\nname = "hebocrbench"\n')["project"]["name"] == "hebocrbench"


def test_toml_compat_falls_back_only_when_tomllib_is_missing():
    calls: list[str] = []
    fallback = SimpleNamespace(loads=lambda text: {"fallback": text})

    def importer(name: str):
        calls.append(name)
        if name == "tomllib":
            error = ModuleNotFoundError("No module named 'tomllib'")
            error.name = "tomllib"
            raise error
        assert name == "tomli"
        return fallback

    assert _select_backend(importer) is fallback
    assert calls == ["tomllib", "tomli"]


def test_toml_compat_does_not_hide_nested_import_failures():
    def importer(name: str):
        error = ModuleNotFoundError("No module named 'other_dependency'")
        error.name = "other_dependency"
        raise error

    with pytest.raises(ModuleNotFoundError, match="other_dependency"):
        _select_backend(importer)
