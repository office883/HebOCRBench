from pathlib import Path

import pytest

from hebocrbench.tesseract_v11_release_hook import (
    EXPECTED_MODERN_TRACKS,
    git_blob_sha1,
    parse_release_invocation,
)


def test_git_blob_sha1_uses_git_object_identity() -> None:
    assert git_blob_sha1(b"test content\n") == "d670460b4b4aece5915caf5c68d12f560a9fe3e4"


def test_parse_release_invocation_selects_only_modern_roots(tmp_path: Path) -> None:
    suite = tmp_path / "suite.json"
    args = ["--modern-suite-lock", str(suite)]
    for track_id in EXPECTED_MODERN_TRACKS:
        args.extend(["--component-root", f"{track_id}={tmp_path / track_id}"])
    args.extend(["--component-root", f"modern-handwriting-v1={tmp_path / 'handwriting'}"])

    observed_suite, roots = parse_release_invocation(args)

    assert observed_suite == suite.resolve()
    assert tuple(roots) == EXPECTED_MODERN_TRACKS
    assert all(path.is_absolute() for path in roots.values())


def test_parse_release_invocation_rejects_missing_modern_track(tmp_path: Path) -> None:
    suite = tmp_path / "suite.json"
    args = ["--modern-suite-lock", str(suite)]
    for track_id in EXPECTED_MODERN_TRACKS[:-1]:
        args.extend(["--component-root", f"{track_id}={tmp_path / track_id}"])

    with pytest.raises(RuntimeError, match="missing Modern component roots"):
        parse_release_invocation(args)
