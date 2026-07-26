from copy import deepcopy
import unicodedata

from hebocrbench.validator import audit_split_leakage, validate_gold_records


def test_duplicate_region_and_line_ids_are_errors(gold_page):
    duplicate = deepcopy(gold_page["regions"][0])
    duplicate["lines"][0]["text"] = "שורה אחרת"
    gold_page["regions"].append(duplicate)
    report = validate_gold_records([gold_page])
    codes = {issue.code for issue in report.errors}
    assert "duplicate_region_id" in codes
    assert "duplicate_line_id" in codes


def test_non_nfc_and_directional_controls_are_rejected_in_gold(gold_page):
    gold_page["regions"][0]["lines"][0]["text"] = "\u200fש\u05b8\u05c1ל"
    report = validate_gold_records([gold_page])
    codes = {issue.code for issue in report.errors}
    assert "bidi_control_in_gold" in codes
    assert "non_nfc_text" in codes or unicodedata.is_normalized(
        "NFC", gold_page["regions"][0]["lines"][0]["text"]
    )


def test_dangling_combining_mark_is_rejected(gold_page):
    gold_page["regions"][0]["lines"][0]["text"] = "\u05b8שלום"
    report = validate_gold_records([gold_page])
    assert any(issue.code == "dangling_combining_mark" for issue in report.errors)


def test_polygon_outside_image_is_rejected(gold_page):
    gold_page["regions"][0]["polygon"][0] = [-1, 40]
    report = validate_gold_records([gold_page])
    assert any(issue.code == "polygon_out_of_bounds" for issue in report.errors)


def test_reading_order_cycle_is_rejected(gold_page):
    second = deepcopy(gold_page["regions"][0])
    second["region_id"] = "r2"
    second["lines"][0]["line_id"] = "l2"
    gold_page["regions"].append(second)
    gold_page["reading_order"]["edges"] = [["r1", "r2"], ["r2", "r1"]]
    report = validate_gold_records([gold_page])
    assert any(issue.code == "reading_order_cycle" for issue in report.errors)


def test_split_audit_detects_document_and_hash_leakage(gold_page):
    other = deepcopy(gold_page)
    other["page_id"] = "doc1-p2"
    other["split"] = "test"
    gold_page["image"]["sha256"] = "abc"
    other["image"]["sha256"] = "abc"
    report = audit_split_leakage([gold_page, other])
    codes = {issue.code for issue in report.errors}
    assert "split_leak_document_id" in codes
    assert "split_leak_image_hash" in codes
