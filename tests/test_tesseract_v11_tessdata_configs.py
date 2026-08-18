from pathlib import Path

from hebocrbench import tesseract_v11_release_hook as hook
from hebocrbench.tesseract_v11_tessdata_configs import (
    TESSERACT_CONFIG_FILES,
    install_pinned_tesseract_configs,
    patch_tessdata_download,
)


def test_installs_exact_tesseract_553_renderer_configs(tmp_path: Path) -> None:
    metadata = install_pinned_tesseract_configs(tmp_path)

    assert set(metadata) == {"txt", "tsv"}
    for name, expected in TESSERACT_CONFIG_FILES.items():
        payload = (tmp_path / "configs" / name).read_bytes()
        assert payload == expected["payload"]
        assert len(payload) == expected["size_bytes"]
        assert metadata[name]["git_blob_sha1"] == expected["git_blob_sha1"]
        assert metadata[name]["path"] == f"tessdata/configs/{name}"


def test_patch_extends_traineddata_download_without_changing_language_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    observed = []

    def fake_download(root: Path) -> dict[str, dict[str, object]]:
        observed.append(root)
        root.mkdir(parents=True, exist_ok=True)
        return {"heb": {"sha256": "he"}, "eng": {"sha256": "en"}}

    monkeypatch.setattr(hook, "_download_tessdata", fake_download)
    assert patch_tessdata_download() is True
    assert patch_tessdata_download() is False

    metadata = hook._download_tessdata(tmp_path)

    assert observed == [tmp_path]
    assert metadata == {"heb": {"sha256": "he"}, "eng": {"sha256": "en"}}
    assert (tmp_path / "configs" / "txt").is_file()
    assert (tmp_path / "configs" / "tsv").is_file()
