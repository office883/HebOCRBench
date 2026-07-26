from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from hebocrbench.corpus_registry import load_registry
from hebocrbench.modern_suite import DEFAULT_HEADLINE_TRACKS, with_suite_fingerprint
from hebocrbench.profiles import load_profiles, profile_fingerprint


ROOT = Path(__file__).resolve().parents[1]


def _suite_lock(tmp_path: Path) -> Path:
    registry = load_registry(ROOT / "corpora" / "registry.yaml")
    profiles = load_profiles(ROOT / "corpora" / "profiles.yaml", registry=registry)
    profile = profiles.profiles["modern-hebrew-print-v1"]
    digest = "a" * 64
    payload = with_suite_fingerprint(
        {
            "schema_version": "1.0",
            "suite_version": "1.0.0",
            "benchmark": "HebOCRBench Modern Hebrew",
            "benchmark_version": "1.0.0",
            "profile_id": profile.profile_id,
            "profile_fingerprint": profile_fingerprint(profile),
            "registry_fingerprint": registry.fingerprint,
            "tracks": {
                track_id: {
                    "maturity": "certified",
                    "headline": True,
                    "dataset_fingerprint": digest,
                    "gold_sha256": digest,
                    "certification_sha256": digest,
                }
                for track_id in DEFAULT_HEADLINE_TRACKS
            },
        }
    )
    path = tmp_path / "modern-suite.lock.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_build_v1_release_refuses_to_publish_without_suite_lock(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_v1_release.py"),
            "--root",
            str(ROOT),
            "--output",
            str(tmp_path / "release"),
            "--clean",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "suite-lock" in result.stderr.lower()


def test_build_v1_release_emits_modern_suite_tracks_and_verifiable_artifacts(tmp_path):
    output = tmp_path / "release"
    suite_lock = _suite_lock(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_v1_release.py"),
            "--root",
            str(ROOT),
            "--output",
            str(output),
            "--suite-lock",
            str(suite_lock),
            "--clean",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    manifest_path = Path(summary["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry_lock = json.loads(Path(summary["registry_lock"]).read_text(encoding="utf-8"))
    profiles_lock = json.loads(Path(summary["profiles_lock"]).read_text(encoding="utf-8"))
    copied_suite = json.loads(Path(summary["suite_lock"]).read_text(encoding="utf-8"))

    assert manifest["version"] == "1.0.0"
    assert manifest["release_date"] == "2026-07-26"
    assert manifest["scope"] == "modern-hebrew-only"
    assert manifest["registry_fingerprint"] == registry_lock["registry_fingerprint"]
    assert manifest["profiles_fingerprint"] == profiles_lock["profiles_fingerprint"]
    assert manifest["suite_fingerprint"] == copied_suite["suite_fingerprint"]
    assert manifest["official_profiles"] == [
        "modern-hebrew-development-v1",
        "modern-hebrew-handwriting-v1",
        "modern-hebrew-print-v1",
    ]
    assert manifest["headline_tracks"] == list(DEFAULT_HEADLINE_TRACKS)
    assert manifest["third_party_corpora_bundled"] is False

    sums = {}
    for line in Path(summary["checksums"]).read_text(encoding="utf-8").splitlines():
        digest, filename = line.split("  ", 1)
        sums[filename] = digest
    expected = set(manifest["artifacts"]) | {manifest_path.name}
    assert expected <= set(sums)
    for filename in expected:
        payload = (output / filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == sums[filename]
