"""Robust Homebrew wrapper for the isolated HarfBuzz 14.3.0 restoration."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import stat


WRAPPER_MARKER = "HEBOCRBENCH_HARFBUZZ_14_3_WRAPPER_V2"
HARFBUZZ_VERSION = "14.3.0"


def brew_wrapper_script(
    *,
    real_brew: Path,
    bottle: Path,
    prefix: Path,
) -> str:
    """Return a narrow wrapper that pins HarfBuzz around dependency installs."""

    real = shlex.quote(str(real_brew))
    bottle_arg = shlex.quote(str(bottle))
    prefix_arg = shlex.quote(str(prefix))
    version = shlex.quote(HARFBUZZ_VERSION)
    return f"""#!/bin/bash
# {WRAPPER_MARKER}
set -euo pipefail
REAL_BREW={real}
BOTTLE={bottle_arg}
PREFIX={prefix_arg}
CELLAR="$PREFIX/Cellar"
VERSION={version}

restore_historical_harfbuzz() {{
  "$REAL_BREW" unlink harfbuzz >/dev/null 2>&1 || true
  mkdir -p "$CELLAR/harfbuzz"
  find "$CELLAR/harfbuzz" -mindepth 1 -maxdepth 1 \
    ! -name "$VERSION" -exec rm -rf {{}} +
  rm -rf "$CELLAR/harfbuzz/$VERSION"
  tar -xzf "$BOTTLE" -C "$CELLAR"
  test -d "$CELLAR/harfbuzz/$VERSION"
  test -f "$CELLAR/harfbuzz/$VERSION/INSTALL_RECEIPT.json"
  "$REAL_BREW" link --overwrite --force harfbuzz
  test "$(basename "$(readlink "$PREFIX/opt/harfbuzz")")" = "$VERSION" \
    || test "$(basename "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' \
      "$PREFIX/opt/harfbuzz")")" = "$VERSION"
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
