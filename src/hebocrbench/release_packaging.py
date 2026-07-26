"""Deterministic, dependency-free release packaging for HebOCRBench.

The project deliberately does not require the external ``wheel`` or ``build``
packages to create its pure-Python release artifact.  This module emits a
standards-compliant wheel, a clean source archive, an environment-aware SBOM and
SHA-256 manifests using only the standard library.
"""

from __future__ import annotations

import base64
import csv
import gzip
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path
import platform
import re
import sys
import tarfile
from typing import Iterable, Mapping

from ._toml import TOMLDecodeError, loads as toml_loads
import zipfile


_RELEASE_DATE = "2026-07-23"
_ZIP_TIMESTAMP = (2026, 7, 23, 0, 0, 0)
_FONT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2"}
_SOURCE_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".release-test-cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".worktrees",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "venv",
}
_SOURCE_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".whl"} | _FONT_SUFFIXES


class ReleasePackagingError(ValueError):
    """Raised when project metadata cannot produce a valid release artifact."""


def _project(project_root: Path) -> Mapping[str, object]:
    try:
        payload = toml_loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
        project = payload["project"]
    except (OSError, KeyError, TOMLDecodeError) as exc:
        raise ReleasePackagingError(f"Cannot load project metadata: {exc}") from exc
    if not isinstance(project, Mapping):
        raise ReleasePackagingError("[project] must be a mapping")
    return project


def _require_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReleasePackagingError(f"Project field {key!r} must be a non-empty string")
    return value.strip()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def _zip_info(name: str, *, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    mode = 0o755 if executable else 0o644
    info.external_attr = mode << 16
    return info


def _write_zip_member(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
    *,
    executable: bool = False,
) -> None:
    archive.writestr(_zip_info(name, executable=executable), payload)


def _dependency_name(requirement: str) -> str:
    head = re.split(r"[<>=!~;\[\s]", requirement, maxsplit=1)[0].strip()
    if not head:
        raise ReleasePackagingError(f"Cannot parse dependency requirement: {requirement!r}")
    return head


def _metadata_text(project_root: Path, project: Mapping[str, object]) -> str:
    name = _require_text(project, "name")
    version = _require_text(project, "version")
    summary = _require_text(project, "description")
    requires_python = _require_text(project, "requires-python")
    license_value = project.get("license", {})
    if isinstance(license_value, Mapping):
        license_name = str(license_value.get("text", "MIT"))
    else:
        license_name = str(license_value or "MIT")
    lines = [
        "Metadata-Version: 2.3",
        f"Name: {name}",
        f"Version: {version}",
        f"Summary: {summary}",
        f"Requires-Python: {requires_python}",
        f"License: {license_name}",
        "Description-Content-Type: text/markdown; charset=UTF-8",
    ]
    authors = project.get("authors", [])
    if isinstance(authors, list):
        for author in authors:
            if isinstance(author, Mapping) and author.get("name"):
                lines.append(f"Author: {author['name']}")
                break
    dependencies = project.get("dependencies", [])
    if isinstance(dependencies, list):
        for requirement in dependencies:
            lines.append(f"Requires-Dist: {requirement}")
    optional = project.get("optional-dependencies", {})
    if isinstance(optional, Mapping):
        for extra in sorted(str(key) for key in optional):
            lines.append(f"Provides-Extra: {extra}")
            values = optional[extra]
            if isinstance(values, list):
                for requirement in values:
                    lines.append(f'Requires-Dist: {requirement}; extra == "{extra}"')
    readme = (project_root / "README.md").read_text(encoding="utf-8")
    return "\n".join(lines) + "\n\n" + readme.rstrip() + "\n"


def build_wheel(project_root: str | Path, output_dir: str | Path) -> Path:
    """Build a deterministic pure-Python wheel without external build tools."""

    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = _project(root)
    name = _require_text(project, "name").replace("-", "_")
    version = _require_text(project, "version")
    if version != "1.0.0":
        raise ReleasePackagingError(f"Refusing to build non-v1 release: {version}")
    package_root = root / "src" / "hebocrbench"
    if not package_root.is_dir():
        raise ReleasePackagingError(f"Package root is missing: {package_root}")
    dist_info = f"{name}-{version}.dist-info"
    wheel_path = output / f"{name}-{version}-py3-none-any.whl"

    members: dict[str, bytes] = {}
    for path in sorted(package_root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root / "src")
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.suffix.lower() in _FONT_SUFFIXES:
            raise ReleasePackagingError(f"Font file must not be bundled: {relative}")
        members[relative.as_posix()] = path.read_bytes()

    members[f"{dist_info}/METADATA"] = _metadata_text(root, project).encode("utf-8")
    members[f"{dist_info}/WHEEL"] = (
        "Wheel-Version: 1.0\n"
        "Generator: HebOCRBench release_packaging 1.0\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode("utf-8")
    members[f"{dist_info}/entry_points.txt"] = (
        "[console_scripts]\nhebocrbench = hebocrbench.cli:main\n"
    ).encode("utf-8")
    members[f"{dist_info}/top_level.txt"] = b"hebocrbench\n"
    members[f"{dist_info}/licenses/LICENSE"] = (root / "LICENSE").read_bytes()

    record_name = f"{dist_info}/RECORD"
    record_buffer = io.StringIO(newline="")
    writer = csv.writer(record_buffer, lineterminator="\n")
    for member_name in sorted(members):
        payload = members[member_name]
        writer.writerow([member_name, _record_digest(payload), str(len(payload))])
    writer.writerow([record_name, "", ""])
    members[record_name] = record_buffer.getvalue().encode("utf-8")

    temporary = wheel_path.with_suffix(wheel_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for member_name in sorted(members):
                executable = member_name.endswith(".py") and member_name.startswith("hebocrbench/")
                _write_zip_member(archive, member_name, members[member_name], executable=executable)
        temporary.replace(wheel_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return wheel_path


def _source_files(root: Path, output: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if _source_file_allowed(path, root, output)),
        key=lambda item: item.relative_to(root).as_posix(),
    )


def _source_file_allowed(path: Path, root: Path, output: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if path.is_symlink() or not path.is_file():
        return False
    if any(part in _SOURCE_EXCLUDED_DIRS for part in relative.parts[:-1]):
        return False
    if relative.name in {".DS_Store", ".git"}:
        return False
    if path.suffix.lower() in _SOURCE_EXCLUDED_SUFFIXES:
        return False
    try:
        path.relative_to(output)
    except ValueError:
        pass
    else:
        return False
    return True


def build_source_zip(project_root: str | Path, output_dir: str | Path) -> Path:
    """Create a deterministic source archive with caches, data downloads and fonts excluded."""

    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = _project(root)
    version = _require_text(project, "version")
    if version != "1.0.0":
        raise ReleasePackagingError(f"Refusing to build non-v1 source archive: {version}")
    archive_path = output / f"HebOCRBench-v{version}.zip"
    prefix = f"HebOCRBench-v{version}"
    files = _source_files(root, output)
    temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
                relative = path.relative_to(root).as_posix()
                executable = relative.startswith("scripts/") and path.suffix == ".py"
                _write_zip_member(
                    archive,
                    f"{prefix}/{relative}",
                    path.read_bytes(),
                    executable=executable,
                )
        temporary.replace(archive_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return archive_path


def build_source_tar_gz(project_root: str | Path, output_dir: str | Path) -> Path:
    """Create a deterministic gzip-compressed source tarball."""

    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = _project(root)
    version = _require_text(project, "version")
    if version != "1.0.0":
        raise ReleasePackagingError(f"Refusing to build non-v1 source archive: {version}")
    archive_path = output / f"HebOCRBench-v{version}.tar.gz"
    prefix = f"HebOCRBench-v{version}"
    files = _source_files(root, output)
    temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(
                    mode="w", fileobj=compressed, format=tarfile.PAX_FORMAT
                ) as archive:
                    for path in files:
                        relative = path.relative_to(root).as_posix()
                        payload = path.read_bytes()
                        info = tarfile.TarInfo(f"{prefix}/{relative}")
                        info.size = len(payload)
                        info.mtime = 0
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        info.mode = (
                            0o755
                            if relative.startswith("scripts/") and path.suffix == ".py"
                            else 0o644
                        )
                        archive.addfile(info, io.BytesIO(payload))
        temporary.replace(archive_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return archive_path


def _distribution_license(distribution_name: str) -> tuple[str | None, str | None]:
    try:
        distribution = metadata.distribution(distribution_name)
    except metadata.PackageNotFoundError:
        return None, None
    license_name = distribution.metadata.get("License-Expression") or distribution.metadata.get(
        "License"
    )
    return distribution.version, license_name


def write_sbom(project_root: str | Path, output_path: str | Path) -> Path:
    """Write a compact JSON SBOM containing declared and observed dependencies."""

    root = Path(project_root).resolve()
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    project = _project(root)
    raw_dependencies = project.get("dependencies", [])
    dependencies: list[dict[str, object]] = []
    if isinstance(raw_dependencies, list):
        for raw in sorted(str(value) for value in raw_dependencies):
            name = _dependency_name(raw)
            installed_version, license_name = _distribution_license(name)
            dependencies.append(
                {
                    "name": name,
                    "requirement": raw,
                    "installed_version": installed_version,
                    "license": license_name,
                }
            )
    payload = {
        "schema_version": "1.0",
        "format": "HebOCRBench compact SBOM",
        "release_date": _RELEASE_DATE,
        "project": {
            "name": _require_text(project, "name"),
            "version": _require_text(project, "version"),
            "license": (
                project.get("license", {}).get("text", "MIT")
                if isinstance(project.get("license"), Mapping)
                else project.get("license", "MIT")
            ),
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": sys.executable,
        },
        "declared_dependencies": dependencies,
    }
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def write_checksums(paths: Iterable[str | Path], output_path: str | Path) -> Path:
    """Write GNU-compatible SHA-256 sums sorted by filename."""

    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[str, str]] = []
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise ReleasePackagingError(f"Cannot checksum missing file: {path}")
        entries.append((path.name, _sha256_bytes(path.read_bytes())))
    text = "".join(f"{digest}  {name}\n" for name, digest in sorted(entries))
    destination.write_text(text, encoding="utf-8")
    return destination
