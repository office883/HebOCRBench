"""Deterministic machine-readable lock payloads for HebOCRBench releases."""

from __future__ import annotations

from .corpus_registry import CorpusRegistry
from .profiles import ProfileRegistry


def registry_lock_payload(
    registry: CorpusRegistry,
    *,
    benchmark_version: str,
) -> dict[str, object]:
    """Return the canonical source-registry lock represented by runtime types."""

    sources: dict[str, object] = {}
    for source_id, source in sorted(registry.sources.items()):
        artifacts: list[dict[str, object]] = []
        for artifact in source.artifacts:
            checksum = None
            if artifact.checksum is not None:
                checksum = {
                    "algorithm": artifact.checksum.algorithm,
                    "value": artifact.checksum.value,
                }
            artifacts.append(
                {
                    "archive": artifact.archive,
                    "artifact_id": artifact.artifact_id,
                    "checksum": checksum,
                    "filename": artifact.filename,
                    "required": artifact.required,
                    "revision": artifact.revision,
                    "size_bytes": artifact.size_bytes,
                    "url": artifact.url,
                    "mirrors": list(artifact.mirrors),
                }
            )
        sources[source_id] = {
            "artifacts": artifacts,
            "citation": dict(source.citation),
            "converter": source.converter,
            "discovery": dict(source.discovery),
            "homepage": source.homepage,
            "languages": list(source.languages),
            "license": {
                "authority": source.license.authority,
                "conflicts": list(source.license.conflicts),
                "redistribution": source.license.redistribution,
                "requires_acceptance": source.license.requires_acceptance,
                "spdx": source.license.spdx,
                "tier": source.license.tier,
                "uri": source.license.uri,
            },
            "metadata": dict(source.metadata),
            "script": source.script,
            "source_version": source.version,
            "split": dict(source.split),
            "status": source.status,
            "task": source.task,
            "title": source.title,
            "track": source.track,
        }
    return {
        "benchmark": registry.benchmark,
        "benchmark_version": benchmark_version,
        "registry_fingerprint": registry.fingerprint,
        "registry_version": registry.registry_version,
        "schema_version": registry.schema_version,
        "sources": sources,
    }


def profile_lock_payload(
    profiles: ProfileRegistry,
    *,
    registry_fingerprint: str,
) -> dict[str, object]:
    """Return the canonical lock for official source/license profiles."""

    return {
        "profiles": {
            profile_id: {
                "allowed_license_tiers": list(profile.allowed_license_tiers),
                "certification_class": profile.certification_class,
                "description": profile.description,
                "score_policy": profile.score_policy,
                "source_ids": list(profile.source_ids),
                "title": profile.title,
            }
            for profile_id, profile in sorted(profiles.profiles.items())
        },
        "profiles_fingerprint": profiles.fingerprint,
        "profiles_version": profiles.profiles_version,
        "registry_fingerprint": registry_fingerprint,
        "schema_version": profiles.schema_version,
    }
