"""Safe, license-aware acquisition of federated corpus artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import time
from typing import BinaryIO, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile

from .corpus_registry import CorpusArtifact, CorpusSource


class AcquisitionError(RuntimeError):
    """Base acquisition failure."""


class ChecksumMismatchError(AcquisitionError):
    """Artifact content does not match its registry metadata."""


class LicenseAcceptanceRequired(PermissionError):
    """Explicit acceptance is required before fetching a restricted source."""


@dataclass(frozen=True, slots=True)
class VerifiedArtifact:
    path: Path
    size_bytes: int
    sha256: str
    registry_checksum: str | None


@dataclass(frozen=True, slots=True)
class ArtifactFetchResult:
    artifact_id: str
    path: Path
    size_bytes: int
    sha256: str
    from_cache: bool
    extracted_to: Path | None = None


@dataclass(frozen=True, slots=True)
class FetchResult:
    source_id: str
    artifacts: tuple[ArtifactFetchResult, ...]

    @property
    def downloaded_count(self) -> int:
        return sum(not artifact.from_cache for artifact in self.artifacts)

    @property
    def cache_hit_count(self) -> int:
        return sum(artifact.from_cache for artifact in self.artifacts)


def _hash_file(path: Path, algorithm: str) -> str:
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise AcquisitionError(f"Unsupported checksum algorithm: {algorithm}") from exc
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(path: str | Path, artifact: CorpusArtifact) -> VerifiedArtifact:
    source = Path(path)
    if not source.is_file():
        raise AcquisitionError(f"Artifact is missing: {source}")
    size = source.stat().st_size
    if artifact.size_bytes is not None and size != artifact.size_bytes:
        raise ChecksumMismatchError(
            f"Size mismatch for {artifact.artifact_id}: expected {artifact.size_bytes}, got {size}"
        )
    registry_checksum: str | None = None
    if artifact.checksum is not None:
        actual = _hash_file(source, artifact.checksum.algorithm)
        registry_checksum = actual
        if actual.lower() != artifact.checksum.value.lower():
            raise ChecksumMismatchError(
                f"Checksum mismatch for {artifact.artifact_id}: expected "
                f"{artifact.checksum.algorithm}:{artifact.checksum.value}, got {actual}"
            )
    return VerifiedArtifact(
        path=source,
        size_bytes=size,
        sha256=_hash_file(source, "sha256"),
        registry_checksum=registry_checksum,
    )


def _safe_member_path(name: str, destination: Path) -> Path:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise AcquisitionError(f"Archive contains unsafe path: {name!r}")
    target = destination.joinpath(*pure.parts)
    resolved_destination = destination.resolve()
    resolved_target = target.resolve(strict=False)
    if (
        resolved_target != resolved_destination
        and resolved_destination not in resolved_target.parents
    ):
        raise AcquisitionError(f"Archive contains unsafe path: {name!r}")
    return target


def _zip_member_is_link(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def safe_extract(
    archive_path: str | Path,
    destination: str | Path,
    *,
    archive_type: str | None = None,
    max_members: int = 100_000,
    max_uncompressed_bytes: int = 20 * 1024 * 1024 * 1024,
) -> tuple[Path, ...]:
    """Extract ZIP/TAR without traversal, links, devices, or unbounded expansion."""

    archive = Path(archive_path)
    output = Path(destination)
    kind = (archive_type or "").lower()
    if not kind or kind == "none":
        suffixes = "".join(archive.suffixes).lower()
        if suffixes.endswith(".zip"):
            kind = "zip"
        elif suffixes.endswith((".tar.gz", ".tgz")):
            kind = "tar.gz"
        elif suffixes.endswith(".tar"):
            kind = "tar"
        elif kind == "none":
            return ()
        else:
            raise AcquisitionError(f"Cannot infer archive type for {archive}")
    output.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    total = 0

    if kind == "zip":
        with zipfile.ZipFile(archive) as handle:
            members = handle.infolist()
            if len(members) > max_members:
                raise AcquisitionError(f"Archive has too many members: {len(members)}")
            for info in members:
                if _zip_member_is_link(info):
                    raise AcquisitionError(f"Archive link is not allowed: {info.filename}")
                total += info.file_size
                if total > max_uncompressed_bytes:
                    raise AcquisitionError("Archive exceeds maximum uncompressed size")
                target = _safe_member_path(info.filename, output)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(info, "r") as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink, length=1024 * 1024)
                extracted.append(target)
        return tuple(extracted)

    if kind in {"tar", "tar.gz", "tgz"}:
        mode = "r:gz" if kind in {"tar.gz", "tgz"} else "r:"
        with tarfile.open(archive, mode) as handle:
            members = handle.getmembers()
            if len(members) > max_members:
                raise AcquisitionError(f"Archive has too many members: {len(members)}")
            for member in members:
                if member.issym() or member.islnk():
                    raise AcquisitionError(f"Archive link is not allowed: {member.name}")
                if member.isdev() or member.isfifo():
                    raise AcquisitionError(f"Archive special file is not allowed: {member.name}")
                total += max(member.size, 0)
                if total > max_uncompressed_bytes:
                    raise AcquisitionError("Archive exceeds maximum uncompressed size")
                target = _safe_member_path(member.name, output)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise AcquisitionError(f"Unsupported TAR member type: {member.name}")
                stream = handle.extractfile(member)
                if stream is None:
                    raise AcquisitionError(f"Cannot read TAR member: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream, target.open("wb") as sink:
                    shutil.copyfileobj(stream, sink, length=1024 * 1024)
                extracted.append(target)
        return tuple(extracted)

    raise AcquisitionError(f"Unsupported archive type: {kind}")


def _tree_sha256(root: Path) -> tuple[str, int]:
    """Hash a checkout by relative path and file content, excluding VCS metadata."""

    digest = hashlib.sha256()
    total = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        if relative.as_posix() == ".hebocrbench-git.json":
            continue
        encoded = relative.as_posix().encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        size = path.stat().st_size
        total += size
        digest.update(size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest(), total


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    environment = dict(os.environ)
    environment.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    try:
        process = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise AcquisitionError("Git is required to acquire this corpus source") from exc
    if process.returncode:
        detail = process.stderr.strip() or process.stdout.strip()
        raise AcquisitionError(f"Git command failed ({' '.join(args)}): {detail}")
    return process.stdout.strip()


def _lfs_pointer_paths(root: Path) -> list[Path]:
    pointers: list[Path] = []
    signature = b"version https://git-lfs.github.com/spec/v1"
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        if path.stat().st_size > 4096:
            continue
        with path.open("rb") as handle:
            if handle.read(len(signature)) == signature:
                pointers.append(path)
    return pointers


def _verify_git_checkout(target: Path, artifact: CorpusArtifact) -> VerifiedArtifact:
    marker_path = target / ".hebocrbench-git.json"
    if not (target / ".git").is_dir() or not marker_path.is_file():
        raise AcquisitionError(f"Incomplete Git cache for {artifact.artifact_id}: {target}")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    actual_revision = _run_git(["rev-parse", "HEAD"], cwd=target)
    if (
        marker.get("requested_revision") != artifact.revision
        or marker.get("resolved_revision") != actual_revision
    ):
        raise ChecksumMismatchError(
            f"Git revision mismatch for {artifact.artifact_id}: requested {artifact.revision}, "
            f"cached {actual_revision}"
        )
    tree_hash, size = _tree_sha256(target)
    if marker.get("tree_sha256") != tree_hash:
        raise ChecksumMismatchError(f"Git tree content changed for {artifact.artifact_id}")
    return VerifiedArtifact(target, size, tree_hash, actual_revision)


def _fetch_git_artifact(
    artifact: CorpusArtifact,
    target: Path,
) -> tuple[VerifiedArtifact, bool]:
    if not artifact.revision:
        raise AcquisitionError(f"Git artifact {artifact.artifact_id} has no locked revision")
    if target.exists():
        try:
            return _verify_git_checkout(target, artifact), True
        except (AcquisitionError, ChecksumMismatchError, OSError, ValueError, json.JSONDecodeError):
            shutil.rmtree(target, ignore_errors=True)

    partial = target.with_name(f".{target.name}.gitpart-{os.getpid()}")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    repository_url = artifact.url.removeprefix("git+")
    try:
        _run_git(["init", "-q"], cwd=partial)
        _run_git(["remote", "add", "origin", repository_url], cwd=partial)
        _run_git(["fetch", "--depth=1", "origin", artifact.revision], cwd=partial)
        _run_git(["checkout", "--detach", "FETCH_HEAD"], cwd=partial)
        pointers = _lfs_pointer_paths(partial)
        if pointers:
            try:
                _run_git(["lfs", "pull"], cwd=partial)
            except AcquisitionError as exc:
                examples = ", ".join(path.relative_to(partial).as_posix() for path in pointers[:3])
                raise AcquisitionError(
                    f"Git LFS is required for {artifact.artifact_id}; unresolved pointers include {examples}"
                ) from exc
            remaining = _lfs_pointer_paths(partial)
            if remaining:
                raise AcquisitionError(f"Git LFS left {len(remaining)} unresolved pointer files")
        resolved = _run_git(["rev-parse", "HEAD"], cwd=partial)
        tree_hash, size = _tree_sha256(partial)
        marker = {
            "schema_version": "1.0",
            "source_url": repository_url,
            "requested_revision": artifact.revision,
            "resolved_revision": resolved,
            "tree_sha256": tree_hash,
        }
        (partial / ".hebocrbench-git.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, target)
        return VerifiedArtifact(target, size, tree_hash, resolved), False
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise


def _open_url(url: str, timeout: float) -> BinaryIO:
    request = Request(url, headers={"User-Agent": "HebOCRBench/1.0 (+public-research benchmark)"})
    return urlopen(request, timeout=timeout)  # noqa: S310 - registry URLs are explicit inputs


def _download(
    artifact: CorpusArtifact,
    destination: Path,
    *,
    timeout: float,
    retries: int,
    opener: Callable[[str, float], BinaryIO] | None,
) -> None:
    open_resource = opener or _open_url
    last_error: Exception | None = None
    for attempt in range(max(retries, 0) + 1):
        try:
            with open_resource(artifact.url, timeout) as source, destination.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            return
        except (OSError, URLError) as exc:
            last_error = exc
            destination.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(min(0.25 * (2**attempt), 2.0))
    raise AcquisitionError(f"Failed to download {artifact.url}: {last_error}") from last_error


def fetch_source(
    source: CorpusSource,
    cache_root: str | Path,
    *,
    accepted_source_ids: set[str] | frozenset[str],
    extract: bool = False,
    timeout: float = 60.0,
    retries: int = 2,
    opener: Callable[[str, float], BinaryIO] | None = None,
) -> FetchResult:
    if source.acceptance_required and source.source_id not in accepted_source_ids:
        raise LicenseAcceptanceRequired(
            f"Source {source.source_id} requires explicit acceptance of {source.license.spdx}"
        )
    root = Path(cache_root) / source.source_id
    root.mkdir(parents=True, exist_ok=True)
    results: list[ArtifactFetchResult] = []
    for artifact in source.artifacts:
        target = root / artifact.filename
        if artifact.url.startswith("git+"):
            verified, from_cache = _fetch_git_artifact(artifact, target)
            results.append(
                ArtifactFetchResult(
                    artifact_id=artifact.artifact_id,
                    path=target,
                    size_bytes=verified.size_bytes,
                    sha256=verified.sha256,
                    from_cache=from_cache,
                    extracted_to=target,
                )
            )
            continue
        if artifact.url.startswith(("api+", "bundled:")):
            raise AcquisitionError(
                f"Artifact {artifact.artifact_id} uses a non-file acquisition scheme: {artifact.url}"
            )
        from_cache = False
        if target.exists():
            try:
                verified = verify_artifact(target, artifact)
                from_cache = True
            except ChecksumMismatchError:
                target.unlink(missing_ok=True)
            else:
                extracted_to = None
                if extract and artifact.archive not in {None, "none"}:
                    extracted_to = root / f"{artifact.artifact_id}.extracted"
                    safe_extract(target, extracted_to, archive_type=artifact.archive)
                results.append(
                    ArtifactFetchResult(
                        artifact_id=artifact.artifact_id,
                        path=target,
                        size_bytes=verified.size_bytes,
                        sha256=verified.sha256,
                        from_cache=True,
                        extracted_to=extracted_to,
                    )
                )
                continue

        partial = target.with_name(f".{target.name}.part-{os.getpid()}")
        partial.unlink(missing_ok=True)
        try:
            _download(
                artifact,
                partial,
                timeout=timeout,
                retries=retries,
                opener=opener,
            )
            verified = verify_artifact(partial, artifact)
            os.replace(partial, target)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        extracted_to = None
        if extract and artifact.archive not in {None, "none"}:
            extracted_to = root / f"{artifact.artifact_id}.extracted"
            safe_extract(target, extracted_to, archive_type=artifact.archive)
        results.append(
            ArtifactFetchResult(
                artifact_id=artifact.artifact_id,
                path=target,
                size_bytes=verified.size_bytes,
                sha256=verified.sha256,
                from_cache=from_cache,
                extracted_to=extracted_to,
            )
        )
    marker = {
        "schema_version": "1.0",
        "source_id": source.source_id,
        "source_version": source.version,
        "verification_status": "verified",
        "license": source.license.spdx,
        "artifacts": [],
    }
    registry_artifacts = {artifact.artifact_id: artifact for artifact in source.artifacts}
    for result in results:
        artifact = registry_artifacts[result.artifact_id]
        checksum = None
        if artifact.checksum is not None:
            checksum = {
                "algorithm": artifact.checksum.algorithm,
                "value": artifact.checksum.value,
            }
        marker["artifacts"].append(
            {
                "artifact_id": result.artifact_id,
                "source_url": artifact.url,
                "requested_revision": artifact.revision,
                "registry_checksum": checksum,
                "actual_sha256": result.sha256,
                "size_bytes": result.size_bytes,
            }
        )
    marker["artifacts"].sort(key=lambda item: item["artifact_id"])
    marker_path = root / ".hebocrbench-source.json"
    temporary_marker = root / f".{marker_path.name}.tmp-{os.getpid()}"
    temporary_marker.write_text(
        json.dumps(marker, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_marker, marker_path)
    return FetchResult(source_id=source.source_id, artifacts=tuple(results))


def verify_source_cache(source: CorpusSource, cache_root: str | Path) -> FetchResult:
    """Verify every cached artifact without network access."""

    root = Path(cache_root) / source.source_id
    results: list[ArtifactFetchResult] = []
    for artifact in source.artifacts:
        target = root / artifact.filename
        if artifact.url.startswith("git+"):
            verified = _verify_git_checkout(target, artifact)
            extracted_to = target
        elif artifact.url.startswith(("api+", "bundled:")):
            if artifact.required:
                raise AcquisitionError(
                    f"Artifact {artifact.artifact_id} cannot be verified as a local file: {artifact.url}"
                )
            continue
        else:
            verified = verify_artifact(target, artifact)
            extracted_to = None
        results.append(
            ArtifactFetchResult(
                artifact_id=artifact.artifact_id,
                path=target,
                size_bytes=verified.size_bytes,
                sha256=verified.sha256,
                from_cache=True,
                extracted_to=extracted_to,
            )
        )
    return FetchResult(source_id=source.source_id, artifacts=tuple(results))
