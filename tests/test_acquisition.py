from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import tarfile
import zipfile

import pytest

from hebocrbench.acquisition import (
    AcquisitionError,
    ChecksumMismatchError,
    LicenseAcceptanceRequired,
    fetch_source,
    safe_extract,
    verify_artifact,
)
from hebocrbench.corpus_registry import (
    ArtifactChecksum,
    CorpusArtifact,
    CorpusLicense,
    CorpusSource,
)


def _source(artifact: CorpusArtifact, *, acceptance: bool = False) -> CorpusSource:
    return CorpusSource(
        source_id="fixture-source",
        title="Fixture source",
        version="1",
        task="end_to_end_ocr",
        track="fixture",
        languages=("he",),
        script="Hebr",
        status="core",
        converter="pagexml",
        homepage="https://example.invalid",
        citation={"key": "fixture", "text": "Fixture"},
        license=CorpusLicense(
            spdx="CC-BY-NC-SA-4.0" if acceptance else "CC-BY-4.0",
            tier="research-nc" if acceptance else "open",
            redistribution="conditional" if acceptance else "allowed",
            requires_acceptance=acceptance,
        ),
        artifacts=(artifact,),
        discovery={"annotation_globs": ["**/*.xml"], "image_roots": ["."]},
        split={"strategy": "none", "group_fields": ["document_id"]},
        metadata={
            "script_style": "fixture",
            "era": "modern",
            "document_type": "fixture",
            "layout_type": "single_column",
            "vocalization": "none",
            "source_type": "fixture",
        },
    )


def _artifact_for(path: Path, *, archive: str = "none") -> CorpusArtifact:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return CorpusArtifact(
        artifact_id="archive",
        url=path.resolve().as_uri(),
        filename=path.name,
        archive=archive,
        checksum=ArtifactChecksum("sha256", digest),
        size_bytes=path.stat().st_size,
    )


def test_fetch_source_downloads_atomically_verifies_and_reuses_cache(tmp_path):
    upstream = tmp_path / "upstream.bin"
    upstream.write_bytes(b"real corpus bytes")
    source = _source(_artifact_for(upstream))
    cache = tmp_path / "cache"

    first = fetch_source(source, cache, accepted_source_ids=set())
    cached = cache / "fixture-source" / "upstream.bin"

    assert first.downloaded_count == 1
    assert first.cache_hit_count == 0
    assert cached.read_bytes() == b"real corpus bytes"
    assert not list(cache.rglob("*.part"))

    second = fetch_source(source, cache, accepted_source_ids=set())
    assert second.downloaded_count == 0
    assert second.cache_hit_count == 1
    assert second.artifacts[0].sha256 == hashlib.sha256(b"real corpus bytes").hexdigest()


def test_fetch_source_requires_explicit_acceptance_for_nc_source(tmp_path):
    upstream = tmp_path / "data.bin"
    upstream.write_bytes(b"licensed")
    source = _source(_artifact_for(upstream), acceptance=True)

    with pytest.raises(LicenseAcceptanceRequired, match="fixture-source"):
        fetch_source(source, tmp_path / "cache", accepted_source_ids=set())

    result = fetch_source(
        source,
        tmp_path / "cache",
        accepted_source_ids={"fixture-source"},
    )
    assert result.downloaded_count == 1


def test_verify_artifact_rejects_checksum_and_size_mismatch(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"wrong")
    artifact = CorpusArtifact(
        artifact_id="archive",
        url=path.as_uri(),
        filename=path.name,
        archive="none",
        checksum=ArtifactChecksum("sha256", "0" * 64),
        size_bytes=999,
    )

    with pytest.raises(ChecksumMismatchError):
        verify_artifact(path, artifact)


def test_safe_extract_zip_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "no")

    with pytest.raises(AcquisitionError, match="unsafe path"):
        safe_extract(archive, tmp_path / "out", archive_type="zip")
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extract_tar_rejects_symlinks(tmp_path):
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as handle:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        handle.addfile(info)

    with pytest.raises(AcquisitionError, match="link"):
        safe_extract(archive, tmp_path / "out", archive_type="tar")


def test_safe_extract_zip_enforces_uncompressed_size_limit(tmp_path):
    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("data.txt", "x" * 128)

    with pytest.raises(AcquisitionError, match="uncompressed size"):
        safe_extract(archive, tmp_path / "out", archive_type="zip", max_uncompressed_bytes=64)


def test_fetch_source_removes_partial_file_after_failure(tmp_path):
    upstream = tmp_path / "data.bin"
    upstream.write_bytes(b"payload")
    artifact = replace(_artifact_for(upstream), checksum=ArtifactChecksum("sha256", "f" * 64))

    with pytest.raises(ChecksumMismatchError):
        fetch_source(_source(artifact), tmp_path / "cache", accepted_source_ids=set())

    assert not list((tmp_path / "cache").rglob("*.part"))


def test_fetch_source_clones_git_artifact_at_locked_revision(tmp_path):
    import subprocess

    upstream = tmp_path / "upstream-repo"
    upstream.mkdir()
    subprocess.run(["git", "init", "-q", str(upstream)], check=True)
    subprocess.run(
        ["git", "-C", str(upstream), "config", "user.email", "fixture@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(upstream), "config", "user.name", "Fixture"], check=True)
    (upstream / "page.txt").write_text("שלום 2026\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(upstream), "add", "page.txt"], check=True)
    subprocess.run(["git", "-C", str(upstream), "commit", "-q", "-m", "fixture"], check=True)
    revision = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    artifact = CorpusArtifact(
        artifact_id="git-repo",
        url="git+" + upstream.resolve().as_uri(),
        filename="checkout",
        archive="none",
        checksum=None,
        revision=revision,
    )

    first = fetch_source(_source(artifact), tmp_path / "cache", accepted_source_ids=set())
    checkout = tmp_path / "cache" / "fixture-source" / "checkout"

    assert first.downloaded_count == 1
    assert (checkout / "page.txt").read_text(encoding="utf-8") == "שלום 2026\n"
    assert (
        subprocess.check_output(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
        ).strip()
        == revision
    )

    second = fetch_source(_source(artifact), tmp_path / "cache", accepted_source_ids=set())
    assert second.cache_hit_count == 1
    assert second.artifacts[0].sha256 == first.artifacts[0].sha256


def test_fetch_source_writes_reproducible_verification_manifest(tmp_path):
    upstream = tmp_path / "payload.bin"
    upstream.write_bytes(b"verified source")
    source = _source(_artifact_for(upstream))

    fetch_source(source, tmp_path / "cache", accepted_source_ids=set())

    marker = tmp_path / "cache" / "fixture-source" / ".hebocrbench-source.json"
    payload = __import__("json").loads(marker.read_text(encoding="utf-8"))
    assert payload["source_id"] == "fixture-source"
    assert payload["verification_status"] == "verified"
    assert payload["artifacts"][0]["artifact_id"] == "archive"
    assert (
        payload["artifacts"][0]["actual_sha256"] == hashlib.sha256(b"verified source").hexdigest()
    )
