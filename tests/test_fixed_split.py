from __future__ import annotations

import pytest

from hebocrbench.splitting import SplitPolicyError, assign_splits


def test_fixed_split_assigns_every_record_to_the_single_partition() -> None:
    records = [
        {"page_id": "a", "document_id": "a", "split": "train"},
        {"page_id": "b", "document_id": "b", "split": "test"},
    ]
    assigned = assign_splits(
        records,
        {
            "strategy": "fixed",
            "group_fields": ["document_id"],
            "ratios": {"diagnostic": 1.0},
        },
    )
    assert [record["split"] for record in assigned] == ["diagnostic", "diagnostic"]
    assert [record["split"] for record in records] == ["train", "test"]


@pytest.mark.parametrize(
    "ratios",
    [
        {"train": 0.5, "diagnostic": 0.5},
        {"mystery": 1.0},
        {},
    ],
)
def test_fixed_split_rejects_ambiguous_or_unknown_partitions(ratios) -> None:
    with pytest.raises(SplitPolicyError):
        assign_splits(
            [{"page_id": "a", "document_id": "a", "split": "train"}],
            {
                "strategy": "fixed",
                "group_fields": ["document_id"],
                "ratios": ratios,
            },
        )
