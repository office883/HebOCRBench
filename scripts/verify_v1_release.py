#!/usr/bin/env python3
"""Verify HebOCRBench 1.0 multi-profile Hebrew release artifacts end to end."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from typing import Mapping, Sequence
import zipfile


EXPECTED_VERSION = "1.0.0"
FONT_SUFFIXES = (".ttf", ".otf", ".woff", ".woff2")
RELEASE_PREFIX = f"HebOCRBench-v{EXPECTED_VERSION}"
EXPECTED_MANIFEST_NAME = f"{RELEASE_PREFIX}-release-manifest.json"
EXPECTED_CHECKSUM_NAME = f"{RELEASE_PREFIX}-SHA256SUMS.txt"
EXPECTED_ARTIFACTS = (
    f"hebocrbench-{EXPECTED_VERSION}-py3-none-any.whl",
    f"{RELEASE_PREFIX}.zip",
    f"{RELEASE_PREFIX}.tar.gz",
    f"{RELEASE_PREFIX}-SBOM.json",
    f"{RELEASE_PREFIX}-registry.lock.json",
    f"{RELEASE_PREFIX}-registry.yaml",
    f"{RELEASE_PREFIX}-profiles.lock.json",
    f"{RELEASE_PREFIX}-profiles.yaml",
    f"{RELEASE_PREFIX}-tracks.lock.json",
    f"{RELEASE_PREFIX}-modern-suite.lock.json",
    f"{RELEASE_PREFIX}-full-suite.lock.json",
    f"{RELEASE_PREFIX}-component-proof.json",
)
EXPECTED_MANIFEST_FIELDS = {
    "schema_version",
    "benchmark",
    "version",
    "release_date",
    "scope",
    "registry_fingerprint",
    "profiles_fingerprint",
    "modern_suite_fingerprint",
    "full_suite_fingerprint",
    "component_proof_fingerprint",
    "profiles",
    "official_profiles",
    "headline_profile",
    "headline_profile_fingerprint",
    "headline_score_policy",
    "headline_tracks",
    "non_headline_profiles",
    "separate_profile_reporting",
    "extensions_blended_into_headline",
    "artifacts",
    "checksum_manifest",
    "certified_components",
    "missing_components",
    "declared_component_coverage",
    "all_certified_component_roots_verified",
    "data_distribution",
    "third_party_corpora_bundled",
    "note",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    name: str,
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    required: bool = True,
) -> dict[str, object]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=merged,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "name": name,
        "command": list(command),
        "cwd": str(cwd),
        "required": required,
        "exit_code": completed.returncode,
        "passed": completed.returncode == 0,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
    }


def _check(name: str, passed: bool, detail: object) -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {label} at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _parse_checksums(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read checksum manifest {path}: {exc}") from exc
    if not lines:
        raise ValueError("checksum manifest is empty")
    result: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        if "  " not in line:
            raise ValueError(f"malformed checksum line {line_number}")
        digest, filename = line.split("  ", 1)
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not filename
            or Path(filename).name != filename
        ):
            raise ValueError(f"invalid checksum entry on line {line_number}")
        if filename in result:
            raise ValueError(f"duplicate checksum entry: {filename}")
        result[filename] = digest
    return result


def _load_release_packaging(root: Path):
    source_root = str(root / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    from hebocrbench import release_packaging

    return release_packaging


def _verify_record(archive: zipfile.ZipFile) -> tuple[bool, list[str]]:
    errors: list[str] = []
    record_names = [name for name in archive.namelist() if name.endswith(".dist-info/RECORD")]
    if len(record_names) != 1:
        return False, [f"Expected one RECORD, found {len(record_names)}"]
    record_name = record_names[0]
    rows = csv.reader(io.StringIO(archive.read(record_name).decode("utf-8")))
    seen_record = False
    for row in rows:
        if len(row) != 3:
            errors.append(f"Malformed RECORD row: {row!r}")
            continue
        name, digest, size = row
        if name == record_name:
            seen_record = True
            if digest or size:
                errors.append("RECORD must have empty hash and size for itself")
            continue
        try:
            payload = archive.read(name)
        except KeyError:
            errors.append(f"RECORD references missing member {name}")
            continue
        if not digest.startswith("sha256="):
            errors.append(f"Unsupported RECORD digest for {name}: {digest}")
        else:
            actual = (
                base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                .rstrip(b"=")
                .decode("ascii")
            )
            if digest.split("=", 1)[1] != actual:
                errors.append(f"RECORD hash mismatch: {name}")
        if size != str(len(payload)):
            errors.append(f"RECORD size mismatch: {name}")
    if not seen_record:
        errors.append("RECORD does not list itself")
    return not errors, errors


def _static_checks(root: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = pyproject["project"]["version"]
    init_text = (root / "src" / "hebocrbench" / "__init__.py").read_text(encoding="utf-8")
    checks.append(_check("project_version", project_version == EXPECTED_VERSION, project_version))
    checks.append(
        _check(
            "package_version",
            f'__version__ = "{EXPECTED_VERSION}"' in init_text,
            init_text.strip(),
        )
    )

    pairs = [
        (
            root / "corpora" / "registry.yaml",
            root / "src" / "hebocrbench" / "data" / "corpus-registry.yaml",
        ),
        (
            root / "corpora" / "registry.lock.json",
            root / "src" / "hebocrbench" / "data" / "corpus-registry.lock.json",
        ),
        (
            root / "corpora" / "profiles.yaml",
            root / "src" / "hebocrbench" / "data" / "corpus-profiles.yaml",
        ),
        (
            root / "corpora" / "profiles.lock.json",
            root / "src" / "hebocrbench" / "data" / "corpus-profiles.lock.json",
        ),
        (root / "benchmark.yaml", root / "src" / "hebocrbench" / "data" / "benchmark.yaml"),
        (root / "data" / "sources.yaml", root / "src" / "hebocrbench" / "data" / "sources.yaml"),
        (root / "data" / "profiles.yaml", root / "src" / "hebocrbench" / "data" / "profiles.yaml"),
        (
            root / "data" / "stress_cases.yaml",
            root / "src" / "hebocrbench" / "data" / "stress_cases.yaml",
        ),
        (
            root / "tracks" / "tracks.lock.json",
            root / "src" / "hebocrbench" / "data" / "tracks" / "tracks.lock.json",
        ),
    ]
    for left, right in pairs:
        passed = left.is_file() and right.is_file() and left.read_bytes() == right.read_bytes()
        checks.append(_check(f"sync:{left.name}", passed, {"left": str(left), "right": str(right)}))

    root_schemas = {path.name: _sha256(path) for path in sorted((root / "schemas").glob("*.json"))}
    package_schemas = {
        path.name: _sha256(path)
        for path in sorted((root / "src" / "hebocrbench" / "schemas").glob("*.json"))
    }
    checks.append(
        _check(
            "schema_sync",
            root_schemas == package_schemas,
            {"root": root_schemas, "package": package_schemas},
        )
    )

    fonts = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in FONT_SUFFIXES
    ]
    checks.append(_check("no_bundled_fonts", not fonts, fonts))

    source_root = str(root / "src")
    inserted_source_root = source_root not in sys.path
    if inserted_source_root:
        sys.path.insert(0, source_root)
    try:
        from hebocrbench.corpus_registry import load_registry
        from hebocrbench.profiles import load_profiles
        from hebocrbench.release_metadata import profile_lock_payload, registry_lock_payload

        registry = load_registry(root / "corpora" / "registry.yaml")
        profiles = load_profiles(root / "corpora" / "profiles.yaml", registry=registry)
        registry_lock = json.loads(
            (root / "corpora" / "registry.lock.json").read_text(encoding="utf-8")
        )
        profiles_lock = json.loads(
            (root / "corpora" / "profiles.lock.json").read_text(encoding="utf-8")
        )
        expected_registry_lock = registry_lock_payload(registry, benchmark_version=EXPECTED_VERSION)
        expected_profiles_lock = profile_lock_payload(
            profiles, registry_fingerprint=registry.fingerprint
        )
        checks.append(
            _check(
                "registry_lock",
                registry_lock == expected_registry_lock,
                {
                    "lock_fingerprint": registry_lock.get("registry_fingerprint"),
                    "actual_fingerprint": registry.fingerprint,
                    "lock_sources": sorted(registry_lock.get("sources", {})),
                    "actual_sources": sorted(registry.sources),
                },
            )
        )
        checks.append(
            _check(
                "profiles_lock",
                profiles_lock == expected_profiles_lock,
                {
                    "lock_fingerprint": profiles_lock.get("profiles_fingerprint"),
                    "actual_fingerprint": profiles.fingerprint,
                    "lock_profiles": sorted(profiles_lock.get("profiles", {})),
                    "actual_profiles": sorted(profiles.profiles),
                },
            )
        )
    except Exception as exc:
        checks.append(_check("registry_lock", False, str(exc)))
        checks.append(_check("profiles_lock", False, str(exc)))
    finally:
        if inserted_source_root:
            sys.path.remove(source_root)

    forbidden_historical = [
        "scripts/materialize_v1_sources.py",
        "src/hebocrbench/converters/wikisource.py",
        "tests/test_wikisource_converter.py",
        "tests/test_materializer_v1.py",
    ]
    present_forbidden = [path for path in forbidden_historical if (root / path).exists()]
    checks.append(_check("deprecated_source_tree_absent", not present_forbidden, present_forbidden))

    required_docs = [
        "README.md",
        "CITATION.cff",
        "THIRD_PARTY_NOTICES.md",
        "docs/BENCHMARK_CARD_HE.md",
        "docs/CORPUS_REGISTRY_HE.md",
        "docs/BUILD_REAL_CORPUS_HE.md",
        "docs/LEADERBOARD_POLICY_HE.md",
        "docs/LICENSE_MATRIX_HE.md",
        "docs/METRICS_HE.md",
        "docs/REPRODUCIBILITY_HE.md",
        "docs/SOURCE_AND_LICENSE_AUDIT_HE.md",
        "docs/V1_DATASET_MANIFEST_HE.md",
    ]
    missing = [path for path in required_docs if not (root / path).is_file()]
    checks.append(_check("release_documents", not missing, missing))
    return checks


def _verify_wheel(
    root: Path, wheel: Path, commands: list[dict[str, object]]
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    expected_name = f"hebocrbench-{EXPECTED_VERSION}-py3-none-any.whl"
    checks.append(_check("wheel_name", wheel.name == expected_name, wheel.name))
    try:
        with zipfile.ZipFile(wheel) as archive:
            bad = archive.testzip()
            names = set(archive.namelist())
            record_ok, record_errors = _verify_record(archive)
            required = {
                "hebocrbench/__init__.py",
                "hebocrbench/data/corpus-registry.yaml",
                "hebocrbench/data/corpus-registry.lock.json",
                "hebocrbench/data/benchmark.yaml",
                "hebocrbench/data/corpus-profiles.yaml",
                "hebocrbench/data/corpus-profiles.lock.json",
                "hebocrbench/data/tracks/tracks.lock.json",
                "hebocrbench/data/tracks/modern-page-ocr-v1.yaml",
                "hebocrbench/modern_scope.py",
                "hebocrbench/modern_suite.py",
                "hebocrbench/modern_score.py",
                "hebocrbench/full_suite.py",
                "hebocrbench/release_integrity.py",
                "hebocrbench/schemas/gold-page.schema.json",
                "hebocrbench/schemas/full-suite-lock.schema.json",
                f"hebocrbench-{EXPECTED_VERSION}.dist-info/METADATA",
            }
            checks.append(_check("wheel_crc", bad is None, bad))
            checks.append(
                _check("wheel_required_members", required <= names, sorted(required - names))
            )
            checks.append(_check("wheel_record", record_ok, record_errors))
            lock_pairs = {
                "hebocrbench/data/corpus-registry.lock.json": (
                    root / "corpora" / "registry.lock.json"
                ),
                "hebocrbench/data/corpus-profiles.lock.json": (
                    root / "corpora" / "profiles.lock.json"
                ),
                "hebocrbench/data/tracks/tracks.lock.json": (root / "tracks" / "tracks.lock.json"),
            }
            lock_mismatches = sorted(
                name
                for name, source in lock_pairs.items()
                if name not in names or archive.read(name) != source.read_bytes()
            )
            checks.append(_check("wheel_canonical_locks", not lock_mismatches, lock_mismatches))
            bundled_fonts = sorted(name for name in names if name.lower().endswith(FONT_SUFFIXES))
            checks.append(_check("wheel_no_fonts", not bundled_fonts, bundled_fonts))
    except (OSError, zipfile.BadZipFile) as exc:
        checks.append(_check("wheel_readable", False, str(exc)))
        return checks

    with tempfile.TemporaryDirectory(prefix="hebocrbench-wheel-install-") as temporary:
        target = Path(temporary) / "site"
        outside = Path(temporary) / "outside"
        outside.mkdir()
        commands.append(
            _run(
                "wheel_install",
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--target",
                    str(target),
                    str(wheel),
                ],
                cwd=outside,
            )
        )
        env = {"PYTHONPATH": str(target)}
        commands.append(
            _run(
                "wheel_import",
                [
                    sys.executable,
                    "-c",
                    (
                        "import hebocrbench; "
                        "from hebocrbench.corpus_registry import load_registry; "
                        "from hebocrbench.profiles import load_profiles; "
                        "from hebocrbench.release_metadata import "
                        "profile_lock_payload, registry_lock_payload; "
                        "from importlib import resources; import json; "
                        "registry = load_registry(None); "
                        "profiles = load_profiles(None, registry=registry); "
                        "registry_lock = json.loads(resources.files('hebocrbench').joinpath("
                        "'data/corpus-registry.lock.json').read_text(encoding='utf-8')); "
                        "profiles_lock = json.loads(resources.files('hebocrbench').joinpath("
                        "'data/corpus-profiles.lock.json').read_text(encoding='utf-8')); "
                        "assert hebocrbench.__version__ == '1.0.0'; "
                        "assert registry_lock_payload(registry, benchmark_version="
                        "hebocrbench.__version__) == registry_lock; "
                        "assert profile_lock_payload(profiles, registry_fingerprint="
                        "registry.fingerprint) == profiles_lock"
                    ),
                ],
                cwd=outside,
                env=env,
            )
        )
        commands.append(
            _run(
                "wheel_cli_version",
                [sys.executable, "-m", "hebocrbench", "--version"],
                cwd=outside,
                env=env,
            )
        )
        commands.append(
            _run(
                "wheel_cli_registry",
                [
                    sys.executable,
                    "-m",
                    "hebocrbench",
                    "data",
                    "list",
                    "--source",
                    "modern-public-documents-v1",
                ],
                cwd=outside,
                env=env,
            )
        )
        commands.append(
            _run(
                "wheel_cli_profiles",
                [sys.executable, "-m", "hebocrbench", "data", "profiles"],
                cwd=outside,
                env=env,
            )
        )
    return checks


def _expected_source_members(root: Path, release_dir: Path) -> dict[str, Path]:
    packaging = _load_release_packaging(root)
    return {
        f"{RELEASE_PREFIX}/{path.relative_to(root).as_posix()}": path
        for path in packaging._source_files(root, release_dir)  # noqa: SLF001
    }


def _verify_source_zip(
    root: Path,
    release_dir: Path,
    source_zip: Path,
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    try:
        expected = _expected_source_members(root, release_dir)
        with zipfile.ZipFile(source_zip) as archive:
            bad = archive.testzip()
            names = archive.namelist()
            checks.append(_check("source_zip_crc", bad is None, bad))
            expected_prefix = f"{RELEASE_PREFIX}/"
            checks.append(
                _check(
                    "source_zip_prefix",
                    bool(names) and all(name.startswith(expected_prefix) for name in names),
                    expected_prefix,
                )
            )
            forbidden = [
                name
                for name in names
                if "/.git/" in name
                or name.endswith("/.git")
                or "/.pytest_cache/" in name
                or "/__pycache__/" in name
                or name.endswith((".pyc", ".pyo"))
                or name.lower().endswith(FONT_SUFFIXES)
            ]
            checks.append(_check("source_zip_clean", not forbidden, forbidden[:50]))
            duplicate_names = sorted({name for name in names if names.count(name) > 1})
            checks.append(_check("source_zip_unique_members", not duplicate_names, duplicate_names))
            observed = set(names)
            expected_names = set(expected)
            checks.append(
                _check(
                    "source_zip_complete",
                    observed == expected_names,
                    {
                        "missing": sorted(expected_names - observed)[:50],
                        "extra": sorted(observed - expected_names)[:50],
                        "expected_count": len(expected_names),
                        "observed_count": len(observed),
                    },
                )
            )
            mismatched = []
            for name in sorted(observed & expected_names):
                if hashlib.sha256(archive.read(name)).hexdigest() != _sha256(expected[name]):
                    mismatched.append(name)
            checks.append(_check("source_zip_source_bytes", not mismatched, mismatched[:50]))
    except (OSError, zipfile.BadZipFile) as exc:
        checks.append(_check("source_zip_readable", False, str(exc)))
    return checks


def _verify_source_tar_gz(
    root: Path,
    release_dir: Path,
    source_tar_gz: Path,
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    try:
        expected = _expected_source_members(root, release_dir)
        with tarfile.open(source_tar_gz, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            expected_prefix = f"{RELEASE_PREFIX}/"
            checks.append(
                _check(
                    "source_tar_prefix",
                    bool(names) and all(name.startswith(expected_prefix) for name in names),
                    expected_prefix,
                )
            )
            forbidden = [
                name
                for name in names
                if "/.git/" in name
                or name.endswith("/.git")
                or "/.pytest_cache/" in name
                or "/__pycache__/" in name
                or name.endswith((".pyc", ".pyo"))
                or name.lower().endswith(FONT_SUFFIXES)
            ]
            unsafe = [
                name for name in names if Path(name).is_absolute() or ".." in Path(name).parts
            ]
            special = [member.name for member in members if not (member.isfile() or member.isdir())]
            checks.append(_check("source_tar_clean", not forbidden, forbidden[:50]))
            checks.append(_check("source_tar_safe_paths", not unsafe, unsafe[:50]))
            checks.append(_check("source_tar_regular_members", not special, special[:50]))
            duplicate_names = sorted({name for name in names if names.count(name) > 1})
            checks.append(_check("source_tar_unique_members", not duplicate_names, duplicate_names))
            observed = set(names)
            expected_names = set(expected)
            checks.append(
                _check(
                    "source_tar_complete",
                    observed == expected_names,
                    {
                        "missing": sorted(expected_names - observed)[:50],
                        "extra": sorted(observed - expected_names)[:50],
                        "expected_count": len(expected_names),
                        "observed_count": len(observed),
                    },
                )
            )
            mismatched = []
            for member in members:
                if member.name not in expected or not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    mismatched.append(member.name)
                    continue
                if hashlib.sha256(extracted.read()).hexdigest() != _sha256(expected[member.name]):
                    mismatched.append(member.name)
            checks.append(_check("source_tar_source_bytes", not mismatched, mismatched[:50]))
    except (OSError, tarfile.TarError) as exc:
        checks.append(_check("source_tar_readable", False, str(exc)))
    return checks


def _verify_sbom(root: Path, sbom_path: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    try:
        sbom = _load_json_object(sbom_path, "SBOM")
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project = pyproject["project"]
        expected_requirements = sorted(str(item) for item in project.get("dependencies", []))
        dependencies = sbom.get("declared_dependencies")
        if not isinstance(dependencies, list):
            raise ValueError("SBOM declared_dependencies must be a list")
        requirements: list[str] = []
        malformed: list[object] = []
        for entry in dependencies:
            if not isinstance(entry, dict) or set(entry) != {
                "name",
                "requirement",
                "installed_version",
                "license",
            }:
                malformed.append(entry)
                continue
            requirement = entry.get("requirement")
            if not isinstance(requirement, str) or not requirement:
                malformed.append(entry)
                continue
            requirements.append(requirement)
        checks.append(
            _check(
                "sbom_identity",
                sbom.get("schema_version") == "1.0"
                and sbom.get("format") == "HebOCRBench compact SBOM"
                and isinstance(sbom.get("runtime"), dict)
                and isinstance(sbom.get("project"), dict)
                and sbom["project"].get("name") == "hebocrbench"
                and sbom["project"].get("version") == EXPECTED_VERSION,
                {"format": sbom.get("format"), "project": sbom.get("project")},
            )
        )
        checks.append(_check("sbom_dependency_shape", not malformed, malformed[:10]))
        checks.append(
            _check(
                "sbom_declared_dependencies_complete",
                sorted(requirements) == expected_requirements
                and len(requirements) == len(set(requirements)),
                {"expected": expected_requirements, "observed": sorted(requirements)},
            )
        )
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        checks.append(_check("sbom_readable", False, str(exc)))
    return checks


def _verify_release_bundle(
    root: Path,
    release_dir: Path,
    manifest_path: Path,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    checks: list[dict[str, object]] = []
    if not release_dir.is_dir():
        return [_check("release_dir", False, str(release_dir))], None
    expected_manifest_path = release_dir / EXPECTED_MANIFEST_NAME
    checks.append(
        _check(
            "release_manifest_location",
            manifest_path == expected_manifest_path,
            {"expected": str(expected_manifest_path), "observed": str(manifest_path)},
        )
    )
    try:
        manifest = _load_json_object(manifest_path, "release manifest")
    except ValueError as exc:
        checks.append(_check("release_manifest_readable", False, str(exc)))
        return checks, None

    checks.append(
        _check(
            "release_manifest_fields",
            set(manifest) == EXPECTED_MANIFEST_FIELDS,
            {
                "missing": sorted(EXPECTED_MANIFEST_FIELDS - set(manifest)),
                "extra": sorted(set(manifest) - EXPECTED_MANIFEST_FIELDS),
            },
        )
    )
    checks.append(
        _check(
            "release_manifest_identity",
            manifest.get("schema_version") == "1.0"
            and manifest.get("benchmark") == "HebOCRBench"
            and manifest.get("version") == EXPECTED_VERSION
            and manifest.get("scope") == "multi-profile-hebrew-suite"
            and manifest.get("checksum_manifest") == EXPECTED_CHECKSUM_NAME
            and manifest.get("all_certified_component_roots_verified") is True
            and manifest.get("separate_profile_reporting") is True
            and manifest.get("extensions_blended_into_headline") is False
            and manifest.get("third_party_corpora_bundled") is False,
            {
                "benchmark": manifest.get("benchmark"),
                "version": manifest.get("version"),
                "scope": manifest.get("scope"),
            },
        )
    )
    artifacts = manifest.get("artifacts")
    artifact_list = artifacts if isinstance(artifacts, list) else []
    safe_artifacts = all(
        isinstance(name, str) and name and Path(name).name == name for name in artifact_list
    )
    checks.append(
        _check(
            "release_manifest_artifacts",
            safe_artifacts
            and artifact_list == list(EXPECTED_ARTIFACTS)
            and len(artifact_list) == len(set(artifact_list)),
            {"expected": list(EXPECTED_ARTIFACTS), "observed": artifact_list},
        )
    )

    checksum_path = release_dir / EXPECTED_CHECKSUM_NAME
    try:
        sums = _parse_checksums(checksum_path)
        expected_sum_names = set(EXPECTED_ARTIFACTS) | {EXPECTED_MANIFEST_NAME}
        checks.append(
            _check(
                "checksum_manifest_membership",
                set(sums) == expected_sum_names,
                {
                    "missing": sorted(expected_sum_names - set(sums)),
                    "extra": sorted(set(sums) - expected_sum_names),
                },
            )
        )
        missing_files = sorted(
            filename for filename in expected_sum_names if not (release_dir / filename).is_file()
        )
        mismatched = sorted(
            filename
            for filename in expected_sum_names - set(missing_files)
            if sums.get(filename) != _sha256(release_dir / filename)
        )
        checks.append(_check("release_artifacts_present", not missing_files, missing_files))
        checks.append(_check("release_artifact_sha256", not mismatched, mismatched))
    except ValueError as exc:
        checks.append(_check("checksum_manifest_readable", False, str(exc)))

    copied_files = {
        f"{RELEASE_PREFIX}-registry.lock.json": root / "corpora" / "registry.lock.json",
        f"{RELEASE_PREFIX}-registry.yaml": root / "corpora" / "registry.yaml",
        f"{RELEASE_PREFIX}-profiles.lock.json": root / "corpora" / "profiles.lock.json",
        f"{RELEASE_PREFIX}-profiles.yaml": root / "corpora" / "profiles.yaml",
        f"{RELEASE_PREFIX}-tracks.lock.json": root / "tracks" / "tracks.lock.json",
    }
    copied_mismatches = sorted(
        name
        for name, source in copied_files.items()
        if not (release_dir / name).is_file()
        or not source.is_file()
        or (release_dir / name).read_bytes() != source.read_bytes()
    )
    checks.append(
        _check("release_copied_locks_and_metadata", not copied_mismatches, copied_mismatches)
    )
    checks.extend(_verify_sbom(root, release_dir / f"{RELEASE_PREFIX}-SBOM.json"))
    return checks, manifest


def _verify_suite_proof(
    root: Path,
    release_dir: Path,
    manifest: Mapping[str, object],
    component_root_values: Sequence[str],
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    source_root = str(root / "src")
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        from hebocrbench.corpus_registry import load_registry
        from hebocrbench.full_suite import load_full_suite_lock, validate_full_suite_contract
        from hebocrbench.modern_suite import (
            DEFAULT_HEADLINE_TRACKS,
            load_modern_suite_lock,
            validate_modern_suite_contract,
        )
        from hebocrbench.profiles import load_profiles, profile_fingerprint
        from hebocrbench.release_integrity import (
            parse_component_roots,
            validate_component_proof,
            verify_release_suite_roots,
        )
        from hebocrbench.tracks import list_official_tracks

        registry = load_registry(root / "corpora" / "registry.yaml")
        profiles = load_profiles(root / "corpora" / "profiles.yaml", registry=registry)
        profile = profiles.profiles["modern-hebrew-print-v1"]
        modern_suite = load_modern_suite_lock(
            release_dir / f"{RELEASE_PREFIX}-modern-suite.lock.json"
        )
        validate_modern_suite_contract(
            modern_suite,
            expected_benchmark_version=EXPECTED_VERSION,
            expected_registry_fingerprint=registry.fingerprint,
            expected_profile_id=profile.profile_id,
            expected_profile_fingerprint=profile_fingerprint(profile),
            allowed_track_ids={track.track_id for track in list_official_tracks()},
        )
        full_suite = load_full_suite_lock(release_dir / f"{RELEASE_PREFIX}-full-suite.lock.json")
        validate_full_suite_contract(
            full_suite,
            expected_benchmark_version=EXPECTED_VERSION,
            expected_registry_fingerprint=registry.fingerprint,
            expected_profiles_fingerprint=profiles.fingerprint,
        )
        proof_value = _load_json_object(
            release_dir / f"{RELEASE_PREFIX}-component-proof.json",
            "component proof",
        )
        validate_component_proof(proof_value, modern_suite, full_suite)
        roots = parse_component_roots(component_root_values)
        rebuilt_proof = verify_release_suite_roots(modern_suite, full_suite, roots)
        if rebuilt_proof != proof_value:
            raise ValueError("component proof does not reconstruct from current root bytes")

        expected_certified = sorted(
            component_id
            for component_id, component in full_suite.components.items()
            if component.status == "certified"
        )
        expected_missing = sorted(set(full_suite.components) - set(expected_certified))
        manifest_bindings = {
            "registry_fingerprint": registry.fingerprint,
            "profiles_fingerprint": profiles.fingerprint,
            "modern_suite_fingerprint": modern_suite.suite_fingerprint,
            "full_suite_fingerprint": full_suite.suite_fingerprint,
            "component_proof_fingerprint": proof_value["proof_fingerprint"],
            "headline_tracks": list(DEFAULT_HEADLINE_TRACKS),
            "certified_components": expected_certified,
            "missing_components": expected_missing,
            "declared_component_coverage": dict(full_suite.coverage),
        }
        observed_bindings = {key: manifest.get(key) for key in manifest_bindings}
        if observed_bindings != manifest_bindings:
            raise ValueError("release manifest fingerprints or coverage differ from suite locks")
        checks.append(
            _check(
                "modern_full_suite_roots_and_proof",
                True,
                {
                    "modern_suite_fingerprint": modern_suite.suite_fingerprint,
                    "full_suite_fingerprint": full_suite.suite_fingerprint,
                    "proof_fingerprint": proof_value["proof_fingerprint"],
                    "verified_components": expected_certified,
                },
            )
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        checks.append(_check("modern_full_suite_roots_and_proof", False, str(exc)))
    return checks


def verify(
    root: Path,
    *,
    release_dir: Path,
    manifest: Path,
    component_roots: Sequence[str],
    run_tests: bool,
    run_sanity: bool,
) -> dict[str, object]:
    root = root.resolve()
    release_dir = release_dir.resolve()
    manifest = manifest.resolve()
    checks = _static_checks(root)
    bundle_checks, manifest_payload = _verify_release_bundle(root, release_dir, manifest)
    checks.extend(bundle_checks)
    if manifest_payload is not None:
        checks.extend(_verify_suite_proof(root, release_dir, manifest_payload, component_roots))
    commands: list[dict[str, object]] = []
    env = {"PYTHONPATH": str(root / "src")}
    commands.append(
        _run(
            "official_track_lock",
            [sys.executable, "-m", "hebocrbench", "tracks", "verify"],
            cwd=root,
            env=env,
        )
    )
    if run_tests:
        commands.append(_run("pytest", [sys.executable, "-m", "pytest", "-q"], cwd=root, env=env))
    commands.append(
        _run(
            "compileall",
            [sys.executable, "-m", "compileall", "-q", "src", "scripts"],
            cwd=root,
            env=env,
        )
    )
    ruff = shutil.which("ruff")
    if ruff:
        commands.append(_run("ruff", [ruff, "check", "src", "tests", "scripts"], cwd=root, env=env))
    else:
        commands.append(
            {
                "name": "ruff",
                "command": ["ruff", "check", "src", "tests", "scripts"],
                "cwd": str(root),
                "required": False,
                "exit_code": None,
                "passed": None,
                "status": "unavailable",
                "stdout": "",
                "stderr": "ruff is not installed in this runtime; pytest, compileall and artifact verification remain required.",
            }
        )
    if run_sanity:
        with tempfile.TemporaryDirectory(prefix="hebocrbench-sanity-") as temporary:
            commands.append(
                _run(
                    "sanity_matrix",
                    [
                        sys.executable,
                        "-m",
                        "hebocrbench",
                        "sanity",
                        "--output",
                        str(Path(temporary) / "sanity"),
                        "--variants",
                        "clean",
                        "--limit",
                        "28",
                    ],
                    cwd=root,
                    env=env,
                )
            )
    wheel = release_dir / f"hebocrbench-{EXPECTED_VERSION}-py3-none-any.whl"
    source_zip = release_dir / f"{RELEASE_PREFIX}.zip"
    source_tar_gz = release_dir / f"{RELEASE_PREFIX}.tar.gz"
    checks.extend(_verify_wheel(root, wheel, commands))
    checks.extend(_verify_source_zip(root, release_dir, source_zip))
    checks.extend(_verify_source_tar_gz(root, release_dir, source_tar_gz))

    required_commands_ok = all(
        command.get("passed") is True for command in commands if command.get("required", True)
    )
    static_ok = all(check["passed"] for check in checks)
    return {
        "schema_version": "1.0",
        "benchmark": "HebOCRBench",
        "expected_version": EXPECTED_VERSION,
        "root": str(root),
        "release_dir": str(release_dir),
        "manifest": str(manifest),
        "verified": static_ok and required_commands_ok,
        "checks": checks,
        "commands": commands,
        "artifacts": {
            "wheel": str(wheel),
            "wheel_sha256": _sha256(wheel) if wheel.is_file() else None,
            "source_zip": str(source_zip),
            "source_zip_sha256": _sha256(source_zip) if source_zip.is_file() else None,
            "source_tar_gz": str(source_tar_gz),
            "source_tar_gz_sha256": (_sha256(source_tar_gz) if source_tar_gz.is_file() else None),
            "modern_suite_lock": str(release_dir / f"{RELEASE_PREFIX}-modern-suite.lock.json"),
            "full_suite_lock": str(release_dir / f"{RELEASE_PREFIX}-full-suite.lock.json"),
            "component_proof": str(release_dir / f"{RELEASE_PREFIX}-component-proof.json"),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--component-root",
        action="append",
        required=True,
        metavar="COMPONENT_ID=PATH",
        help="Certified component root; repeat for every component certified by the full suite",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-sanity", action="store_true")
    args = parser.parse_args(argv)
    report = verify(
        args.root,
        release_dir=args.release_dir,
        manifest=args.manifest,
        component_roots=args.component_root,
        run_tests=not args.skip_tests,
        run_sanity=not args.skip_sanity,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
