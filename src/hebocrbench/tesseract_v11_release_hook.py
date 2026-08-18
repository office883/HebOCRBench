"""One-off, branch-gated Tesseract 5.5.3 baseline hook for the v1.1 BiDi contract.

This module is intentionally activated only by ``src/sitecustomize.py`` while
``scripts/build_v1_release.py`` is running on the isolated baseline branch. It
keeps raw predictions under ``RUNNER_TEMP`` and writes only a verified compact
pack below the already-uploaded ``sanity`` directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Iterable, Sequence


RUN_BRANCH = "agent/tesseract-v1-1-run"
TESSERACT_VERSION = "5.5.3"
TESSDATA_REPOSITORY = "tesseract-ocr/tessdata_fast"
TESSDATA_REVISION = "87416418657359cb625c412a48b6e1d6d41c29bd"
TESSDATA_FILES = {
    "heb": {
        "git_blob_sha1": "7356caf3cddc9c867fe6727e17726727b8284608",
        "size_bytes": 961404,
    },
    "eng": {
        "git_blob_sha1": "bbef4675053b5b468cdb477053e28b1c698ba08e",
        "size_bytes": 4113088,
    },
}
RESULT_NAME = "tesseract-fast-5.5.3-bidi-1.1.0"
EXPECTED_MODERN_TRACKS = (
    "modern-bidi-v1",
    "modern-line-recognition-v1",
    "modern-page-ocr-v1",
    "modern-tables-v1",
    "modern-robustness-v1",
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def git_blob_sha1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()  # noqa: S324 - Git identity


def parse_release_invocation(argv: Sequence[str]) -> tuple[Path, dict[str, Path]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--modern-suite-lock", type=Path, required=True)
    parser.add_argument("--component-root", action="append", required=True)
    args, _ = parser.parse_known_args(list(argv))

    roots: dict[str, Path] = {}
    for value in args.component_root:
        if "=" not in value:
            raise RuntimeError(f"invalid --component-root value: {value!r}")
        track_id, raw_path = value.split("=", 1)
        if not track_id or not raw_path or track_id in roots:
            raise RuntimeError(f"invalid or duplicate component root: {value!r}")
        roots[track_id] = Path(raw_path).expanduser().resolve()

    missing = sorted(set(EXPECTED_MODERN_TRACKS) - set(roots))
    if missing:
        raise RuntimeError("missing Modern component roots: " + ", ".join(missing))
    return args.modern_suite_lock.expanduser().resolve(), {
        track_id: roots[track_id] for track_id in EXPECTED_MODERN_TRACKS
    }


def _run(
    command: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("[tesseract-v1.1-hook] $", " ".join(command), flush=True)
    return subprocess.run(
        list(command),
        check=True,
        text=True,
        env=env,
        capture_output=capture_output,
    )


def _install_tesseract() -> tuple[str, str]:
    brew = shutil.which("brew")
    if brew is None:
        raise RuntimeError("Homebrew is required on the certified macOS runner")
    env = dict(os.environ)
    env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
    _run([brew, "install", "tesseract"], env=env)

    executable = shutil.which("tesseract")
    if executable is None:
        raise RuntimeError("Homebrew completed but tesseract is not on PATH")
    version = _run([executable, "--version"], env=env, capture_output=True).stdout.splitlines()[0]
    expected = f"tesseract {TESSERACT_VERSION}"
    if version != expected:
        raise RuntimeError(f"expected {expected!r}, got {version!r}")
    return executable, version


def _download_tessdata(root: Path) -> dict[str, dict[str, object]]:
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("curl is required to fetch pinned traineddata")
    root.mkdir(parents=True, exist_ok=True)
    environment: dict[str, dict[str, object]] = {}

    for language, expected in TESSDATA_FILES.items():
        destination = root / f"{language}.traineddata"
        temporary = destination.with_suffix(destination.suffix + ".part")
        url = (
            "https://raw.githubusercontent.com/"
            f"{TESSDATA_REPOSITORY}/{TESSDATA_REVISION}/{language}.traineddata"
        )
        _run(
            [
                curl,
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--retry",
                "5",
                "--retry-all-errors",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                url,
                "--output",
                str(temporary),
            ]
        )
        payload = temporary.read_bytes()
        if len(payload) != expected["size_bytes"]:
            raise RuntimeError(
                f"{language}.traineddata size mismatch: "
                f"expected {expected['size_bytes']}, got {len(payload)}"
            )
        observed_blob = git_blob_sha1(payload)
        if observed_blob != expected["git_blob_sha1"]:
            raise RuntimeError(
                f"{language}.traineddata Git blob mismatch: "
                f"expected {expected['git_blob_sha1']}, got {observed_blob}"
            )
        temporary.replace(destination)
        environment[language] = {
            **expected,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    return environment


def _assert_complete_run(summary: dict[str, object]) -> tuple[dict[str, object], int]:
    tracks = summary.get("tracks")
    if not isinstance(tracks, dict) or set(tracks) != set(EXPECTED_MODERN_TRACKS):
        raise RuntimeError("baseline summary does not contain exactly five Modern tracks")

    selected = 0
    failures = 0
    api_failures = 0
    for track_id, value in tracks.items():
        if not isinstance(value, dict):
            raise RuntimeError(f"invalid track summary for {track_id}")
        selected += int(value.get("selected_pages", -1))
        failures += int(value.get("failures", -1))
        api_failures += int(value.get("api_failures", -1))
    if selected != 34267:
        raise RuntimeError(f"expected 34,267 evaluated inputs, got {selected}")
    if failures != 0 or api_failures != 0:
        raise RuntimeError(f"baseline has failures: runner={failures}, api={api_failures}")

    score = summary.get("modern_score")
    if not isinstance(score, dict):
        raise RuntimeError("baseline summary has no Modern score")
    if score.get("missing_tracks") != []:
        raise RuntimeError(f"Modern score is missing tracks: {score.get('missing_tracks')}")
    admission = score.get("score_admission")
    if not isinstance(admission, dict):
        raise RuntimeError("Modern score has no score_admission evidence")
    required_admission = {
        "status": "verified_recomputed",
        "artifact_hashes_verified": True,
        "component_roots_verified": True,
        "blind_input_contract_verified": True,
        "metrics_recomputed": True,
        "gold_assistance": False,
        "oracle_layout": False,
    }
    for field, expected in required_admission.items():
        if admission.get(field) != expected:
            raise RuntimeError(
                f"score_admission.{field} must be {expected!r}, got {admission.get(field)!r}"
            )
    return score, selected


def _run_full_baseline() -> None:
    from hebocrbench.baseline_runner import BaselineSettings, run_modern_baseline_suite
    from hebocrbench.public_results import (
        build_public_results_pack,
        verify_public_results_pack,
    )

    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    suite_lock, track_roots = parse_release_invocation(os.sys.argv[1:])
    executable, version_line = _install_tesseract()

    tessdata_root = runner_temp / f"tessdata-fast-{TESSDATA_REVISION}"
    traineddata = _download_tessdata(tessdata_root)
    previous_tessdata = os.environ.get("TESSDATA_PREFIX")
    os.environ["TESSDATA_PREFIX"] = str(tessdata_root)
    try:
        languages = _run(
            [executable, "--list-langs"],
            env=dict(os.environ),
            capture_output=True,
        ).stdout.splitlines()
        if "heb" not in languages or "eng" not in languages:
            raise RuntimeError(f"pinned tessdata is not visible to Tesseract: {languages}")

        run_root = runner_temp / "tesseract-private-run-v1.1.0"
        if run_root.exists():
            shutil.rmtree(run_root)
        model_version = (
            f"{TESSERACT_VERSION}+tessdata_fast@{TESSDATA_REVISION}"
            f"+heb-{traineddata['heb']['sha256']}"
            f"+eng-{traineddata['eng']['sha256']}"
        )
        settings = BaselineSettings(
            engine="tesseract",
            model_version=model_version,
            timeout_seconds=120.0,
            tesseract_executable=executable,
            tesseract_language="heb+eng",
            tesseract_page_psm=3,
            tesseract_line_psm=7,
        )
        summary = run_modern_baseline_suite(
            track_roots,
            suite_lock,
            run_root,
            settings=settings,
            workers=4,
        )
        score, selected = _assert_complete_run(summary)

        bundle_root = runner_temp / "sanity" / "tesseract-fast-v1.1.0"
        if bundle_root.exists():
            shutil.rmtree(bundle_root)
        public_root = bundle_root / "public-pack"
        build_public_results_pack(
            {RESULT_NAME: run_root},
            public_root,
            clean=True,
        )
        manifest = verify_public_results_pack(public_root)

        environment = {
            "schema_version": "1.0",
            "engine": {
                "name": "Tesseract",
                "version": TESSERACT_VERSION,
                "version_line": version_line,
            },
            "traineddata": {
                "repository": TESSDATA_REPOSITORY,
                "revision": TESSDATA_REVISION,
                "languages": traineddata,
            },
            "language_expression": "heb+eng",
            "page_psm": 3,
            "line_psm": 7,
            "workers": 4,
            "timeout_seconds": 120.0,
            "github": {
                "repository": os.environ.get("GITHUB_REPOSITORY"),
                "sha": os.environ.get("GITHUB_SHA"),
                "ref_name": os.environ.get("GITHUB_REF_NAME"),
                "run_id": os.environ.get("GITHUB_RUN_ID"),
            },
        }
        run_summary = {
            "schema_version": "1.0",
            "benchmark_contract": "modern-bidi-v1@1.1.0",
            "result_name": RESULT_NAME,
            "evaluated_items": selected,
            "runner_failures": 0,
            "api_failures": 0,
            "status": score.get("status"),
            "headline_score": score.get("headline_score"),
            "bidi_quality_status": score.get("bidi_quality_status"),
            "quality_warnings": score.get("quality_warnings", []),
            "suite_fingerprint": score.get("suite_fingerprint"),
            "pack_fingerprint": manifest.get("pack_fingerprint"),
            "model": summary.get("model"),
        }
        _write_json(bundle_root / "tesseract-environment.json", environment)
        _write_json(bundle_root / "run-summary.json", run_summary)
        (bundle_root / "READY_TO_PUBLISH.txt").write_text(
            "Verified compact results for ssdataanalysis/hebocrbench-v1-results\n"
            "Target path: releases/v1.1.0/tesseract-fast-5.5.3\n"
            "Suggested tag: tesseract-v1.1.0\n",
            encoding="utf-8",
        )
        print(
            "[tesseract-v1.1-hook] completed:",
            json.dumps(run_summary, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
    finally:
        if previous_tessdata is None:
            os.environ.pop("TESSDATA_PREFIX", None)
        else:
            os.environ["TESSDATA_PREFIX"] = previous_tessdata


def install_release_hook() -> bool:
    """Patch release checksum finalization exactly once on the isolated branch."""

    if not (
        os.environ.get("GITHUB_REF_NAME") == RUN_BRANCH
        or os.environ.get("GITHUB_REF") == f"refs/heads/{RUN_BRANCH}"
    ):
        return False
    if Path(os.sys.argv[0]).name != "build_v1_release.py":
        return False

    from hebocrbench import release_packaging

    original: Callable[..., Path] = release_packaging.write_checksums
    if getattr(original, "_tesseract_v11_hook", False):
        return True

    def wrapped(paths: Iterable[Path], output: Path) -> Path:
        result = original(paths, output)
        _run_full_baseline()
        return result

    setattr(wrapped, "_tesseract_v11_hook", True)
    release_packaging.write_checksums = wrapped
    print("[tesseract-v1.1-hook] installed", flush=True)
    return True
