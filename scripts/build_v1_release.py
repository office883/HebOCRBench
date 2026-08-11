#!/usr/bin/env python3
"""Build the HebOCRBench 1.0 multi-profile Hebrew release artifacts."""

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
from hebocrbench.full_suite import (  # noqa: E402
    load_full_suite_lock,
    validate_full_suite_contract,
)
from hebocrbench.modern_suite import (  # noqa: E402
    DEFAULT_HEADLINE_TRACKS,
    load_modern_suite_lock,
    validate_modern_suite_contract,
)
from hebocrbench.profiles import load_profiles, profile_fingerprint  # noqa: E402
from hebocrbench.release_metadata import (  # noqa: E402
    profile_lock_payload,
    registry_lock_payload,
)
from hebocrbench.release_packaging import (  # noqa: E402
    build_source_tar_gz,
    build_source_zip,
    build_wheel,
    write_checksums,
    write_sbom,
)
from hebocrbench.release_integrity import (  # noqa: E402
    ReleaseIntegrityError,
    parse_component_roots,
    verify_release_suite_roots,
)
from hebocrbench.tracks import list_official_tracks, verify_track_lock  # noqa: E402


VERSION = "1.0.0"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--modern-suite-lock",
        type=Path,
        required=True,
        help="Certified Modern Hebrew suite lock bound to the release corpus bytes",
    )
    parser.add_argument(
        "--full-suite-lock",
        type=Path,
        required=True,
        help="Unified multi-profile suite lock bound to every certified component root",
    )
    parser.add_argument(
        "--component-root",
        action="append",
        required=True,
        metavar="COMPONENT_ID=PATH",
        help="Certified component root; repeat for every component certified by the full suite",
    )
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = args.output.resolve()
    modern_suite_lock_source = args.modern_suite_lock.resolve()
    full_suite_lock_source = args.full_suite_lock.resolve()

    registry = load_registry(root / "corpora" / "registry.yaml")
    profiles = load_profiles(root / "corpora" / "profiles.yaml", registry=registry)
    profile = profiles.profiles.get("modern-hebrew-print-v1")
    if profile is None:
        parser.error("canonical profile modern-hebrew-print-v1 is missing")
    registry_payload = json.loads(
        (root / "corpora" / "registry.lock.json").read_text(encoding="utf-8")
    )
    expected_registry_payload = registry_lock_payload(registry, benchmark_version=VERSION)
    if registry_payload != expected_registry_payload:
        parser.error("registry.lock.json does not match the authoritative registry.yaml")
    profiles_payload = json.loads(
        (root / "corpora" / "profiles.lock.json").read_text(encoding="utf-8")
    )
    expected_profiles_payload = profile_lock_payload(
        profiles, registry_fingerprint=registry.fingerprint
    )
    if profiles_payload != expected_profiles_payload:
        parser.error("profiles.lock.json does not match the authoritative profiles.yaml")
    track_report = verify_track_lock(root / "tracks")
    if not track_report.valid:
        parser.error("official track lock is invalid: " + "; ".join(track_report.issues))
    suite = load_modern_suite_lock(modern_suite_lock_source)
    validate_modern_suite_contract(
        suite,
        expected_benchmark_version=VERSION,
        expected_registry_fingerprint=registry.fingerprint,
        expected_profile_id=profile.profile_id,
        expected_profile_fingerprint=profile_fingerprint(profile),
        allowed_track_ids={spec.track_id for spec in list_official_tracks()},
    )
    full_suite = load_full_suite_lock(full_suite_lock_source)
    validate_full_suite_contract(
        full_suite,
        expected_benchmark_version=VERSION,
        expected_registry_fingerprint=registry.fingerprint,
        expected_profiles_fingerprint=profiles.fingerprint,
    )
    try:
        component_roots = parse_component_roots(args.component_root)
        component_proof_payload = verify_release_suite_roots(
            suite,
            full_suite,
            component_roots,
        )
    except ReleaseIntegrityError as exc:
        parser.error(str(exc))

    if root.is_relative_to(output):
        parser.error("release output cannot be the repository root or one of its ancestors")
    for label, source in (
        ("Modern suite lock", modern_suite_lock_source),
        ("full-suite lock", full_suite_lock_source),
    ):
        if source.is_relative_to(output):
            parser.error(f"{label} cannot be stored inside the release output directory")
    overlapping_roots = sorted(
        component_id
        for component_id, component_root in component_roots.items()
        if component_root.is_relative_to(output) or output.is_relative_to(component_root)
    )
    if overlapping_roots:
        parser.error(
            "release output must not overlap certified component roots: "
            + ", ".join(overlapping_roots)
        )

    if output.exists():
        if not output.is_dir():
            parser.error("release output exists and is not a directory")
        if args.clean:
            shutil.rmtree(output)
        elif any(output.iterdir()):
            parser.error("release output directory is not empty; pass --clean explicitly")
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
    modern_suite_lock = output / f"HebOCRBench-v{VERSION}-modern-suite.lock.json"
    full_suite_lock = output / f"HebOCRBench-v{VERSION}-full-suite.lock.json"
    component_proof = output / f"HebOCRBench-v{VERSION}-component-proof.json"
    shutil.copyfile(root / "corpora" / "registry.lock.json", registry_lock)
    shutil.copyfile(root / "corpora" / "registry.yaml", registry_yaml)
    shutil.copyfile(root / "corpora" / "profiles.lock.json", profiles_lock)
    shutil.copyfile(root / "corpora" / "profiles.yaml", profiles_yaml)
    shutil.copyfile(root / "tracks" / "tracks.lock.json", tracks_lock)
    shutil.copyfile(modern_suite_lock_source, modern_suite_lock)
    shutil.copyfile(full_suite_lock_source, full_suite_lock)
    component_proof.write_text(
        json.dumps(component_proof_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = output / f"HebOCRBench-v{VERSION}-release-manifest.json"
    profile_ids = sorted(profiles.profiles)
    non_headline_profiles = [
        profile_id for profile_id in profile_ids if profile_id != profile.profile_id
    ]
    payload = {
        "schema_version": "1.0",
        "benchmark": "HebOCRBench",
        "version": VERSION,
        "release_date": "2026-07-26",
        "scope": "multi-profile-hebrew-suite",
        "registry_fingerprint": registry_payload["registry_fingerprint"],
        "profiles_fingerprint": profiles_payload["profiles_fingerprint"],
        "modern_suite_fingerprint": suite.suite_fingerprint,
        "full_suite_fingerprint": full_suite.suite_fingerprint,
        "component_proof_fingerprint": component_proof_payload["proof_fingerprint"],
        "profiles": profile_ids,
        "official_profiles": profile_ids,
        "headline_profile": profile.profile_id,
        "headline_profile_fingerprint": profile_fingerprint(profile),
        "headline_score_policy": profile.score_policy,
        "headline_tracks": list(DEFAULT_HEADLINE_TRACKS),
        "non_headline_profiles": non_headline_profiles,
        "separate_profile_reporting": True,
        "extensions_blended_into_headline": False,
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
            modern_suite_lock.name,
            full_suite_lock.name,
            component_proof.name,
        ],
        "checksum_manifest": f"HebOCRBench-v{VERSION}-SHA256SUMS.txt",
        "certified_components": component_proof_payload["certified_components"],
        "missing_components": component_proof_payload["missing_components"],
        "declared_component_coverage": dict(full_suite.coverage),
        "all_certified_component_roots_verified": True,
        "data_distribution": "federated",
        "third_party_corpora_bundled": False,
        "note": (
            "The release ships a multi-profile Hebrew evaluator suite. Its guarded public "
            "headline is exclusively the certified five-track Modern Hebrew print profile; "
            "development, handwriting and historical profiles are reported separately and "
            "are never blended into that headline. Corpus bytes remain federated or are "
            "published as separately checksummed assets according to their source terms."
        ),
    }
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
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
            modern_suite_lock,
            full_suite_lock,
            component_proof,
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
                "modern_suite_lock": str(modern_suite_lock),
                "full_suite_lock": str(full_suite_lock),
                "component_proof": str(component_proof),
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
