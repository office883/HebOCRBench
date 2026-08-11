"""Unified, non-blended HebOCRBench multi-profile suite locks.

The Modern Hebrew print headline is one guarded five-track score.  Handwriting,
historical handwriting, synthetic diagnostics, and future coverage targets are
different scientific claims and therefore different reporting units.  This
module records all of them in one tamper-evident manifest without inventing a
cross-family aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .io import sha256_file
from .modern_suite import DEFAULT_HEADLINE_TRACKS


class FullSuiteError(ValueError):
    """The unified suite contract, lock, or bound corpus evidence is invalid."""


@dataclass(frozen=True, slots=True)
class ComponentContract:
    component_id: str
    family_id: str
    profile_id: str | None
    evidence_class: str
    reporting_role: str
    missing_reason: str
    root_eligible: bool = True


@dataclass(frozen=True, slots=True)
class FullSuiteComponent:
    component_id: str
    family_id: str
    profile_id: str | None
    evidence_class: str
    reporting_role: str
    status: str
    missing_reason: str | None
    evidence: Mapping[str, object] | None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "family_id": self.family_id,
            "profile_id": self.profile_id,
            "evidence_class": self.evidence_class,
            "reporting_role": self.reporting_role,
            "status": self.status,
        }
        if self.status == "missing":
            result["missing_reason"] = self.missing_reason
        else:
            result["evidence"] = dict(self.evidence or {})
        return result


@dataclass(frozen=True, slots=True)
class FullSuiteSpec:
    schema_version: str
    suite_version: str
    benchmark: str
    benchmark_version: str
    manifest_type: str
    registry_fingerprint: str
    profiles_fingerprint: str
    reporting_policy: Mapping[str, object]
    families: Mapping[str, Mapping[str, object]]
    components: Mapping[str, FullSuiteComponent]
    coverage: Mapping[str, object]
    suite_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "suite_version": self.suite_version,
            "benchmark": self.benchmark,
            "benchmark_version": self.benchmark_version,
            "manifest_type": self.manifest_type,
            "registry_fingerprint": self.registry_fingerprint,
            "profiles_fingerprint": self.profiles_fingerprint,
            "reporting_policy": dict(self.reporting_policy),
            "families": {
                family_id: dict(family) for family_id, family in sorted(self.families.items())
            },
            "components": {
                component_id: component.to_dict()
                for component_id, component in sorted(self.components.items())
            },
            "coverage": dict(self.coverage),
            "suite_fingerprint": self.suite_fingerprint,
        }


MODERN_HEADLINE_COMPONENTS = tuple(DEFAULT_HEADLINE_TRACKS)
REAL_EXTENSION_COMPONENTS = (
    "modern-handwriting-v1",
    "historical-hebrew-press-mixed-v1",
    "historical-pinkas-handwriting-v1",
)
SYNTHETIC_DIAGNOSTIC_COMPONENTS = (
    "biblical-niqqud-synthetic-diagnostic-v1",
    "rashi-print-synthetic-diagnostic-v1",
)
EXPERIMENTAL_COMPONENTS = ("modern-forms-v1",)
REAL_COVERAGE_TARGETS = (
    "biblical-cantillation-real-v1",
    "rashi-pure-print-real-v1",
)


def _component_contracts() -> dict[str, ComponentContract]:
    contracts: list[ComponentContract] = []
    for component_id in MODERN_HEADLINE_COMPONENTS:
        evidence_class = (
            "synthetic-conformance"
            if component_id == "modern-bidi-v1"
            else "real-public-fixed-derived"
        )
        contracts.append(
            ComponentContract(
                component_id=component_id,
                family_id="modern-print-v1",
                profile_id="modern-hebrew-print-v1",
                evidence_class=evidence_class,
                reporting_role="modern-headline-component",
                missing_reason="certified Modern Hebrew print track root not supplied",
            )
        )
    contracts.extend(
        (
            ComponentContract(
                component_id="modern-forms-v1",
                family_id="modern-forms-v1",
                profile_id="modern-hebrew-print-v1",
                evidence_class="missing-real-gold",
                reporting_role="experimental-non-rankable",
                missing_reason=(
                    "modern selection discovery signals are not field-level gold; the audited "
                    "700-page corpus has zero form_fields"
                ),
                root_eligible=False,
            ),
            ComponentContract(
                component_id="modern-handwriting-v1",
                family_id="modern-handwriting-v1",
                profile_id="modern-hebrew-handwriting-v1",
                evidence_class="real-public-fixed",
                reporting_role="separate-extension",
                missing_reason="certified real Modern Hebrew handwriting root not supplied",
            ),
            ComponentContract(
                component_id="historical-hebrew-press-mixed-v1",
                family_id="historical-hebrew-press-mixed-v1",
                profile_id="historical-hebrew-press-mixed-v1",
                evidence_class="real-public-fixed-mixed-square-rashi-no-script-labels",
                reporting_role="separate-extension",
                missing_reason="certified real HaZefira PAGE/ALTO root not supplied",
            ),
            ComponentContract(
                component_id="historical-pinkas-handwriting-v1",
                family_id="historical-pinkas-handwriting-v1",
                profile_id="historical-pinkas-handwriting-v1",
                evidence_class="real-public-fixed-narrow-single-collection",
                reporting_role="separate-extension",
                missing_reason="certified real Pinkas handwriting root not supplied",
            ),
            ComponentContract(
                component_id="biblical-niqqud-synthetic-diagnostic-v1",
                family_id="biblical-niqqud-diagnostic-v1",
                profile_id="biblical-niqqud-synthetic-diagnostic-v1",
                evidence_class="synthetic-public-fixed-heldout-diagnostic",
                reporting_role="non-rankable-diagnostic",
                missing_reason="certified synthetic niqqud diagnostic root not supplied",
            ),
            ComponentContract(
                component_id="rashi-print-synthetic-diagnostic-v1",
                family_id="rashi-diagnostic-v1",
                profile_id="rashi-print-synthetic-diagnostic-v1",
                evidence_class="synthetic-public-fixed-heldout-diagnostic",
                reporting_role="non-rankable-diagnostic",
                missing_reason="certified synthetic Rashi diagnostic root not supplied",
            ),
            ComponentContract(
                component_id="biblical-cantillation-real-v1",
                family_id="biblical-cantillation-real-v1",
                profile_id=None,
                evidence_class="real-coverage-target",
                reporting_role="future-separate-real-track",
                missing_reason=(
                    "no certified real Biblical Hebrew corpus with cantillation is available"
                ),
            ),
            ComponentContract(
                component_id="rashi-pure-print-real-v1",
                family_id="rashi-pure-print-real-v1",
                profile_id=None,
                evidence_class="real-coverage-target",
                reporting_role="future-separate-real-track",
                missing_reason=(
                    "the certified mixed historical-press extension has no region- or "
                    "line-level Rashi labels; no certified pure-Rashi real-print corpus is available"
                ),
            ),
        )
    )
    return {contract.component_id: contract for contract in contracts}


COMPONENT_CONTRACTS = _component_contracts()

REPORTING_POLICY: dict[str, object] = {
    "cross_family_score": "forbidden",
    "modern_print_score": "guarded-five-track-within-family-only",
    "extensions": "report-each-extension-separately",
    "synthetic_diagnostics": "separate-non-rankable",
    "missing_coverage": "must-remain-explicit-and-unscored",
}

FAMILY_CONTRACTS: dict[str, dict[str, object]] = {
    "modern-print-v1": {
        "title": "Modern Hebrew print headline",
        "profile_id": "modern-hebrew-print-v1",
        "component_ids": list(MODERN_HEADLINE_COMPONENTS),
        "score_policy": "guarded-five-track-within-family-only",
    },
    "modern-handwriting-v1": {
        "title": "Modern Hebrew handwriting",
        "profile_id": "modern-hebrew-handwriting-v1",
        "component_ids": ["modern-handwriting-v1"],
        "score_policy": "separate-track-only",
    },
    "modern-forms-v1": {
        "title": "Modern Hebrew forms experimental coverage target",
        "profile_id": "modern-hebrew-print-v1",
        "component_ids": ["modern-forms-v1"],
        "score_policy": "experimental-unscored-until-real-field-gold",
    },
    "historical-pinkas-handwriting-v1": {
        "title": "Pinkas historical Hebrew handwriting",
        "profile_id": "historical-pinkas-handwriting-v1",
        "component_ids": ["historical-pinkas-handwriting-v1"],
        "score_policy": "separate-track-only-narrow-single-collection",
    },
    "historical-hebrew-press-mixed-v1": {
        "title": "HaZefira historical Hebrew press mixed square/Rashi",
        "profile_id": "historical-hebrew-press-mixed-v1",
        "component_ids": ["historical-hebrew-press-mixed-v1"],
        "score_policy": "separate-track-only-no-pure-rashi-claim",
    },
    "biblical-niqqud-diagnostic-v1": {
        "title": "Synthetic Biblical Hebrew niqqud diagnostic",
        "profile_id": "biblical-niqqud-synthetic-diagnostic-v1",
        "component_ids": ["biblical-niqqud-synthetic-diagnostic-v1"],
        "score_policy": "non-rankable-diagnostic-only",
    },
    "rashi-diagnostic-v1": {
        "title": "Synthetic Rashi diagnostic",
        "profile_id": "rashi-print-synthetic-diagnostic-v1",
        "component_ids": ["rashi-print-synthetic-diagnostic-v1"],
        "score_policy": "non-rankable-diagnostic-only",
    },
    "biblical-cantillation-real-v1": {
        "title": "Real Biblical Hebrew with cantillation coverage target",
        "profile_id": None,
        "component_ids": ["biblical-cantillation-real-v1"],
        "score_policy": "unscored-until-certified-real-root",
    },
    "rashi-pure-print-real-v1": {
        "title": "Real pure-Rashi print coverage target",
        "profile_id": None,
        "component_ids": ["rashi-pure-print-real-v1"],
        "score_policy": "unscored-until-certified-real-root",
    },
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
        raise FullSuiteError(f"{location} must be a mapping")
    return value


def _text(value: Mapping[str, Any], key: str, location: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise FullSuiteError(f"{location}.{key} must be a non-empty string")
    return result.strip()


def _digest(value: Mapping[str, Any], key: str, location: str) -> str:
    result = _text(value, key, location)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise FullSuiteError(f"{location}.{key} must be a lowercase SHA-256 digest")
    return result


def _fingerprint_basis(payload: Mapping[str, Any]) -> dict[str, object]:
    return {key: payload.get(key) for key in sorted(payload) if key != "suite_fingerprint"}


def with_full_suite_fingerprint(payload: Mapping[str, Any]) -> dict[str, object]:
    """Return a canonical unified-suite payload with its self-verifying digest."""

    result = dict(payload)
    result.pop("suite_fingerprint", None)
    result["suite_fingerprint"] = _canonical_hash(_fingerprint_basis(result))
    return result


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FullSuiteError(f"cannot read {label} at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise FullSuiteError(f"invalid JSON in {label} at {path}: {exc}") from exc
    return _mapping(value, label)


def _safe_inventory_path(root: Path, relative: object, component_id: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise FullSuiteError(f"{component_id}.manifest inventory path is invalid")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise FullSuiteError(f"{component_id}.manifest inventory path escapes the root: {relative}")
    result = root.joinpath(*pure.parts)
    try:
        result.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise FullSuiteError(
            f"{component_id}.manifest inventory path resolves outside the root: {relative}"
        ) from exc
    return result


def _root_evidence(
    component: ComponentContract,
    root_value: str | Path,
    *,
    benchmark_version: str,
    registry_fingerprint: str,
) -> dict[str, object]:
    root = Path(root_value)
    if not root.is_dir():
        raise FullSuiteError(f"component root is not a directory: {component.component_id}={root}")

    paths = {
        "manifest": root / "manifest.json",
        "dataset_lock": root / "dataset.lock.json",
        "frozen": root / "FROZEN.json",
        "certified": root / "CERTIFIED.json",
        "certification": root / "certification.json",
        "gold": root / "gold.jsonl",
        "stats": root / "stats.json",
    }
    manifest = _read_json_object(paths["manifest"], f"{component.component_id}.manifest")
    dataset_lock = _read_json_object(
        paths["dataset_lock"], f"{component.component_id}.dataset_lock"
    )
    frozen = _read_json_object(paths["frozen"], f"{component.component_id}.frozen")
    certified = _read_json_object(paths["certified"], f"{component.component_id}.certified")
    _read_json_object(paths["certification"], f"{component.component_id}.certification")
    for label in ("gold", "stats"):
        if not paths[label].is_file():
            raise FullSuiteError(f"{component.component_id}.{paths[label].name} is missing")

    inventory = manifest.get("files")
    if not isinstance(inventory, list) or not inventory:
        raise FullSuiteError(f"{component.component_id}.manifest has no file inventory")
    seen_inventory_paths: set[str] = set()
    for raw_entry in inventory:
        entry = _mapping(raw_entry, f"{component.component_id}.manifest.files[]")
        relative = entry.get("path")
        if isinstance(relative, str) and relative in seen_inventory_paths:
            raise FullSuiteError(
                f"{component.component_id}.manifest inventory path is duplicated: {relative}"
            )
        if isinstance(relative, str):
            seen_inventory_paths.add(relative)
        item_path = _safe_inventory_path(root, relative, component.component_id)
        if not item_path.is_file():
            raise FullSuiteError(f"{component.component_id}.manifest file is missing: {relative}")
        if entry.get("size_bytes") != item_path.stat().st_size:
            raise FullSuiteError(f"{component.component_id}.manifest size is stale: {relative}")
        if entry.get("sha256") != sha256_file(item_path):
            raise FullSuiteError(f"{component.component_id}.manifest hash is stale: {relative}")

    if manifest.get("benchmark") != "HebOCRBench":
        raise FullSuiteError(f"{component.component_id}.manifest benchmark identity is invalid")
    for label, value in (("manifest", manifest), ("CERTIFIED.json", certified)):
        if value.get("benchmark_version") != benchmark_version:
            raise FullSuiteError(
                f"{component.component_id}.{label} benchmark_version differs from the suite"
            )
        if value.get("registry_fingerprint") != registry_fingerprint:
            raise FullSuiteError(
                f"{component.component_id}.{label} registry_fingerprint differs from the suite"
            )
    if manifest.get("track_id") != component.component_id:
        raise FullSuiteError(
            f"{component.component_id}.manifest track_id does not bind this component"
        )
    observed_profile = manifest.get("profile")
    if not isinstance(observed_profile, str) or not observed_profile:
        raise FullSuiteError(f"{component.component_id}.manifest profile is missing")
    if component.profile_id is not None and observed_profile != component.profile_id:
        raise FullSuiteError(
            f"{component.component_id}.manifest profile differs from {component.profile_id}"
        )
    profile_scope = manifest.get("profile_scope")
    if profile_scope not in {"full", "track-component"}:
        raise FullSuiteError(
            f"{component.component_id}.manifest profile_scope must be full or track-component"
        )
    if (
        component.reporting_role == "modern-headline-component"
        and profile_scope != "track-component"
    ):
        raise FullSuiteError(
            f"{component.component_id}.manifest must be a Modern headline track-component"
        )
    if certified.get("certified") is not True:
        raise FullSuiteError(f"{component.component_id}.CERTIFIED.json is not certified")

    dataset_fingerprint = manifest.get("dataset_fingerprint")
    if not isinstance(dataset_fingerprint, str) or not dataset_fingerprint:
        raise FullSuiteError(f"{component.component_id}.dataset_fingerprint is missing")
    for label, value in (
        ("dataset.lock.json", dataset_lock),
        ("FROZEN.json", frozen),
        ("CERTIFIED.json", certified),
    ):
        if value.get("dataset_fingerprint") != dataset_fingerprint:
            raise FullSuiteError(f"{component.component_id} dataset_fingerprint differs in {label}")
    if dataset_lock.get("records_sha256") != sha256_file(paths["gold"]):
        raise FullSuiteError(f"{component.component_id}.dataset.lock records_sha256 is stale")
    if dataset_lock.get("stats_sha256") != sha256_file(paths["stats"]):
        raise FullSuiteError(f"{component.component_id}.dataset.lock stats_sha256 is stale")
    if frozen.get("manifest_sha256") != sha256_file(paths["manifest"]):
        raise FullSuiteError(f"{component.component_id}.FROZEN.json manifest_sha256 is stale")
    certification_sha256 = sha256_file(paths["certification"])
    if certified.get("certification_sha256") != certification_sha256:
        raise FullSuiteError(
            f"{component.component_id}.CERTIFIED.json certification_sha256 is stale"
        )

    page_count = manifest.get("page_count")
    source_ids = manifest.get("source_ids")
    if not isinstance(page_count, int) or page_count < 1:
        raise FullSuiteError(f"{component.component_id}.manifest page_count is invalid")
    if not isinstance(source_ids, list) or not source_ids:
        raise FullSuiteError(f"{component.component_id}.manifest source_ids is invalid")
    return {
        "root_format": "frozen-certified-corpus-v1",
        "track_id": component.component_id,
        "profile_id": observed_profile,
        "profile_scope": profile_scope,
        "registry_fingerprint": registry_fingerprint,
        "dataset_fingerprint": dataset_fingerprint,
        "page_count": page_count,
        "source_ids": sorted(str(source_id) for source_id in source_ids),
        "manifest_sha256": sha256_file(paths["manifest"]),
        "dataset_lock_sha256": sha256_file(paths["dataset_lock"]),
        "frozen_sha256": sha256_file(paths["frozen"]),
        "gold_sha256": sha256_file(paths["gold"]),
        "stats_sha256": sha256_file(paths["stats"]),
        "certification_sha256": certification_sha256,
        "certified_sha256": sha256_file(paths["certified"]),
    }


def _coverage_payload(component_statuses: Mapping[str, str]) -> dict[str, object]:
    modern_missing = [
        component_id
        for component_id in MODERN_HEADLINE_COMPONENTS
        if component_statuses[component_id] == "missing"
    ]
    extension_available = [
        component_id
        for component_id in REAL_EXTENSION_COMPONENTS
        if component_statuses[component_id] == "certified"
    ]
    diagnostic_available = [
        component_id
        for component_id in SYNTHETIC_DIAGNOSTIC_COMPONENTS
        if component_statuses[component_id] == "certified"
    ]
    unresolved_real = [
        component_id
        for component_id in REAL_COVERAGE_TARGETS
        if component_statuses[component_id] == "missing"
    ]
    return {
        "modern_headline_status": "complete" if not modern_missing else "incomplete",
        "modern_headline_missing_components": modern_missing,
        "real_public_fixed_extensions_available": extension_available,
        "real_public_fixed_extensions_missing": sorted(
            set(REAL_EXTENSION_COMPONENTS) - set(extension_available)
        ),
        "synthetic_diagnostics_available": diagnostic_available,
        "synthetic_diagnostics_missing": sorted(
            set(SYNTHETIC_DIAGNOSTIC_COMPONENTS) - set(diagnostic_available)
        ),
        "modern_forms_status": "missing-real-gold",
        "experimental_components": list(EXPERIMENTAL_COMPONENTS),
        "declared_real_target_coverage": "complete" if not unresolved_real else "incomplete",
        "unresolved_real_coverage_gaps": unresolved_real,
    }


def build_full_suite_lock(
    component_roots: Mapping[str, str | Path],
    *,
    registry_fingerprint: str,
    profiles_fingerprint: str,
    benchmark_version: str = "1.0.0",
    suite_version: str = "1.0.0",
) -> dict[str, object]:
    """Build the complete suite manifest, leaving unsupplied components explicit."""

    unknown = sorted(set(component_roots) - set(COMPONENT_CONTRACTS))
    if unknown:
        raise FullSuiteError("unknown full-suite components: " + ", ".join(unknown))
    ineligible = sorted(
        component_id
        for component_id in component_roots
        if not COMPONENT_CONTRACTS[component_id].root_eligible
    )
    if ineligible:
        raise FullSuiteError(
            "components cannot be certified until their real gold contract is implemented: "
            + ", ".join(ineligible)
        )
    components: dict[str, dict[str, object]] = {}
    statuses: dict[str, str] = {}
    for component_id, contract in sorted(COMPONENT_CONTRACTS.items()):
        base: dict[str, object] = {
            "family_id": contract.family_id,
            "profile_id": contract.profile_id,
            "evidence_class": contract.evidence_class,
            "reporting_role": contract.reporting_role,
        }
        if component_id in component_roots:
            base["status"] = "certified"
            base["evidence"] = _root_evidence(
                contract,
                component_roots[component_id],
                benchmark_version=benchmark_version,
                registry_fingerprint=registry_fingerprint,
            )
            statuses[component_id] = "certified"
        else:
            base["status"] = "missing"
            base["missing_reason"] = contract.missing_reason
            statuses[component_id] = "missing"
        components[component_id] = base

    return with_full_suite_fingerprint(
        {
            "schema_version": "1.0",
            "suite_version": suite_version,
            "benchmark": "HebOCRBench",
            "benchmark_version": benchmark_version,
            "manifest_type": "multi-profile-suite-lock",
            "registry_fingerprint": registry_fingerprint,
            "profiles_fingerprint": profiles_fingerprint,
            "reporting_policy": REPORTING_POLICY,
            "families": FAMILY_CONTRACTS,
            "components": components,
            "coverage": _coverage_payload(statuses),
        }
    )


def parse_full_suite_lock(value: object) -> FullSuiteSpec:
    """Parse a lock and enforce both its digest and the non-blending contract."""

    root = _mapping(value, "full_suite")
    expected_root_keys = {
        "schema_version",
        "suite_version",
        "benchmark",
        "benchmark_version",
        "manifest_type",
        "registry_fingerprint",
        "profiles_fingerprint",
        "reporting_policy",
        "families",
        "components",
        "coverage",
        "suite_fingerprint",
    }
    if set(root) != expected_root_keys:
        raise FullSuiteError("full_suite fields differ from the canonical lock schema")
    observed_fingerprint = _digest(root, "suite_fingerprint", "full_suite")
    expected_fingerprint = _canonical_hash(_fingerprint_basis(root))
    if observed_fingerprint != expected_fingerprint:
        raise FullSuiteError(
            "full_suite.suite_fingerprint does not match the canonical suite contents"
        )
    if root.get("reporting_policy") != REPORTING_POLICY:
        raise FullSuiteError("full_suite reporting policy must forbid cross-family scoring")
    if root.get("families") != FAMILY_CONTRACTS:
        raise FullSuiteError("full_suite family contracts differ from the canonical contract")

    raw_components = _mapping(root.get("components"), "full_suite.components")
    expected_ids = set(COMPONENT_CONTRACTS)
    observed_ids = {str(key) for key in raw_components}
    if observed_ids != expected_ids:
        missing = sorted(expected_ids - observed_ids)
        extra = sorted(observed_ids - expected_ids)
        raise FullSuiteError(
            f"full_suite component membership differs: missing={missing}, extra={extra}"
        )
    components: dict[str, FullSuiteComponent] = {}
    statuses: dict[str, str] = {}
    for component_id, contract in sorted(COMPONENT_CONTRACTS.items()):
        location = f"full_suite.components.{component_id}"
        raw = _mapping(raw_components[component_id], location)
        expected_static = {
            "family_id": contract.family_id,
            "profile_id": contract.profile_id,
            "evidence_class": contract.evidence_class,
            "reporting_role": contract.reporting_role,
        }
        for key, expected in expected_static.items():
            if raw.get(key) != expected:
                raise FullSuiteError(f"{location}.{key} differs from the canonical contract")
        status = raw.get("status")
        if status not in {"certified", "missing"}:
            raise FullSuiteError(f"{location}.status must be certified or missing")
        evidence: Mapping[str, object] | None = None
        missing_reason: str | None = None
        if status == "missing":
            expected_keys = set(expected_static) | {"status", "missing_reason"}
            if set(raw) != expected_keys:
                raise FullSuiteError(f"{location} has invalid fields for missing status")
            if "evidence" in raw:
                raise FullSuiteError(f"{location} cannot contain evidence while missing")
            missing_reason = _text(raw, "missing_reason", location)
            if missing_reason != contract.missing_reason:
                raise FullSuiteError(f"{location}.missing_reason differs from the contract")
        else:
            if not contract.root_eligible:
                raise FullSuiteError(
                    f"{location} cannot be certified until its real gold contract is implemented"
                )
            expected_keys = set(expected_static) | {"status", "evidence"}
            if set(raw) != expected_keys:
                raise FullSuiteError(f"{location} has invalid fields for certified status")
            if "missing_reason" in raw:
                raise FullSuiteError(f"{location} cannot contain missing_reason while certified")
            evidence = _mapping(raw.get("evidence"), f"{location}.evidence")
            evidence_location = f"{location}.evidence"
            evidence_keys = {
                "root_format",
                "track_id",
                "profile_id",
                "profile_scope",
                "registry_fingerprint",
                "dataset_fingerprint",
                "page_count",
                "source_ids",
                "manifest_sha256",
                "dataset_lock_sha256",
                "frozen_sha256",
                "gold_sha256",
                "stats_sha256",
                "certification_sha256",
                "certified_sha256",
            }
            if set(evidence) != evidence_keys:
                raise FullSuiteError(f"{evidence_location} fields differ from the lock schema")
            if evidence.get("root_format") != "frozen-certified-corpus-v1":
                raise FullSuiteError(f"{evidence_location}.root_format is invalid")
            if evidence.get("track_id") != component_id:
                raise FullSuiteError(f"{location}.evidence.track_id is invalid")
            if evidence.get("registry_fingerprint") != root.get("registry_fingerprint"):
                raise FullSuiteError(f"{location}.evidence registry fingerprint differs")
            observed_profile = evidence.get("profile_id")
            if not isinstance(observed_profile, str) or not observed_profile:
                raise FullSuiteError(f"{evidence_location}.profile_id is invalid")
            if contract.profile_id is not None and observed_profile != contract.profile_id:
                raise FullSuiteError(f"{evidence_location}.profile_id differs from the contract")
            profile_scope = evidence.get("profile_scope")
            if profile_scope not in {"full", "track-component"}:
                raise FullSuiteError(f"{evidence_location}.profile_scope is invalid")
            if (
                contract.reporting_role == "modern-headline-component"
                and profile_scope != "track-component"
            ):
                raise FullSuiteError(
                    f"{evidence_location}.profile_scope is invalid for a Modern headline component"
                )
            if not isinstance(evidence.get("page_count"), int) or evidence["page_count"] < 1:
                raise FullSuiteError(f"{evidence_location}.page_count is invalid")
            source_ids = evidence.get("source_ids")
            if (
                not isinstance(source_ids, list)
                or not source_ids
                or any(not isinstance(item, str) or not item for item in source_ids)
                or source_ids != sorted(set(source_ids))
            ):
                raise FullSuiteError(f"{evidence_location}.source_ids is invalid")
            for digest_key in (
                "registry_fingerprint",
                "dataset_fingerprint",
                "manifest_sha256",
                "dataset_lock_sha256",
                "frozen_sha256",
                "gold_sha256",
                "stats_sha256",
                "certification_sha256",
                "certified_sha256",
            ):
                _digest(evidence, digest_key, evidence_location)
        statuses[component_id] = str(status)
        components[component_id] = FullSuiteComponent(
            component_id=component_id,
            family_id=contract.family_id,
            profile_id=contract.profile_id,
            evidence_class=contract.evidence_class,
            reporting_role=contract.reporting_role,
            status=str(status),
            missing_reason=missing_reason,
            evidence=evidence,
        )

    coverage = _mapping(root.get("coverage"), "full_suite.coverage")
    if coverage != _coverage_payload(statuses):
        raise FullSuiteError("full_suite.coverage is inconsistent with component statuses")
    return FullSuiteSpec(
        schema_version=_text(root, "schema_version", "full_suite"),
        suite_version=_text(root, "suite_version", "full_suite"),
        benchmark=_text(root, "benchmark", "full_suite"),
        benchmark_version=_text(root, "benchmark_version", "full_suite"),
        manifest_type=_text(root, "manifest_type", "full_suite"),
        registry_fingerprint=_digest(root, "registry_fingerprint", "full_suite"),
        profiles_fingerprint=_digest(root, "profiles_fingerprint", "full_suite"),
        reporting_policy=_mapping(root["reporting_policy"], "full_suite.reporting_policy"),
        families={
            str(key): _mapping(item, f"full_suite.families.{key}")
            for key, item in _mapping(root["families"], "full_suite.families").items()
        },
        components=components,
        coverage=coverage,
        suite_fingerprint=observed_fingerprint,
    )


def load_full_suite_lock(path: str | Path) -> FullSuiteSpec:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FullSuiteError(f"cannot read full-suite lock {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise FullSuiteError(f"invalid JSON in full-suite lock {source}: {exc}") from exc
    return parse_full_suite_lock(value)


def validate_full_suite_contract(
    suite: FullSuiteSpec,
    *,
    expected_benchmark_version: str,
    expected_registry_fingerprint: str,
    expected_profiles_fingerprint: str,
) -> None:
    """Bind a parsed lock to the current release metadata."""

    if suite.schema_version != "1.0":
        raise FullSuiteError("full-suite schema_version must be 1.0")
    if suite.benchmark != "HebOCRBench":
        raise FullSuiteError("full-suite benchmark identity is invalid")
    if suite.manifest_type != "multi-profile-suite-lock":
        raise FullSuiteError("full-suite manifest_type is invalid")
    if suite.benchmark_version != expected_benchmark_version:
        raise FullSuiteError("full-suite benchmark_version differs from the release")
    if suite.registry_fingerprint != expected_registry_fingerprint:
        raise FullSuiteError("full-suite registry_fingerprint differs from the release")
    if suite.profiles_fingerprint != expected_profiles_fingerprint:
        raise FullSuiteError("full-suite profiles_fingerprint differs from the release")


def verify_full_suite_roots(
    suite: FullSuiteSpec,
    component_roots: Mapping[str, str | Path],
    *,
    require_all_certified: bool = True,
) -> dict[str, object]:
    """Verify locked component evidence against current root bytes."""

    unknown = sorted(set(component_roots) - set(COMPONENT_CONTRACTS))
    if unknown:
        raise FullSuiteError("unknown full-suite components: " + ", ".join(unknown))
    certified_ids = {
        component_id
        for component_id, component in suite.components.items()
        if component.status == "certified"
    }
    if require_all_certified:
        missing_roots = sorted(certified_ids - set(component_roots))
        if missing_roots:
            raise FullSuiteError(
                "verification roots missing for certified components: " + ", ".join(missing_roots)
            )
    stale_roots = sorted(
        component_id
        for component_id in component_roots
        if suite.components[component_id].status == "missing"
    )
    if stale_roots:
        raise FullSuiteError(
            "lock marks supplied component roots as missing; rebuild the lock: "
            + ", ".join(stale_roots)
        )
    verified: list[str] = []
    for component_id, root in sorted(component_roots.items()):
        observed = _root_evidence(
            COMPONENT_CONTRACTS[component_id],
            root,
            benchmark_version=suite.benchmark_version,
            registry_fingerprint=suite.registry_fingerprint,
        )
        expected = suite.components[component_id].evidence
        if observed != expected:
            raise FullSuiteError(
                f"{component_id} current root evidence differs from the full-suite lock"
            )
        verified.append(component_id)
    return {
        "valid": True,
        "suite_fingerprint": suite.suite_fingerprint,
        "verified_components": verified,
        "missing_components": sorted(
            component_id
            for component_id, component in suite.components.items()
            if component.status == "missing"
        ),
    }


__all__ = [
    "COMPONENT_CONTRACTS",
    "EXPERIMENTAL_COMPONENTS",
    "FAMILY_CONTRACTS",
    "MODERN_HEADLINE_COMPONENTS",
    "REAL_COVERAGE_TARGETS",
    "REAL_EXTENSION_COMPONENTS",
    "REPORTING_POLICY",
    "SYNTHETIC_DIAGNOSTIC_COMPONENTS",
    "FullSuiteComponent",
    "FullSuiteError",
    "FullSuiteSpec",
    "build_full_suite_lock",
    "load_full_suite_lock",
    "parse_full_suite_lock",
    "validate_full_suite_contract",
    "verify_full_suite_roots",
    "with_full_suite_fingerprint",
]
