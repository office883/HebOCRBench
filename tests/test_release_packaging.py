from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

from hebocrbench.release_packaging import (
    build_source_tar_gz,
    build_source_zip,
    build_wheel,
    write_checksums,
    write_sbom,
)


ROOT = Path(__file__).resolve().parents[1]


def _verify_record(archive: zipfile.ZipFile) -> None:
    record_name = next(name for name in archive.namelist() if name.endswith(".dist-info/RECORD"))
    rows = csv.reader(io.StringIO(archive.read(record_name).decode("utf-8")))
    for name, digest, size in rows:
        if not digest:
            assert name == record_name
            continue
        algorithm, encoded = digest.split("=", 1)
        assert algorithm == "sha256"
        payload = archive.read(name)
        actual = (
            base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
        )
        assert encoded == actual
        assert int(size) == len(payload)


def test_build_wheel_contains_runtime_resources_and_valid_record(tmp_path):
    wheel = build_wheel(ROOT, tmp_path)

    assert wheel.name == "hebocrbench-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "hebocrbench/__init__.py" in names
        assert "hebocrbench/data/corpus-registry.yaml" in names
        assert "hebocrbench/data/corpus-registry.lock.json" in names
        assert "hebocrbench/data/corpus-profiles.yaml" in names
        assert "hebocrbench/data/corpus-profiles.lock.json" in names
        assert "hebocrbench/data/benchmark.yaml" in names
        assert "hebocrbench/data/tracks/tracks.lock.json" in names
        assert "hebocrbench/data/tracks/modern-page-ocr-v1.yaml" in names
        assert "hebocrbench/modern_scope.py" in names
        assert "hebocrbench/modern_suite.py" in names
        assert "hebocrbench/modern_score.py" in names
        assert "hebocrbench/schemas/gold-page.schema.json" in names
        metadata_name = "hebocrbench-1.0.0.dist-info/METADATA"
        assert metadata_name in names
        metadata = archive.read(metadata_name).decode("utf-8")
        assert "Name: hebocrbench\n" in metadata
        assert "Version: 1.0.0\n" in metadata
        assert "Requires-Python: >=3.10\n" in metadata
        assert "Modern Hebrew" in metadata
        _verify_record(archive)


def test_build_source_zip_excludes_worktrees_caches_fonts_and_bytecode(tmp_path):
    fake_cache = ROOT / ".release-test-cache"
    fake_cache.mkdir(exist_ok=True)
    try:
        (fake_cache / "ignored.pyc").write_bytes(b"x")
        archive_path = build_source_zip(ROOT, tmp_path)
    finally:
        (fake_cache / "ignored.pyc").unlink(missing_ok=True)
        fake_cache.rmdir()

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert names
        assert all(name.startswith("HebOCRBench-v1.0.0/") for name in names)
        assert not any(
            "/.git/" in name or name.endswith("/.git") or "/.pytest_cache/" in name
            for name in names
        )
        assert not any("/__pycache__/" in name or name.endswith((".pyc", ".pyo")) for name in names)
        assert not any(name.lower().endswith((".ttf", ".otf", ".woff", ".woff2")) for name in names)
        assert any(name.endswith("/README.md") for name in names)
        assert any(name.endswith("/corpora/registry.lock.json") for name in names)


def test_sbom_and_checksum_manifest_are_deterministic(tmp_path):
    sbom_path = write_sbom(ROOT, tmp_path / "sbom.json")
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert sbom["project"]["name"] == "hebocrbench"
    assert sbom["project"]["version"] == "1.0.0"
    assert {item["name"] for item in sbom["declared_dependencies"]} >= {"PyYAML", "Pillow"}

    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a\n", encoding="utf-8")
    second.write_text("b\n", encoding="utf-8")
    sums = write_checksums([second, first], tmp_path / "SHA256SUMS.txt")
    lines = sums.read_text(encoding="utf-8").splitlines()
    assert lines == sorted(lines, key=lambda line: line.split("  ", 1)[1])


def test_build_source_tar_gz_is_clean_and_deterministic(tmp_path):
    first = build_source_tar_gz(ROOT, tmp_path / "first")
    second = build_source_tar_gz(ROOT, tmp_path / "second")

    assert first.name == "HebOCRBench-v1.0.0.tar.gz"
    assert (
        hashlib.sha256(first.read_bytes()).hexdigest()
        == hashlib.sha256(second.read_bytes()).hexdigest()
    )
    with tarfile.open(first, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        assert names
        assert all(name.startswith("HebOCRBench-v1.0.0/") for name in names)
        assert all(member.uid == 0 and member.gid == 0 for member in members)
        assert not any("/.git/" in name or "/__pycache__/" in name for name in names)
        assert not any(
            name.endswith((".pyc", ".pyo", ".ttf", ".otf", ".woff", ".woff2")) for name in names
        )
        assert any(name.endswith("/corpora/profiles.lock.json") for name in names)
