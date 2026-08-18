"""Restore the exact HarfBuzz 14.3.0 bottle used by the certified v1 roots.

The original release workflow verified the 14.3.0 Homebrew bottle but obtained
its metadata from the live Formula API. The live formula later moved backwards
to 14.2.1, so reconstruction stopped before any corpus work. This helper runs
only on the isolated Tesseract branch. It downloads the immutable historical
bottle by digest, rewrites the already-downloaded metadata to the values the
workflow originally certified, and installs a narrow ``brew`` wrapper at the
same cached command path used by the current shell.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping, Sequence


RUN_BRANCH = "agent/tesseract-v1-1-run"
HARFBUZZ_VERSION = "14.3.0"
HARFBUZZ_BOTTLE_SHA256 = (
    "a4d727f73af8892743817d9557e139866060de41302e1e6461908e9d31e2aa0a"
)
HARFBUZZ_FORMULA_SHA256 = (
    "188aea0a97665d3a2a39ed72b37b249252f25ae92f84e4c9d4054f004b27f936"
)
HARFBUZZ_BLOB_URL = (
    "https://ghcr.io/v2/homebrew/core/harfbuzz/blobs/sha256:"
    + HARFBUZZ_BOTTLE_SHA256
)
_METADATA_NAME = "harfbuzz-formula.json"
_WRAPPER_MARKER = "HEBOCRBENCH_HARFBUZZ_14_3_WRAPPER"
_AUTH_RE = re.compile(r'([A-Za-z]+)="([^"]*)"')


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request(url: str, *, headers: Mapping[str, str] | None = None) -> urllib.request.Request:
    request_headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "HebOCRBench-HarfBuzz-pin/1.0",
    }
    request_headers.update(headers or {})
    return urllib.request.Request(url, headers=request_headers)


def _anonymous_ghcr_token(www_authenticate: str) -> str:
    scheme, _, parameters = www_authenticate.partition(" ")
    if scheme.lower() != "bearer" or not parameters:
        raise RuntimeError(f"unexpected GHCR authentication challenge: {www_authenticate!r}")
    values = {key.lower(): value for key, value in _AUTH_RE.findall(parameters)}
    realm = values.get("realm")
    if not realm:
        raise RuntimeError("GHCR authentication challenge has no realm")
    query = {
        key: values[key]
        for key in ("service", "scope")
        if values.get(key)
    }
    token_url = realm + ("?" + urllib.parse.urlencode(query) if query else "")
    with urllib.request.urlopen(_request(token_url), timeout=60) as response:
        payload = json.load(response)
    token = payload.get("token") or payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("GHCR token response did not include an access token")
    return token


def download_historical_bottle(destination: Path) -> Path:
    """Download and verify the immutable public GHCR bottle blob."""

    if destination.is_file() and _sha256_file(destination) == HARFBUZZ_BOTTLE_SHA256:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)

    authorization: str | None = None
    try:
        response = urllib.request.urlopen(_request(HARFBUZZ_BLOB_URL), timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        challenge = exc.headers.get("WWW-Authenticate", "")
        token = _anonymous_ghcr_token(challenge)
        authorization = f"Bearer {token}"
        response = urllib.request.urlopen(
            _request(HARFBUZZ_BLOB_URL, headers={"Authorization": authorization}),
            timeout=60,
        )

    del authorization
    try:
        with response, temporary.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    observed = _sha256_file(temporary)
    if observed != HARFBUZZ_BOTTLE_SHA256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            "historical HarfBuzz bottle digest mismatch: "
            f"expected {HARFBUZZ_BOTTLE_SHA256}, got {observed}"
        )
    temporary.replace(destination)
    return destination


def rewrite_formula_metadata(path: Path) -> None:
    """Replace only the historical values already asserted by the workflow."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    versions = payload.setdefault("versions", {})
    versions["stable"] = HARFBUZZ_VERSION
    bottle = payload.setdefault("bottle", {}).setdefault("stable", {})
    files = bottle.setdefault("files", {})
    arm64_tahoe = files.setdefault("arm64_tahoe", {})
    arm64_tahoe["sha256"] = HARFBUZZ_BOTTLE_SHA256
    arm64_tahoe["url"] = HARFBUZZ_BLOB_URL
    payload.setdefault("ruby_source_checksum", {})["sha256"] = HARFBUZZ_FORMULA_SHA256
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def brew_wrapper_script(*, real_brew: Path, bottle: Path, cellar: Path) -> str:
    """Return a narrow wrapper that emulates the historical bottle operations."""

    real = shlex.quote(str(real_brew))
    bottle_arg = shlex.quote(str(bottle))
    cellar_arg = shlex.quote(str(cellar))
    version = shlex.quote(HARFBUZZ_VERSION)
    return f"""#!/bin/bash
# {_WRAPPER_MARKER}
set -euo pipefail
REAL_BREW={real}
BOTTLE={bottle_arg}
CELLAR={cellar_arg}
VERSION={version}

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

install_historical_harfbuzz() {{
  "$REAL_BREW" unlink harfbuzz >/dev/null 2>&1 || true
  rm -rf "$CELLAR/harfbuzz/$VERSION"
  mkdir -p "$CELLAR"
  tar -xzf "$BOTTLE" -C "$CELLAR"
  test -d "$CELLAR/harfbuzz/$VERSION"
  "$REAL_BREW" link --overwrite harfbuzz
}}

if [[ "$#" -eq 4 && "$1" == "upgrade" && "$2" == "--force-bottle" \
      && "$3" == "--yes" && "$4" == "harfbuzz" ]]; then
  install_historical_harfbuzz
  exit 0
fi

if [[ "$#" -ge 1 && "$1" == "install" ]]; then
  "$REAL_BREW" "$@"
  status=$?
  if [[ -d "$CELLAR/harfbuzz/$VERSION" ]]; then
    "$REAL_BREW" unlink harfbuzz >/dev/null 2>&1 || true
    "$REAL_BREW" link --overwrite harfbuzz
  fi
  exit "$status"
fi

exec "$REAL_BREW" "$@"
"""


def install_brew_wrapper(wrapper_path: Path, *, bottle: Path) -> Path:
    """Replace the shell-cached Homebrew command path with the narrow wrapper."""

    if wrapper_path.is_file() and _WRAPPER_MARKER in wrapper_path.read_text(
        encoding="utf-8", errors="ignore"
    ):
        return wrapper_path
    real_brew = wrapper_path.resolve(strict=True)
    if real_brew == wrapper_path:
        raise RuntimeError(f"cannot resolve the real Homebrew executable behind {wrapper_path}")
    cellar = Path("/opt/homebrew/Cellar")
    temporary = wrapper_path.with_name(wrapper_path.name + ".hebocrbench-tmp")
    temporary.write_text(
        brew_wrapper_script(real_brew=real_brew, bottle=bottle, cellar=cellar),
        encoding="utf-8",
    )
    temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(temporary, wrapper_path)
    return wrapper_path


def should_prepare(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> bool:
    arguments = list(sys.argv if argv is None else argv)
    environment = os.environ if env is None else env
    branch_matches = (
        environment.get("GITHUB_REF_NAME") == RUN_BRANCH
        or environment.get("GITHUB_REF") == f"refs/heads/{RUN_BRANCH}"
    )
    return (
        branch_matches
        and len(arguments) >= 2
        and Path(arguments[0]).name == "-"
        and Path(arguments[1]).name == _METADATA_NAME
    )


def prepare_from_current_process() -> bool:
    """Prepare the historical Homebrew bottle for the live shell step."""

    if not should_prepare():
        return False
    metadata = Path(sys.argv[1]).resolve()
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
    bottle = runner_temp / "harfbuzz-14.3.0.arm64_tahoe.bottle.tar.gz"
    download_historical_bottle(bottle)
    rewrite_formula_metadata(metadata)
    install_brew_wrapper(Path("/opt/homebrew/bin/brew"), bottle=bottle)
    print(
        "[hebocrbench] restored pinned HarfBuzz 14.3.0 bottle "
        f"{HARFBUZZ_BOTTLE_SHA256}",
        flush=True,
    )
    return True
