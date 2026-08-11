#!/usr/bin/env python3
"""Derive all printed Modern-Hebrew track roots from one frozen page root."""

from __future__ import annotations

import argparse
import json

from hebocrbench.derived_tracks import build_canonical_track_roots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            build_canonical_track_roots(
                args.source_root,
                args.output,
                overwrite=args.overwrite,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
