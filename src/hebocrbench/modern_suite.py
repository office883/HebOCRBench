"""Cryptographically bound release-suite contracts for Modern Hebrew scoring.

A track configuration proves *how* a task was scored.  A suite lock proves
*which frozen corpus bytes* were scored.  Official composite scores require
both.  This prevents mixing reports from different corpus revisions, profiles,
or test packs under one headline number.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .io import sha256_file

_ALLOWED_MATURITY = frozenset({"certified", "diagnostic", "experimental"})
DEFAULT_HEADLINE_TRACKS: tuple[str, ...] = (
    "modern-bidi-v1",
    "modern-line-recognition-v1",
    "modern-page-ocr-v1",
    "modern-tables-v1",
    "modern-robustness-v1",
)


class ModernSuiteError(ValueError):
    """A Modern Hebrew suite lock or evidence binding is invalid."""


@dataclass(frozen=True, slots=True)
class SuiteTrackSpec:
    track_id: str
    maturity: str
    headline: bool
    dataset_fingerprint: str
    gold_sha256: str
    certification_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "maturity": self.maturity,
            "headline": self.headline,
            "dataset_fingerprint": self.dataset_fingerprint,
            "gold_sha256": self.gold_sha256,
            "certification_sha256": self.certification_sha256,
        }


@dataclass(frozen=True, slots=True)
class ModernSuiteSpec:
    schema_version: str
    suite_version: str
    benchmark: str
    benchmark_version: str
    profile_id: str
    profile_fingerprint: str
    registry_fingerprint: str
    tracks: Mapping[str, SuiteTrackSpec]
    suite_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "suite_version": self.suite_version,
            "benchmark": self.benchmark,
            "benchmark_version": self.benchmark_version,
            "profile_id": self.profile_id,
            "profile_fingerprint": self.profile_fingerprint,
            "registry_fingerprint": self.registry_fingerprint,
            "tracks": {
                track_id: track.to_dict()
                for track_id, track in sorted(self.tracks.items())
            },
            "suite_fingerprint": self.suite_fingerprint,
        }


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModernSuiteError(f"{location} must be a mapping")
    return value


def _text(value: Mapping[str, Any], key: str, location: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ModernSuiteError(f"{location}.{key} must be a non-empty string")
    return result.strip()


def _fingerprint_basis(payload: Mapping[str, Any]) -> dict[str, object]:
    required = (
        "schema_version",
        "suite_version",
        "benchmark",
        "benchmark_version",
        "profile_id",
        "profile_fingerprint",
        "registry_fingerprint",
        "tracks",
    )
    return {key: payload.get(key) for key in required}


def with_suite_fingerprint(payload: Mapping[str, Any]) -> dict[str, object]:
    """Return a canonical suite-lock payload with its self-verifying digest."""

    result = dict(payload)
    result.pop("suite_fingerprint", None)
    result["suite_fingerprint"] = _canonical_hash(_fingerprint_basis(result))
    return result


def parse_modern_suite_lock(value: object) -> ModernSuiteSpec:
    root = _mapping(value, "modern_suite")
    observed_fingerprint = _text(root, "suite_fingerprint", "modern_suite")
    expected_fingerprint = _canonical_hash(_fingerprint_basis(root))
    if observed_fingerprint != expected_fingerprint:
        raise ModernSuiteError(
            "modern_suite.suite_fingerprint does not match the canonical suite contents"
        )

    raw_tracks = _mapping(root.get("tracks"), "modern_suite.tracks")
    if not raw_tracks:
        raise ModernSuiteError("modern_suite.tracks must not be empty")
    tracks: dict[str, SuiteTrackSpec] = {}
    for raw_track_id in sorted(str(key) for key in raw_tracks):
        location = f"modern_suite.tracks.{raw_track_id}"
        item = _mapping(raw_tracks[raw_track_id], location)
        maturity = _text(item, "maturity", location)
        if maturity not in _ALLOWED_MATURITY:
            raise ModernSuiteError(
                f"{location}.maturity must be one of {sorted(_ALLOWED_MATURITY)}"
            )
        headline = item.get("headline")
        if not isinstance(headline, bool):
            raise ModernSuiteError(f"{location}.headline must be boolean")
        tracks[raw_track_id] = SuiteTrackSpec(
            track_id=raw_track_id,
            maturity=maturity,
            headline=headline,
            dataset_fingerprint=_text(item, "dataset_fingerprint", location),
            gold_sha256=_text(item, "gold_sha256", location),
            certification_sha256=_text(item, "certification_sha256", location),
        )

    return ModernSuiteSpec(
        schema_version=_text(root, "schema_version", "modern_suite"),
        suite_version=_text(root, "suite_version", "modern_suite"),
        benchmark=_text(root, "benchmark", "modern_suite"),
        benchmark_version=_text(root, "benchmark_version", "modern_suite"),
        profile_id=_text(root, "profile_id", "modern_suite"),
        profile_fingerprint=_text(root, "profile_fingerprint", "modern_suite"),
        registry_fingerprint=_text(root, "registry_fingerprint", "modern_suite"),
        tracks=tracks,
        suite_fingerprint=observed_fingerprint,
    )




def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModernSuiteError(f"cannot read {label} at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ModernSuiteError(f"invalid JSON in {label} at {path}: {exc}") from exc
    return _mapping(value, label)


def build_modern_suite_lock(
    track_roots: Mapping[str, str | Path],
    *,
    profile_id: str,
    profile_fingerprint: str,
    registry_fingerprint: str,
    benchmark_version: str = "1.0.0",
    suite_version: str = "1.0.0",
    maturity: Mapping[str, str] | None = None,
    headline_tracks: tuple[str, ...] = DEFAULT_HEADLINE_TRACKS,
) -> dict[str, object]:
    """Build a suite lock exclusively from frozen, independently certified roots."""

    normalized_roots = {str(track_id): Path(root) for track_id, root in track_roots.items()}
    missing = sorted(set(headline_tracks) - set(normalized_roots))
    if missing:
        raise ModernSuiteError(
            "suite is missing required headline track roots: " + ", ".join(missing)
        )
    maturity_map = {str(key): str(value) for key, value in (maturity or {}).items()}
    track_payloads: dict[str, object] = {}
    for track_id in sorted(normalized_roots):
        root = normalized_roots[track_id]
        if not root.is_dir():
            raise ModernSuiteError(f"track root is not a directory: {track_id}={root}")
        manifest_path = root / "manifest.json"
        lock_path = root / "dataset.lock.json"
        frozen_path = root / "FROZEN.json"
        certified_path = root / "CERTIFIED.json"
        certification_path = root / "certification.json"
        gold_path = root / "gold.jsonl"
        manifest = _read_json_object(manifest_path, f"{track_id}.manifest")
        lock = _read_json_object(lock_path, f"{track_id}.dataset_lock")
        frozen = _read_json_object(frozen_path, f"{track_id}.frozen")
        certified = _read_json_object(certified_path, f"{track_id}.certified")
        if not gold_path.is_file():
            raise ModernSuiteError(f"{track_id}.gold.jsonl is missing")
        if certified.get("certified") is not True:
            raise ModernSuiteError(f"{track_id}.CERTIFIED.json is not certified")
        if manifest.get("benchmark_version") != benchmark_version:
            raise ModernSuiteError(
                f"{track_id}.manifest benchmark version differs from {benchmark_version}"
            )
        if certified.get("benchmark_version") != benchmark_version:
            raise ModernSuiteError(
                f"{track_id}.CERTIFIED.json benchmark version differs from {benchmark_version}"
            )
        if manifest.get("registry_fingerprint") != registry_fingerprint:
            raise ModernSuiteError(
                f"{track_id}.manifest registry_fingerprint differs from the suite registry"
            )
        if certified.get("registry_fingerprint") != registry_fingerprint:
            raise ModernSuiteError(
                f"{track_id}.CERTIFIED.json registry_fingerprint differs from the suite registry"
            )
        dataset_fingerprint = str(manifest.get("dataset_fingerprint") or "")
        if not dataset_fingerprint or any(
            value.get("dataset_fingerprint") != dataset_fingerprint
            for value in (lock, frozen, certified)
        ):
            raise ModernSuiteError(
                f"{track_id} dataset_fingerprint differs across manifest, lock, freeze and certification"
            )
        if frozen.get("manifest_sha256") != sha256_file(manifest_path):
            raise ModernSuiteError(f"{track_id}.FROZEN.json has a stale manifest_sha256")
        actual_certification_sha256 = sha256_file(certification_path)
        if certified.get("certification_sha256") != actual_certification_sha256:
            raise ModernSuiteError(
                f"{track_id}.CERTIFIED.json certification_sha256 is stale"
            )
        track_maturity = maturity_map.get(
            track_id, "certified" if track_id in headline_tracks else "diagnostic"
        )
        if track_maturity not in _ALLOWED_MATURITY:
            raise ModernSuiteError(
                f"{track_id} maturity must be one of {sorted(_ALLOWED_MATURITY)}"
            )
        is_headline = track_id in headline_tracks
        if is_headline and track_maturity != "certified":
            raise ModernSuiteError(f"headline track {track_id} must have certified maturity")
        track_payloads[track_id] = {
            "maturity": track_maturity,
            "headline": is_headline,
            "dataset_fingerprint": dataset_fingerprint,
            "gold_sha256": sha256_file(gold_path),
            "certification_sha256": actual_certification_sha256,
        }

    return with_suite_fingerprint(
        {
            "schema_version": "1.0",
            "suite_version": suite_version,
            "benchmark": "HebOCRBench Modern Hebrew",
            "benchmark_version": benchmark_version,
            "profile_id": profile_id,
            "profile_fingerprint": profile_fingerprint,
            "registry_fingerprint": registry_fingerprint,
            "tracks": track_payloads,
        }
    )


def load_modern_suite_lock(path: str | Path) -> ModernSuiteSpec:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModernSuiteError(f"cannot read Modern Hebrew suite lock {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ModernSuiteError(f"invalid JSON in Modern Hebrew suite lock {source}: {exc}") from exc
    return parse_modern_suite_lock(value)


def coerce_modern_suite_lock(value: ModernSuiteSpec | Mapping[str, Any]) -> ModernSuiteSpec:
    if isinstance(value, ModernSuiteSpec):
        return value
    return parse_modern_suite_lock(value)


def validate_modern_suite_contract(
    suite: ModernSuiteSpec,
    *,
    expected_benchmark_version: str,
    expected_registry_fingerprint: str,
    expected_profile_id: str,
    expected_profile_fingerprint: str,
    allowed_track_ids: set[str] | frozenset[str] | None = None,
    required_headline_tracks: tuple[str, ...] = DEFAULT_HEADLINE_TRACKS,
) -> None:
    """Verify a parsed suite against the canonical release metadata."""

    if suite.benchmark != "HebOCRBench Modern Hebrew":
        raise ModernSuiteError("suite benchmark identity is not HebOCRBench Modern Hebrew")
    if suite.benchmark_version != expected_benchmark_version:
        raise ModernSuiteError(
            "suite benchmark_version differs from the release benchmark version"
        )
    if suite.registry_fingerprint != expected_registry_fingerprint:
        raise ModernSuiteError(
            "suite registry_fingerprint differs from the canonical registry"
        )
    if suite.profile_id != expected_profile_id:
        raise ModernSuiteError("suite profile_id differs from the requested profile")
    if suite.profile_fingerprint != expected_profile_fingerprint:
        raise ModernSuiteError(
            "suite profile_fingerprint differs from the canonical profile contract"
        )
    if allowed_track_ids is not None:
        unknown = sorted(set(suite.tracks) - set(allowed_track_ids))
        if unknown:
            raise ModernSuiteError(
                "suite contains non-official track IDs: " + ", ".join(unknown)
            )
    headline = {track_id for track_id, track in suite.tracks.items() if track.headline}
    expected_headline = set(required_headline_tracks)
    if headline != expected_headline:
        missing = sorted(expected_headline - headline)
        extra = sorted(headline - expected_headline)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise ModernSuiteError(
            "suite headline track membership differs (" + "; ".join(details) + ")"
        )
    for track_id in required_headline_tracks:
        track = suite.tracks[track_id]
        if track.maturity != "certified":
            raise ModernSuiteError(f"headline track {track_id} is not certified")


def suite_evidence_for_track(
    suite: ModernSuiteSpec,
    track_id: str,
    gold_path: str | Path,
) -> dict[str, object]:
    track = suite.tracks.get(track_id)
    if track is None:
        raise ModernSuiteError(f"track {track_id!r} is not present in suite {suite.suite_fingerprint}")
    source = Path(gold_path)
    if not source.is_file():
        raise ModernSuiteError(f"gold file is missing: {source}")
    observed_gold_sha256 = sha256_file(source)
    if observed_gold_sha256 != track.gold_sha256:
        raise ModernSuiteError(
            f"gold SHA-256 for {track_id} differs from the suite lock: "
            f"expected {track.gold_sha256}, got {observed_gold_sha256}"
        )
    return {
        "suite_version": suite.suite_version,
        "suite_fingerprint": suite.suite_fingerprint,
        "benchmark_version": suite.benchmark_version,
        "profile_id": suite.profile_id,
        "profile_fingerprint": suite.profile_fingerprint,
        "registry_fingerprint": suite.registry_fingerprint,
        "track_id": track_id,
        **track.to_dict(),
    }


__all__ = [
    "DEFAULT_HEADLINE_TRACKS",
    "ModernSuiteError",
    "ModernSuiteSpec",
    "SuiteTrackSpec",
    "build_modern_suite_lock",
    "coerce_modern_suite_lock",
    "load_modern_suite_lock",
    "parse_modern_suite_lock",
    "suite_evidence_for_track",
    "validate_modern_suite_contract",
    "with_suite_fingerprint",
]
