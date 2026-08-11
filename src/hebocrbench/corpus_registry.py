"""Federated corpus registry with deterministic fingerprints and license gates."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml


class RegistryError(ValueError):
    """Invalid registry metadata or selection."""


@dataclass(frozen=True, slots=True)
class ArtifactChecksum:
    algorithm: str
    value: str


@dataclass(frozen=True, slots=True)
class CorpusArtifact:
    artifact_id: str
    url: str
    filename: str
    archive: str | None
    checksum: ArtifactChecksum | None
    size_bytes: int | None = None
    revision: str | None = None
    required: bool = True
    mirrors: tuple[str, ...] = ()
    ignored_archive_members: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CorpusLicense:
    spdx: str
    tier: str
    redistribution: str
    requires_acceptance: bool
    uri: str | None = None
    authority: str | None = None
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CorpusSource:
    source_id: str
    title: str
    version: str
    task: str
    track: str
    languages: tuple[str, ...]
    script: str
    status: str
    converter: str
    homepage: str
    citation: Mapping[str, object]
    license: CorpusLicense
    artifacts: tuple[CorpusArtifact, ...]
    discovery: Mapping[str, object]
    split: Mapping[str, object]
    metadata: Mapping[str, object]

    @property
    def is_open(self) -> bool:
        return self.license.tier == "open"

    @property
    def acceptance_required(self) -> bool:
        return self.license.requires_acceptance


@dataclass(frozen=True, slots=True)
class CorpusRegistry:
    schema_version: str
    registry_version: str
    benchmark: str
    sources: Mapping[str, CorpusSource]
    fingerprint: str

    def select(
        self,
        *,
        tiers: set[str] | frozenset[str] | None = None,
        source_ids: set[str] | frozenset[str] | None = None,
    ) -> tuple[CorpusSource, ...]:
        if source_ids is not None:
            unknown = sorted(set(source_ids) - set(self.sources))
            if unknown:
                raise RegistryError("Unknown source selection: " + ", ".join(unknown))
        values = []
        for source_id in sorted(self.sources):
            source = self.sources[source_id]
            if source_ids is not None and source_id not in source_ids:
                continue
            if tiers is not None and source.license.tier not in tiers:
                continue
            values.append(source)
        return tuple(values)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_mapping(value: object, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RegistryError(f"{location} must be a mapping")
    return value


def _require_text(value: Mapping[str, Any], key: str, location: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise RegistryError(f"{location}.{key} must be a non-empty string")
    return item


def _parse_checksum(value: object, location: str) -> ArtifactChecksum | None:
    if value is None:
        return None
    mapping = _require_mapping(value, location)
    algorithm = _require_text(mapping, "algorithm", location).lower()
    digest = _require_text(mapping, "value", location).lower()
    expected_lengths = {"md5": 32, "sha256": 64, "sha512": 128}
    if algorithm not in expected_lengths:
        raise RegistryError(f"{location}.algorithm is unsupported: {algorithm}")
    if len(digest) != expected_lengths[algorithm] or any(
        ch not in "0123456789abcdef" for ch in digest
    ):
        raise RegistryError(f"{location}.value is not a valid {algorithm} checksum")
    return ArtifactChecksum(algorithm=algorithm, value=digest)


def _parse_artifacts(value: object, source_id: str, *, core: bool) -> tuple[CorpusArtifact, ...]:
    if not isinstance(value, list):
        raise RegistryError(f"sources.{source_id}.artifacts must be a list")
    artifacts: list[CorpusArtifact] = []
    for index, raw in enumerate(value):
        location = f"sources.{source_id}.artifacts[{index}]"
        item = _require_mapping(raw, location)
        url = _require_text(item, "url", location)
        checksum = _parse_checksum(item.get("checksum"), f"{location}.checksum")
        raw_ignored = item.get("ignored_archive_members", [])
        if not isinstance(raw_ignored, list):
            raise RegistryError(f"{location}.ignored_archive_members must be a list")
        ignored_archive_members = tuple(str(name) for name in raw_ignored)
        if any(not name for name in ignored_archive_members) or len(
            set(ignored_archive_members)
        ) != len(ignored_archive_members):
            raise RegistryError(
                f"{location}.ignored_archive_members must contain unique non-empty strings"
            )
        immutable_download = not url.startswith(("git+", "api+", "bundled:"))
        revision = str(item["revision"]) if item.get("revision") is not None else None
        if core and immutable_download and checksum is None:
            raise RegistryError(f"{location} requires a checksum for a core artifact")
        if core and url.startswith("git+") and not revision:
            raise RegistryError(
                f"{location} requires an immutable revision for a core Git artifact"
            )
        artifacts.append(
            CorpusArtifact(
                artifact_id=_require_text(item, "artifact_id", location),
                url=url,
                filename=_require_text(item, "filename", location),
                archive=str(item["archive"]) if item.get("archive") is not None else None,
                checksum=checksum,
                size_bytes=int(item["size_bytes"]) if item.get("size_bytes") is not None else None,
                revision=revision,
                required=bool(item.get("required", True)),
                mirrors=tuple(str(url) for url in item.get("mirrors", [])),
                ignored_archive_members=ignored_archive_members,
            )
        )
    return tuple(artifacts)


def _parse_license(value: object, source_id: str) -> CorpusLicense:
    location = f"sources.{source_id}.license"
    item = _require_mapping(value, location)
    spdx = _require_text(item, "spdx", location)
    tier = _require_text(item, "tier", location)
    redistribution = _require_text(item, "redistribution", location)
    if tier not in {"open", "research-nc", "external-review", "bundled"}:
        raise RegistryError(f"{location}.tier is unsupported: {tier}")
    upper = spdx.upper()
    if tier == "open" and ("-NC" in upper or "NONCOMMERCIAL" in upper):
        raise RegistryError(f"{source_id}: non-commercial license cannot be marked open")
    if tier == "open" and ("-ND" in upper or "NODERIV" in upper):
        raise RegistryError(f"{source_id}: no-derivatives license cannot be marked open")
    return CorpusLicense(
        spdx=spdx,
        tier=tier,
        redistribution=redistribution,
        requires_acceptance=bool(item.get("requires_acceptance", False)),
        uri=str(item["uri"]) if item.get("uri") is not None else None,
        authority=str(item["authority"]) if item.get("authority") is not None else None,
        conflicts=tuple(str(conflict) for conflict in item.get("conflicts", [])),
    )


def load_registry(path: str | Path | None = None) -> CorpusRegistry:
    if path is None:
        source_label = "package:data/corpus-registry.yaml"
        try:
            text = (
                resources.files("hebocrbench")
                .joinpath("data/corpus-registry.yaml")
                .read_text(encoding="utf-8")
            )
        except OSError as exc:
            raise RegistryError(f"Cannot load registry {source_label}: {exc}") from exc
    else:
        source_path = Path(path)
        source_label = str(source_path)
        try:
            text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RegistryError(f"Cannot load registry {source_label}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RegistryError(f"Cannot parse registry {source_label}: {exc}") from exc
    root = _require_mapping(raw, "registry")
    schema_version = _require_text(root, "schema_version", "registry")
    registry_version = _require_text(root, "registry_version", "registry")
    benchmark = _require_text(root, "benchmark", "registry")
    source_values = _require_mapping(root.get("sources"), "registry.sources")
    parsed: dict[str, CorpusSource] = {}
    for source_id in sorted(str(key) for key in source_values):
        item = _require_mapping(source_values[source_id], f"sources.{source_id}")
        status = _require_text(item, "status", f"sources.{source_id}")
        license_spec = _parse_license(item.get("license"), source_id)
        languages = item.get("languages")
        if not isinstance(languages, list) or not languages:
            raise RegistryError(f"sources.{source_id}.languages must be a non-empty list")
        parsed[source_id] = CorpusSource(
            source_id=source_id,
            title=_require_text(item, "title", f"sources.{source_id}"),
            version=_require_text(item, "version", f"sources.{source_id}"),
            task=_require_text(item, "task", f"sources.{source_id}"),
            track=_require_text(item, "track", f"sources.{source_id}"),
            languages=tuple(str(language) for language in languages),
            script=_require_text(item, "script", f"sources.{source_id}"),
            status=status,
            converter=_require_text(item, "converter", f"sources.{source_id}"),
            homepage=_require_text(item, "homepage", f"sources.{source_id}"),
            citation=dict(_require_mapping(item.get("citation"), f"sources.{source_id}.citation")),
            license=license_spec,
            artifacts=_parse_artifacts(item.get("artifacts", []), source_id, core=status == "core"),
            discovery=dict(
                _require_mapping(item.get("discovery"), f"sources.{source_id}.discovery")
            ),
            split=dict(_require_mapping(item.get("split"), f"sources.{source_id}.split")),
            metadata=dict(_require_mapping(item.get("metadata"), f"sources.{source_id}.metadata")),
        )
    canonical = {
        "schema_version": schema_version,
        "registry_version": registry_version,
        "benchmark": benchmark,
        "sources": root["sources"],
    }
    return CorpusRegistry(
        schema_version=schema_version,
        registry_version=registry_version,
        benchmark=benchmark,
        sources=parsed,
        fingerprint=_canonical_hash(canonical),
    )
