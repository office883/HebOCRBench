#!/usr/bin/env python3
"""Verify a compact HebOCRBench public results pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hebocrbench.public_results import PublicResultsError, verify_public_results_pack  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = verify_public_results_pack(args.pack)
    except PublicResultsError as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
