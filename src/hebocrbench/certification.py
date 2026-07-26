"""Independent release-certification gates for HebOCRBench corpus builds."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from importlib import resources
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Mapping, Sequence

from jsonschema import Draft202012Validator

from .corpus_registry import CorpusRegistry, CorpusSource
from .corpus_stats import compute_corpus_stats
from .dataset_audit import audit_dataset
from .io import load_jsonl, sha256_file
from .profiles import ProfileRegistry, validate_profile_selection
from .validator import validate_gold_records


@dataclass(frozen=True, slots=True)
class CertificationIssue:
    severity: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass(slots=True)
class CertificationReport:
    build_root: Path
    expected_version: str
    dataset_fingerprint: str | None = None
    checks: dict[str, bool] = field(default_factory=dict)
    issues: list[CertificationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[CertificationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[CertificationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def certified(self) -> bool:
        return not self.errors and bool(self.checks) and all(self.checks.values())

    @property
    def is_certified(self) -> bool:
        """Readable alias retained as the public v1 API."""

        return self.certified

    def add(self, severity: str, code: str, message: str) -> None:
        issue = CertificationIssue(severity, code, message)
        if issue not in self.issues:
            self.issues.append(issue)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "certified": self.certified,
            "expected_version": self.expected_version,
            "dataset_fingerprint": self.dataset_fingerprint,
            "checks": dict(sorted(self.checks.items())),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _read_json(path: Path, report: CertificationReport, code: str) -> object | None:
    if not path.is_file():
        report.add("error", "required_file_missing", f"Required file is missing: {path.name}")
        report.add("error", f"missing_{code}", f"Required file is missing: {path.name}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.add("error", f"invalid_{code}", f"Cannot read {path.name}: {exc}")
        return None


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    return bool(value) and not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_manifest_schema(manifest: object, report: CertificationReport) -> bool:
    if not isinstance(manifest, Mapping):
        report.add("error", "manifest_not_object", "manifest.json must contain an object")
        return False
    try:
        schema = json.loads(
            resources.files("hebocrbench")
            .joinpath("schemas/corpus-manifest.schema.json")
            .read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        report.add("error", "manifest_schema_unavailable", f"Cannot load manifest schema: {exc}")
        return False
    errors = sorted(
        Draft202012Validator(schema).iter_errors(manifest),
        key=lambda item: tuple(str(part) for part in item.path),
    )
    for error in errors:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        report.add("error", "manifest_schema", f"{location}: {error.message}")
    return not errors


def _verify_inventory(root: Path, manifest: Mapping[str, object], report: CertificationReport) -> bool:
    files = manifest.get("files")
    if not isinstance(files, list):
        report.add("error", "manifest_inventory_missing", "Manifest file inventory is missing")
        return False
    valid = True
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, Mapping):
            report.add("error", "manifest_inventory_entry", "Manifest inventory entry is not an object")
            valid = False
            continue
        relative = str(item.get("path", ""))
        if relative in seen:
            report.add("error", "manifest_duplicate_path", f"Duplicate manifest path: {relative}")
            valid = False
            continue
        seen.add(relative)
        if not _safe_relative_path(relative):
            report.add("error", "manifest_unsafe_path", f"Unsafe manifest path: {relative!r}")
            valid = False
            continue
        path = root / PurePosixPath(relative)
        if not path.is_file():
            report.add("error", "manifest_missing_file", f"Manifest file is missing: {relative}")
            report.add("error", "required_file_missing", f"Required file is missing: {relative}")
            valid = False
            continue
        expected_size = item.get("size_bytes")
        if not isinstance(expected_size, int) or path.stat().st_size != expected_size:
            report.add(
                "error",
                "manifest_size_mismatch",
                f"Manifest size mismatch for {relative}: expected {expected_size}, got {path.stat().st_size}",
            )
            report.add("error", "file_size_mismatch", f"File size mismatch: {relative}")
            valid = False
        expected_hash = str(item.get("sha256", ""))
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            report.add(
                "error",
                "manifest_hash_mismatch",
                f"Manifest hash mismatch for {relative}: expected {expected_hash}, got {actual_hash}",
            )
            report.add("error", "file_hash_mismatch", f"File hash mismatch: {relative}")
            valid = False
    return valid


def _required_files(root: Path, source_ids: Sequence[str], report: CertificationReport) -> bool:
    required = [
        "manifest.json",
        "dataset.lock.json",
        "FROZEN.json",
        "gold.jsonl",
        "stats.json",
        "audit.json",
        "attribution.jsonl",
        "citations.bib",
    ]
    required.extend(f"licenses/{source_id}.txt" for source_id in source_ids)
    required.extend(f"source_reports/{source_id}.json" for source_id in source_ids)
    missing = [relative for relative in required if not (root / relative).is_file()]
    for relative in missing:
        report.add("error", "required_file_missing", f"Required release file is missing: {relative}")
    return not missing


def _artifact_is_locked(source: CorpusSource) -> tuple[bool, str]:
    required = [artifact for artifact in source.artifacts if artifact.required]
    if not required:
        return False, "core source has no required artifact lock"
    for artifact in required:
        if artifact.url.startswith("git+"):
            if not artifact.revision:
                return False, f"Git artifact {artifact.artifact_id} has no revision"
        elif artifact.url.startswith(("api+", "bundled:")):
            if not artifact.revision and artifact.checksum is None:
                return False, f"artifact {artifact.artifact_id} has no immutable identity"
        elif artifact.checksum is None:
            return False, f"artifact {artifact.artifact_id} has no checksum"
    return True, ""


def _verify_sources(
    manifest: Mapping[str, object],
    registry: CorpusRegistry,
    report: CertificationReport,
) -> tuple[list[CorpusSource], bool]:
    raw_ids = manifest.get("source_ids", [])
    source_ids = [str(item) for item in raw_ids] if isinstance(raw_ids, list) else []
    unknown = sorted(set(source_ids) - set(registry.sources))
    if unknown:
        report.add("error", "unknown_manifest_source", "Unknown manifest sources: " + ", ".join(unknown))
    if not source_ids:
        report.add("error", "empty_manifest_sources", "Manifest contains no source IDs")
    selected = [registry.sources[source_id] for source_id in source_ids if source_id in registry.sources]

    verification = manifest.get("source_verification", {})
    verification_map = verification if isinstance(verification, Mapping) else {}
    accepted_raw = manifest.get("accepted_source_ids", [])
    accepted = {str(item) for item in accepted_raw} if isinstance(accepted_raw, list) else set()
    valid = bool(source_ids) and not unknown
    for source in selected:
        if source.license.requires_acceptance and source.source_id not in accepted:
            report.add(
                "error",
                "license_acceptance_missing",
                f"License acceptance is not recorded for {source.source_id}",
            )
            valid = False
        if source.status != "core":
            continue
        locked, reason = _artifact_is_locked(source)
        evidence = verification_map.get(source.source_id)
        status = evidence.get("verification_status") if isinstance(evidence, Mapping) else None
        if not locked or status != "verified_acquisition":
            detail = reason or f"acquisition status is {status!r}"
            report.add(
                "error",
                "core_source_unverified",
                f"Core source {source.source_id} is not independently verified: {detail}",
            )
            valid = False
    return selected, valid


def _verify_profile(
    manifest: Mapping[str, object],
    selected: Sequence[CorpusSource],
    registry: CorpusRegistry,
    report: CertificationReport,
    profiles: ProfileRegistry | None,
) -> bool:
    expected_tiers = sorted({source.license.tier for source in selected})
    raw_tiers = manifest.get("license_tiers", [])
    declared_tiers = sorted(str(value) for value in raw_tiers) if isinstance(raw_tiers, list) else []
    valid = declared_tiers == expected_tiers
    if not valid:
        report.add("error", "license_tier_mismatch", "Manifest license tiers do not match registry sources")

    profile_id = str(manifest.get("profile", ""))
    profile_key = profile_id.lower()
    accepted_raw = manifest.get("accepted_source_ids", [])
    accepted = [str(value) for value in accepted_raw] if isinstance(accepted_raw, list) else []
    if profiles is not None and profile_id in profiles.profiles:
        selection = validate_profile_selection(
            profiles.profiles[profile_id],
            selected_source_ids=[source.source_id for source in selected],
            registry=registry,
            accepted_source_ids=accepted,
        )
        for issue in selection.issues:
            report.add("error", issue.code, issue.message)
        if selection.issues:
            valid = False
    elif any(token in profile_key for token in ("open", "permissive")):
        restricted = [source.source_id for source in selected if source.license.tier not in {"open", "bundled"}]
        if restricted:
            message = "Open profile includes restricted sources: " + ", ".join(restricted)
            report.add("error", "profile_license_violation", message)
            report.add("error", "open_profile_restricted_license", message)
            valid = False
    external = [source.source_id for source in selected if source.license.tier == "external-review"]
    if external:
        report.add(
            "error",
            "external_review_source",
            "External-review sources cannot be release-certified: " + ", ".join(external),
        )
        valid = False
    return valid


def _recompute_fingerprint(lock: Mapping[str, object]) -> str | None:
    keys = (
        "benchmark",
        "benchmark_version",
        "schema_version",
        "registry_version",
        "registry_fingerprint",
        "profile",
        "source_ids",
        "accepted_source_ids",
        "source_verification",
        "records_sha256",
        "stats_sha256",
        "image_files",
    )
    if any(key not in lock for key in keys):
        return None
    return _canonical_hash({key: lock[key] for key in keys})


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def certify_release(
    build_root: str | Path,
    registry: CorpusRegistry,
    expected_version: str = "1.0.0",
    *,
    profiles: ProfileRegistry | None = None,
) -> CertificationReport:
    """Re-run every release gate and emit a marker only after all checks pass."""

    root = Path(build_root)
    report = CertificationReport(root, expected_version)
    for marker in ("CERTIFIED", "CERTIFIED.json", "certification.json"):
        (root / marker).unlink(missing_ok=True)

    manifest_value = _read_json(root / "manifest.json", report, "manifest")
    lock_value = _read_json(root / "dataset.lock.json", report, "dataset_lock")
    frozen_value = _read_json(root / "FROZEN.json", report, "frozen_marker")
    saved_audit_value = _read_json(root / "audit.json", report, "audit")
    manifest_valid = _validate_manifest_schema(manifest_value, report)
    report.checks["manifest_schema"] = manifest_valid

    manifest = manifest_value if isinstance(manifest_value, Mapping) else {}
    lock = lock_value if isinstance(lock_value, Mapping) else {}
    frozen = frozen_value if isinstance(frozen_value, Mapping) else {}
    report.dataset_fingerprint = str(manifest.get("dataset_fingerprint")) if manifest.get("dataset_fingerprint") else None

    source_ids_raw = manifest.get("source_ids", [])
    source_ids = [str(value) for value in source_ids_raw] if isinstance(source_ids_raw, list) else []
    report.checks["required_files"] = _required_files(root, source_ids, report)

    version_ok = manifest.get("benchmark_version") == expected_version
    if not version_ok:
        report.add(
            "error",
            "benchmark_version_mismatch",
            f"Expected benchmark version {expected_version}, got {manifest.get('benchmark_version')!r}",
        )
    report.checks["benchmark_version"] = version_ok

    registry_ok = manifest.get("registry_fingerprint") == registry.fingerprint
    if not registry_ok:
        report.add("error", "registry_fingerprint_mismatch", "Build registry fingerprint differs from supplied registry")
    report.checks["registry_fingerprint"] = registry_ok

    recomputed = _recompute_fingerprint(lock)
    fingerprint_ok = (
        bool(report.dataset_fingerprint)
        and lock.get("dataset_fingerprint") == report.dataset_fingerprint
        and frozen.get("dataset_fingerprint") == report.dataset_fingerprint
        and recomputed == report.dataset_fingerprint
    )
    if not fingerprint_ok:
        report.add("error", "dataset_fingerprint_mismatch", "Manifest, lock, freeze and recomputed fingerprint disagree")
    report.checks["dataset_fingerprint"] = fingerprint_ok

    frozen_manifest_ok = bool(frozen) and frozen.get("manifest_sha256") == (
        sha256_file(root / "manifest.json") if (root / "manifest.json").is_file() else None
    )
    if not frozen_manifest_ok:
        report.add("error", "stale_freeze_marker", "FROZEN.json does not bind the current manifest")
    report.checks["freeze_marker"] = frozen_manifest_ok

    inventory_ok = manifest_valid and _verify_inventory(root, manifest, report)
    report.checks["manifest_inventory"] = inventory_ok

    selected, sources_ok = _verify_sources(manifest, registry, report)
    report.checks["source_provenance"] = sources_ok
    report.checks["license_profile"] = _verify_profile(
        manifest, selected, registry, report, profiles
    )

    gold_ok = False
    audit_ok = False
    stats_ok = False
    gold_path = root / "gold.jsonl"
    if gold_path.is_file():
        try:
            records = load_jsonl(gold_path)
            validation = validate_gold_records(records, dataset_root=root)
            gold_ok = validation.is_valid and len(records) == manifest.get("page_count")
            for issue in validation.errors[:50]:
                report.add("error", f"gold_{issue.code}", issue.message)
            if len(records) != manifest.get("page_count"):
                report.add("error", "page_count_mismatch", "Gold page count differs from manifest")

            current_audit = audit_dataset(records, root)
            audit_ok = current_audit.is_valid
            for issue in current_audit.errors[:50]:
                report.add("error", issue.code, issue.message)
            if isinstance(saved_audit_value, Mapping) and bool(saved_audit_value.get("is_valid")) != current_audit.is_valid:
                report.add("error", "stale_audit", "Saved audit status differs from current audit")
                audit_ok = False

            stats_path = root / "stats.json"
            if stats_path.is_file():
                try:
                    saved_stats = json.loads(stats_path.read_text(encoding="utf-8"))
                    stats_ok = saved_stats == compute_corpus_stats(records)
                except (OSError, json.JSONDecodeError):
                    stats_ok = False
            if not stats_ok:
                report.add("error", "stale_statistics", "stats.json does not equal recomputed corpus statistics")
        except (OSError, ValueError) as exc:
            report.add("error", "gold_unreadable", f"Cannot validate gold data: {exc}")
    else:
        report.add("error", "required_file_missing", "gold.jsonl is missing")
    report.checks["gold_validation"] = gold_ok
    report.checks["leakage_audit"] = audit_ok
    report.checks["statistics_recomputed"] = stats_ok

    _atomic_json(root / "certification.json", report.to_dict())
    if report.certified:
        marker = {
            "schema_version": "1.0",
            "certified": True,
            "benchmark_version": expected_version,
            "dataset_fingerprint": report.dataset_fingerprint,
            "registry_fingerprint": registry.fingerprint,
            "certification_sha256": sha256_file(root / "certification.json"),
        }
        _atomic_json(root / "CERTIFIED.json", marker)
        _atomic_json(root / "CERTIFIED", marker)
    return report
