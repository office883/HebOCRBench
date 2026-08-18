"""Branch-gated bootstrap for the one-off Tesseract v1.1 benchmark run."""

from __future__ import annotations

import os


run_branch = "agent/tesseract-v1-1-run"
branch_matches = (
    os.environ.get("GITHUB_REF_NAME") == run_branch
    or os.environ.get("GITHUB_REF") == f"refs/heads/{run_branch}"
)

if branch_matches:
    import hebocrbench.harfbuzz_14_3_pin as harfbuzz_pin
    from hebocrbench.harfbuzz_brew_wrapper import install_brew_wrapper

    harfbuzz_pin.install_brew_wrapper = install_brew_wrapper
    harfbuzz_pin.prepare_from_current_process()

if branch_matches:
    from hebocrbench.tesseract_v11_gate import is_certified_release_invocation

    if is_certified_release_invocation():
        from hebocrbench.tesseract_v11_tessdata_configs import patch_tessdata_download

        patch_tessdata_download()
        from hebocrbench.tesseract_v11_release_hook import install_release_hook

        install_release_hook()
