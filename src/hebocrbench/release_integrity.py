"""Fail-closed integrity binding for HebOCRBench release suite roots.

Release packaging must not merely copy suite locks.  It must prove that every
root marked certified in the unified suite still has the exact bytes recorded
by both the unified lock and, for the Modern headline, the Modern suite lock.
The resulting path-free proof can be shipped with the release artifacts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

from .full_suite import FullSuiteSpec, verify_full_suite_roots
from .modern_suite import (
    DEFAULT_HEADLINE_TRACKS,
    ModernSuiteSpec,
    build_modern_suite_lock,
)


class ReleaseIntegrityError(ValueError):
    """The release locks, roots, or component proof disagree."""


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_component_roots(values: Sequence[str]) -> dict[str, Path]:
    """Parse repeatable ``COMPONENT_ID=PATH`` arguments without silent overwrite."""

    roots: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ReleaseIntegrityError(
                f"component root must use COMPONENT_ID=PATH syntax: {value!r}"
            )
        component_id, raw_path = value.split("=", 1)
        component_id = component_id.strip()
        raw_path = raw_path.strip()
        if not component_id or not raw_path:
            raise ReleaseIntegrityError(
                f"component root must use non-empty COMPONENT_ID=PATH syntax: {value!r}"
            )
        if component_id in roots:
            raise ReleaseIntegrityError(f"duplicate component root: {component_id}")
        roots[component_id] = Path(raw_path).resolve()
    if not roots:
        raise ReleaseIntegrityError("at least one --component-root is required")
    return roots


def _proof_basis(
    modern_suite: ModernSuiteSpec,
    full_suite: FullSuiteSpec,
) -> dict[str, object]:
    certified = sorted(
        component_id
        for component_id, component in full_suite.components.items()
        if component.status == "certified"
    )
    missing = sorted(set(full_suite.components) - set(certified))
    return {
        "schema_version": "1.0",
        "benchmark": "HebOCRBench",
        "benchmark_version": modern_suite.benchmark_version,
        "proof_type": "certified-component-root-evidence",
        "registry_fingerprint": full_suite.registry_fingerprint,
        "profiles_fingerprint": full_suite.profiles_fingerprint,
        "modern_suite_fingerprint": modern_suite.suite_fingerprint,
        "full_suite_fingerprint": full_suite.suite_fingerprint,
        "modern_headline_tracks": list(DEFAULT_HEADLINE_TRACKS),
        "certified_components": certified,
        "missing_components": missing,
        "components": {
            component_id: dict(full_suite.components[component_id].evidence or {})
            for component_id in certified
        },
    }


def component_proof_payload(
    modern_suite: ModernSuiteSpec,
    full_suite: FullSuiteSpec,
) -> dict[str, object]:
    """Create the canonical, path-free proof payload for already verified roots."""

    payload = _proof_basis(modern_suite, full_suite)
    payload["proof_fingerprint"] = _canonical_hash(payload)
    return payload


def verify_release_suite_roots(
    modern_suite: ModernSuiteSpec,
    full_suite: FullSuiteSpec,
    component_roots: Mapping[str, str | Path],
) -> dict[str, object]:
    """Re-hash every certified root and bind both release locks to the same bytes."""

    roots = {
        str(component_id): Path(path).resolve() for component_id, path in component_roots.items()
    }
    certified = {
        component_id
        for component_id, component in full_suite.components.items()
        if component.status == "certified"
    }
    if set(roots) != certified:
        missing = sorted(certified - set(roots))
        extra = sorted(set(roots) - certified)
        raise ReleaseIntegrityError(
            f"component roots must exactly match certified full-suite components: "
            f"missing={missing}, extra={extra}"
        )

    expected_modern = set(DEFAULT_HEADLINE_TRACKS)
    if set(modern_suite.tracks) != expected_modern:
        raise ReleaseIntegrityError(
            "release Modern suite must contain exactly the five canonical headline tracks"
        )
    missing_modern = sorted(expected_modern - certified)
    if missing_modern:
        raise ReleaseIntegrityError(
            "full-suite lock is missing certified Modern headline components: "
            + ", ".join(missing_modern)
        )

    try:
        verify_full_suite_roots(full_suite, roots, require_all_certified=True)
        rebuilt_modern = build_modern_suite_lock(
            {track_id: roots[track_id] for track_id in DEFAULT_HEADLINE_TRACKS},
            profile_id=modern_suite.profile_id,
            profile_fingerprint=modern_suite.profile_fingerprint,
            registry_fingerprint=modern_suite.registry_fingerprint,
            benchmark_version=modern_suite.benchmark_version,
            suite_version=modern_suite.suite_version,
            maturity={
                track_id: modern_suite.tracks[track_id].maturity
                for track_id in DEFAULT_HEADLINE_TRACKS
            },
            headline_tracks=DEFAULT_HEADLINE_TRACKS,
        )
    except ValueError as exc:
        raise ReleaseIntegrityError(str(exc)) from exc
    if rebuilt_modern != modern_suite.to_dict():
        raise ReleaseIntegrityError(
            "Modern suite lock does not reconstruct from the supplied certified roots"
        )

    for track_id in DEFAULT_HEADLINE_TRACKS:
        modern_track = modern_suite.tracks[track_id]
        evidence = full_suite.components[track_id].evidence or {}
        expected = {
            "dataset_fingerprint": modern_track.dataset_fingerprint,
            "gold_sha256": modern_track.gold_sha256,
            "certification_sha256": modern_track.certification_sha256,
        }
        observed = {key: evidence.get(key) for key in expected}
        if observed != expected:
            raise ReleaseIntegrityError(
                f"Modern and full-suite locks bind different evidence for {track_id}"
            )

    return component_proof_payload(modern_suite, full_suite)


def validate_component_proof(
    value: object,
    modern_suite: ModernSuiteSpec,
    full_suite: FullSuiteSpec,
) -> dict[str, object]:
    """Require a shipped proof to equal the canonical evidence in both suite locks."""

    if not isinstance(value, Mapping):
        raise ReleaseIntegrityError("component proof must be a JSON object")
    observed = dict(value)
    expected = component_proof_payload(modern_suite, full_suite)
    if observed != expected:
        raise ReleaseIntegrityError("component proof differs from the canonical suite evidence")
    return observed


__all__ = [
    "ReleaseIntegrityError",
    "component_proof_payload",
    "parse_component_roots",
    "validate_component_proof",
    "verify_release_suite_roots",
]
