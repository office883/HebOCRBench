"""Robust Homebrew wrapper for the isolated HarfBuzz 14.3.0 restoration."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import stat


WRAPPER_MARKER = "HEBOCRBENCH_HARFBUZZ_14_3_WRAPPER_V5"
HARFBUZZ_VERSION = "14.3.0"
HISTORICAL_BOTTLE_NAME = "harfbuzz--14.3.0.arm64_tahoe.bottle.tar.gz"


def homebrew_pour_script() -> str:
    """Return the Homebrew-native Ruby installer for a local bottle path."""

    return """# frozen_string_literal: true

require "formulary"
require "formula_installer"
require "pathname"

bottle_path = Pathname(ARGV.fetch(0)).expand_path
raise "local bottle does not exist: #{bottle_path}" unless bottle_path.file?

formula = Formulary.factory(bottle_path)
installer = FormulaInstaller.new(
  formula,
  force_bottle: true,
  ignore_deps: true,
  link_keg: true,
  installed_on_request: false,
  show_header: true,
  force: true,
  overwrite: true,
  verbose: true,
)
installer.prelude
installer.install
installer.finish

keg = Keg.new(formula.prefix)
tab = keg.tab
raise "bottle receipt was not marked built_as_bottle" unless tab.built_as_bottle
raise "bottle receipt was not marked poured_from_bottle" unless tab.poured_from_bottle
puts formula.prefix
"""


def brew_wrapper_script(
    *,
    real_brew: Path,
    bottle: Path,
    prefix: Path,
) -> str:
    """Return a narrow wrapper that pours the pinned bottle with Homebrew internals."""

    real = shlex.quote(str(real_brew))
    bottle_arg = shlex.quote(str(bottle))
    prefix_arg = shlex.quote(str(prefix))
    version = shlex.quote(HARFBUZZ_VERSION)
    bottle_name = shlex.quote(HISTORICAL_BOTTLE_NAME)
    ruby_payload = homebrew_pour_script()
    return f"""#!/bin/bash
# {WRAPPER_MARKER}
set -euo pipefail
REAL_BREW={real}
BOTTLE={bottle_arg}
PREFIX={prefix_arg}
CELLAR="$PREFIX/Cellar"
VERSION={version}
BOTTLE_NAME={bottle_name}
LOCAL_BOTTLE="$PREFIX/var/homebrew/cache/$BOTTLE_NAME"
POUR_SCRIPT="$PREFIX/var/homebrew/cache/hebocrbench-pour-local-bottle.rb"

restore_historical_harfbuzz() {{
  echo "[hebocrbench] pouring HarfBuzz $VERSION through FormulaInstaller" >&2
  set -x
  mkdir -p "$(dirname "$LOCAL_BOTTLE")"
  cp "$BOTTLE" "$LOCAL_BOTTLE"
  cat > "$POUR_SCRIPT" <<'HEBOCRBENCH_RUBY'
{ruby_payload}HEBOCRBENCH_RUBY
  "$REAL_BREW" uninstall --ignore-dependencies --force harfbuzz >/dev/null 2>&1 \
    || true
  HOMEBREW_DEVELOPER=1 "$REAL_BREW" ruby "$POUR_SCRIPT" "$LOCAL_BOTTLE"
  test -d "$CELLAR/harfbuzz/$VERSION"
  test -f "$CELLAR/harfbuzz/$VERSION/INSTALL_RECEIPT.json"
  test "$(basename "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' \
    "$PREFIX/opt/harfbuzz")")" = "$VERSION"
  set +x
}}

if [[ "$#" -eq 4 && "$1" == "fetch" && "$2" == "--force" \
      && "$3" == "--bottle-tag=arm64_tahoe" && "$4" == "harfbuzz" ]]; then
  test -f "$BOTTLE"
  exit 0
fi

if [[ "$#" -eq 3 && "$1" == "--cache" \
      && "$2" == "--bottle-tag=arm64_tahoe" && "$3" == "harfbuzz" ]]; then
  printf '%s\\n' "$BOTTLE"
  exit 0
fi

if [[ "$#" -eq 4 && "$1" == "upgrade" && "$2" == "--force-bottle" \
      && "$3" == "--yes" && "$4" == "harfbuzz" ]]; then
  restore_historical_harfbuzz
  exit 0
fi

if [[ "$#" -ge 1 && "$1" == "install" ]]; then
  "$REAL_BREW" "$@"
  status=$?
  restore_historical_harfbuzz
  exit "$status"
fi

exec "$REAL_BREW" "$@"
"""


def install_brew_wrapper(wrapper_path: Path, *, bottle: Path) -> Path:
    """Back up a regular or symlinked brew executable and install the wrapper."""

    if wrapper_path.is_file() and WRAPPER_MARKER in wrapper_path.read_text(
        encoding="utf-8", errors="ignore"
    ):
        return wrapper_path
    if not wrapper_path.exists():
        raise RuntimeError(f"Homebrew executable does not exist: {wrapper_path}")

    prefix = wrapper_path.parent.parent.resolve()
    backup = wrapper_path.with_name("brew.hebocrbench-real")
    if not backup.exists():
        shutil.copy2(wrapper_path, backup, follow_symlinks=True)
    backup.chmod(backup.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    temporary = wrapper_path.with_name(wrapper_path.name + ".hebocrbench-tmp")
    temporary.write_text(
        brew_wrapper_script(real_brew=backup, bottle=bottle, prefix=prefix),
        encoding="utf-8",
    )
    temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(temporary, wrapper_path)
    return wrapper_path
