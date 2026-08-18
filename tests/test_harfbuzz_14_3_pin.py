import json
from pathlib import Path

from hebocrbench.harfbuzz_14_3_pin import (
    HARFBUZZ_BLOB_URL,
    HARFBUZZ_BOTTLE_SHA256,
    HARFBUZZ_FORMULA_SHA256,
    HARFBUZZ_VERSION,
    RUN_BRANCH,
    brew_wrapper_script,
    rewrite_formula_metadata,
    should_prepare,
)


def test_rewrite_formula_metadata_restores_certified_values(tmp_path: Path) -> None:
    metadata = tmp_path / "harfbuzz-formula.json"
    metadata.write_text(
        json.dumps(
            {
                "versions": {"stable": "14.2.1"},
                "bottle": {
                    "stable": {
                        "files": {
                            "arm64_tahoe": {
                                "url": "https://example.invalid/current",
                                "sha256": "0" * 64,
                            }
                        }
                    }
                },
                "ruby_source_checksum": {"sha256": "1" * 64},
            }
        ),
        encoding="utf-8",
    )

    rewrite_formula_metadata(metadata)
    payload = json.loads(metadata.read_text(encoding="utf-8"))

    assert payload["versions"]["stable"] == HARFBUZZ_VERSION
    bottle = payload["bottle"]["stable"]["files"]["arm64_tahoe"]
    assert bottle["sha256"] == HARFBUZZ_BOTTLE_SHA256
    assert bottle["url"] == HARFBUZZ_BLOB_URL
    assert payload["ruby_source_checksum"]["sha256"] == HARFBUZZ_FORMULA_SHA256


def test_should_prepare_is_exactly_branch_and_metadata_scoped() -> None:
    matching_env = {"GITHUB_REF": f"refs/heads/{RUN_BRANCH}"}

    assert should_prepare(
        ["-", "/tmp/harfbuzz-formula.json"], matching_env
    )
    assert not should_prepare(["-", "/tmp/other.json"], matching_env)
    assert not should_prepare(
        ["-", "/tmp/harfbuzz-formula.json"],
        {"GITHUB_REF": "refs/heads/main"},
    )


def test_brew_wrapper_intercepts_only_historical_harfbuzz_operations(
    tmp_path: Path,
) -> None:
    script = brew_wrapper_script(
        real_brew=tmp_path / "real-brew",
        bottle=tmp_path / "harfbuzz.bottle.tar.gz",
        cellar=tmp_path / "Cellar",
    )

    assert "HEBOCRBENCH_HARFBUZZ_14_3_WRAPPER" in script
    assert '"$1" == "fetch"' in script
    assert '"$1" == "--cache"' in script
    assert '"$1" == "upgrade"' in script
    assert "tar -xzf" in script
    assert 'exec "$REAL_BREW" "$@"' in script
