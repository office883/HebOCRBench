from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from hebocrbench.adapters.apple_vision import (
    AppleVisionInvocationError,
    AppleVisionObservation,
    AppleVisionPageOutput,
    invoke_apple_vision_page,
    run_apple_vision_page_ocr,
)
from hebocrbench.validator import validate_prediction_records


class _GuardedRecord(dict):
    def __init__(self, allowed, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed = set(allowed)

    def get(self, key, default=None):
        if key not in self.allowed:
            raise AssertionError(f"adapter read forbidden gold field: {key}")
        return super().get(key, default)


def _output() -> AppleVisionPageOutput:
    return AppleVisionPageOutput(
        observations=(
            AppleVisionObservation("כותרת", 0.99, (0.2, 0.84, 0.6, 0.08)),
            AppleVisionObservation("טור שמאל", 0.95, (0.08, 0.64, 0.35, 0.07)),
            AppleVisionObservation("טור ימין", 0.97, (0.57, 0.64, 0.35, 0.07)),
            AppleVisionObservation("OCR v2", 0.91, (0.1, 0.42, 0.4, 0.06)),
        ),
        image_width=1000,
        image_height=1200,
        request_revision=3,
        operating_system_version="26.5.2",
        inference_timing_ms=48.5,
    )


def test_page_adapter_is_blind_and_emits_schema_valid_logical_order(tmp_path):
    image_path = tmp_path / "images" / "page.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"runner fixture; not decoded")

    image = _GuardedRecord(
        {"path"},
        path="images/page.png",
        width=9999,
        height=9999,
        rotation_degrees=270,
    )
    envelope = _GuardedRecord(
        {"page_id", "image"},
        page_id="secret-page",
        image=image,
        regions=[{"text": "אסור לקרוא"}],
        page_text="אסור לקרוא",
        reading_order={"edges": [["secret-a", "secret-b"]]},
    )
    seen = []

    def fake_runner(path, languages, level, correction, revision, timeout):
        seen.append((path, languages, level, correction, revision, timeout))
        return _output()

    prediction = run_apple_vision_page_ocr(
        [envelope],
        dataset_root=tmp_path,
        runner=fake_runner,
        model_version="vision-test",
    )[0]

    assert seen == [(image_path.resolve(), ("he-IL", "en-US"), "accurate", True, None, 120.0)]
    assert prediction["status"] == "ok"
    assert prediction["failure"] is None
    assert prediction["api_failures"] == 0
    assert prediction["page_text"].splitlines() == [
        "כותרת",
        "טור ימין",
        "טור שמאל",
        "OCR v2",
    ]
    assert prediction["regions"][0]["polygon"] == [
        [200.0, 96.0],
        [800.0, 96.0],
        [800.0, 192.0],
        [200.0, 192.0],
    ]
    assert prediction["regions"][1]["base_direction"] == "rtl"
    assert prediction["regions"][3]["base_direction"] == "ltr"
    assert prediction["reading_order"] == {
        "edges": [
            ["pred-av-r0001", "pred-av-r0002"],
            ["pred-av-r0002", "pred-av-r0003"],
            ["pred-av-r0003", "pred-av-r0004"],
        ]
    }
    model = prediction["model"]
    assert model["family"] == "apple-vision"
    assert model["adapter"] == "apple_vision_page_e2e"
    assert model["oracle_layout"] is False
    assert model["request_revision"] == 3
    assert model["character_order"] == "logical-as-returned-by-engine"
    assert prediction["adapter_diagnostics"]["inference_timing_ms"] == 48.5
    assert validate_prediction_records([prediction]).is_valid


def test_page_adapter_emits_visible_schema_valid_failure(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"runner fixture; not decoded")

    def failing_runner(path, languages, level, correction, revision, timeout):
        raise AppleVisionInvocationError(
            "Vision request failed",
            return_code=9,
            stderr="unsupported recognition language",
        )

    prediction = run_apple_vision_page_ocr(
        [{"page_id": "failed-page", "image": {"path": "page.png"}}],
        dataset_root=tmp_path,
        runner=failing_runner,
    )[0]

    assert prediction["status"] == "failed"
    assert prediction["regions"] == []
    assert prediction["page_text"] == ""
    assert prediction["api_failures"] == 1
    assert prediction["failure"] == {
        "error_type": "AppleVisionInvocationError",
        "message": "Vision request failed",
        "return_code": 9,
        "stderr": "unsupported recognition language",
    }
    assert prediction["model"]["family"] == "apple-vision"
    assert validate_prediction_records([prediction]).is_valid


def test_mocked_helper_subprocess_contract(tmp_path, monkeypatch):
    image_path = tmp_path / "page image.png"
    image_path.write_bytes(b"not decoded by mocked helper")
    payload = {
        "schema_version": "1.0",
        "framework": "Vision",
        "operating_system_version": "26.5.2",
        "request_revision": 3,
        "recognition_level": "accurate",
        "recognition_languages": ["he-IL", "en-US"],
        "uses_language_correction": True,
        "image_width": 800,
        "image_height": 600,
        "inference_timing_ms": 12.25,
        "observations": [
            {
                "text": "שלום 123",
                "confidence": 0.98,
                "bounding_box": {"x": 0.2, "y": 0.7, "width": 0.6, "height": 0.1},
            }
        ],
    }
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("hebocrbench.adapters.apple_vision.subprocess.run", fake_run)
    result = invoke_apple_vision_page(
        image_path,
        ("he-IL", "en-US"),
        "accurate",
        True,
        3,
        45.0,
        executable=Path("/tmp/apple-vision-helper"),
    )

    assert observed["command"] == [
        "/tmp/apple-vision-helper",
        "--image",
        str(image_path),
        "--recognition-level",
        "accurate",
        "--languages",
        "he-IL,en-US",
        "--language-correction",
        "true",
        "--revision",
        "3",
    ]
    assert observed["kwargs"]["timeout"] == 45.0
    assert result.image_width == 800
    assert result.observations == (AppleVisionObservation("שלום 123", 0.98, (0.2, 0.7, 0.6, 0.1)),)


def test_invalid_configuration_rejected_before_running(tmp_path):
    def runner(*args):
        return _output()

    envelope = [{"page_id": "p1", "image": {"path": "missing.png"}}]

    for kwargs, message in (
        ({"languages": []}, "languages must not be empty"),
        ({"recognition_level": "turbo"}, "recognition_level must be accurate or fast"),
        ({"revision": 0}, "revision must be a positive integer"),
        ({"timeout_seconds": 0}, "timeout_seconds must be positive"),
    ):
        try:
            run_apple_vision_page_ocr(
                envelope,
                dataset_root=tmp_path,
                runner=runner,
                **kwargs,
            )
        except ValueError as exc:
            assert str(exc) == message
        else:
            raise AssertionError(f"configuration should fail: {kwargs}")
