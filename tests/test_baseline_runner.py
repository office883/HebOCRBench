from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from PIL import Image
import pytest

import hebocrbench.baseline_runner as runner_module
from hebocrbench.baseline_runner import (
    BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK,
    BaselineRunnerError,
    BaselineSettings,
    HISTORICAL_PRESS_TRACK,
    _effective_surya_max_tokens,
    _extension_input_mode,
    _limited_evaluation_records,
    _modern_input_mode,
    run_baseline_track,
    run_extension_baseline_suite,
)
from hebocrbench.io import load_jsonl
from hebocrbench.validator import validate_prediction_records


def _gold_root(
    root: Path,
    *,
    pages: int = 2,
    held_out_split: str = "test",
    all_held_out: bool = False,
    gold_track: str = "modern_line_recognition",
) -> Path:
    images = root / "images"
    images.mkdir(parents=True)
    records = []
    for index in range(pages):
        image_path = images / f"line-{index}.png"
        Image.new("RGB", (120, 30), "white").save(image_path)
        records.append(
            {
                "schema_version": "1.0",
                "page_id": f"page-{index}",
                "document_id": f"document-{index}",
                "split": held_out_split if index == 0 or all_held_out else "train",
                "track": gold_track,
                "image": {
                    "path": image_path.relative_to(root).as_posix(),
                    "width": 120,
                    "height": 30,
                    "rotation_degrees": 0,
                    "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                },
                "metadata": {
                    "source_id": "fixture",
                    "source_page_id": f"fixture-{index}",
                    "source_url": "https://example.invalid/fixture",
                    "license": "CC0-1.0",
                    "document_type": "fixture",
                    "template_family": "fixture",
                    "layout_type": "single_line",
                    "source_type": "fixture",
                    "vocalization": "none",
                    "languages": ["he"],
                    "script": "Hebr",
                    "script_style": "modern_square_print",
                    "era": "modern",
                    "source_collection": "fixture",
                },
                "regions": [
                    {
                        "region_id": f"gold-region-{index}",
                        "type": "text_line",
                        "polygon": [[0, 0], [120, 0], [120, 30], [0, 30]],
                        "base_direction": "rtl",
                        "reading_index": 0,
                        "lines": [
                            {
                                "line_id": f"gold-secret-line-{index}",
                                "polygon": [[2, 2], [118, 2], [118, 28], [2, 28]],
                                "text": "טקסט זהב שאסור לקרוא",
                                "base_direction": "rtl",
                                "language": "he",
                                "reading_index": 0,
                            }
                        ],
                    }
                ],
                "reading_order": {"edges": []},
                "tables": [],
                "form_fields": [],
            }
        )
    (root / "gold.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize(
    ("track_id", "expected"),
    (
        ("modern-line-recognition-v1", "blind_whole_line_image"),
        ("modern-page-ocr-v1", "blind_full_page_image"),
        ("modern-layout-order-v1", "blind_full_page_image"),
        ("modern-tables-forms-v1", "blind_full_page_image"),
        ("modern-robustness-v1", "blind_full_page_image"),
    ),
)
def test_modern_reports_declare_the_blind_input_mode(track_id, expected):
    assert _modern_input_mode(track_id) == expected


@pytest.mark.parametrize(
    ("track_id", "engine", "expected"),
    (
        ("modern-handwriting-v1", "tesseract", "blind_whole_line_image"),
        ("modern-handwriting-v1", "surya2-llamacpp", "blind_whole_line_image"),
        ("historical-pinkas-handwriting-v1", "surya2-llamacpp", "blind_whole_line_image"),
        (
            BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK,
            "surya2-llamacpp",
            "blind_whole_line_image",
        ),
        (
            "rashi-print-synthetic-diagnostic-v1",
            "surya2-llamacpp",
            "blind_whole_line_image",
        ),
        (HISTORICAL_PRESS_TRACK, "tesseract", "oracle_layout_line_crops"),
        (HISTORICAL_PRESS_TRACK, "surya2-llamacpp", "blind_full_page_image"),
    ),
)
def test_extension_reports_declare_engine_specific_input_modes(track_id, engine, expected):
    assert _extension_input_mode(track_id, engine) == expected


def test_robustness_smoke_limit_keeps_complete_parent_groups():
    records = [
        {
            "page_id": f"{parent}-{variant}",
            "metadata": {
                "parent_page_id": parent,
                "degradation_variant": variant,
                "degradation_is_control": variant == "clean",
            },
        }
        for parent in ("p1", "p2")
        for variant in ("clean", "blur", "jpeg")
    ]
    selected = _limited_evaluation_records("modern-robustness-v1", records, 1)
    assert [record["page_id"] for record in selected] == ["p1-clean", "p1-blur", "p1-jpeg"]


def _prediction(page_id: str) -> dict[str, object]:
    polygon = [[0, 0], [120, 0], [120, 30], [0, 30]]
    return {
        "schema_version": "1.0",
        "page_id": page_id,
        "page_text": "פלט מנוע",
        "regions": [
            {
                "region_id": "prediction-region",
                "type": "text_line",
                "polygon": polygon,
                "base_direction": "rtl",
                "reading_index": 0,
                "lines": [
                    {
                        "line_id": "prediction-line",
                        "polygon": polygon,
                        "text": "פלט מנוע",
                        "base_direction": "rtl",
                        "language": "he",
                        "reading_index": 0,
                    }
                ],
            }
        ],
        "reading_order": {"edges": []},
        "tables": [],
        "form_fields": [],
        "timing_ms": 12.5,
        "status": "ok",
        "failure": None,
        "api_failures": 0,
        "model": {"adapter": "fixture"},
    }


def _oracle_prediction(envelope: dict[str, object]) -> dict[str, object]:
    regions = []
    for raw_region in envelope["regions"]:
        lines = []
        for raw_line in raw_region["lines"]:
            line = {
                "line_id": raw_line["line_id"],
                "polygon": raw_line["polygon"],
                "text": "פלט מנוע",
                "base_direction": "rtl",
                "language": "he",
            }
            if "reading_index" in raw_line:
                line["reading_index"] = raw_line["reading_index"]
            lines.append(line)
        region = {
            "region_id": raw_region["region_id"],
            "type": "text",
            "polygon": raw_region["polygon"],
            "base_direction": "rtl",
            "lines": lines,
        }
        if "reading_index" in raw_region:
            region["reading_index"] = raw_region["reading_index"]
        regions.append(region)
    return {
        "schema_version": "1.0",
        "page_id": envelope["page_id"],
        "regions": regions,
        "reading_order": {"edges": []},
        "tables": [],
        "form_fields": [],
        "timing_ms": 1.0,
        "status": "ok",
        "failure": None,
        "api_failures": 0,
        "model": {"adapter": "fixture-oracle-layout", "oracle_layout": True},
    }


def _historical_press_root(root: Path, *, pages: int = 34, total_lines: int = 4016) -> Path:
    images = root / "images"
    images.mkdir(parents=True)
    base_lines, extra = divmod(total_lines, pages)
    records = []
    for page_index in range(pages):
        image_path = images / f"press-{page_index:02d}.png"
        Image.new("RGB", (120, 30), "white").save(image_path)
        line_count = base_lines + (1 if page_index < extra else 0)
        lines = [
            {
                "line_id": f"press-{page_index:02d}-line-{line_index:04d}",
                "polygon": [[2, 2], [118, 2], [118, 28], [2, 28]],
                "text": "טקסט זהב שאסור לקרוא",
                "base_direction": "rtl",
                "language": "he",
                "reading_index": line_index,
            }
            for line_index in range(line_count)
        ]
        records.append(
            {
                "schema_version": "1.0",
                "page_id": f"press-page-{page_index:02d}",
                "document_id": f"press-document-{page_index:02d}",
                "split": "test",
                "track": "historical_hebrew_press_mixed",
                "image": {
                    "path": image_path.relative_to(root).as_posix(),
                    "width": 120,
                    "height": 30,
                    "rotation_degrees": 0,
                    "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                },
                "metadata": {
                    "source_id": "fixture",
                    "source_page_id": f"press-{page_index:02d}",
                    "source_url": "https://example.invalid/fixture",
                    "license": "CC0-1.0",
                    "document_type": "historical_newspaper",
                    "template_family": "fixture",
                    "layout_type": "multi_line",
                    "source_type": "fixture",
                    "vocalization": "none",
                    "languages": ["he"],
                    "script": "Hebr",
                    "script_style": "mixed_square_rashi_print",
                    "era": "historical",
                    "source_collection": "fixture",
                },
                "regions": [
                    {
                        "region_id": f"press-region-{page_index:02d}",
                        "type": "text",
                        "polygon": [[0, 0], [120, 0], [120, 30], [0, 30]],
                        "base_direction": "rtl",
                        "reading_index": 0,
                        "lines": lines,
                    }
                ],
                "reading_order": {"edges": []},
                "tables": [],
                "form_fields": [],
            }
        )
    (root / "gold.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return root


def test_track_runner_is_test_only_blind_and_resumable(tmp_path: Path) -> None:
    root = _gold_root(tmp_path / "dataset")
    seen = []

    def predictor(track_id, envelope, dataset_root, settings):
        seen.append((track_id, envelope, dataset_root, settings.engine))
        assert set(envelope) == {"page_id", "image"}
        assert set(envelope["image"]) == {"path"}
        serialized = json.dumps(envelope, ensure_ascii=False)
        assert "טקסט זהב" not in serialized
        assert "gold-secret" not in serialized
        return _prediction(str(envelope["page_id"]))

    settings = BaselineSettings(engine="tesseract", model_version="fixture-1")
    first = run_baseline_track(
        "modern-line-recognition-v1",
        root,
        tmp_path / "first.jsonl",
        cache_root=tmp_path / "cache",
        settings=settings,
        predictor=predictor,
    )
    second = run_baseline_track(
        "modern-line-recognition-v1",
        root,
        tmp_path / "second.jsonl",
        cache_root=tmp_path / "cache",
        settings=settings,
        predictor=lambda *args: (_ for _ in ()).throw(AssertionError("cache miss")),
    )

    assert len(seen) == 1
    assert first.selected_pages == first.source_evaluation_pages == 1
    assert first.evaluation_split == "test"
    assert first.cache_hits == 0 and first.cache_misses == 1
    assert second.cache_hits == 1 and second.cache_misses == 0
    assert (tmp_path / "first.jsonl").read_bytes() == (tmp_path / "second.jsonl").read_bytes()
    predictions = load_jsonl(tmp_path / "second.jsonl")
    assert validate_prediction_records(predictions).is_valid
    assert predictions[0]["model"]["system_id"] == "tesseract::fixture-1"


def test_apple_vision_unsupported_is_a_visible_valid_failure(tmp_path: Path) -> None:
    root = _gold_root(tmp_path / "dataset", pages=1)

    def unsupported(track_id, envelope, dataset_root, settings):
        raise RuntimeError("Apple Vision is available only on macOS")

    result = run_baseline_track(
        "modern-page-ocr-v1",
        root,
        tmp_path / "predictions.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(engine="apple-vision", model_version="unsupported-test"),
        predictor=unsupported,
    )
    prediction = load_jsonl(result.prediction_path)[0]

    assert result.failures == 1
    assert prediction["status"] == "failed"
    assert prediction["api_failures"] == 1
    assert prediction["failure"]["category"] == "unsupported_platform_or_runtime"
    assert prediction["model"]["system_id"] == "apple-vision::unsupported-test"
    assert validate_prediction_records([prediction]).is_valid


def test_cache_key_changes_when_engine_configuration_changes(tmp_path: Path) -> None:
    root = _gold_root(tmp_path / "dataset", pages=1)
    calls = 0

    def predictor(track_id, envelope, dataset_root, settings):
        nonlocal calls
        calls += 1
        return _prediction(str(envelope["page_id"]))

    for psm in (6, 7):
        run_baseline_track(
            "modern-line-recognition-v1",
            root,
            tmp_path / f"predictions-{psm}.jsonl",
            cache_root=tmp_path / "cache",
            settings=BaselineSettings(
                engine="tesseract",
                model_version="fixture-1",
                tesseract_line_psm=psm,
            ),
            predictor=predictor,
        )

    assert calls == 2


def test_surya_server_settings_route_to_hash_bound_loopback_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _gold_root(tmp_path / "dataset", pages=1)
    model = tmp_path / "surya.gguf"
    projector = tmp_path / "surya-mmproj.gguf"
    model.write_bytes(b"model")
    projector.write_bytes(b"projector")
    seen = {}

    def fake_surya(envelopes, **kwargs):
        seen.update(kwargs)
        return [_prediction(str(envelopes[0]["page_id"]))]

    monkeypatch.setattr("hebocrbench.baseline_runner.run_surya2_page_ocr", fake_surya)
    monkeypatch.setattr("hebocrbench.baseline_runner.llama_cpp_version", lambda value: "10090")
    result = run_baseline_track(
        "modern-page-ocr-v1",
        root,
        tmp_path / "predictions.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(
            engine="surya2-llamacpp",
            surya_model_path=str(model),
            surya_mmproj_path=str(projector),
            surya_backend="server",
            surya_server_url="http://127.0.0.1:8137",
            surya_server_parallel=4,
            surya_server_context_size=32768,
        ),
    )
    prediction = load_jsonl(result.prediction_path)[0]

    assert seen["backend"] == "server"
    assert seen["server_url"] == "http://127.0.0.1:8137"
    assert seen["model_sha256"] == hashlib.sha256(b"model").hexdigest()
    assert seen["mmproj_sha256"] == hashlib.sha256(b"projector").hexdigest()
    assert prediction["model"]["inference_backend"] == "server"
    assert prediction["model"]["server_url"] == ("http://127.0.0.1:8137/v1/chat/completions")
    assert prediction["model"]["server_parallel"] == 4
    assert prediction["model"]["server_context_size"] == 32768
    assert prediction["model"]["server_context_per_slot"] == 8192


def test_surya_server_settings_fail_closed_before_non_loopback_inference(tmp_path: Path) -> None:
    model = tmp_path / "surya.gguf"
    projector = tmp_path / "surya-mmproj.gguf"
    model.write_bytes(b"model")
    projector.write_bytes(b"projector")
    settings = BaselineSettings(
        engine="surya2-llamacpp",
        surya_model_path=str(model),
        surya_mmproj_path=str(projector),
        surya_backend="server",
        surya_server_url="http://example.com:8137",
        surya_server_parallel=4,
        surya_server_context_size=32768,
    )
    with pytest.raises(BaselineRunnerError, match="127.0.0.1"):
        settings.validate()


def test_surya_server_settings_require_auditable_slot_context(tmp_path: Path) -> None:
    model = tmp_path / "surya.gguf"
    projector = tmp_path / "surya-mmproj.gguf"
    model.write_bytes(b"model")
    projector.write_bytes(b"projector")
    base = {
        "engine": "surya2-llamacpp",
        "surya_model_path": str(model),
        "surya_mmproj_path": str(projector),
        "surya_backend": "server",
        "surya_server_url": "http://127.0.0.1:8137",
    }

    with pytest.raises(BaselineRunnerError, match="surya_server_parallel"):
        BaselineSettings(**base).validate()
    with pytest.raises(BaselineRunnerError, match="context per slot"):
        BaselineSettings(
            **base,
            surya_server_parallel=4,
            surya_server_context_size=16384,
        ).validate()


@pytest.mark.parametrize(
    ("diagnostic_max_tokens", "message"),
    (
        (0, "surya_diagnostic_max_tokens must be positive"),
        (4097, "must not exceed surya_max_tokens"),
    ),
)
def test_surya_diagnostic_token_cap_validation(diagnostic_max_tokens: int, message: str) -> None:
    with pytest.raises(BaselineRunnerError, match=message):
        BaselineSettings(
            engine="tesseract",
            surya_max_tokens=4096,
            surya_diagnostic_max_tokens=diagnostic_max_tokens,
        ).validate()

    BaselineSettings(
        engine="tesseract",
        surya_max_tokens=4096,
        surya_diagnostic_max_tokens=512,
    ).validate()


def test_surya_diagnostic_token_cap_is_selected_only_for_synthetic_diagnostics() -> None:
    settings = BaselineSettings(
        engine="surya2-llamacpp",
        surya_model_path="fixture-model.gguf",
        surya_mmproj_path="fixture-mmproj.gguf",
        surya_max_tokens=4096,
        surya_diagnostic_max_tokens=512,
    )

    assert _effective_surya_max_tokens(BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK, settings) == 512
    assert _effective_surya_max_tokens("rashi-print-synthetic-diagnostic-v1", settings) == 512
    assert _effective_surya_max_tokens("modern-handwriting-v1", settings) == 4096
    assert _effective_surya_max_tokens(HISTORICAL_PRESS_TRACK, settings) == 4096


def test_surya_non_diagnostic_cache_is_byte_identical_with_diagnostic_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _gold_root(
        tmp_path / "handwriting",
        pages=1,
        gold_track="modern_handwriting",
    )
    model = tmp_path / "surya.gguf"
    projector = tmp_path / "surya-mmproj.gguf"
    model.write_bytes(b"non-diagnostic model")
    projector.write_bytes(b"non-diagnostic projector")
    calls: list[int] = []

    def fake_surya(envelopes, **kwargs):
        calls.append(kwargs["max_tokens"])
        return [_prediction(str(envelopes[0]["page_id"]))]

    monkeypatch.setattr("hebocrbench.baseline_runner.run_surya2_page_ocr", fake_surya)
    monkeypatch.setattr("hebocrbench.baseline_runner.llama_cpp_version", lambda value: "10123")
    base = {
        "engine": "surya2-llamacpp",
        "surya_model_path": str(model),
        "surya_mmproj_path": str(projector),
        "surya_max_tokens": 4096,
    }
    first = run_baseline_track(
        "modern-handwriting-v1",
        root,
        tmp_path / "first.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(**base),
    )
    cache_dir = tmp_path / "cache/1.0/surya2-llamacpp/modern-handwriting-v1"
    before = {path.name: path.read_bytes() for path in cache_dir.glob("*.json")}
    second = run_baseline_track(
        "modern-handwriting-v1",
        root,
        tmp_path / "second.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(**base, surya_diagnostic_max_tokens=512),
        predictor=lambda *args: (_ for _ in ()).throw(AssertionError("cache miss")),
    )
    after = {path.name: path.read_bytes() for path in cache_dir.glob("*.json")}

    assert first.cache_misses == 1 and second.cache_hits == 1
    assert calls == [4096]
    assert before == after
    assert first.prediction_path.read_bytes() == second.prediction_path.read_bytes()
    assert load_jsonl(second.prediction_path)[0]["model"]["max_tokens"] == 4096


def test_surya_diagnostic_cap_reuses_legacy_default_cache_and_creates_a_new_capped_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _gold_root(
        tmp_path / "niqqud",
        pages=1,
        held_out_split="diagnostic",
        gold_track="biblical_niqqud_synthetic_diagnostic",
    )
    model = tmp_path / "surya.gguf"
    projector = tmp_path / "surya-mmproj.gguf"
    model.write_bytes(b"diagnostic model")
    projector.write_bytes(b"diagnostic projector")
    calls: list[int] = []

    def fake_surya(envelopes, **kwargs):
        calls.append(kwargs["max_tokens"])
        return [_prediction(str(envelopes[0]["page_id"]))]

    monkeypatch.setattr("hebocrbench.baseline_runner.run_surya2_page_ocr", fake_surya)
    monkeypatch.setattr("hebocrbench.baseline_runner.llama_cpp_version", lambda value: "10123")
    real_asdict = runner_module.asdict

    def legacy_asdict(value):
        serialized = real_asdict(value)
        serialized.pop("surya_diagnostic_max_tokens", None)
        return serialized

    base = {
        "engine": "surya2-llamacpp",
        "surya_model_path": str(model),
        "surya_mmproj_path": str(projector),
        "surya_max_tokens": 4096,
    }
    monkeypatch.setattr(runner_module, "asdict", legacy_asdict)
    legacy = run_baseline_track(
        BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK,
        root,
        tmp_path / "legacy.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(**base),
    )
    monkeypatch.setattr(runner_module, "asdict", real_asdict)
    default = run_baseline_track(
        BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK,
        root,
        tmp_path / "default.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(**base),
        predictor=lambda *args: (_ for _ in ()).throw(AssertionError("legacy cache miss")),
    )
    capped = run_baseline_track(
        BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK,
        root,
        tmp_path / "capped.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(**base, surya_diagnostic_max_tokens=512),
    )
    capped_again = run_baseline_track(
        BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK,
        root,
        tmp_path / "capped-again.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(**base, surya_diagnostic_max_tokens=512),
        predictor=lambda *args: (_ for _ in ()).throw(AssertionError("capped cache miss")),
    )
    cache_dir = tmp_path / f"cache/1.0/surya2-llamacpp/{BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK}"

    assert legacy.cache_misses == 1 and default.cache_hits == 1
    assert capped.cache_misses == 1 and capped_again.cache_hits == 1
    assert calls == [4096, 512]
    assert len(list(cache_dir.glob("*.json"))) == 2
    assert load_jsonl(legacy.prediction_path)[0]["model"]["max_tokens"] == 4096
    assert load_jsonl(capped.prediction_path)[0]["model"]["max_tokens"] == 512
    assert capped.prediction_path.read_bytes() == capped_again.prediction_path.read_bytes()


def test_bidi_locked_diagnostic_split_is_the_only_non_test_exception(tmp_path: Path) -> None:
    root = _gold_root(tmp_path / "dataset", pages=1, held_out_split="diagnostic")

    result = run_baseline_track(
        "modern-bidi-v1",
        root,
        tmp_path / "predictions.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(engine="tesseract", model_version="fixture-1"),
        predictor=lambda track_id, envelope, dataset_root, settings: _prediction(
            str(envelope["page_id"])
        ),
    )

    assert result.evaluation_split == "diagnostic"
    assert result.selected_pages == result.source_evaluation_pages == 1


def test_diagnostic_split_is_restricted_to_explicit_diagnostic_tracks(tmp_path: Path) -> None:
    diagnostic_root = _gold_root(
        tmp_path / "diagnostic",
        pages=1,
        held_out_split="diagnostic",
    )
    result = run_baseline_track(
        BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK,
        diagnostic_root,
        tmp_path / "diagnostic.jsonl",
        cache_root=tmp_path / "diagnostic-cache",
        settings=BaselineSettings(engine="tesseract", model_version="fixture-1"),
        predictor=lambda track_id, envelope, dataset_root, settings: _prediction(
            str(envelope["page_id"])
        ),
    )
    assert result.evaluation_split == "diagnostic"

    non_diagnostic_root = _gold_root(
        tmp_path / "non-diagnostic",
        pages=1,
        held_out_split="diagnostic",
        gold_track="modern_handwriting",
    )
    with pytest.raises(BaselineRunnerError, match="required split=test"):
        run_baseline_track(
            "modern-handwriting-v1",
            non_diagnostic_root,
            tmp_path / "must-not-run.jsonl",
            cache_root=tmp_path / "non-diagnostic-cache",
            settings=BaselineSettings(engine="tesseract", model_version="fixture-1"),
            predictor=lambda track_id, envelope, dataset_root, settings: _prediction(
                str(envelope["page_id"])
            ),
        )


def test_extension_tesseract_reads_only_the_whole_line_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _gold_root(
        tmp_path / "handwriting",
        pages=1,
        gold_track="modern_handwriting",
    )
    observed: dict[str, object] = {}

    def recognize(image, language, psm, **kwargs):
        observed.update({"size": image.size, "language": language, "psm": psm})
        return "פלט כתב יד\n"

    monkeypatch.setattr("hebocrbench.baseline_runner.recognize_with_tesseract", recognize)
    result = run_baseline_track(
        "modern-handwriting-v1",
        root,
        tmp_path / "predictions.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(engine="tesseract", model_version="fixture-1"),
    )
    prediction = load_jsonl(result.prediction_path)[0]
    serialized = json.dumps(prediction, ensure_ascii=False)

    assert observed == {"size": (120, 30), "language": "heb+eng", "psm": 7}
    assert prediction["page_text"] == "פלט כתב יד"
    assert prediction["model"]["adapter"] == "tesseract_line_image"
    assert prediction["adapter_diagnostics"] == {
        "input_contract": ["page_id", "image.path"],
        "whole_line_image": True,
    }
    assert "gold-secret-line" not in serialized
    assert "טקסט זהב שאסור לקרוא" not in serialized


@pytest.mark.parametrize(
    ("track_id", "gold_track", "held_out_split"),
    (
        ("modern-handwriting-v1", "modern_handwriting", "test"),
        (
            "historical-pinkas-handwriting-v1",
            "historical_pinkas_handwriting",
            "test",
        ),
        (
            BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK,
            "biblical_niqqud_synthetic_diagnostic",
            "diagnostic",
        ),
        (
            "rashi-print-synthetic-diagnostic-v1",
            "rashi_print_synthetic_diagnostic",
            "diagnostic",
        ),
    ),
)
def test_surya_separate_line_tracks_route_blind_images_with_hash_bound_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    track_id: str,
    gold_track: str,
    held_out_split: str,
) -> None:
    root = _gold_root(
        tmp_path / "dataset",
        pages=1,
        held_out_split=held_out_split,
        gold_track=gold_track,
    )
    model = tmp_path / "surya.gguf"
    projector = tmp_path / "surya-mmproj.gguf"
    model.write_bytes(b"extension model")
    projector.write_bytes(b"extension projector")
    seen: dict[str, object] = {}

    def fake_surya(envelopes, **kwargs):
        assert len(envelopes) == 1
        envelope = envelopes[0]
        assert set(envelope) == {"page_id", "image"}
        assert set(envelope["image"]) == {"path"}
        serialized = json.dumps(envelope, ensure_ascii=False)
        assert "טקסט זהב" not in serialized
        assert "gold-secret" not in serialized
        seen.update(kwargs)
        return [_prediction(str(envelope["page_id"]))]

    monkeypatch.setattr("hebocrbench.baseline_runner.run_surya2_page_ocr", fake_surya)
    monkeypatch.setattr("hebocrbench.baseline_runner.llama_cpp_version", lambda value: "10123")
    result = run_baseline_track(
        track_id,
        root,
        tmp_path / "predictions.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(
            engine="surya2-llamacpp",
            model_version="surya-extension-test",
            surya_model_path=str(model),
            surya_mmproj_path=str(projector),
        ),
    )
    prediction = load_jsonl(result.prediction_path)[0]

    model_sha256 = hashlib.sha256(b"extension model").hexdigest()
    mmproj_sha256 = hashlib.sha256(b"extension projector").hexdigest()
    assert seen["backend"] == "cli"
    assert seen["model_sha256"] == model_sha256
    assert seen["mmproj_sha256"] == mmproj_sha256
    assert prediction["model"]["system_id"] == f"surya-ocr-2::{model_sha256}::{mmproj_sha256}"
    assert prediction["model"]["version"] == "surya-extension-test"
    assert prediction["model"]["engine_version"] == "10123"
    assert prediction["model"]["input_mode"] == "blind_whole_line_image"
    assert prediction["model"]["oracle_layout"] is False
    assert prediction["model"]["gold_assistance"] is False


def test_historical_press_oracle_envelope_exposes_layout_but_never_gold_text(
    tmp_path: Path,
) -> None:
    root = _gold_root(
        tmp_path / "historical-press",
        pages=1,
        gold_track="historical_hebrew_press_mixed",
    )
    seen: list[dict[str, object]] = []

    def predictor(track_id, envelope, dataset_root, settings):
        seen.append(envelope)
        assert track_id == HISTORICAL_PRESS_TRACK
        assert set(envelope) == {"page_id", "image", "regions"}
        assert set(envelope["image"]) == {"path"}
        assert set(envelope["regions"][0]) == {
            "region_id",
            "polygon",
            "reading_index",
            "lines",
        }
        assert set(envelope["regions"][0]["lines"][0]) == {
            "line_id",
            "polygon",
            "reading_index",
        }
        serialized = json.dumps(envelope, ensure_ascii=False)
        assert "טקסט זהב" not in serialized
        assert '"text"' not in serialized
        assert "base_direction" not in serialized
        assert "language" not in serialized
        assert "metadata" not in serialized
        return _oracle_prediction(envelope)

    result = run_baseline_track(
        HISTORICAL_PRESS_TRACK,
        root,
        tmp_path / "predictions.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(engine="tesseract", model_version="fixture-1"),
        predictor=predictor,
    )
    prediction = load_jsonl(result.prediction_path)[0]

    assert len(seen) == 1
    assert prediction["regions"][0]["region_id"] == "gold-region-0"
    assert prediction["regions"][0]["lines"][0]["line_id"] == "gold-secret-line-0"
    assert prediction["model"]["oracle_layout"] is True


def test_historical_press_cache_binds_layout_projection_but_not_gold_text(
    tmp_path: Path,
) -> None:
    root = _gold_root(
        tmp_path / "historical-press",
        pages=1,
        gold_track="historical_hebrew_press_mixed",
    )
    settings = BaselineSettings(engine="tesseract", model_version="fixture-1")
    calls = 0

    def predictor(track_id, envelope, dataset_root, received_settings):
        nonlocal calls
        calls += 1
        return _oracle_prediction(envelope)

    first = run_baseline_track(
        HISTORICAL_PRESS_TRACK,
        root,
        tmp_path / "first.jsonl",
        cache_root=tmp_path / "cache",
        settings=settings,
        predictor=predictor,
    )
    record = load_jsonl(root / "gold.jsonl")[0]
    record["regions"][0]["lines"][0]["text"] = "סוד זהב אחר"
    (root / "gold.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    text_only = run_baseline_track(
        HISTORICAL_PRESS_TRACK,
        root,
        tmp_path / "text-only.jsonl",
        cache_root=tmp_path / "cache",
        settings=settings,
        predictor=predictor,
    )
    record["regions"][0]["lines"][0]["polygon"] = [
        [3, 2],
        [118, 2],
        [118, 28],
        [3, 28],
    ]
    (root / "gold.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    geometry_changed = run_baseline_track(
        HISTORICAL_PRESS_TRACK,
        root,
        tmp_path / "geometry-changed.jsonl",
        cache_root=tmp_path / "cache",
        settings=settings,
        predictor=predictor,
    )

    assert first.cache_misses == 1
    assert text_only.cache_hits == 1
    assert geometry_changed.cache_misses == 1
    assert calls == 2


def test_historical_press_rejects_unsupported_apple_engine(tmp_path: Path) -> None:
    root = _gold_root(
        tmp_path / "historical-press",
        pages=1,
        gold_track="historical_hebrew_press_mixed",
    )
    with pytest.raises(BaselineRunnerError, match="Tesseract oracle-layout.*Surya OCR 2"):
        run_baseline_track(
            HISTORICAL_PRESS_TRACK,
            root,
            tmp_path / "predictions.jsonl",
            cache_root=tmp_path / "cache",
            settings=BaselineSettings(engine="apple-vision", model_version="fixture-1"),
        )


def test_historical_press_surya_is_blind_and_cache_ignores_gold_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _gold_root(
        tmp_path / "historical-press",
        pages=1,
        gold_track="historical_hebrew_press_mixed",
    )
    model = tmp_path / "surya.gguf"
    projector = tmp_path / "surya-mmproj.gguf"
    model.write_bytes(b"press model")
    projector.write_bytes(b"press projector")
    calls = 0

    def fake_surya(envelopes, **kwargs):
        nonlocal calls
        calls += 1
        envelope = envelopes[0]
        assert set(envelope) == {"page_id", "image"}
        assert set(envelope["image"]) == {"path"}
        serialized = json.dumps(envelope, ensure_ascii=False)
        assert "regions" not in serialized
        assert "טקסט זהב" not in serialized
        assert "gold-secret" not in serialized
        return [_prediction(str(envelope["page_id"]))]

    monkeypatch.setattr("hebocrbench.baseline_runner.run_surya2_page_ocr", fake_surya)
    monkeypatch.setattr("hebocrbench.baseline_runner.llama_cpp_version", lambda value: "10123")
    settings = BaselineSettings(
        engine="surya2-llamacpp",
        surya_model_path=str(model),
        surya_mmproj_path=str(projector),
    )
    first = run_baseline_track(
        HISTORICAL_PRESS_TRACK,
        root,
        tmp_path / "first.jsonl",
        cache_root=tmp_path / "cache",
        settings=settings,
    )
    record = load_jsonl(root / "gold.jsonl")[0]
    record["regions"][0]["lines"][0]["polygon"] = [
        [3, 2],
        [118, 2],
        [118, 28],
        [3, 28],
    ]
    (root / "gold.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    geometry_changed = run_baseline_track(
        HISTORICAL_PRESS_TRACK,
        root,
        tmp_path / "geometry-changed.jsonl",
        cache_root=tmp_path / "cache",
        settings=settings,
    )
    prediction = load_jsonl(first.prediction_path)[0]

    assert first.cache_misses == 1
    assert geometry_changed.cache_hits == 1
    assert calls == 1
    assert prediction["model"]["input_mode"] == "blind_full_page_image"
    assert prediction["model"]["oracle_layout"] is False
    assert prediction["model"]["gold_assistance"] is False


def test_historical_press_all_34_pages_and_4016_lines_are_runnable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _historical_press_root(tmp_path / "historical-press")
    monkeypatch.setattr(
        "hebocrbench.baseline_runner.recognize_with_tesseract",
        lambda image, language, psm, **kwargs: "פלט עיתונות",
    )
    result = run_baseline_track(
        HISTORICAL_PRESS_TRACK,
        root,
        tmp_path / "predictions.jsonl",
        cache_root=tmp_path / "cache",
        settings=BaselineSettings(engine="tesseract", model_version="fixture-1"),
        workers=4,
    )
    predictions = load_jsonl(result.prediction_path)
    prediction_line_ids = {
        line["line_id"]
        for page in predictions
        for region in page["regions"]
        for line in region["lines"]
    }
    gold_line_ids = {
        line["line_id"]
        for page in load_jsonl(root / "gold.jsonl")
        for region in page["regions"]
        for line in region["lines"]
    }

    assert result.selected_pages == result.source_evaluation_pages == 34
    assert result.failures == 0
    assert len(predictions) == 34
    assert len(prediction_line_ids) == len(gold_line_ids) == 4016
    assert prediction_line_ids == gold_line_ids
    assert all(page["model"]["oracle_layout"] is True for page in predictions)
    assert all(
        page["adapter_diagnostics"]["input_mode"] == "oracle_layout_line_crops"
        for page in predictions
    )


def test_parallel_workers_preserve_gold_order_and_resume_across_worker_counts(
    tmp_path: Path,
) -> None:
    root = _gold_root(tmp_path / "parallel", pages=4, all_held_out=True)

    def out_of_order_predictor(track_id, envelope, dataset_root, settings):
        index = int(str(envelope["page_id"]).rsplit("-", 1)[1])
        time.sleep((3 - index) * 0.005)
        return _prediction(str(envelope["page_id"]))

    settings = BaselineSettings(engine="tesseract", model_version="fixture-1")
    first = run_baseline_track(
        "modern-line-recognition-v1",
        root,
        tmp_path / "parallel-first.jsonl",
        cache_root=tmp_path / "cache",
        settings=settings,
        workers=4,
        predictor=out_of_order_predictor,
    )
    second = run_baseline_track(
        "modern-line-recognition-v1",
        root,
        tmp_path / "parallel-second.jsonl",
        cache_root=tmp_path / "cache",
        settings=settings,
        workers=2,
        predictor=lambda *args: (_ for _ in ()).throw(AssertionError("cache miss")),
    )

    assert [item["page_id"] for item in load_jsonl(first.prediction_path)] == [
        "page-0",
        "page-1",
        "page-2",
        "page-3",
    ]
    assert first.cache_misses == 4 and first.cache_hits == 0
    assert second.cache_hits == 4 and second.cache_misses == 0
    assert first.prediction_path.read_bytes() == second.prediction_path.read_bytes()


def test_extension_suite_emits_separate_non_headline_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _gold_root(
        tmp_path / "handwriting",
        pages=1,
        gold_track="modern_handwriting",
    )
    monkeypatch.setattr(
        "hebocrbench.baseline_runner.recognize_with_tesseract",
        lambda image, language, psm, **kwargs: "פלט מנוע",
    )
    output = tmp_path / "extension-output"
    summary = run_extension_baseline_suite(
        {"modern-handwriting-v1": root},
        output,
        settings=BaselineSettings(engine="tesseract", model_version="fixture-1"),
        workers=2,
    )

    assert summary["reporting_policy"] == {
        "modern_headline_blending": False,
        "combined_score": None,
        "synthetic_diagnostics_rankable": False,
    }
    assert summary["groups"] == {
        "separate_real_extensions": ["modern-handwriting-v1"],
        "synthetic_diagnostics": [],
    }
    track = summary["tracks"]["modern-handwriting-v1"]
    assert track["reporting_class"] == "separate_real_extension"
    assert track["modern_headline_eligible"] is False
    assert track["diagnostic_only"] is False
    assert (output / "separate-baseline-run.json").is_file()
    assert (output / "reports/modern-handwriting-v1/run_manifest.json").is_file()
    assert not (output / "modern-score.json").exists()


def test_historical_press_report_declares_oracle_layout_input_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _gold_root(
        tmp_path / "historical-press",
        pages=1,
        gold_track="historical_hebrew_press_mixed",
    )
    monkeypatch.setattr(
        "hebocrbench.baseline_runner.recognize_with_tesseract",
        lambda image, language, psm, **kwargs: "פלט עיתונות",
    )
    output = tmp_path / "extension-output"
    summary = run_extension_baseline_suite(
        {HISTORICAL_PRESS_TRACK: root},
        output,
        settings=BaselineSettings(engine="tesseract", model_version="fixture-1"),
        max_pages=1,
    )
    run_manifest = json.loads(
        (output / f"reports/{HISTORICAL_PRESS_TRACK}/run_manifest.json").read_text(encoding="utf-8")
    )

    assert summary["groups"]["separate_real_extensions"] == [HISTORICAL_PRESS_TRACK]
    assert summary["tracks"][HISTORICAL_PRESS_TRACK]["input_mode"] == ("oracle_layout_line_crops")
    assert run_manifest["configuration"]["input_mode"] == "oracle_layout_line_crops"
    assert run_manifest["model"]["input_mode"] == "oracle_layout_line_crops"
    assert run_manifest["model"]["oracle_layout"] is True


def test_surya_extension_reports_keep_line_and_page_modes_separate_and_non_headline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handwriting = _gold_root(
        tmp_path / "handwriting",
        pages=1,
        gold_track="modern_handwriting",
    )
    press = _gold_root(
        tmp_path / "historical-press",
        pages=1,
        gold_track="historical_hebrew_press_mixed",
    )
    niqqud = _gold_root(
        tmp_path / "niqqud",
        pages=1,
        held_out_split="diagnostic",
        gold_track="biblical_niqqud_synthetic_diagnostic",
    )
    model = tmp_path / "surya.gguf"
    projector = tmp_path / "surya-mmproj.gguf"
    model.write_bytes(b"suite model")
    projector.write_bytes(b"suite projector")
    calls: list[dict[str, object]] = []

    def fake_surya(envelopes, **kwargs):
        assert set(envelopes[0]) == {"page_id", "image"}
        calls.append(dict(kwargs))
        return [_prediction(str(envelopes[0]["page_id"]))]

    monkeypatch.setattr("hebocrbench.baseline_runner.run_surya2_page_ocr", fake_surya)
    monkeypatch.setattr("hebocrbench.baseline_runner.llama_cpp_version", lambda value: "10123")
    output = tmp_path / "surya-extension-output"
    settings = BaselineSettings(
        engine="surya2-llamacpp",
        model_version="surya-suite-test",
        surya_model_path=str(model),
        surya_mmproj_path=str(projector),
        surya_backend="server",
        surya_server_url="http://127.0.0.1:8137",
        surya_server_parallel=4,
        surya_server_context_size=32768,
        surya_max_tokens=4096,
        surya_diagnostic_max_tokens=512,
        surya_image_max_tokens=2048,
    )
    summary = run_extension_baseline_suite(
        {
            "modern-handwriting-v1": handwriting,
            HISTORICAL_PRESS_TRACK: press,
            BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK: niqqud,
        },
        output,
        settings=settings,
        max_pages=1,
        workers=2,
    )

    assert len(calls) == 3
    assert all(call["backend"] == "server" for call in calls)
    assert [call["max_tokens"] for call in calls] == [4096, 4096, 512]
    assert summary["engine"] == "surya2-llamacpp"
    assert summary["settings"]["surya_model_path"] == str(model)
    assert summary["settings"]["surya_mmproj_path"] == str(projector)
    assert summary["settings"]["surya_backend"] == "server"
    assert summary["settings"]["surya_server_url"] == "http://127.0.0.1:8137"
    assert summary["settings"]["surya_server_parallel"] == 4
    assert summary["settings"]["surya_server_context_size"] == 32768
    assert summary["settings"]["surya_max_tokens"] == 4096
    assert summary["settings"]["surya_diagnostic_max_tokens"] == 512
    assert summary["settings"]["surya_image_max_tokens"] == 2048
    assert summary["reporting_policy"] == {
        "modern_headline_blending": False,
        "combined_score": None,
        "synthetic_diagnostics_rankable": False,
    }
    expected = {
        "modern-handwriting-v1": ("blind_whole_line_image", 4096),
        HISTORICAL_PRESS_TRACK: ("blind_full_page_image", 4096),
        BIBLICAL_NIQQUD_DIAGNOSTIC_TRACK: ("blind_whole_line_image", 512),
    }
    for track_id, (input_mode, max_tokens) in expected.items():
        track = summary["tracks"][track_id]
        manifest = json.loads(
            (output / f"reports/{track_id}/run_manifest.json").read_text(encoding="utf-8")
        )
        prediction = load_jsonl(output / f"predictions/{track_id}.jsonl")[0]
        assert track["input_mode"] == input_mode
        assert track["effective_max_tokens"] == max_tokens
        assert track["oracle_layout"] is False
        assert track["gold_assistance"] is False
        assert track["adapter"] == "surya2_llamacpp_server_page_e2e"
        assert manifest["configuration"]["input_mode"] == input_mode
        assert manifest["configuration"]["oracle_layout"] is False
        assert manifest["configuration"]["gold_assistance"] is False
        assert manifest["model"]["input_mode"] == input_mode
        assert manifest["model"]["oracle_layout"] is False
        assert manifest["model"]["gold_assistance"] is False
        assert manifest["model"]["adapter"] == "surya2_llamacpp_server_page_e2e"
        assert manifest["model"]["max_tokens"] == max_tokens
        assert manifest["configuration"]["surya_effective_max_tokens"] == max_tokens
        assert prediction["model"]["max_tokens"] == max_tokens
    assert not (output / "modern-score.json").exists()
