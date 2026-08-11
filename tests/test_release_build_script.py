from __future__ import annotations

import hashlib
from importlib import util
import json
from pathlib import Path
import subprocess
import sys

from hebocrbench.corpus_registry import load_registry
from hebocrbench.full_suite import build_full_suite_lock
from hebocrbench.io import sha256_file
from hebocrbench.modern_suite import DEFAULT_HEADLINE_TRACKS, build_modern_suite_lock
from hebocrbench.profiles import load_profiles, profile_fingerprint
from hebocrbench.release_packaging import build_wheel


ROOT = Path(__file__).resolve().parents[1]


def _verifier_module():
    path = ROOT / "scripts" / "verify_v1_release.py"
    spec = util.spec_from_file_location("hebocrbench_verify_v1_release_test", path)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _certified_root(
    root: Path,
    *,
    track_id: str,
    registry_fingerprint: str,
) -> Path:
    root.mkdir(parents=True)
    gold = root / "gold.jsonl"
    stats = root / "stats.json"
    gold.write_text(
        json.dumps(
            {"page_id": f"{track_id}-page-1", "track": track_id},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(stats, {"page_count": 1, "track_id": track_id})
    dataset_fingerprint = hashlib.sha256(
        f"{track_id}\0{sha256_file(gold)}\0{sha256_file(stats)}".encode()
    ).hexdigest()
    dataset_lock = root / "dataset.lock.json"
    _write_json(
        dataset_lock,
        {
            "dataset_fingerprint": dataset_fingerprint,
            "records_sha256": sha256_file(gold),
            "stats_sha256": sha256_file(stats),
        },
    )
    inventory = [
        {
            "path": path.name,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in (dataset_lock, gold, stats)
    ]
    manifest = root / "manifest.json"
    _write_json(
        manifest,
        {
            "benchmark": "HebOCRBench",
            "benchmark_version": "1.0.0",
            "profile": "modern-hebrew-print-v1",
            "profile_scope": "track-component",
            "track_id": track_id,
            "dataset_fingerprint": dataset_fingerprint,
            "registry_fingerprint": registry_fingerprint,
            "page_count": 1,
            "source_ids": [f"fixture-{track_id}"],
            "files": inventory,
        },
    )
    certification = root / "certification.json"
    _write_json(certification, {"valid": True, "track_id": track_id})
    _write_json(
        root / "FROZEN.json",
        {
            "dataset_fingerprint": dataset_fingerprint,
            "manifest_sha256": sha256_file(manifest),
        },
    )
    _write_json(
        root / "CERTIFIED.json",
        {
            "benchmark_version": "1.0.0",
            "certification_sha256": sha256_file(certification),
            "certified": True,
            "dataset_fingerprint": dataset_fingerprint,
            "registry_fingerprint": registry_fingerprint,
        },
    )
    return root


def _suite_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    registry = load_registry(ROOT / "corpora" / "registry.yaml")
    profiles = load_profiles(ROOT / "corpora" / "profiles.yaml", registry=registry)
    profile = profiles.profiles["modern-hebrew-print-v1"]
    roots = {
        track_id: _certified_root(
            tmp_path / "roots" / track_id,
            track_id=track_id,
            registry_fingerprint=registry.fingerprint,
        )
        for track_id in DEFAULT_HEADLINE_TRACKS
    }
    modern_payload = build_modern_suite_lock(
        roots,
        profile_id=profile.profile_id,
        profile_fingerprint=profile_fingerprint(profile),
        registry_fingerprint=registry.fingerprint,
    )
    full_payload = build_full_suite_lock(
        roots,
        registry_fingerprint=registry.fingerprint,
        profiles_fingerprint=profiles.fingerprint,
    )
    modern_path = tmp_path / "modern-suite.lock.json"
    full_path = tmp_path / "full-suite.lock.json"
    _write_json(modern_path, modern_payload)
    _write_json(full_path, full_payload)
    return modern_path, full_path, roots


def _root_arguments(roots: dict[str, Path]) -> list[str]:
    result: list[str] = []
    for track_id, path in sorted(roots.items()):
        result.extend(["--component-root", f"{track_id}={path}"])
    return result


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
    assert "modern-suite-lock" in result.stderr.lower()


def test_build_v1_release_emits_modern_suite_tracks_and_verifiable_artifacts(tmp_path):
    output = tmp_path / "release"
    modern_suite_lock, full_suite_lock, roots = _suite_fixture(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_v1_release.py"),
            "--root",
            str(ROOT),
            "--output",
            str(output),
            "--modern-suite-lock",
            str(modern_suite_lock),
            "--full-suite-lock",
            str(full_suite_lock),
            *_root_arguments(roots),
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
    copied_modern_suite = json.loads(Path(summary["modern_suite_lock"]).read_text(encoding="utf-8"))
    copied_full_suite = json.loads(Path(summary["full_suite_lock"]).read_text(encoding="utf-8"))
    component_proof = json.loads(Path(summary["component_proof"]).read_text(encoding="utf-8"))

    assert manifest["version"] == "1.0.0"
    assert manifest["release_date"] == "2026-07-26"
    assert manifest["scope"] == "multi-profile-hebrew-suite"
    assert manifest["registry_fingerprint"] == registry_lock["registry_fingerprint"]
    assert manifest["profiles_fingerprint"] == profiles_lock["profiles_fingerprint"]
    assert manifest["modern_suite_fingerprint"] == copied_modern_suite["suite_fingerprint"]
    assert manifest["full_suite_fingerprint"] == copied_full_suite["suite_fingerprint"]
    assert manifest["component_proof_fingerprint"] == component_proof["proof_fingerprint"]
    assert manifest["profiles"] == sorted(profiles_lock["profiles"])
    assert manifest["official_profiles"] == manifest["profiles"]
    assert manifest["headline_profile"] == "modern-hebrew-print-v1"
    assert manifest["headline_profile_fingerprint"] == copied_modern_suite["profile_fingerprint"]
    assert manifest["headline_score_policy"] == "guarded-five-track-weighted-geometric"
    assert manifest["headline_tracks"] == list(DEFAULT_HEADLINE_TRACKS)
    assert manifest["non_headline_profiles"] == sorted(
        set(profiles_lock["profiles"]) - {manifest["headline_profile"]}
    )
    assert manifest["separate_profile_reporting"] is True
    assert manifest["extensions_blended_into_headline"] is False
    assert manifest["third_party_corpora_bundled"] is False
    assert manifest["certified_components"] == sorted(DEFAULT_HEADLINE_TRACKS)
    assert manifest["all_certified_component_roots_verified"] is True
    assert component_proof["components"]

    sums = {}
    for line in Path(summary["checksums"]).read_text(encoding="utf-8").splitlines():
        digest, filename = line.split("  ", 1)
        sums[filename] = digest
    expected = set(manifest["artifacts"]) | {manifest_path.name}
    assert expected <= set(sums)
    for filename in expected:
        payload = (output / filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == sums[filename]

    verifier = _verifier_module()
    report = verifier.verify(
        ROOT,
        release_dir=output,
        manifest=manifest_path,
        component_roots=[f"{track_id}={path}" for track_id, path in sorted(roots.items())],
        run_tests=False,
        run_sanity=False,
    )
    assert report["verified"] is True, report
    checks = {item["name"]: item for item in report["checks"]}
    for name in (
        "checksum_manifest_membership",
        "release_artifact_sha256",
        "sbom_declared_dependencies_complete",
        "modern_full_suite_roots_and_proof",
        "wheel_canonical_locks",
        "source_zip_complete",
        "source_zip_source_bytes",
        "source_tar_complete",
        "source_tar_source_bytes",
    ):
        assert checks[name]["passed"] is True, checks[name]

    # Re-hashing a corrupted SBOM is insufficient: semantic completeness still fails closed.
    sbom_path = Path(summary["sbom"])
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    sbom["declared_dependencies"] = []
    _write_json(sbom_path, sbom)
    checksum_path = Path(summary["checksums"])
    rewritten = []
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        _, filename = line.split("  ", 1)
        digest = hashlib.sha256((output / filename).read_bytes()).hexdigest()
        rewritten.append(f"{digest}  {filename}")
    checksum_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    bundle_checks, _ = verifier._verify_release_bundle(ROOT, output, manifest_path)
    bundle_checks = {item["name"]: item for item in bundle_checks}
    assert bundle_checks["release_artifact_sha256"]["passed"] is True
    assert bundle_checks["sbom_declared_dependencies_complete"]["passed"] is False


def test_release_verifier_rehashes_component_roots_and_fails_after_tampering(tmp_path):
    output = tmp_path / "release"
    modern_suite_lock, full_suite_lock, roots = _suite_fixture(tmp_path)
    command = [
        sys.executable,
        str(ROOT / "scripts" / "build_v1_release.py"),
        "--root",
        str(ROOT),
        "--output",
        str(output),
        "--modern-suite-lock",
        str(modern_suite_lock),
        "--full-suite-lock",
        str(full_suite_lock),
        *_root_arguments(roots),
        "--clean",
    ]
    built = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert built.returncode == 0, built.stderr

    target = roots["modern-line-recognition-v1"] / "gold.jsonl"
    with target.open("a", encoding="utf-8") as handle:
        handle.write('{"page_id":"tampered"}\n')
    verifier = _verifier_module()
    report = verifier.verify(
        ROOT,
        release_dir=output,
        manifest=output / "HebOCRBench-v1.0.0-release-manifest.json",
        component_roots=[f"{track_id}={path}" for track_id, path in sorted(roots.items())],
        run_tests=False,
        run_sanity=False,
    )
    proof_check = next(
        item for item in report["checks"] if item["name"] == "modern_full_suite_roots_and_proof"
    )
    assert report["verified"] is False
    assert proof_check["passed"] is False
    assert "stale" in str(proof_check["detail"])


def test_verifier_compares_runtime_metadata_to_packaged_locks_dynamically(tmp_path):
    verifier = _verifier_module()
    static_checks = {item["name"]: item for item in verifier._static_checks(ROOT)}
    registry_lock = json.loads(
        (ROOT / "corpora" / "registry.lock.json").read_text(encoding="utf-8")
    )
    profiles_lock = json.loads(
        (ROOT / "corpora" / "profiles.lock.json").read_text(encoding="utf-8")
    )

    assert static_checks["registry_lock"]["passed"] is True
    assert static_checks["registry_lock"]["detail"]["actual_sources"] == sorted(
        registry_lock["sources"]
    )
    assert static_checks["profiles_lock"]["passed"] is True
    assert static_checks["profiles_lock"]["detail"]["actual_profiles"] == sorted(
        profiles_lock["profiles"]
    )

    wheel = build_wheel(ROOT, tmp_path)
    commands = []
    wheel_checks = verifier._verify_wheel(ROOT, wheel, commands)
    assert all(item["passed"] for item in wheel_checks)
    wheel_import = next(item for item in commands if item["name"] == "wheel_import")
    assert wheel_import["passed"] is True
    command_text = " ".join(wheel_import["command"])
    assert "registry_lock_payload" in command_text
    assert "profile_lock_payload" in command_text
