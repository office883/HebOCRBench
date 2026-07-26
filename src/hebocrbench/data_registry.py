"""Validated source registry and license-aware benchmark profiles."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
import yaml


class LicensePolicyError(ValueError):
    """A source is incompatible with the selected profile."""


class LicenseAcceptanceRequired(PermissionError):
    """Raised when restricted source terms have not been accepted explicitly."""

    def __init__(self, source_ids: list[str]):
        self.source_ids = tuple(sorted(source_ids))
        super().__init__("License acceptance required for: " + ", ".join(self.source_ids))


@dataclass(frozen=True, slots=True)
class DownloadSpec:
    kind: str
    url: str
    filename: str | None = None
    revision: str | None = None
    md5: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class LicenseSpec:
    expression: str
    license_class: str
    authority: str
    requires_acceptance: bool
    attribution: str
    restrictions: tuple[str, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    name: str
    version: str
    task: str
    languages: tuple[str, ...]
    script: str
    role: str
    status: str
    format: str
    primary_score: bool
    landing_page: str
    downloads: tuple[DownloadSpec, ...]
    license: LicenseSpec
    expected: Mapping[str, object]
    split_policy: str | None = None
    importer: str | None = None
    notes: tuple[str, ...] = ()
    citations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceRegistry:
    schema_version: str
    sources: Mapping[str, SourceRecord]

    def get(self, source_id: str) -> SourceRecord:
        try:
            return self.sources[source_id]
        except KeyError as exc:
            raise KeyError(f"Unknown source_id: {source_id}") from exc


@dataclass(frozen=True, slots=True)
class DataProfile:
    profile_id: str
    description: str
    source_ids: tuple[str, ...]
    allowed_license_classes: tuple[str, ...]
    require_acceptance: bool
    primary_score_only: bool = False


def _package_text(relative: str) -> str:
    return resources.files("hebocrbench").joinpath(relative).read_text(encoding="utf-8")


def _load_yaml(path: str | Path | None, packaged_relative: str) -> Mapping[str, Any]:
    if path is None:
        text = _package_text(packaged_relative)
        source_name = f"package:{packaged_relative}"
    else:
        source = Path(path)
        text = source.read_text(encoding="utf-8")
        source_name = str(source)
    value = yaml.safe_load(text)
    if not isinstance(value, Mapping):
        raise ValueError(f"{source_name} must contain a YAML mapping")
    return value


def _load_schema(name: str) -> Mapping[str, Any]:
    value = json.loads(_package_text(f"schemas/{name}"))
    if not isinstance(value, Mapping):
        raise AssertionError(f"Packaged schema {name} is not an object")
    return value


def _validate(value: Mapping[str, Any], schema_name: str, label: str) -> None:
    errors = sorted(
        Draft202012Validator(_load_schema(schema_name)).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors[:5]
        )
        raise ValueError(f"{label} schema validation failed: {detail}")


def _download(value: Mapping[str, Any]) -> DownloadSpec:
    return DownloadSpec(
        kind=str(value["kind"]),
        url=str(value["url"]),
        filename=str(value["filename"]) if value.get("filename") is not None else None,
        revision=str(value["revision"]) if value.get("revision") is not None else None,
        md5=str(value["md5"]).lower() if value.get("md5") is not None else None,
        sha256=str(value["sha256"]).lower() if value.get("sha256") is not None else None,
        size_bytes=int(value["size_bytes"]) if value.get("size_bytes") is not None else None,
    )


def _license(value: Mapping[str, Any]) -> LicenseSpec:
    return LicenseSpec(
        expression=str(value["expression"]),
        license_class=str(value["class"]),
        authority=str(value["authority"]),
        requires_acceptance=bool(value["requires_acceptance"]),
        attribution=str(value["attribution"]),
        restrictions=tuple(str(item) for item in value.get("restrictions", [])),
        conflicts=tuple(str(item) for item in value.get("conflicts", [])),
    )


def load_source_registry(path: str | Path | None = None) -> SourceRegistry:
    raw = _load_yaml(path, "data/sources.yaml")
    _validate(raw, "source-registry.schema.json", "source registry")
    raw_sources = raw["sources"]
    assert isinstance(raw_sources, Mapping)
    sources: dict[str, SourceRecord] = {}
    for source_id, source_value in raw_sources.items():
        assert isinstance(source_value, Mapping)
        downloads = source_value.get("downloads", [])
        expected = source_value.get("expected", {})
        assert isinstance(downloads, list)
        assert isinstance(expected, Mapping)
        sources[str(source_id)] = SourceRecord(
            source_id=str(source_id),
            name=str(source_value["name"]),
            version=str(source_value["version"]),
            task=str(source_value["task"]),
            languages=tuple(str(item) for item in source_value["languages"]),
            script=str(source_value["script"]),
            role=str(source_value["role"]),
            status=str(source_value["status"]),
            format=str(source_value["format"]),
            primary_score=bool(source_value["primary_score"]),
            landing_page=str(source_value["landing_page"]),
            downloads=tuple(_download(item) for item in downloads if isinstance(item, Mapping)),
            license=_license(source_value["license"]),
            expected=dict(expected),
            split_policy=str(source_value["split_policy"])
            if source_value.get("split_policy") is not None
            else None,
            importer=str(source_value["importer"])
            if source_value.get("importer") is not None
            else None,
            notes=tuple(str(item) for item in source_value.get("notes", [])),
            citations=tuple(str(item) for item in source_value.get("citations", [])),
        )
    return SourceRegistry(schema_version=str(raw["schema_version"]), sources=sources)


def load_profiles(path: str | Path | None = None) -> dict[str, DataProfile]:
    raw = _load_yaml(path, "data/profiles.yaml")
    _validate(raw, "data-profiles.schema.json", "data profiles")
    raw_profiles = raw["profiles"]
    assert isinstance(raw_profiles, Mapping)
    return {
        str(profile_id): DataProfile(
            profile_id=str(profile_id),
            description=str(value["description"]),
            source_ids=tuple(str(item) for item in value["source_ids"]),
            allowed_license_classes=tuple(str(item) for item in value["allowed_license_classes"]),
            require_acceptance=bool(value["require_acceptance"]),
            primary_score_only=bool(value.get("primary_score_only", False)),
        )
        for profile_id, value in raw_profiles.items()
        if isinstance(value, Mapping)
    }


def resolve_profile(
    registry: SourceRegistry,
    profile: DataProfile,
    *,
    accepted_source_ids: set[str] | frozenset[str],
) -> tuple[SourceRecord, ...]:
    resolved: list[SourceRecord] = []
    missing_acceptance: list[str] = []
    allowed = set(profile.allowed_license_classes)
    for source_id in profile.source_ids:
        source = registry.get(source_id)
        if source.license.license_class not in allowed:
            raise LicensePolicyError(
                f"Source {source_id} has license class {source.license.license_class!r}, "
                f"outside profile {profile.profile_id!r} policy"
            )
        if source.license.requires_acceptance and source_id not in accepted_source_ids:
            missing_acceptance.append(source_id)
        if profile.primary_score_only and not source.primary_score:
            continue
        resolved.append(source)
    if missing_acceptance:
        raise LicenseAcceptanceRequired(missing_acceptance)
    return tuple(resolved)
