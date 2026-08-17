from __future__ import annotations

import json
from pathlib import Path

from hebocrbench.cli import main
from hebocrbench.tracks import load_track, track_lock_payload, verify_track_lock


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [
    "biblical-niqqud-synthetic-diagnostic-v1",
    "historical-hebrew-press-mixed-v1",
    "historical-pinkas-handwriting-v1",
    "modern-bidi-v1",
    "modern-forms-v1",
    "modern-handwriting-v1",
    "modern-line-recognition-v1",
    "modern-page-ocr-v1",
    "modern-robustness-v1",
    "modern-tables-v1",
    "rashi-print-synthetic-diagnostic-v1",
]


def test_official_modern_tracks_lock_identity_policy():
    bidi = load_track("modern-bidi-v1")
    assert bidi.version == "1.1.0"
    assert bidi.benchmark_config.conformance.gate_quality_metrics is False
    assert bidi.benchmark_config.conformance.gate_all_bidi_controls is False
    assert bidi.benchmark_config.conformance.min_visual_order_gain == 0.25
    assert bidi.benchmark_config.conformance.max_visual_order_error_rate == 0.25
    assert load_track("modern-page-ocr-v1").benchmark_config.matching.use_shared_ids is False
    assert load_track("modern-tables-v1").benchmark_config.matching.use_shared_ids is False
    oracle = load_track("modern-line-recognition-v1")
    assert oracle.benchmark_config.matching.use_shared_ids is True
    assert oracle.accepted_gold_tracks == ("modern_line_recognition", "modern_page_ocr")
    handwriting = load_track("modern-handwriting-v1")
    assert handwriting.accepted_gold_tracks == ("modern_handwriting",)
    pinkas = load_track("historical-pinkas-handwriting-v1")
    assert pinkas.accepted_gold_tracks == ("historical_pinkas_handwriting",)
    assert pinkas.benchmark_config.matching.use_shared_ids is True
    press = load_track("historical-hebrew-press-mixed-v1")
    assert press.accepted_gold_tracks == ("historical_hebrew_press_mixed",)
    assert press.prediction_mode == "oracle-layout"
    assert "pure-Rashi" in press.description
    niqqud = load_track("biblical-niqqud-synthetic-diagnostic-v1")
    rashi = load_track("rashi-print-synthetic-diagnostic-v1")
    assert niqqud.accepted_gold_tracks == ("biblical_niqqud_synthetic_diagnostic",)
    assert "diacritics.mark_f1" in niqqud.primary_metrics
    assert rashi.accepted_gold_tracks == ("rashi_print_synthetic_diagnostic",)
    assert niqqud.benchmark_config.matching.use_shared_ids is True
    assert rashi.benchmark_config.matching.use_shared_ids is True


def test_authoritative_track_lock_is_generated_from_yaml_and_matches_package():
    expected = json.loads((ROOT / "tracks" / "tracks.lock.json").read_text(encoding="utf-8"))
    assert expected["tracks_version"] == "1.1.0"
    assert track_lock_payload(ROOT / "tracks") == expected
    report = verify_track_lock(ROOT / "tracks")
    assert report.valid
    assert report.checked == len(EXPECTED)
    assert verify_track_lock().valid


def test_track_lock_detects_tampered_yaml(tmp_path):
    for path in (ROOT / "tracks").iterdir():
        if path.is_file():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    path = tmp_path / "modern-page-ocr-v1.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")

    report = verify_track_lock(tmp_path)

    assert not report.valid
    assert any("sha256 mismatch" in issue for issue in report.issues)


def test_tracks_cli_lists_shows_and_verifies(capsys):
    assert main(["tracks", "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [item["track_id"] for item in listed["tracks"]] == EXPECTED
    assert main(["tracks", "show", "modern-page-ocr-v1"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["configuration"]["matching"]["use_shared_ids"] is False
    assert main(["tracks", "verify"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
