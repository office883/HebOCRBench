#!/usr/bin/env python3
"""Build, verify, or remap blind HebOCRBench Modern Hebrew suite packs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hebocrbench.modern_packs import (  # noqa: E402
    ModernPackError,
    build_modern_suite_packs,
    remap_pack_predictions,
    verify_modern_suite_packs,
)


def _track_roots(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--track-root requires TRACK_ID=PATH")
        track_id, raw_path = value.split("=", 1)
        if not track_id.strip() or not raw_path.strip() or track_id in result:
            raise ValueError(f"invalid or duplicate track root: {value!r}")
        result[track_id] = Path(raw_path).expanduser()
    return result


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build participant and organizer packs")
    build.add_argument("--suite-lock", required=True, type=Path)
    build.add_argument("--track-root", required=True, action="append", metavar="TRACK=PATH")
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--id-key-file", type=Path)
    build.add_argument("--overwrite", action="store_true")

    verify = subparsers.add_parser("verify", help="Verify both pack roles and their bindings")
    verify.add_argument("--participant", required=True, type=Path)
    verify.add_argument("--organizer", required=True, type=Path)
    verify.add_argument("--suite-lock", type=Path)
    verify.add_argument("--track-root", action="append", default=[], metavar="TRACK=PATH")

    remap = subparsers.add_parser("remap", help="Remap opaque predictions for scoring")
    remap.add_argument("--predictions", required=True, type=Path)
    remap.add_argument("--organizer", required=True, type=Path)
    remap.add_argument("--track")
    remap.add_argument("--output", required=True, type=Path)
    remap.add_argument("--allow-incomplete", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            key = args.id_key_file.read_bytes() if args.id_key_file else secrets.token_bytes(32)
            result = build_modern_suite_packs(
                args.suite_lock,
                _track_roots(args.track_root),
                args.output,
                id_key=key,
                overwrite=args.overwrite,
            )
            _json(
                {
                    "participant_root": str(result.participant_root),
                    "organizer_root": str(result.organizer_root),
                    "suite_fingerprint": result.suite_fingerprint,
                    "page_count": result.page_count,
                    "participant_fingerprint": result.participant_fingerprint,
                    "organizer_fingerprint": result.organizer_fingerprint,
                }
            )
            return 0
        if args.command == "verify":
            roots = _track_roots(args.track_root) if args.track_root else None
            report = verify_modern_suite_packs(
                args.participant,
                args.organizer,
                suite_lock=args.suite_lock,
                track_roots=roots,
            )
            _json(
                {
                    "valid": report.valid,
                    "suite_fingerprint": report.suite_fingerprint,
                    "page_count": report.page_count,
                    "participant_fingerprint": report.participant_fingerprint,
                    "organizer_fingerprint": report.organizer_fingerprint,
                    "checks": dict(report.checks),
                }
            )
            return 0 if report.valid else 2
        records = remap_pack_predictions(
            args.predictions,
            args.organizer,
            track_id=args.track,
            output_path=args.output,
            require_complete=not args.allow_incomplete,
        )
        _json({"output": str(args.output), "prediction_count": len(records)})
        return 0
    except (ModernPackError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
