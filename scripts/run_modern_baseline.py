#!/usr/bin/env python3
"""Run and resume a real baseline over the five Modern Hebrew test tracks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hebocrbench.baseline_runner import (  # noqa: E402
    BaselineRunnerError,
    BaselineSettings,
    run_modern_baseline_suite,
)


def _track_roots(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError("--track-root requires TRACK_ID=PATH")
        track_id, raw_path = value.split("=", 1)
        if not track_id.strip() or not raw_path.strip():
            raise argparse.ArgumentTypeError("--track-root requires TRACK_ID=PATH")
        if track_id in result:
            raise argparse.ArgumentTypeError(f"duplicate track root: {track_id}")
        result[track_id] = Path(raw_path).expanduser()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        required=True,
        choices=("tesseract", "apple-vision", "surya2-llamacpp"),
    )
    parser.add_argument("--suite-lock", required=True, type=Path)
    parser.add_argument("--track-root", required=True, action="append", metavar="TRACK=PATH")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-version")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-pages", type=int, help="Smoke only; disables headline scoring")
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--tesseract-executable", default="tesseract")
    parser.add_argument("--tesseract-language", default="heb+eng")
    parser.add_argument("--tesseract-page-psm", type=int, default=3)
    parser.add_argument("--tesseract-line-psm", type=int, default=7)
    parser.add_argument("--apple-executable")
    parser.add_argument("--apple-languages", default="he-IL,en-US")
    parser.add_argument(
        "--apple-recognition-level", choices=("accurate", "fast"), default="accurate"
    )
    parser.add_argument("--apple-revision", type=int)
    parser.add_argument("--surya-model-path")
    parser.add_argument("--surya-mmproj-path")
    parser.add_argument("--surya-backend", choices=("cli", "server"), default="cli")
    parser.add_argument(
        "--surya-server-url",
        help="Loopback llama-server base URL, e.g. http://127.0.0.1:8137",
    )
    parser.add_argument(
        "--surya-server-parallel",
        type=int,
        help="Exact llama-server slot count; required for the server backend",
    )
    parser.add_argument(
        "--surya-server-context-size",
        type=int,
        help="Exact total llama-server context size; required for the server backend",
    )
    parser.add_argument("--surya-executable", default="llama-cli")
    parser.add_argument("--surya-server-executable", default="llama-server")
    parser.add_argument("--surya-max-tokens", type=int, default=4096)
    parser.add_argument("--surya-image-max-tokens", type=int, default=2048)
    parser.add_argument(
        "--no-apple-language-correction",
        action="store_true",
        help="Disable VNRecognizeTextRequest language correction",
    )
    args = parser.parse_args(argv)
    try:
        roots = _track_roots(args.track_root)
        settings = BaselineSettings(
            engine=args.engine,
            model_version=args.model_version,
            timeout_seconds=args.timeout_seconds,
            tesseract_executable=args.tesseract_executable,
            tesseract_language=args.tesseract_language,
            tesseract_page_psm=args.tesseract_page_psm,
            tesseract_line_psm=args.tesseract_line_psm,
            apple_languages=tuple(
                value.strip() for value in args.apple_languages.split(",") if value.strip()
            ),
            apple_recognition_level=args.apple_recognition_level,
            apple_language_correction=not args.no_apple_language_correction,
            apple_revision=args.apple_revision,
            apple_executable=args.apple_executable,
            surya_model_path=args.surya_model_path,
            surya_mmproj_path=args.surya_mmproj_path,
            surya_backend=args.surya_backend,
            surya_server_url=args.surya_server_url,
            surya_server_parallel=args.surya_server_parallel,
            surya_server_context_size=args.surya_server_context_size,
            surya_executable=args.surya_executable,
            surya_server_executable=args.surya_server_executable,
            surya_max_tokens=args.surya_max_tokens,
            surya_image_max_tokens=args.surya_image_max_tokens,
        )
        result = run_modern_baseline_suite(
            roots,
            args.suite_lock,
            args.output,
            settings=settings,
            max_pages=args.max_pages,
            retry_failures=args.retry_failures,
            workers=args.workers,
        )
    except (BaselineRunnerError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
