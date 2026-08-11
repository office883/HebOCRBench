#!/usr/bin/env python3
"""Launch a hash-bound, loopback-only llama-server for Surya OCR 2.

Keep this foreground process running while ``run_modern_baseline.py`` uses the
``server`` Surya backend.  The alias is derived from the exact GGUF bytes; the
client submits that alias on every request and rejects a mismatched response.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--mmproj-path", required=True, type=Path)
    parser.add_argument("--executable", default="llama-server")
    parser.add_argument("--port", type=int, default=8137)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--context-size", type=int, default=16384)
    parser.add_argument("--image-max-tokens", type=int, default=2048)
    parser.add_argument("--max-generation-tokens", type=int, default=4096)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args(argv)

    model = args.model_path.expanduser().resolve()
    projector = args.mmproj_path.expanduser().resolve()
    if not model.is_file():
        parser.error(f"model does not exist: {model}")
    if not projector.is_file():
        parser.error(f"multimodal projector does not exist: {projector}")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.parallel <= 0:
        parser.error("parallel must be positive")
    if args.context_size <= 0:
        parser.error("context-size must be positive")
    if args.image_max_tokens <= 0 or args.max_generation_tokens <= 0:
        parser.error("token limits must be positive")
    if args.timeout_seconds <= 0:
        parser.error("timeout-seconds must be positive")
    minimum_context = args.parallel * (args.image_max_tokens + args.max_generation_tokens + 256)
    if args.context_size < minimum_context:
        parser.error(
            f"context-size is too small for all slots: require at least {minimum_context} tokens"
        )

    model_sha256 = _sha256(model)
    mmproj_sha256 = _sha256(projector)
    alias = f"surya-ocr-2::{model_sha256}::{mmproj_sha256}"
    command = [
        args.executable,
        "--model",
        str(model),
        "--mmproj",
        str(projector),
        "--alias",
        alias,
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--parallel",
        str(args.parallel),
        "--ctx-size",
        str(args.context_size),
        "--image-max-tokens",
        str(args.image_max_tokens),
        "--temp",
        "0",
        "--seed",
        "1",
        "--timeout",
        str(args.timeout_seconds),
        "--cors-origins",
        "localhost",
        "--no-cors-credentials",
        "--no-ui",
    ]
    print(
        json.dumps(
            {
                "backend": "llama-server",
                "url": f"http://127.0.0.1:{args.port}",
                "model_sha256": model_sha256,
                "mmproj_sha256": mmproj_sha256,
                "model_alias": alias,
                "temperature": 0,
                "seed": 1,
                "parallel": args.parallel,
                "context_size": args.context_size,
                "loopback_only": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        completed = subprocess.run(command, check=False)
    except FileNotFoundError as exc:
        parser.error(f"llama-server executable not found: {args.executable}")
        raise AssertionError("argparse.error always exits") from exc
    except KeyboardInterrupt:
        return 130
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
