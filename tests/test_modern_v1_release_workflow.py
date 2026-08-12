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


def test_modern_v1_release_pins_and_probes_the_pdf_extraction_runtime():
    payload, _ = _workflow()
    env = payload["env"]
    assert isinstance(env, dict)
    assert env["PYTHON_VERSION"] == "3.12.13"
    assert env["PYTHON_RUNTIME_URL"] == (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        "20260303/cpython-3.12.13%2B20260303-aarch64-apple-darwin-"
        "install_only_stripped.tar.gz"
    )
    assert env["PYTHON_RUNTIME_SHA256"] == (
        "377234f346fce41b6d3112b5ead89cb6af2d5596244f9edc1a739065770dde1f"
    )
    assert env["PYTHON_RUNTIME_SIZE"] == "17665685"

    jobs = payload["jobs"]
    assert isinstance(jobs, dict)
    build = jobs["build"]
    assert isinstance(build, dict)
    assert build["runs-on"] == "macos-26"
    steps = {
        step["name"]: step
        for step in build["steps"]
        if isinstance(step, dict) and isinstance(step.get("name"), str)
    }
    python_setup = _step_command(payload, "Install the exact canonical Python runtime")
    assert "actions/setup-python" not in str(steps["Install the exact canonical Python runtime"])
    for value in (
        "stat -f '%z'",
        "shasum -a 256 --check --strict",
        'platform.python_version() == "3.12.13"',
        "3.12.13 (main, Mar  3 2026, 15:35:03) [Clang 21.1.4 ]",
        'sysconfig.get_config_var("HOST_GNU_TYPE") == "aarch64-apple-darwin"',
        "eb9d74b9c7cfdfb2c9b91614edb2c3607360ba46c5aa7fc4557b3a4a23e97cff",
        "0d755ba198b32fa06bea90d801ec33a4a83961e55839b16a8e322dc23ed74e48",
        "39ecccd16d25f60793675a79ec545ba50bdd10bd4c13f756ee9a50ab485cc6ee",
        'ssl.OPENSSL_VERSION == "OpenSSL 3.5.5 27 Jan 2026"',
        'pip.__version__ == "26.0.1"',
        '>> "$GITHUB_PATH"',
    ):
        assert value in python_setup
    setup = steps["Set up the pinned Poppler text extractor"]
    assert setup["uses"] == ("mamba-org/setup-micromamba@f457c30a868e4760d3a6fcea5f25dc655b8edf39")
    assert setup["with"]["micromamba-version"] == "2.3.2-0"
    assert setup["with"]["environment-name"] == "hebocrbench-poppler"
    assert "poppler=26.07.0=h4cfec15_3" in setup["with"]["create-args"]
    assert setup["with"]["init-shell"] == "none"

    expose = _step_command(payload, "Expose and verify the pinned Poppler executable")
    assert 'test "$version" = "pdftotext version 26.07.0"' in expose
    assert '>> "$GITHUB_PATH"' in expose
    install = _step_command(payload, "Install the deterministic extraction stack")
    assert "poppler-utils" not in install
    assert "apt-get" not in install
    assert "brew update" in install
    assert "export HOMEBREW_NO_AUTO_UPDATE=1" in install
    assert "https://formulae.brew.sh/api/formula/harfbuzz.json" in install
    assert 'payload["versions"]["stable"] == "14.3.0"' in install
    assert "a4d727f73af8892743817d9557e139866060de41302e1e6461908e9d31e2aa0a" in install
    assert "188aea0a97665d3a2a39ed72b37b249252f25ae92f84e4c9d4054f004b27f936" in install
    assert "brew fetch --force --bottle-tag=arm64_tahoe harfbuzz" in install
    assert "brew --cache --bottle-tag=arm64_tahoe harfbuzz" in install
    assert "brew upgrade harfbuzz" in install
    assert "brew install libraqm jpeg-turbo libtiff openjpeg" in install
    assert "test -x /usr/bin/ar" in install
    assert "--no-cache-dir" in install
    assert "--no-binary Pillow" in install
    assert "export AR=/usr/bin/ar" in install
    assert "export ARFLAGS=rcs" in install
    for value in (
        'assert platform.system() == "Darwin"',
        'assert platform.machine() == "arm64"',
        'features.version("raqm") == "0.11.0"',
        'features.version("freetype2") == "2.14.3"',
        'features.version("jpg") == "8.0"',
        'features.version("jpg_2000") == "2.5.4"',
        'features.version("libtiff") == "4.7.2"',
        '"harfbuzz 14.3.0"',
        '"jpeg-turbo 3.2.0"',
        '"libtiff 4.7.2"',
        '"openjpeg 2.5.4"',
    ):
        assert value in install
    assert 'test "$(pdftotext -v 2>&1 | head -n 1)" = "pdftotext version 26.07.0"' in install

    probe = _step_command(payload, "Verify the canonical PDF extraction contract")
    for value in (
        "knesset-bill-12458079.pdf",
        "6c584719459959d456ad8efce4373960fc99f75767018eec5139e9ed7b7a20c3",
        "f487d38f6be4dfc46d324cf8d534c31030a5ab69eb337fb634bc7b6bf3d12d25",
        "5f0f489a303487bc96699261228c662f4e2569d37f2a67591dc1aeb902308d07",
        "df30e112805ae5fd61035d28e7938c0344087b4694bd646146ac84df3aad271f",
        "365988",
        "0.9829351535836177",
        "0.9715698393077874",
        "minimum=0.98",
    ):
        assert value in probe


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


def test_release_workflow_prepopulates_exact_pinned_public_source_mirror():
    payload, _ = _workflow()
    env = payload["env"]
    assert isinstance(env, dict)
    assert env["SOURCE_MIRROR_REPOSITORY"] == ("ssdataanalysis/hebocrbench-v1-sources")
    assert env["SOURCE_MIRROR_REVISION"] == ("3d6dcbfedeeeb1234db84131abc92272abff0625")

    mirror = _step_command(
        payload, "Prepopulate locked extension sources from pinned public mirror"
    )
    expected = {
        "foundation/test-synthetic-mixed-000.tar": (
            "64563200",
            "12886b77eefb54f73ed2ea9ba9ddf4766de60ed2635126248344739626608927",
        ),
        "foundation/train-niqqud-000.tar": (
            "318689280",
            "05cd60b91ce566b23dd7024665026a615f2127b7abfaf8e8a10afd92d3945ff4",
        ),
        "foundation/train-rashi-000.tar": (
            "118077440",
            "f1adca1ba117160266325b2002abe62896f06986242ef145a34296852f7190ee",
        ),
        "htr/stage3_human_finetune/test-00000.parquet": (
            "18939757",
            "19823993891409e7fc90cac38230822cb88dd2010955a53d4618bfbc226f7d45",
        ),
        "pinkas/historical-pinkas-handwriting-test-v1.tar": (
            "418498560",
            "d986a3527d1ddae19cf2f09f3ff5e84458eeb5e1f6f9cb4e2a48d895dfcd5eb6",
        ),
    }
    assert mirror.count("download_locked \\") == 5
    assert "install -D" not in mirror
    assert "shutil.copyfile" in mirror
    for path, (size, sha256) in expected.items():
        assert path in mirror
        assert size in mirror
        assert sha256 in mirror

    fetch = _step_command(payload, "Fetch and verify all locked extension and diagnostic sources")
    expected_hits = {
        '"modern-handwriting-lines-v1": (1, 0)',
        '"historical-pinkas-handwriting-v1": (1, 0)',
        '"biblical-niqqud-synthetic-diagnostic-v1": (2, 0)',
        '"rashi-print-synthetic-diagnostic-v1": (2, 0)',
        '"historical-hebrew-press-mixed-v1": (0, 1)',
    }
    for expectation in expected_hits:
        assert expectation in fetch


def test_release_workflow_reconstructs_canonical_source_metadata_and_fingerprints():
    payload, text = _workflow()
    fetch = _step_command(payload, "Fetch and verify all locked extension and diagnostic sources")
    build = _step_command(payload, "Build freeze and certify all extension and diagnostic roots")
    parent = _step_command(payload, "Build the two canonical parent roots")
    derived = _step_command(payload, "Derive and certify the four evaluation-only roots")
    proof = _step_command(payload, "Write non-redistributive certification proof bundle")

    assert 'verify_report="$RUNNER_TEMP/extension-source-verify.json"' in fetch
    assert '"$RUNNER_TEMP/acquisition-evidence"' in fetch
    assert '"$RUNNER_TEMP/canonical-extension-sources"' in fetch
    assert "os.link(source, destination)" in fetch
    assert 'canonical.rglob(".hebocrbench-source.json")' in fetch
    assert 'markers / f"{source_id}.json"' in fetch
    assert 'cp -R "$RUNNER_TEMP/acquisition-evidence" "$proof/"' in proof

    canonical_sources = {
        "modern-handwriting-lines-v1",
        "historical-pinkas-handwriting-v1",
        "biblical-niqqud-synthetic-diagnostic-v1",
        "rashi-print-synthetic-diagnostic-v1",
    }
    for source_id in canonical_sources:
        assert f'--source-root "{source_id}=$canonical/{source_id}"' in build
    assert build.count("--profile-scope full") == 4
    assert build.count("--profile-scope track-component") == 1
    assert (
        '--source-root "historical-hebrew-press-mixed-v1='
        "$cache/historical-hebrew-press-mixed-v1/"
        'locked-omilab-hazefira-page-alto-zip.extracted"'
    ) in build

    expected_fingerprints = {
        "51f11870b20aa2a0a9f789391ab9ea97b897a62207c23cc73a064c0e5809c756",
        "c071a31d87630e5f6dfba15886dbf934c55379fa3e0f9ed22924f938231bee62",
        "e8c56278525c00e3532d0c8445469af4ddb431257f7cc613ef3e3be45366233d",
        "99da38cde7317555806301e3fda9572ac0628bbde06eceb37cc07070296fd261",
        "cf801f22cb848295d36021e1f43e721da4366c51b3ba4264744fb6d1fa5e16b4",
        "d0145c7e63faf72a605991edebfd5a3010e436e73f145d51124af4beb6d37e31",
        "9b0ee1b4c8c230c4b012906cdd3d344e9f25a1325058ed5849fafe579e46767b",
        "fb687ec4a77c54db10f6aadb0bc982eceeb71c28083cddd566d6a2d6ba4dcb9d",
        "16aed8a8fc31ae1aaf957fe973b2b1dbdd0f911156f7539a78ceaafe56240143",
        "4c6ecba0b4487213fa95a1a7833fa546e41e2664b91fd9700f915c5c686e38fb",
        "c9e2732ab5ca0467635ae386c84b5d98045973641091887eedf0fd5cbba937a5",
    }
    combined = "\n".join((parent, derived, build))
    for fingerprint in expected_fingerprints:
        assert fingerprint in combined
    assert "c861f4eb8e9694fde099f86822068f6982ff7d6c04abb69c7033317ab639d628" not in text


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
