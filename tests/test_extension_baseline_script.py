from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_extension_baselines.py"
SPEC = importlib.util.spec_from_file_location("run_extension_baselines_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_cli_forwards_the_full_surya_server_configuration(tmp_path, monkeypatch, capsys):
    model = tmp_path / "surya.gguf"
    projector = tmp_path / "surya-mmproj.gguf"
    track_root = tmp_path / "handwriting"
    output = tmp_path / "output"
    seen = {}

    def fake_suite(track_roots, output_root, **kwargs):
        seen.update(track_roots=track_roots, output_root=output_root, **kwargs)
        return {"engine": kwargs["settings"].engine, "status": "fixture"}

    monkeypatch.setattr(MODULE, "run_extension_baseline_suite", fake_suite)
    result = MODULE.main(
        [
            "--engine",
            "surya2-llamacpp",
            "--track-root",
            f"modern-handwriting-v1={track_root}",
            "--output",
            str(output),
            "--model-version",
            "surya-extension-v1",
            "--timeout-seconds",
            "321",
            "--workers",
            "4",
            "--max-pages",
            "7",
            "--retry-failures",
            "--surya-model-path",
            str(model),
            "--surya-mmproj-path",
            str(projector),
            "--surya-backend",
            "server",
            "--surya-server-url",
            "http://127.0.0.1:8137",
            "--surya-server-parallel",
            "4",
            "--surya-server-context-size",
            "32768",
            "--surya-executable",
            "llama-cli-test",
            "--surya-server-executable",
            "llama-server-test",
            "--surya-max-tokens",
            "4096",
            "--surya-image-max-tokens",
            "2048",
        ]
    )

    assert result == 0
    assert seen["track_roots"] == {"modern-handwriting-v1": track_root}
    assert seen["output_root"] == output
    assert seen["workers"] == 4
    assert seen["max_pages"] == 7
    assert seen["retry_failures"] is True
    settings = seen["settings"]
    assert settings.engine == "surya2-llamacpp"
    assert settings.model_version == "surya-extension-v1"
    assert settings.timeout_seconds == 321
    assert settings.surya_model_path == str(model)
    assert settings.surya_mmproj_path == str(projector)
    assert settings.surya_backend == "server"
    assert settings.surya_server_url == "http://127.0.0.1:8137"
    assert settings.surya_server_parallel == 4
    assert settings.surya_server_context_size == 32768
    assert settings.surya_executable == "llama-cli-test"
    assert settings.surya_server_executable == "llama-server-test"
    assert settings.surya_max_tokens == 4096
    assert settings.surya_image_max_tokens == 2048
    assert json.loads(capsys.readouterr().out) == {
        "engine": "surya2-llamacpp",
        "status": "fixture",
    }
