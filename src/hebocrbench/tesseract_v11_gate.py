"""Exact invocation gate for the one-off full Tesseract benchmark hook."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import sys


RUN_BRANCH = "agent/tesseract-v1-1-run"


def _option_value(argv: Sequence[str], option: str) -> str | None:
    values = list(argv)
    for index, value in enumerate(values):
        if value == option:
            if index + 1 >= len(values):
                return None
            return values[index + 1]
        prefix = option + "="
        if value.startswith(prefix):
            return value[len(prefix) :]
    return None


def is_certified_release_invocation(
    argv: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return true only for the real final release build on the isolated branch.

    Release-pipeline smoke tests also execute ``build_v1_release.py`` with tiny
    fixture roots. They must never trigger a 34,267-input OCR run. The real
    workflow uses three deterministic paths below ``RUNNER_TEMP``; requiring all
    three distinguishes it from every fixture invocation without weakening the
    benchmark or peeking at gold content.
    """

    arguments = list(sys.argv if argv is None else argv)
    environment = os.environ if env is None else env
    branch_matches = (
        environment.get("GITHUB_REF_NAME") == RUN_BRANCH
        or environment.get("GITHUB_REF") == f"refs/heads/{RUN_BRANCH}"
    )
    if not branch_matches or not arguments:
        return False
    if Path(arguments[0]).name != "build_v1_release.py":
        return False

    raw_runner_temp = environment.get("RUNNER_TEMP")
    if not raw_runner_temp:
        return False
    runner_temp = Path(raw_runner_temp).expanduser().resolve()

    expected = {
        "--output": runner_temp / "release",
        "--modern-suite-lock": runner_temp / "modern-suite-v1.lock.json",
        "--full-suite-lock": runner_temp / "full-suite-v1.lock.json",
    }
    for option, expected_path in expected.items():
        raw_value = _option_value(arguments[1:], option)
        if raw_value is None:
            return False
        if Path(raw_value).expanduser().resolve() != expected_path:
            return False
    return True
