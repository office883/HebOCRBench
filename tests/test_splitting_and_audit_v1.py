from __future__ import annotations

from copy import deepcopy
import hashlib

from PIL import Image

from hebocrbench.dataset_audit import audit_dataset
from hebocrbench.splitting import SplitPolicyError, assign_splits


def _record(gold_page, *, page_id: str, document_id: str, split: str = "train"):
    record = deepcopy(gold_page)
    record["page_id"] = page_id
    record["document_id"] = document_id
    record["split"] = split
    record["metadata"].update(
        {
            "source_id": "fixture-source",
            "source_version": "1",
            "source_page_id": page_id,
            "source_url": "https://example.invalid/source",
            "rights_uri": "https://creativecommons.org/licenses/by/4.0/",
            "redistribution": "allowed",
            "citation_key": "fixture",
        }
    )
    return record


def test_hash_group_split_is_deterministic_and_document_disjoint(gold_page):
    records = []
    for document_index in range(30):
        for page_index in range(2):
            records.append(
                _record(
                    gold_page,
                    page_id=f"d{document_index}-p{page_index}",
                    document_id=f"d{document_index}",
                    split="train",
                )
            )
    policy = {
        "strategy": "hash_group",
        "group_fields": ["document_id"],
        "ratios": {"train": 0.6, "dev": 0.2, "test": 0.2},
        "seed": 20260723,
    }

    first = assign_splits(records, policy)
    second = assign_splits(records, policy)

    assert [record["split"] for record in first] == [record["split"] for record in second]
    by_document = {}
    for record in first:
        by_document.setdefault(record["document_id"], set()).add(record["split"])
    assert all(len(splits) == 1 for splits in by_document.values())
    assert {record["split"] for record in first} == {"train", "dev", "test"}


def test_upstream_split_strategy_preserves_official_partition(gold_page):
    records = [
        _record(gold_page, page_id="a", document_id="a", split="train"),
        _record(gold_page, page_id="b", document_id="b", split="test"),
    ]

    assigned = assign_splits(records, {"strategy": "upstream", "group_fields": ["document_id"]})

    assert [record["split"] for record in assigned] == ["train", "test"]


def test_invalid_split_ratios_are_rejected(gold_page):
    records = [_record(gold_page, page_id="a", document_id="a")]

    try:
        assign_splits(
            records,
            {
                "strategy": "hash_group",
                "group_fields": ["document_id"],
                "ratios": {"train": 0.9, "test": 0.9},
                "seed": 1,
            },
        )
    except SplitPolicyError as exc:
        assert "sum" in str(exc)
    else:
        raise AssertionError("invalid ratios must fail")


def test_dataset_audit_detects_group_image_and_text_leakage(tmp_path, gold_page):
    image_path = tmp_path / "same.png"
    Image.new("RGB", (1200, 400), "white").save(image_path)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    first = _record(gold_page, page_id="p1", document_id="shared", split="train")
    second = _record(gold_page, page_id="p2", document_id="shared", split="test")
    for record in (first, second):
        record["image"]["path"] = "same.png"
        record["image"]["sha256"] = digest

    report = audit_dataset([first, second], tmp_path)
    codes = {issue.code for issue in report.errors}

    assert "split_leak_document_id" in codes
    assert "split_leak_image_hash" in codes
    assert any(issue.code == "split_duplicate_text" for issue in report.warnings)
    assert report.is_valid is False


def test_dataset_audit_verifies_real_image_hash_and_required_provenance(tmp_path, gold_page):
    image_path = tmp_path / "page.png"
    Image.new("RGB", (1200, 400), "white").save(image_path)
    record = _record(gold_page, page_id="p1", document_id="d1")
    record["image"]["path"] = "page.png"
    record["image"]["sha256"] = "0" * 64
    del record["metadata"]["citation_key"]

    report = audit_dataset([record], tmp_path)
    codes = {issue.code for issue in report.errors}

    assert "image_sha256_mismatch" in codes
    assert "missing_provenance_citation_key" in codes


def test_dataset_audit_reports_near_duplicate_cross_split_text(tmp_path, gold_page):
    records = []
    for index, (split, text) in enumerate(
        [
            ("train", "זהו טקסט ארוך מספיק לבדיקת כמעט כפילות במסמך עברי מספר אחת"),
            ("test", "זהו טקסט ארוך מספיק לבדיקת כמעט כפילות במסמך עברי מספר שתיים"),
        ]
    ):
        path = tmp_path / f"p{index}.png"
        Image.new("RGB", (1200, 400), "white").save(path)
        record = _record(gold_page, page_id=f"p{index}", document_id=f"d{index}", split=split)
        record["regions"][0]["lines"][0]["text"] = text
        record["image"]["path"] = path.name
        record["image"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(record)

    report = audit_dataset(records, tmp_path, near_text_threshold=0.88)

    assert any(issue.code == "split_near_duplicate_text" for issue in report.warnings)
    assert report.stats["pages"] == 2
    assert report.stats["splits"] == {"test": 1, "train": 1}
