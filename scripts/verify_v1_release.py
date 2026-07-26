#!/usr/bin/env python3
"""Verify HebOCRBench 1.0 source and release artifacts end to end."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
from importlib import util
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


def _load_release_packaging(root: Path):
    path = root / "src" / "hebocrbench" / "release_packaging.py"
    specification = util.spec_from_file_location("hebocrbench_release_packaging_verify", path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Cannot load release packaging module: {path}")
    module = util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


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

    try:
        lock = json.loads((root / "corpora" / "registry.lock.json").read_text(encoding="utf-8"))
        registry_module_path = root / "src" / "hebocrbench" / "corpus_registry.py"
        spec = util.spec_from_file_location("hebocrbench_registry_verify", registry_module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot load corpus registry module")
        module = util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        registry = module.load_registry(root / "corpora" / "registry.yaml")
        lock_ok = (
            lock.get("benchmark_version") == EXPECTED_VERSION
            and lock.get("registry_version") == registry.registry_version
            and lock.get("registry_fingerprint") == registry.fingerprint
            and set(lock.get("sources", {})) == set(registry.sources)
        )
        checks.append(
            _check(
                "registry_lock",
                lock_ok,
                {"lock": lock.get("registry_fingerprint"), "actual": registry.fingerprint},
            )
        )
    except Exception as exc:
        checks.append(_check("registry_lock", False, str(exc)))

    forbidden_historical = [
        "scripts/materialize_v1_sources.py",
        "src/hebocrbench/converters/wikisource.py",
        "tests/test_wikisource_converter.py",
        "tests/test_materializer_v1.py",
    ]
    present_forbidden = [path for path in forbidden_historical if (root / path).exists()]
    checks.append(_check("modern_only_source_tree", not present_forbidden, present_forbidden))

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
                "hebocrbench/schemas/gold-page.schema.json",
                f"hebocrbench-{EXPECTED_VERSION}.dist-info/METADATA",
            }
            checks.append(_check("wheel_crc", bad is None, bad))
            checks.append(
                _check("wheel_required_members", required <= names, sorted(required - names))
            )
            checks.append(_check("wheel_record", record_ok, record_errors))
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
                        "registry = load_registry(None); "
                        "profiles = load_profiles(None, registry=registry); "
                        "assert hebocrbench.__version__ == '1.0.0'; "
                        "assert registry.registry_version == '1.0.0'; "
                        "assert set(profiles.profiles) == {'modern-hebrew-print-v1', 'modern-hebrew-development-v1', 'modern-hebrew-handwriting-v1'}; assert set(registry.sources) == {'modern-bidi-diagnostic-v1', 'modern-public-documents-v1', 'modern-print-lines-development-v1', 'modern-handwriting-lines-v1'}"
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


def _verify_source_zip(source_zip: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(source_zip) as archive:
            bad = archive.testzip()
            names = archive.namelist()
            checks.append(_check("source_zip_crc", bad is None, bad))
            expected_prefix = f"HebOCRBench-v{EXPECTED_VERSION}/"
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
    except (OSError, zipfile.BadZipFile) as exc:
        checks.append(_check("source_zip_readable", False, str(exc)))
    return checks


def _verify_source_tar_gz(source_tar_gz: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    try:
        with tarfile.open(source_tar_gz, "r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            expected_prefix = f"HebOCRBench-v{EXPECTED_VERSION}/"
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
    except (OSError, tarfile.TarError) as exc:
        checks.append(_check("source_tar_readable", False, str(exc)))
    return checks


def verify(
    root: Path,
    *,
    wheel: Path | None,
    source_zip: Path | None,
    source_tar_gz: Path | None,
    suite_lock: Path,
    run_tests: bool,
    run_sanity: bool,
) -> dict[str, object]:
    root = root.resolve()
    checks = _static_checks(root)
    commands: list[dict[str, object]] = []
    env = {"PYTHONPATH": str(root / "src")}
    commands.append(
        _run(
            "modern_suite_lock",
            [
                sys.executable,
                "-m",
                "hebocrbench",
                "modern-suite",
                "verify",
                "--lock",
                str(suite_lock.resolve()),
            ],
            cwd=root,
            env=env,
        )
    )
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
    if wheel is not None:
        checks.extend(_verify_wheel(root, wheel.resolve(), commands))
    if source_zip is not None:
        checks.extend(_verify_source_zip(source_zip.resolve()))
    if source_tar_gz is not None:
        checks.extend(_verify_source_tar_gz(source_tar_gz.resolve()))

    required_commands_ok = all(
        command.get("passed") is True for command in commands if command.get("required", True)
    )
    static_ok = all(check["passed"] for check in checks)
    return {
        "schema_version": "1.0",
        "benchmark": "HebOCRBench",
        "expected_version": EXPECTED_VERSION,
        "root": str(root),
        "verified": static_ok and required_commands_ok,
        "checks": checks,
        "commands": commands,
        "artifacts": {
            "wheel": str(wheel.resolve()) if wheel is not None else None,
            "wheel_sha256": _sha256(wheel.resolve())
            if wheel is not None and wheel.is_file()
            else None,
            "source_zip": str(source_zip.resolve()) if source_zip is not None else None,
            "source_zip_sha256": _sha256(source_zip.resolve())
            if source_zip is not None and source_zip.is_file()
            else None,
            "source_tar_gz": str(source_tar_gz.resolve()) if source_tar_gz is not None else None,
            "source_tar_gz_sha256": (
                _sha256(source_tar_gz.resolve())
                if source_tar_gz is not None and source_tar_gz.is_file()
                else None
            ),
            "suite_lock": str(suite_lock.resolve()),
            "suite_lock_sha256": _sha256(suite_lock.resolve()) if suite_lock.is_file() else None,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--source-zip", type=Path)
    parser.add_argument("--source-tar-gz", type=Path)
    parser.add_argument("--suite-lock", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-sanity", action="store_true")
    args = parser.parse_args(argv)
    report = verify(
        args.root,
        wheel=args.wheel,
        source_zip=args.source_zip,
        source_tar_gz=args.source_tar_gz,
        suite_lock=args.suite_lock,
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
