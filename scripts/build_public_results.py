#!/usr/bin/env python3
"""Build a compact public pack from completed Modern and extension baselines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hebocrbench.public_results import (  # noqa: E402
    PublicResultsError,
    build_public_results_pack,
)


def _parse_runs(values: Sequence[str]) -> dict[str, Path]:
    runs: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise PublicResultsError(f"run must use NAME=PATH syntax: {value!r}")
        name, raw_path = value.split("=", 1)
        name = name.strip()
        raw_path = raw_path.strip()
        if not name or not raw_path:
            raise PublicResultsError(f"run must use non-empty NAME=PATH syntax: {value!r}")
        if name in runs:
            raise PublicResultsError(f"duplicate result name: {name}")
        runs[name] = Path(raw_path)
    return runs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="completed Modern baseline directory; repeat for each model",
    )
    parser.add_argument(
        "--extension-run",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="completed separate extension/diagnostic directory; repeat for each model",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--clean",
        action="store_true",
        help="replace an existing non-empty output only after the new pack verifies",
    )
    args = parser.parse_args(argv)
    try:
        runs = _parse_runs(args.run)
        extension_runs = _parse_runs(args.extension_run)
        manifest = build_public_results_pack(
            runs,
            args.output,
            extension_runs=extension_runs,
            clean=args.clean,
        )
    except PublicResultsError as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
