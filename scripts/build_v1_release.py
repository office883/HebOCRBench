#!/usr/bin/env python3
"""Build HebOCRBench 1.0 source, wheel, registry, SBOM and checksum artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hebocrbench.corpus_registry import load_registry  # noqa: E402
from hebocrbench.modern_suite import (  # noqa: E402
    DEFAULT_HEADLINE_TRACKS,
    load_modern_suite_lock,
    validate_modern_suite_contract,
)
from hebocrbench.profiles import load_profiles, profile_fingerprint  # noqa: E402
from hebocrbench.release_packaging import (  # noqa: E402
    build_source_tar_gz,
    build_source_zip,
    build_wheel,
    write_checksums,
    write_sbom,
)
from hebocrbench.tracks import list_official_tracks, verify_track_lock  # noqa: E402


VERSION = "1.0.0"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--suite-lock",
        type=Path,
        required=True,
        help="Certified Modern Hebrew suite lock bound to the release corpus bytes",
    )
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = args.output.resolve()
    suite_lock_source = args.suite_lock.resolve()

    registry = load_registry(root / "corpora" / "registry.yaml")
    profiles = load_profiles(root / "corpora" / "profiles.yaml", registry=registry)
    profile = profiles.profiles.get("modern-hebrew-print-v1")
    if profile is None:
        parser.error("canonical profile modern-hebrew-print-v1 is missing")
    track_report = verify_track_lock(root / "tracks")
    if not track_report.valid:
        parser.error("official track lock is invalid: " + "; ".join(track_report.issues))
    suite = load_modern_suite_lock(suite_lock_source)
    validate_modern_suite_contract(
        suite,
        expected_benchmark_version=VERSION,
        expected_registry_fingerprint=registry.fingerprint,
        expected_profile_id=profile.profile_id,
        expected_profile_fingerprint=profile_fingerprint(profile),
        allowed_track_ids={spec.track_id for spec in list_official_tracks()},
    )

    if args.clean and output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    wheel = build_wheel(root, output)
    source_zip = build_source_zip(root, output)
    source_tar_gz = build_source_tar_gz(root, output)
    sbom = write_sbom(root, output / f"HebOCRBench-v{VERSION}-SBOM.json")
    registry_lock = output / f"HebOCRBench-v{VERSION}-registry.lock.json"
    registry_yaml = output / f"HebOCRBench-v{VERSION}-registry.yaml"
    profiles_lock = output / f"HebOCRBench-v{VERSION}-profiles.lock.json"
    profiles_yaml = output / f"HebOCRBench-v{VERSION}-profiles.yaml"
    tracks_lock = output / f"HebOCRBench-v{VERSION}-tracks.lock.json"
    suite_lock = output / f"HebOCRBench-v{VERSION}-modern-suite.lock.json"
    shutil.copyfile(root / "corpora" / "registry.lock.json", registry_lock)
    shutil.copyfile(root / "corpora" / "registry.yaml", registry_yaml)
    shutil.copyfile(root / "corpora" / "profiles.lock.json", profiles_lock)
    shutil.copyfile(root / "corpora" / "profiles.yaml", profiles_yaml)
    shutil.copyfile(root / "tracks" / "tracks.lock.json", tracks_lock)
    shutil.copyfile(suite_lock_source, suite_lock)
    manifest = output / f"HebOCRBench-v{VERSION}-release-manifest.json"
    registry_payload = json.loads(registry_lock.read_text(encoding="utf-8"))
    profiles_payload = json.loads(profiles_lock.read_text(encoding="utf-8"))
    payload = {
        "schema_version": "1.0",
        "benchmark": "HebOCRBench",
        "version": VERSION,
        "release_date": "2026-07-26",
        "scope": "modern-hebrew-only",
        "registry_fingerprint": registry_payload["registry_fingerprint"],
        "profiles_fingerprint": profiles_payload["profiles_fingerprint"],
        "suite_fingerprint": suite.suite_fingerprint,
        "official_profiles": sorted(profiles_payload["profiles"]),
        "headline_tracks": list(DEFAULT_HEADLINE_TRACKS),
        "artifacts": [
            wheel.name,
            source_zip.name,
            source_tar_gz.name,
            sbom.name,
            registry_lock.name,
            registry_yaml.name,
            profiles_lock.name,
            profiles_yaml.name,
            tracks_lock.name,
            suite_lock.name,
        ],
        "data_distribution": "federated",
        "third_party_corpora_bundled": False,
        "note": (
            "The release ships Modern Hebrew evaluator code, locked task contracts, source "
            "recipes and a certified suite identity. Corpus bytes remain federated or are "
            "published as separately checksummed assets according to their source terms."
        ),
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sums = write_checksums(
        [
            wheel,
            source_zip,
            source_tar_gz,
            sbom,
            registry_lock,
            registry_yaml,
            profiles_lock,
            profiles_yaml,
            tracks_lock,
            suite_lock,
            manifest,
        ],
        output / f"HebOCRBench-v{VERSION}-SHA256SUMS.txt",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "wheel": str(wheel),
                "source_zip": str(source_zip),
                "source_tar_gz": str(source_tar_gz),
                "sbom": str(sbom),
                "registry_lock": str(registry_lock),
                "registry": str(registry_yaml),
                "profiles_lock": str(profiles_lock),
                "profiles": str(profiles_yaml),
                "tracks_lock": str(tracks_lock),
                "suite_lock": str(suite_lock),
                "manifest": str(manifest),
                "checksums": str(sums),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
