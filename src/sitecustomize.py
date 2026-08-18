"""Branch-gated bootstrap for the one-off Tesseract v1.1 benchmark run."""

from __future__ import annotations

import os
from pathlib import Path
import sys


run_branch = "agent/tesseract-v1-1-run"
branch_matches = (
    os.environ.get("GITHUB_REF_NAME") == run_branch
    or os.environ.get("GITHUB_REF") == f"refs/heads/{run_branch}"
)

if branch_matches:
    from hebocrbench.harfbuzz_14_3_pin import prepare_from_current_process

    prepare_from_current_process()

if branch_matches and Path(sys.argv[0]).name == "build_v1_release.py":
    from hebocrbench.tesseract_v11_release_hook import install_release_hook

    install_release_hook()
