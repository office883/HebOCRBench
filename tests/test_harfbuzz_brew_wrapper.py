from pathlib import Path
import stat

from hebocrbench.harfbuzz_brew_wrapper import (
    WRAPPER_MARKER,
    brew_wrapper_script,
    install_brew_wrapper,
)


def test_install_brew_wrapper_backs_up_regular_executable(tmp_path: Path) -> None:
    prefix = tmp_path / "homebrew"
    wrapper = prefix / "bin" / "brew"
    wrapper.parent.mkdir(parents=True)
    original = "#!/bin/bash\nprintf 'real brew\\n'\n"
    wrapper.write_text(original, encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    bottle = tmp_path / "harfbuzz-14.3.0.bottle.tar.gz"
    bottle.write_bytes(b"pinned bottle placeholder")

    observed = install_brew_wrapper(wrapper, bottle=bottle)

    backup = wrapper.with_name("brew.hebocrbench-real")
    assert observed == wrapper
    assert backup.read_text(encoding="utf-8") == original
    assert backup.stat().st_mode & stat.S_IXUSR
    installed = wrapper.read_text(encoding="utf-8")
    assert WRAPPER_MARKER in installed
    assert str(backup) in installed
    assert str(bottle) in installed
    assert str(prefix) in installed


def test_wrapper_removes_newer_kegs_and_restores_after_install(
    tmp_path: Path,
) -> None:
    script = brew_wrapper_script(
        real_brew=tmp_path / "brew.hebocrbench-real",
        bottle=tmp_path / "harfbuzz.bottle.tar.gz",
        prefix=tmp_path / "homebrew",
    )

    assert '! -name "$VERSION" -exec rm -rf {} +' in script
    assert 'rm -rf "$CELLAR/harfbuzz/$VERSION"' in script
    assert 'tar -xzf "$BOTTLE" -C "$CELLAR"' in script
    assert '"$REAL_BREW" link --overwrite --force harfbuzz' in script
    assert 'if [[ "$#" -ge 1 && "$1" == "install" ]]' in script
    assert "restore_historical_harfbuzz" in script
