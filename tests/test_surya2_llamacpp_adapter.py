from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from hebocrbench.adapters.surya2_llamacpp import (
    FULL_PAGE_PROMPT,
    Surya2InvocationError,
    Surya2PageOutput,
    invoke_surya2_page,
    invoke_surya2_server_page,
    parse_surya2_html,
    run_surya2_page_ocr,
)
from hebocrbench.validator import validate_prediction_records


HTML = """<div data-bbox="520 80 960 210" data-label="Section-Header">
<h2>כותרת 2026</h2></div>
<div data-label="Text" data-bbox="100 260 900 500">
<p>שלום עולם.</p><p>OCR v2</p></div>"""

# Verbatim structure observed from a live Surya OCR 2 generation on the clear
# RTL table diagnostic image.  The OCR mistake in the price cells is retained:
# this parser must map model output, never correct it from gold knowledge.
TABLE_HTML = """<div data-bbox="323 54 941 106" data-label="Section-Header">
<h2>טבלת הזמנות — עמודות לוגיות מימין לשמאל</h2>
</div>
<div data-bbox="58 169 933 822" data-label="Table">
<table border="1">
<thead>
<tr><th>פריט</th><th>כמות</th><th>מחיר</th><th>תאריך</th></tr>
</thead>
<tbody>
<tr><td>מחברת</td><td>12</td><td>48.00 שם</td><td>22/07/2026</td></tr>
<tr><td>עיפרון HB</td><td>30</td><td>15.90 שם</td><td>23/07/2026</td></tr>
<tr><td>סרגל cm 30</td><td>5</td><td>27.50 שם</td><td>24/07/2026</td></tr>
</tbody>
</table>
</div>"""


class _GuardedRecord(dict):
    def __init__(self, allowed, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed = set(allowed)

    def get(self, key, default=None):
        if key not in self.allowed:
            raise AssertionError(f"adapter read forbidden gold field: {key}")
        return super().get(key, default)


def _weights(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "surya-2.gguf"
    projector = tmp_path / "surya-2-mmproj.gguf"
    model.write_bytes(b"model fixture")
    projector.write_bytes(b"projector fixture")
    return model, projector


def test_html_parser_preserves_order_text_direction_and_scaled_boxes():
    regions, page_text, tables, diagnostics = parse_surya2_html(HTML, 1000, 2000)

    assert page_text == "כותרת 2026\nשלום עולם.\nOCR v2"
    assert [region["type"] for region in regions] == ["section_header", "text"]
    assert regions[0]["polygon"] == [
        [520.0, 160.0],
        [960.0, 160.0],
        [960.0, 420.0],
        [520.0, 420.0],
    ]
    assert regions[0]["base_direction"] == "rtl"
    assert regions[1]["lines"][0]["geometry_level"] == "block-box-proxy"
    assert diagnostics["engine_blocks"] == 2
    assert diagnostics["invalid_block_boxes"] == 0
    assert diagnostics["protocol_fallback"] is False
    assert tables == []


def test_real_surya_semantic_table_maps_to_logical_grid_without_cell_geometry():
    regions, page_text, tables, diagnostics = parse_surya2_html(TABLE_HTML, 1800, 1200)

    assert len(regions) == 2
    assert regions[1]["type"] == "table"
    assert "48.00 שם" in page_text
    assert len(tables) == 1
    table = tables[0]
    assert table["table_id"] == "pred-surya2-table-0001"
    assert table["region_id"] == "pred-surya2-r0002"
    assert table["polygon"] == [
        [104.4, 202.8],
        [1679.4, 202.8],
        [1679.4, 986.4],
        [104.4, 986.4],
    ]
    assert (table["n_rows"], table["n_cols"], len(table["cells"])) == (4, 4, 16)
    assert table["cells"][0] == {
        "row_start": 0,
        "row_end": 1,
        "col_start": 0,
        "col_end": 1,
        "text": "פריט",
    }
    assert table["cells"][6]["text"] == "48.00 שם"
    assert all("polygon" not in cell for cell in table["cells"])
    assert diagnostics["explicit_table_blocks"] == 1
    assert diagnostics["semantic_table_elements"] == 1
    assert diagnostics["emitted_tables"] == 1
    assert diagnostics["emitted_table_cells"] == 16
    assert diagnostics["rejected_semantic_tables"] == 0


def test_semantic_table_honors_rowspan_and_colspan():
    html = """<div data-label="Table" data-bbox="10 20 900 800"><table>
    <tr><th rowspan="2">א</th><th colspan="2">ב</th></tr>
    <tr><td>ג</td><td>ד</td></tr></table></div>"""

    _, _, tables, diagnostics = parse_surya2_html(html, 1000, 1000)

    assert diagnostics["rejected_semantic_tables"] == 0
    assert (tables[0]["n_rows"], tables[0]["n_cols"]) == (2, 3)
    assert tables[0]["cells"] == [
        {"row_start": 0, "row_end": 2, "col_start": 0, "col_end": 1, "text": "א"},
        {"row_start": 0, "row_end": 1, "col_start": 1, "col_end": 3, "text": "ב"},
        {"row_start": 1, "row_end": 2, "col_start": 1, "col_end": 2, "text": "ג"},
        {"row_start": 1, "row_end": 2, "col_start": 2, "col_end": 3, "text": "ד"},
    ]


@pytest.mark.parametrize(
    ("html", "diagnostic"),
    (
        (
            '<div data-label="Table" data-bbox="0 0 1000 1000"><p>א  ב  ג</p></div>',
            "table_blocks_without_semantic_grid",
        ),
        (
            '<div data-label="Text" data-bbox="0 0 1000 1000"><table>'
            "<tr><td>א</td></tr></table></div>",
            "semantic_table_elements",
        ),
    ),
)
def test_table_parser_never_infers_grid_without_explicit_surya_table_protocol(html, diagnostic):
    _, _, tables, diagnostics = parse_surya2_html(html, 1000, 1000)

    assert tables == []
    assert diagnostics[diagnostic] == (1 if diagnostic.startswith("table_blocks") else 0)


def test_malformed_semantic_span_is_rejected_instead_of_repaired():
    html = """<div data-label="Table" data-bbox="0 0 1000 1000"><table>
    <tr><td rowspan="0">א</td><td>ב</td></tr></table></div>"""

    _, _, tables, diagnostics = parse_surya2_html(html, 1000, 1000)

    assert tables == []
    assert diagnostics["semantic_table_elements"] == 1
    assert diagnostics["rejected_semantic_tables"] == 1


def test_page_adapter_is_blind_and_emits_schema_valid_prediction(tmp_path):
    image_path = tmp_path / "images" / "page.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"runner fixture; not decoded")
    model, projector = _weights(tmp_path)

    image = _GuardedRecord(
        {"path"},
        path="images/page.png",
        width=9999,
        rotation_degrees=270,
    )
    envelope = _GuardedRecord(
        {"page_id", "image"},
        page_id="secret-page",
        image=image,
        page_text="אסור לקרוא",
        regions=[{"text": "אסור לקרוא"}],
        reading_order={"edges": [["secret-a", "secret-b"]]},
    )
    seen = []

    def fake_runner(path, selected_model, selected_projector, prompt, maximum, image_max, timeout):
        seen.append((path, selected_model, selected_projector, prompt, maximum, image_max, timeout))
        return Surya2PageOutput(HTML, 1000, 2000)

    prediction = run_surya2_page_ocr(
        [envelope],
        dataset_root=tmp_path,
        model_path=model,
        mmproj_path=projector,
        max_tokens=512,
        image_max_tokens=1024,
        timeout_seconds=45,
        runner=fake_runner,
        model_version="surya-test",
    )[0]

    assert seen == [
        (
            image_path.resolve(),
            model.resolve(),
            projector.resolve(),
            FULL_PAGE_PROMPT,
            512,
            1024,
            45,
        )
    ]
    assert prediction["status"] == "ok"
    assert prediction["page_text"] == "כותרת 2026\nשלום עולם.\nOCR v2"
    assert prediction["reading_order"] == {"edges": [["pred-surya2-r0001", "pred-surya2-r0002"]]}
    assert prediction["model"]["family"] == "surya-ocr-2"
    assert prediction["model"]["adapter"] == "surya2_llamacpp_page_e2e"
    assert prediction["model"]["oracle_layout"] is False
    assert prediction["model"]["gold_assistance"] is False
    assert prediction["model"]["input_mode"] == "blind_full_page_image"
    assert prediction["model"]["engine"] == "llama.cpp"
    assert len(prediction["model"]["model_sha256"]) == 64
    assert prediction["failure"] is None
    assert prediction["api_failures"] == 0
    assert validate_prediction_records([prediction]).is_valid


def test_page_adapter_emits_table_from_raw_generation_without_reading_gold(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"blind image envelope fixture")
    model, projector = _weights(tmp_path)
    envelope = _GuardedRecord(
        {"page_id", "image"},
        page_id="blind-table-page",
        image=_GuardedRecord({"path"}, path="page.png", width=9999),
        tables=[{"table_id": "forbidden-gold-table"}],
        page_text="forbidden gold text",
    )

    prediction = run_surya2_page_ocr(
        [envelope],
        dataset_root=tmp_path,
        model_path=model,
        mmproj_path=projector,
        runner=lambda *args: Surya2PageOutput(TABLE_HTML, 1800, 1200),
    )[0]

    assert prediction["status"] == "ok"
    assert len(prediction["tables"]) == 1
    assert prediction["tables"][0]["table_id"] == "pred-surya2-table-0001"
    assert prediction["tables"][0]["cells"][0]["text"] == "פריט"
    assert prediction["adapter_diagnostics"]["emitted_table_cells"] == 16
    assert validate_prediction_records([prediction]).is_valid


def test_page_adapter_keeps_bounded_visible_failure(tmp_path):
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"runner fixture; not decoded")
    model, projector = _weights(tmp_path)

    def failing_runner(*args):
        raise Surya2InvocationError(
            "engine failed",
            return_code=17,
            stderr="e" * 5000,
            stdout="s" * 5000,
        )

    prediction = run_surya2_page_ocr(
        [{"page_id": "failed", "image": {"path": "page.png"}}],
        dataset_root=tmp_path,
        model_path=model,
        mmproj_path=projector,
        runner=failing_runner,
    )[0]

    assert prediction["status"] == "failed"
    assert prediction["page_text"] == ""
    assert prediction["regions"] == []
    assert prediction["failure"]["return_code"] == 17
    assert len(prediction["failure"]["stderr"]) == 4000
    assert len(prediction["failure"]["stdout"]) == 4000
    assert prediction["api_failures"] == 1
    assert validate_prediction_records([prediction]).is_valid


def test_invoke_uses_single_turn_deterministic_cli_contract(tmp_path, monkeypatch):
    image_path = tmp_path / "page image.png"
    Image.new("RGB", (800, 600), "white").save(image_path)
    model, projector = _weights(tmp_path)
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        stdout = (
            "banner\n> "
            + FULL_PAGE_PROMPT
            + '\n<div data-bbox="0 0 1000 1000" data-label="Text"><p>שלום</p></div>'
            + "\n\n[ Prompt: 1 t/s | Generation: 2 t/s ]\n\nExiting...\n"
        )
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("hebocrbench.adapters.surya2_llamacpp.subprocess.run", fake_run)
    output = invoke_surya2_page(
        image_path,
        model,
        projector,
        FULL_PAGE_PROMPT,
        512,
        1024,
        45.0,
        executable="llama-cli-test",
    )

    command = observed["command"]
    assert command[0] == "llama-cli-test"
    assert command[1:7] == [
        "-m",
        str(model),
        "--mmproj",
        str(projector),
        "--image",
        str(image_path),
    ]
    assert "--single-turn" in command
    assert command[command.index("--temp") + 1] == "0"
    assert command[command.index("--seed") + 1] == "1"
    assert observed["kwargs"]["timeout"] == 45.0
    assert output.image_width == 800
    assert output.image_height == 600
    assert output.html.startswith("<div data-bbox=")
    assert output.html.endswith("</div>")


def test_server_backend_posts_blind_image_with_deterministic_sampling(tmp_path):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (320, 180), "white").save(image_path)
    seen = {}

    def fake_post(url, body, timeout, limit):
        seen.update(url=url, timeout=timeout, limit=limit, payload=json.loads(body))
        return json.dumps(
            {"model": "surya-hash-bound", "choices": [{"message": {"content": HTML}}]},
            ensure_ascii=False,
        ).encode("utf-8")

    output = invoke_surya2_server_page(
        image_path,
        "http://127.0.0.1:8123",
        FULL_PAGE_PROMPT,
        777,
        45.0,
        model_id="surya-hash-bound",
        http_post=fake_post,
    )

    assert seen["url"] == "http://127.0.0.1:8123/v1/chat/completions"
    assert seen["timeout"] == 45.0
    payload = seen["payload"]
    assert payload["temperature"] == 0
    assert payload["seed"] == 1
    assert payload["max_tokens"] == 777
    assert payload["stream"] is False
    assert payload["model"] == "surya-hash-bound"
    content = payload["messages"][0]["content"]
    assert content[1] == {"type": "text", "text": FULL_PAGE_PROMPT}
    prefix, encoded = content[0]["image_url"]["url"].split(",", 1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded) == image_path.read_bytes()
    assert output == Surya2PageOutput(HTML, 320, 180)


def test_server_backend_rejects_response_from_a_different_model(tmp_path):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (20, 10), "white").save(image_path)

    def wrong_model(*args):
        return json.dumps(
            {"model": "other", "choices": [{"message": {"content": HTML}}]},
            ensure_ascii=False,
        ).encode("utf-8")

    with pytest.raises(Surya2InvocationError, match="artifact hashes"):
        invoke_surya2_server_page(
            image_path,
            "http://127.0.0.1:8123",
            FULL_PAGE_PROMPT,
            64,
            5.0,
            model_id="surya-hash-bound",
            http_post=wrong_model,
        )


@pytest.mark.parametrize(
    "url",
    (
        "https://127.0.0.1:8123",
        "http://example.com:8123",
        "http://127.0.0.1",
        "http://user:password@127.0.0.1:8123",
        "http://127.0.0.1:8123/redirect",
        "http://127.0.0.1:8123?next=http://example.com",
    ),
)
def test_server_backend_rejects_every_non_loopback_or_ambiguous_url(tmp_path, url):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (20, 10), "white").save(image_path)
    with pytest.raises(ValueError):
        invoke_surya2_server_page(
            image_path,
            url,
            FULL_PAGE_PROMPT,
            64,
            5.0,
            http_post=lambda *args: pytest.fail("invalid URL reached HTTP transport"),
        )


def test_server_backend_bounds_response_and_failure_evidence(tmp_path):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (20, 10), "white").save(image_path)

    with pytest.raises(Surya2InvocationError, match="100-byte safety limit"):
        invoke_surya2_server_page(
            image_path,
            "http://localhost:8123/v1/chat/completions",
            FULL_PAGE_PROMPT,
            64,
            5.0,
            http_post=lambda *args: b"x" * 101,
            response_limit_bytes=100,
        )

    model, projector = _weights(tmp_path)

    def failed_http(*args):
        raise Surya2InvocationError("server failed", stderr="e" * 5000)

    prediction = run_surya2_page_ocr(
        [{"page_id": "server-failed", "image": {"path": "page.png"}}],
        dataset_root=tmp_path,
        model_path=model,
        mmproj_path=projector,
        backend="server",
        server_url="http://[::1]:8123",
        server_http_post=failed_http,
    )[0]
    assert prediction["status"] == "failed"
    assert len(prediction["failure"]["stderr"]) == 4000
    assert prediction["model"]["inference_backend"] == "server"
    assert prediction["model"]["server_url"] == "http://[::1]:8123/v1/chat/completions"
    assert prediction["model"]["adapter"] == "surya2_llamacpp_server_page_e2e"
    assert validate_prediction_records([prediction]).is_valid


def test_invalid_configuration_rejected_before_inference(tmp_path):
    model, projector = _weights(tmp_path)
    envelope = [{"page_id": "p1", "image": {"path": "missing.png"}}]

    for kwargs, message in (
        ({"prompt": ""}, "prompt must not be empty"),
        ({"max_tokens": 0}, "max_tokens must be positive"),
        ({"image_max_tokens": 0}, "image_max_tokens must be positive"),
        ({"timeout_seconds": 0}, "timeout_seconds must be positive"),
    ):
        try:
            run_surya2_page_ocr(
                envelope,
                dataset_root=tmp_path,
                model_path=model,
                mmproj_path=projector,
                runner=lambda *args: Surya2PageOutput("", 1, 1),
                **kwargs,
            )
        except ValueError as exc:
            assert str(exc) == message
        else:
            raise AssertionError(f"configuration should fail: {kwargs}")
