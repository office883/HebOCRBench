#!/usr/bin/env python3
"""Build or verify the non-blended HebOCRBench multi-profile suite lock."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hebocrbench.cli import main as hebocrbench_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    return hebocrbench_main(["full-suite", *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
