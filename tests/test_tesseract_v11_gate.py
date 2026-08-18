from pathlib import Path

from hebocrbench.tesseract_v11_gate import (
    RUN_BRANCH,
    is_certified_release_invocation,
)


def _final_args(runner_temp: Path) -> list[str]:
    return [
        "/repo/scripts/build_v1_release.py",
        "--root",
        "/repo",
        "--output",
        str(runner_temp / "release"),
        "--modern-suite-lock",
        str(runner_temp / "modern-suite-v1.lock.json"),
        "--full-suite-lock",
        str(runner_temp / "full-suite-v1.lock.json"),
        "--component-root",
        f"modern-bidi-v1={runner_temp / 'roots' / 'modern-bidi-v1'}",
    ]


def test_gate_accepts_only_real_final_release_paths(tmp_path: Path) -> None:
    env = {
        "GITHUB_REF": f"refs/heads/{RUN_BRANCH}",
        "RUNNER_TEMP": str(tmp_path),
    }

    assert is_certified_release_invocation(_final_args(tmp_path), env)


def test_gate_rejects_release_smoke_fixture_output(tmp_path: Path) -> None:
    env = {
        "GITHUB_REF_NAME": RUN_BRANCH,
        "RUNNER_TEMP": str(tmp_path / "runner-temp"),
    }
    fixture_args = _final_args(tmp_path / "runner-temp")
    fixture_args[fixture_args.index("--output") + 1] = str(tmp_path / "pytest-fixture" / "release")

    assert not is_certified_release_invocation(fixture_args, env)


def test_gate_rejects_main_branch_and_missing_runner_temp(tmp_path: Path) -> None:
    args = _final_args(tmp_path)

    assert not is_certified_release_invocation(
        args,
        {"GITHUB_REF": "refs/heads/main", "RUNNER_TEMP": str(tmp_path)},
    )
    assert not is_certified_release_invocation(
        args,
        {"GITHUB_REF": f"refs/heads/{RUN_BRANCH}"},
    )


def test_gate_accepts_equals_form_options(tmp_path: Path) -> None:
    args = [
        "/repo/scripts/build_v1_release.py",
        f"--output={tmp_path / 'release'}",
        f"--modern-suite-lock={tmp_path / 'modern-suite-v1.lock.json'}",
        f"--full-suite-lock={tmp_path / 'full-suite-v1.lock.json'}",
    ]
    env = {
        "GITHUB_REF_NAME": RUN_BRANCH,
        "RUNNER_TEMP": str(tmp_path),
    }

    assert is_certified_release_invocation(args, env)
