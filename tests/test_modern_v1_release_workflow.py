from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "modern-v1-release.yml"

MODERN_COMPONENTS = {
    "modern-bidi-v1",
    "modern-line-recognition-v1",
    "modern-page-ocr-v1",
    "modern-tables-v1",
    "modern-robustness-v1",
}
EXTENSION_COMPONENTS = {
    "modern-handwriting-v1",
    "historical-pinkas-handwriting-v1",
    "historical-hebrew-press-mixed-v1",
    "biblical-niqqud-synthetic-diagnostic-v1",
    "rashi-print-synthetic-diagnostic-v1",
}
ALL_CERTIFIED_COMPONENTS = MODERN_COMPONENTS | EXTENSION_COMPONENTS
EXTENSION_SOURCES = {
    "modern-handwriting-lines-v1",
    "historical-pinkas-handwriting-v1",
    "historical-hebrew-press-mixed-v1",
    "biblical-niqqud-synthetic-diagnostic-v1",
    "rashi-print-synthetic-diagnostic-v1",
}


def _workflow() -> tuple[dict[str, object], str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    assert isinstance(payload, dict)
    return payload, text


def _commands(payload: dict[str, object]) -> str:
    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    scripts = []
    for job in jobs.values():
        assert isinstance(job, dict)
        for step in job.get("steps", []):
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                scripts.append(step["run"])
    return "\n".join(scripts)


def _step_command(payload: dict[str, object], name: str) -> str:
    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    build = jobs["build"]
    assert isinstance(build, dict)
    matching = [
        step["run"]
        for step in build["steps"]
        if isinstance(step, dict) and step.get("name") == name
    ]
    assert len(matching) == 1
    assert isinstance(matching[0], str)
    return matching[0]


def test_modern_v1_release_uses_all_locked_shards_and_fails_closed():
    payload, text = _workflow()
    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    shard_job = jobs["shards"]
    build_job = jobs["build"]
    assert isinstance(shard_job, dict)
    assert isinstance(build_job, dict)
    assert shard_job["strategy"]["matrix"]["shard"] == list(range(8))
    assert build_job["needs"] == "shards"
    assert "continue-on-error" not in text
    assert "secrets." not in text
    assert "uses: actions/checkout@v" not in text
    assert "uses: actions/setup-python@v" not in text
    assert "uses: actions/upload-artifact@v" not in text
    assert "uses: actions/download-artifact@v" not in text
    assert "if-no-files-found: error" in text


def test_modern_v1_release_executes_the_locked_certification_path_only():
    payload, text = _workflow()
    commands = _commands(payload)
    required = (
        "scripts/materialize_selection_shard.py",
        "scripts/assemble_selection_shards.py",
        "modern-public-quality-replacements-v1",
        "scripts/build_canonical_tracks.py",
        "data freeze",
        "release certify",
        "modern-suite build",
        "modern-suite verify",
        "full-suite build",
        "full-suite verify",
        "scripts/build_v1_release.py",
        "scripts/verify_v1_release.py",
        "--modern-suite-lock",
        "--full-suite-lock",
        "--release-dir",
        "--manifest",
        "HebOCRBench-v1.0.0-component-proof.json",
        "non_modern_profiles",
    )
    for value in required:
        assert value in commands or value in text
    assert "scripts/build_modern_public_corpus.py" not in commands
    assert "scripts/run_modern_baseline.py" not in commands
    assert "--editable" not in commands
    assert "git status --porcelain" in commands
    assert '"costly_model_baselines_executed_by_this_workflow": False' in text


def test_modern_v1_release_uses_a_separate_evaluation_projection():
    payload, _ = _workflow()
    commands = _commands(payload)
    assert '--output "$RUNNER_TEMP/evaluation-roots"' in commands
    assert '--track-root "modern-page-ocr-v1=$roots/modern-page-ocr-v1"' in commands
    assert '--track-root "modern-bidi-v1=$RUNNER_TEMP/roots/modern-bidi-v1"' in commands


def test_modern_v1_release_fetches_and_verifies_every_locked_extension_source():
    payload, text = _workflow()
    command = _step_command(payload, "Fetch and verify all locked extension and diagnostic sources")
    assert "data fetch" in command
    assert "data verify" in command
    assert "--extract" in command
    for source_id in EXTENSION_SOURCES:
        assert command.count(f"--source {source_id}") == 2
    assert "locked-human-test-parquet" in command
    assert "locked-pinkas-test-webdataset" in command
    assert "locked-omilab-hazefira-page-alto-zip" in command
    assert "ssdataanalysis-hebrew-htr-curated-v1" in command
    assert "pyarrow==23.0.1" in (ROOT / ".github/modern-v1-build-constraints.txt").read_text(
        encoding="utf-8"
    )
    assert '"pyarrow": "23.0.1"' in text


def test_full_suite_build_verify_and_release_are_bound_to_all_ten_roots():
    payload, text = _workflow()
    lock_command = _step_command(
        payload, "Build and independently verify the Modern and full-suite locks"
    )
    release_command = _step_command(payload, "Build and verify release-code artifacts")
    proof_command = _step_command(payload, "Write non-redistributive certification proof bundle")

    full_suite_build, remainder = lock_command.split(
        "python -m hebocrbench full-suite verify", maxsplit=1
    )
    _, full_suite_build = full_suite_build.split(
        "python -m hebocrbench full-suite build", maxsplit=1
    )
    full_suite_verify, _ = remainder.split('python - "$suite" "$full_suite"', maxsplit=1)
    release_build, remainder = release_command.split("python - ", maxsplit=1)
    _, release_build = release_build.split("python scripts/build_v1_release.py", maxsplit=1)
    _, release_verify = remainder.split("python scripts/verify_v1_release.py", maxsplit=1)

    for component_id in ALL_CERTIFIED_COMPONENTS:
        needle = f'--component-root "{component_id}='
        assert needle in full_suite_build
        assert needle in full_suite_verify
        assert needle in release_build
        assert needle in release_verify
        assert component_id in proof_command

    assert "expected_full = expected_modern |" in lock_command
    assert "assert certified == expected_full" in lock_command
    assert "assert len(certified) == 10" in release_command
    assert 'coverage["real_public_fixed_extensions_missing"] == []' in lock_command
    assert 'coverage["synthetic_diagnostics_missing"] == []' in lock_command
    assert '"ten_component_full_suite_built": True' in text
    assert '"ten_component_full_suite_verified": True' in text
    assert "hebocrbench-v1-multi-profile-release-candidate" in text


def test_release_workflow_pins_the_final_suite_and_registry_fingerprints():
    payload, _ = _workflow()
    env = payload["env"]
    assert isinstance(env, dict)
    assert env["REGISTRY_FINGERPRINT"] == (
        "8c0cc599208d4ca1a4ef3d3ead0a57325c4eaad5f27b346714ab9b8045291bfa"
    )
    assert env["PROFILES_FINGERPRINT"] == (
        "a84ada5741ec4d314075775493bc5d57cc5ec271d3d9d495b199a4b9498da173"
    )
    assert env["MODERN_SUITE_FINGERPRINT"] == (
        "c68250ec4320485e243171b7d3f86c9b3b526f8ada317eda592cd7289f4df5ea"
    )
    assert env["FULL_SUITE_FINGERPRINT"] == (
        "6d2b847121d307b225ec7e785ded7060f40da20b1d8dee28982ef7da06e032d4"
    )
