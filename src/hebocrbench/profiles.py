"""Machine-readable, license-aware benchmark profiles for HebOCRBench 1.0."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .corpus_registry import CorpusRegistry


class ProfileError(ValueError):
    """Invalid profile metadata or profile/source selection."""


@dataclass(frozen=True, slots=True)
class CorpusProfile:
    profile_id: str
    title: str
    description: str
    source_ids: tuple[str, ...]
    allowed_license_tiers: tuple[str, ...]
    certification_class: str
    score_policy: str


@dataclass(frozen=True, slots=True)
class ProfileRegistry:
    schema_version: str
    profiles_version: str
    profiles: Mapping[str, CorpusProfile]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ProfileSelectionIssue:
    code: str
    message: str
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileSelectionReport:
    profile_id: str
    selected_source_ids: tuple[str, ...]
    issues: tuple[ProfileSelectionIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "selected_source_ids": list(self.selected_source_ids),
            "is_valid": self.is_valid,
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "source_id": issue.source_id,
                }
                for issue in self.issues
            ],
        }


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileError(f"{location} must be a mapping")
    return value


def _text(value: Mapping[str, Any], key: str, location: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ProfileError(f"{location}.{key} must be a non-empty string")
    return result.strip()


def _text_list(value: Mapping[str, Any], key: str, location: str) -> tuple[str, ...]:
    raw = value.get(key)
    if not isinstance(raw, list) or not raw:
        raise ProfileError(f"{location}.{key} must be a non-empty list")
    values = tuple(str(item).strip() for item in raw)
    if any(not item for item in values) or len(set(values)) != len(values):
        raise ProfileError(f"{location}.{key} must contain unique non-empty strings")
    return values


def load_profiles(
    path: str | Path | None = None,
    *,
    registry: CorpusRegistry,
) -> ProfileRegistry:
    """Load and validate the canonical official benchmark profiles."""

    if path is None:
        source_label = "package:data/corpus-profiles.yaml"
        try:
            text = (
                resources.files("hebocrbench")
                .joinpath("data/corpus-profiles.yaml")
                .read_text(encoding="utf-8")
            )
        except OSError as exc:
            raise ProfileError(f"Cannot load profiles {source_label}: {exc}") from exc
    else:
        source_path = Path(path)
        source_label = str(source_path)
        try:
            text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProfileError(f"Cannot load profiles {source_label}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileError(f"Cannot parse profiles {source_label}: {exc}") from exc
    root = _mapping(raw, "profiles")
    schema_version = _text(root, "schema_version", "profiles")
    profiles_version = _text(root, "profiles_version", "profiles")
    raw_profiles = _mapping(root.get("profiles"), "profiles.profiles")

    parsed: dict[str, CorpusProfile] = {}
    for profile_id in sorted(str(key) for key in raw_profiles):
        location = f"profiles.{profile_id}"
        item = _mapping(raw_profiles[profile_id], location)
        source_ids = _text_list(item, "source_ids", location)
        unknown = sorted(set(source_ids) - set(registry.sources))
        if unknown:
            raise ProfileError(
                f"{location} references unknown sources: " + ", ".join(unknown)
            )
        allowed = _text_list(item, "allowed_license_tiers", location)
        actual_tiers = {registry.sources[source_id].license.tier for source_id in source_ids}
        disallowed = sorted(actual_tiers - set(allowed))
        if disallowed:
            raise ProfileError(
                f"{location} includes license tiers outside its policy: " + ", ".join(disallowed)
            )
        parsed[profile_id] = CorpusProfile(
            profile_id=profile_id,
            title=_text(item, "title", location),
            description=_text(item, "description", location),
            source_ids=source_ids,
            allowed_license_tiers=allowed,
            certification_class=_text(item, "certification_class", location),
            score_policy=_text(item, "score_policy", location),
        )

    canonical = {
        "schema_version": schema_version,
        "profiles_version": profiles_version,
        "profiles": raw_profiles,
    }
    return ProfileRegistry(
        schema_version=schema_version,
        profiles_version=profiles_version,
        profiles=parsed,
        fingerprint=_canonical_hash(canonical),
    )


def profile_fingerprint(profile: CorpusProfile) -> str:
    """Return the identity of one exact official profile contract."""

    return _canonical_hash(
        {
            "profile_id": profile.profile_id,
            "title": profile.title,
            "description": profile.description,
            "source_ids": list(profile.source_ids),
            "allowed_license_tiers": list(profile.allowed_license_tiers),
            "certification_class": profile.certification_class,
            "score_policy": profile.score_policy,
        }
    )


def validate_profile_selection(
    profile: CorpusProfile,
    *,
    selected_source_ids: Sequence[str],
    registry: CorpusRegistry,
    accepted_source_ids: Sequence[str],
) -> ProfileSelectionReport:
    """Validate exact source membership, license tiers and explicit acceptance."""

    selected = tuple(sorted(dict.fromkeys(str(value) for value in selected_source_ids)))
    accepted = {str(value) for value in accepted_source_ids}
    issues: list[ProfileSelectionIssue] = []
    expected = set(profile.source_ids)
    actual = set(selected)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        parts: list[str] = []
        if missing:
            parts.append("missing=" + ",".join(missing))
        if extra:
            parts.append("extra=" + ",".join(extra))
        issues.append(
            ProfileSelectionIssue(
                "profile_source_mismatch",
                f"Profile {profile.profile_id} source selection differs ({'; '.join(parts)})",
            )
        )

    for source_id in selected:
        source = registry.sources.get(source_id)
        if source is None:
            issues.append(
                ProfileSelectionIssue(
                    "profile_unknown_source",
                    f"Profile selection contains unknown source {source_id}",
                    source_id,
                )
            )
            continue
        if source.license.tier not in profile.allowed_license_tiers:
            issues.append(
                ProfileSelectionIssue(
                    "profile_license_tier_violation",
                    (
                        f"Source {source_id} has tier {source.license.tier}, outside "
                        f"profile policy {profile.allowed_license_tiers}"
                    ),
                    source_id,
                )
            )
        if source.license.requires_acceptance and source_id not in accepted:
            issues.append(
                ProfileSelectionIssue(
                    "profile_acceptance_missing",
                    f"Source {source_id} requires explicit license acceptance",
                    source_id,
                )
            )

    return ProfileSelectionReport(profile.profile_id, selected, tuple(issues))
