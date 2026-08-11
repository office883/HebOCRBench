from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_surya2_llama_server.py"
SPEC = importlib.util.spec_from_file_location("run_surya2_llama_server_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_launcher_binds_loopback_and_hashes_the_exact_artifacts(tmp_path, monkeypatch, capsys):
    model = tmp_path / "model.gguf"
    projector = tmp_path / "mmproj.gguf"
    model.write_bytes(b"model")
    projector.write_bytes(b"projector")
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)
    result = MODULE.main(
        [
            "--model-path",
            str(model),
            "--mmproj-path",
            str(projector),
            "--executable",
            "llama-server-test",
            "--port",
            "9123",
        ]
    )

    assert result == 0
    command = seen["command"]
    assert command[0] == "llama-server-test"
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "9123"
    assert command[command.index("--temp") + 1] == "0"
    assert command[command.index("--seed") + 1] == "1"
    assert "--no-ui" in command
    assert "--no-cors-credentials" in command
    model_hash = hashlib.sha256(b"model").hexdigest()
    projector_hash = hashlib.sha256(b"projector").hexdigest()
    expected_alias = f"surya-ocr-2::{model_hash}::{projector_hash}"
    assert command[command.index("--alias") + 1] == expected_alias
    evidence = json.loads(capsys.readouterr().out)
    assert evidence["model_alias"] == expected_alias
    assert evidence["loopback_only"] is True


def test_launcher_rejects_context_that_cannot_fit_every_slot(tmp_path):
    model = tmp_path / "model.gguf"
    projector = tmp_path / "mmproj.gguf"
    model.write_bytes(b"model")
    projector.write_bytes(b"projector")
    with pytest.raises(SystemExit):
        MODULE.main(
            [
                "--model-path",
                str(model),
                "--mmproj-path",
                str(projector),
                "--parallel",
                "2",
                "--context-size",
                "8192",
            ]
        )
