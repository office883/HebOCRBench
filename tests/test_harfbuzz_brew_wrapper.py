from pathlib import Path
import stat

from hebocrbench.harfbuzz_brew_wrapper import (
    HISTORICAL_BOTTLE_NAME,
    WRAPPER_MARKER,
    brew_wrapper_script,
    homebrew_pour_script,
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


def test_homebrew_pour_script_uses_local_bottle_loader_and_formula_installer() -> None:
    script = homebrew_pour_script()

    assert 'formula = Formulary.factory(bottle_path)' in script
    assert 'force_bottle: true' in script
    assert 'ignore_deps: true' in script
    assert 'link_keg: true' in script
    assert 'installer.prelude' in script
    assert 'installer.install' in script
    assert 'installer.finish' in script
    assert 'tab.built_as_bottle' in script
    assert 'tab.poured_from_bottle' in script


def test_wrapper_pours_with_homebrew_ruby_and_restores_after_dependency_install(
    tmp_path: Path,
) -> None:
    script = brew_wrapper_script(
        real_brew=tmp_path / "brew.hebocrbench-real",
        bottle=tmp_path / "harfbuzz.bottle.tar.gz",
        prefix=tmp_path / "homebrew",
    )

    assert HISTORICAL_BOTTLE_NAME in script
    assert 'cp "$BOTTLE" "$LOCAL_BOTTLE"' in script
    assert 'cat > "$POUR_SCRIPT"' in script
    assert "uninstall --ignore-dependencies --force harfbuzz" in script
    assert 'HOMEBREW_DEVELOPER=1 "$REAL_BREW" ruby' in script
    assert 'test -f "$CELLAR/harfbuzz/$VERSION/INSTALL_RECEIPT.json"' in script
    assert 'if [[ "$#" -ge 1 && "$1" == "install" ]]' in script
    assert "restore_historical_harfbuzz" in script
