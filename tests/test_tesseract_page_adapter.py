from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hebocrbench.adapters.tesseract_page import (
    TesseractPageInvocationError,
    TesseractPageOutput,
    invoke_tesseract_page,
    parse_tesseract_tsv,
    run_tesseract_page_ocr,
)
from hebocrbench.validator import validate_prediction_records


TSV = """level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext
1\t1\t0\t0\t0\t0\t0\t0\t500\t300\t-1\t
5\t1\t1\t1\t1\t1\t260\t20\t80\t30\t96.0\tשלום
5\t1\t1\t1\t1\t2\t160\t20\t80\t30\t94.0\tעולם
5\t1\t2\t1\t1\t1\t20\t100\t60\t25\t91.0\tOCR
5\t1\t2\t1\t1\t2\t90\t100\t40\t25\t90.0\tv2
"""


class _GuardedRecord(dict):
    def __init__(self, allowed, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed = set(allowed)

    def get(self, key, default=None):
        if key not in self.allowed:
            raise AssertionError(f"adapter read forbidden gold field: {key}")
        return super().get(key, default)

    def __getitem__(self, key):
        if key not in self.allowed:
            raise AssertionError(f"adapter read forbidden gold field: {key}")
        return super().__getitem__(key)


def test_tsv_parser_builds_blind_regions_lines_and_order_metadata():
    regions, diagnostics = parse_tesseract_tsv(TSV)

    assert [region["region_id"] for region in regions] == ["pred-r0001", "pred-r0002"]
    assert regions[0]["polygon"] == [[160, 20], [340, 20], [340, 50], [160, 50]]
    assert regions[0]["base_direction"] == "rtl"
    assert regions[0]["lines"][0]["text"] == "שלום עולם"
    assert regions[0]["lines"][0]["language"] == "he"
    assert regions[0]["lines"][0]["confidence"] == 95.0
    assert regions[1]["base_direction"] == "ltr"
    assert regions[1]["lines"][0]["language"] == "en"
    assert diagnostics == {
        "tsv_rows": 5,
        "recognized_words": 4,
        "recognized_lines": 2,
        "recognized_regions": 2,
    }


def test_page_adapter_reads_only_page_id_and_image_path(tmp_path):
    image_path = tmp_path / "images" / "page.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"runner fixture; not decoded")

    image = _GuardedRecord(
        {"path"},
        path="images/page.png",
        width=9999,
        height=9999,
        rotation_degrees=270,
    )
    gold = _GuardedRecord(
        {"page_id", "image"},
        page_id="secret-gold-page",
        image=image,
        regions=[{"text": "אסור לקרוא", "polygon": [[1, 1], [2, 1], [2, 2]]}],
        reading_order={"edges": [["secret-a", "secret-b"]]},
        page_text="אסור לקרוא",
    )
    seen = []

    def fake_runner(path, language, psm, timeout_seconds):
        seen.append((path, language, psm, timeout_seconds))
        return TesseractPageOutput(text="טקסט מנוע\n\f", tsv=TSV)

    prediction = run_tesseract_page_ocr(
        [gold],
        dataset_root=tmp_path,
        runner=fake_runner,
        model_version="5.5.1-test",
    )[0]

    assert seen == [(image_path.resolve(), "heb+eng", 3, 120.0)]
    assert prediction["page_id"] == "secret-gold-page"
    assert prediction["page_text"] == "טקסט מנוע"
    assert prediction["reading_order"] == {"edges": [["pred-r0001", "pred-r0002"]]}
    assert prediction["model"]["adapter"] == "tesseract_page_e2e"
    assert prediction["model"]["oracle_layout"] is False
    assert prediction["model"]["output_formats"] == ["txt", "tsv"]
    assert prediction["status"] == "ok"
    assert prediction["failure"] is None
    assert prediction["api_failures"] == 0
    assert validate_prediction_records([prediction]).is_valid


def test_page_adapter_returns_schema_valid_visible_failure(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"runner fixture; not decoded")

    def failing_runner(path, language, psm, timeout_seconds):
        raise TesseractPageInvocationError(
            "engine exited",
            return_code=17,
            stderr="missing heb.traineddata",
        )

    prediction = run_tesseract_page_ocr(
        [{"page_id": "p-failed", "image": {"path": "page.png"}}],
        dataset_root=tmp_path,
        runner=failing_runner,
    )[0]

    assert prediction["status"] == "failed"
    assert prediction["api_failures"] == 1
    assert prediction["regions"] == []
    assert prediction["failure"] == {
        "error_type": "TesseractPageInvocationError",
        "message": "engine exited",
        "return_code": 17,
        "stderr": "missing heb.traineddata",
    }
    assert validate_prediction_records([prediction]).is_valid


def test_default_invocation_requests_txt_and_tsv(tmp_path, monkeypatch):
    image_path = tmp_path / "page image.png"
    image_path.write_bytes(b"not decoded by mocked subprocess")
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        output_base = Path(command[2])
        Path(f"{output_base}.txt").write_text("פלט\n", encoding="utf-8")
        Path(f"{output_base}.tsv").write_text(TSV, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("hebocrbench.adapters.tesseract_page.subprocess.run", fake_run)
    output = invoke_tesseract_page(
        image_path,
        "heb+eng",
        3,
        45.0,
        executable="tesseract-test",
    )

    assert observed["command"][0] == "tesseract-test"
    assert observed["command"][-2:] == ["txt", "tsv"]
    assert observed["command"][3:7] == ["-l", "heb+eng", "--psm", "3"]
    assert observed["kwargs"]["timeout"] == 45.0
    assert output.text == "פלט\n"
    assert output.tsv == TSV
