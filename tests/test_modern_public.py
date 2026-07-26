import hashlib
import json

from hebocrbench.modern_public import (
    rank_page_candidates,
    template_family_id,
    write_source_evidence,
)


def test_rank_page_candidates_prefers_tables_forms_and_mixed_bidi():
    pages = [
        {
            "page_number": 1,
            "usable": True,
            "table_count": 0,
            "form_signal": 0,
            "mixed_bidi": False,
            "hebrew_letters": 500,
        },
        {
            "page_number": 2,
            "usable": True,
            "table_count": 2,
            "form_signal": 0,
            "mixed_bidi": True,
            "hebrew_letters": 400,
        },
        {
            "page_number": 3,
            "usable": True,
            "table_count": 0,
            "form_signal": 3,
            "mixed_bidi": True,
            "hebrew_letters": 300,
        },
        {
            "page_number": 4,
            "usable": False,
            "table_count": 9,
            "form_signal": 9,
            "mixed_bidi": True,
            "hebrew_letters": 900,
        },
    ]
    assert rank_page_candidates(pages, maximum=3) == [2, 3, 1]


def test_template_family_id_is_stable_and_ignores_document_numbers():
    left = template_family_id(
        catalog_table="KNS_DocumentLaw",
        group_type_id=10,
        width=595.0,
        height=842.0,
        fonts=["Arial", "David"],
        header_text="מסמך 123 תיק 456",
    )
    right = template_family_id(
        catalog_table="KNS_DocumentLaw",
        group_type_id=10,
        width=595.01,
        height=841.99,
        fonts=["David", "Arial"],
        header_text="מסמך 999 תיק 777",
    )
    assert left == right
    assert left.startswith("knesset-documentlaw-10-")


def test_write_source_evidence_binds_every_file(tmp_path):
    (tmp_path / "a.txt").write_text("א", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.bin").write_bytes(b"b")
    result = write_source_evidence(
        tmp_path,
        source_id="modern-public-documents-v1",
        source_version="2026-07-25",
        artifact_id="knesset-open-data-api",
        requested_revision="snapshot-v1",
        extra={"accepted_page_count": 2},
    )
    marker = json.loads((tmp_path / ".hebocrbench-source.json").read_text(encoding="utf-8"))
    inventory = json.loads((tmp_path / "SOURCE_INVENTORY.json").read_text(encoding="utf-8"))
    assert marker["verification_status"] == "verified"
    assert marker["tree_sha256"] == inventory["tree_sha256"]
    assert result["inventory"]["file_count"] >= 3
    paths = {item["path"] for item in inventory["files"]}
    assert {"a.txt", "nested/b.bin", "SOURCE_EVIDENCE.json"} <= paths
    expected = hashlib.sha256(
        json.dumps(
            inventory["files"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert inventory["tree_sha256"] == expected
