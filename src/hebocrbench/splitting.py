"""Deterministic, group-disjoint dataset splitting."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Mapping, Sequence


class SplitPolicyError(ValueError):
    """A split policy is invalid or cannot be applied safely."""


_ALLOWED_SPLITS = ("train", "dev", "test")
_ALLOWED_FIXED_SPLITS = (*_ALLOWED_SPLITS, "diagnostic")


def _field_value(record: Mapping[str, object], field: str) -> object:
    value: object = record
    for part in field.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise SplitPolicyError(
                f"Group field {field!r} is missing on page {record.get('page_id', '<unknown>')}"
            )
        value = value[part]
    if value in (None, ""):
        raise SplitPolicyError(
            f"Group field {field!r} is empty on page {record.get('page_id', '<unknown>')}"
        )
    return value


def _group_key(record: Mapping[str, object], fields: Sequence[str]) -> str:
    values = [_field_value(record, field) for field in fields]
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validated_ratios(policy: Mapping[str, object]) -> tuple[tuple[str, float], ...]:
    raw = policy.get("ratios")
    if not isinstance(raw, Mapping) or not raw:
        raise SplitPolicyError("hash_group requires a non-empty ratios mapping")
    unknown = set(str(key) for key in raw) - set(_ALLOWED_SPLITS)
    if unknown:
        raise SplitPolicyError(f"Unsupported split names: {', '.join(sorted(unknown))}")
    values: dict[str, float] = {}
    for split in _ALLOWED_SPLITS:
        if split not in raw:
            continue
        value = float(raw[split])
        if not math.isfinite(value) or value <= 0:
            raise SplitPolicyError(f"Ratio for {split} must be finite and greater than zero")
        values[split] = value
    if not math.isclose(sum(values.values()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise SplitPolicyError(f"Split ratios must sum to 1.0, got {sum(values.values()):.12g}")
    return tuple((split, values[split]) for split in _ALLOWED_SPLITS if split in values)


def _fixed_split(policy: Mapping[str, object]) -> str:
    raw = policy.get("ratios")
    if not isinstance(raw, Mapping) or len(raw) != 1:
        raise SplitPolicyError("fixed strategy requires exactly one ratios entry")
    split, raw_ratio = next(iter(raw.items()))
    split = str(split)
    if split not in _ALLOWED_FIXED_SPLITS:
        raise SplitPolicyError(f"Unsupported fixed split name: {split}")
    ratio = float(raw_ratio)
    if not math.isfinite(ratio) or not math.isclose(ratio, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise SplitPolicyError("fixed strategy ratio must equal 1.0")
    return split


def _hash_unit_interval(seed: object, group_key: str) -> float:
    payload = f"hebocrbench-split-v1\0{seed}\0{group_key}".encode("utf-8")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value / 2**64


def _choose_split(unit: float, ratios: Sequence[tuple[str, float]]) -> str:
    cumulative = 0.0
    for split, ratio in ratios:
        cumulative += ratio
        if unit < cumulative:
            return split
    return ratios[-1][0]


def assign_splits(
    records: Sequence[Mapping[str, object]], policy: Mapping[str, object]
) -> list[dict[str, object]]:
    """Return copied records with deterministic split assignments.

    ``hash_group`` hashes an explicit group key, never a page index. Consequently,
    adding or reordering pages cannot move existing groups between splits.
    """

    strategy = str(policy.get("strategy", "")).strip().lower()
    fields_raw = policy.get("group_fields", ["document_id"])
    if not isinstance(fields_raw, Sequence) or isinstance(fields_raw, (str, bytes)):
        raise SplitPolicyError("group_fields must be an array of field paths")
    fields = tuple(str(field) for field in fields_raw)
    if not fields:
        raise SplitPolicyError("group_fields must contain at least one field")

    copied = [deepcopy(dict(record)) for record in records]
    if strategy in {"upstream", "official"}:
        for record in copied:
            split = record.get("split")
            if split not in {*_ALLOWED_SPLITS, "diagnostic"}:
                raise SplitPolicyError(
                    f"Upstream split missing or invalid on page {record.get('page_id', '<unknown>')}"
                )
        return copied
    if strategy in {"none", "preserve"}:
        return copied
    if strategy in {"fixed", "diagnostic"}:
        split = _fixed_split(policy)
        for record in copied:
            record["split"] = split
        return copied
    if strategy != "hash_group":
        raise SplitPolicyError(f"Unsupported split strategy: {strategy!r}")

    ratios = _validated_ratios(policy)
    seed = policy.get("seed", 0)
    assignments: dict[str, str] = {}
    for record in copied:
        key = _group_key(record, fields)
        split = assignments.setdefault(key, _choose_split(_hash_unit_interval(seed, key), ratios))
        record["split"] = split
    return copied
