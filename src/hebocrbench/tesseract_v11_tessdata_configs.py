"""Install the exact Tesseract 5.5.3 page-output configs in isolated tessdata."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable


TESSERACT_CONFIG_REPOSITORY = "tesseract-ocr/tesseract"
TESSERACT_CONFIG_REVISION = "db0ec62f81b0737fbbe184d8fea40af5738f8eef"
TESSERACT_CONFIG_FILES = {
    "txt": {
        "payload": (
            b"# This config file should be used with other config files which create renderers.\n"
            b"# usage example: tesseract eurotext.tif eurotext txt hocr pdf\n"
            b"tessedit_create_txt 1\n"
        ),
        "git_blob_sha1": "a0cc952977f0f3562a5c94011c13044ace865519",
        "size_bytes": 166,
    },
    "tsv": {
        "payload": b"tessedit_create_tsv 1\n",
        "git_blob_sha1": "dc52478177fd6fb7b1fe278e1374c2054f3e2442",
        "size_bytes": 22,
    },
}


def git_blob_sha1(payload: bytes) -> str:
    """Return the SHA-1 Git uses to identify one blob."""

    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()  # noqa: S324 - Git identity


def install_pinned_tesseract_configs(root: Path) -> dict[str, dict[str, object]]:
    """Write and independently verify the two renderer configs required by page OCR."""

    config_root = root / "configs"
    config_root.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, dict[str, object]] = {}
    for name, expected in TESSERACT_CONFIG_FILES.items():
        payload = expected["payload"]
        assert isinstance(payload, bytes)
        destination = config_root / name
        destination.write_bytes(payload)
        observed = destination.read_bytes()
        if len(observed) != expected["size_bytes"]:
            raise RuntimeError(
                f"Tesseract {name} config size mismatch: "
                f"expected {expected['size_bytes']}, got {len(observed)}"
            )
        observed_blob = git_blob_sha1(observed)
        if observed_blob != expected["git_blob_sha1"]:
            raise RuntimeError(
                f"Tesseract {name} config Git blob mismatch: "
                f"expected {expected['git_blob_sha1']}, got {observed_blob}"
            )
        metadata[name] = {
            "repository": TESSERACT_CONFIG_REPOSITORY,
            "revision": TESSERACT_CONFIG_REVISION,
            "path": f"tessdata/configs/{name}",
            "git_blob_sha1": observed_blob,
            "sha256": hashlib.sha256(observed).hexdigest(),
            "size_bytes": len(observed),
        }
    return metadata


def patch_tessdata_download() -> bool:
    """Extend the one-off hook's traineddata materializer exactly once."""

    from hebocrbench import tesseract_v11_release_hook as hook

    original: Callable[[Path], dict[str, dict[str, object]]] = hook._download_tessdata
    if getattr(original, "_tesseract_v11_configs", False):
        return False

    def wrapped(root: Path) -> dict[str, dict[str, object]]:
        languages = original(root)
        install_pinned_tesseract_configs(root)
        return languages

    setattr(wrapped, "_tesseract_v11_configs", True)
    hook._download_tessdata = wrapped
    return True
