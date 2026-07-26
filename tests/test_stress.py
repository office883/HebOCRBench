from __future__ import annotations


from hebocrbench.io import load_jsonl, sha256_file
from hebocrbench.stress import discover_hebrew_font, generate_stress_suite
from hebocrbench.validator import validate_gold_records


def test_stress_generation_is_deterministic_and_logical(tmp_path):
    font = discover_hebrew_font()
    first = generate_stress_suite(
        tmp_path / "one", seed=17, variants=("clean", "blur"), limit=2, font_path=font
    )
    second = generate_stress_suite(
        tmp_path / "two", seed=17, variants=("clean", "blur"), limit=2, font_path=font
    )
    first_pages = load_jsonl(first.gold_path)
    second_pages = load_jsonl(second.gold_path)
    assert [page["page_id"] for page in first_pages] == [page["page_id"] for page in second_pages]
    assert [page["image"]["sha256"] for page in first_pages] == [
        page["image"]["sha256"] for page in second_pages
    ]
    texts = [
        line["text"]
        for page in first_pages
        for region in page["regions"]
        for line in region["lines"]
    ]
    assert "בשנת 2026 הופעלה גרסה OCR-v2.1 (בטא)." in texts
    assert "אלפבית עברי: אבגדהוזחטיךכלםמןנסעףפץצקרשת." in texts
    assert all(text != text[::-1] for text in texts)
    assert validate_gold_records(first_pages, dataset_root=first.root).is_valid
    for page in first_pages:
        assert sha256_file(first.root / page["image"]["path"]) == page["image"]["sha256"]


def test_structured_pages_encode_logical_rtl_table_columns(tmp_path):
    result = generate_stress_suite(
        tmp_path / "suite", seed=9, variants=("clean",), include_structured=True, limit=1
    )
    pages = load_jsonl(result.gold_path)
    table_page = next(page for page in pages if page["track"] == "structured_documents")
    table = table_page["tables"][0]
    first_logical_cell = next(
        cell for cell in table["cells"] if cell["row_start"] == 0 and cell["col_start"] == 0
    )
    last_logical_cell = next(
        cell
        for cell in table["cells"]
        if cell["row_start"] == 0 and cell["col_start"] == table["n_cols"] - 1
    )
    assert min(point[0] for point in first_logical_cell["polygon"]) > min(
        point[0] for point in last_logical_cell["polygon"]
    )


def test_all_declared_stress_text_is_nfc():
    import unicodedata
    from hebocrbench.stress import load_stress_cases

    assert all(unicodedata.is_normalized("NFC", case["text"]) for case in load_stress_cases())


def test_official_stress_suite_contains_only_modern_hebrew_cases():
    from hebocrbench.modern_scope import modern_scope_issues
    from hebocrbench.stress import load_stress_cases

    cases = load_stress_cases()
    assert all(case.get("language", "he") == "he" for case in cases)
    assert all(
        not ({"yiddish_codepoint", "biblical_mark"} & set(modern_scope_issues(case["text"])))
        for case in cases
    )
