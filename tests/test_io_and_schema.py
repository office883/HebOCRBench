from pathlib import Path

import pytest

from hebocrbench.io import DuplicatePageIdError, index_by_page_id, load_jsonl, write_jsonl
from hebocrbench.validator import validate_gold_records, validate_prediction_records


def test_gold_and_prediction_fixtures_satisfy_json_schema(gold_page, prediction_page):
    assert validate_gold_records([gold_page]).is_valid
    assert validate_prediction_records([prediction_page]).is_valid


def test_jsonl_roundtrip_preserves_unicode(tmp_path, gold_page):
    path = tmp_path / "gold.jsonl"
    write_jsonl(path, [gold_page])
    loaded = load_jsonl(path)
    assert loaded == [gold_page]
    assert "2026" in path.read_text(encoding="utf-8")


def test_duplicate_page_id_is_rejected(gold_page):
    with pytest.raises(DuplicatePageIdError):
        index_by_page_id([gold_page, gold_page])


def test_schema_rejects_missing_image_dimensions(gold_page):
    del gold_page["image"]["width"]
    report = validate_gold_records([gold_page])
    assert not report.is_valid
    assert any(issue.code == "schema" for issue in report.errors)
