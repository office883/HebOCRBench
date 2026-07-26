"""Deterministic assignment of predicted document units to ground truth units.

The benchmark has two fundamentally different evaluation modes:

* oracle/recognition mode may use stable shared IDs supplied by the benchmark;
* blind end-to-end mode must assign units without access to gold IDs.

This module makes that distinction explicit and returns the identity maps needed
by downstream structured metrics such as reading order.  Unmatched prediction
units are retained under synthetic names so that false-positive graph edges are
never silently discarded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .geometry import match_geometries


@dataclass(frozen=True, slots=True)
class UnitMatch:
    """One one-to-one assignment between a gold and prediction unit."""

    gold_index: int
    prediction_index: int
    method: str
    score: float
    gold_id: str
    prediction_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "gold_index": self.gold_index,
            "prediction_index": self.prediction_index,
            "method": self.method,
            "score": self.score,
            "gold_id": self.gold_id,
            "prediction_id": self.prediction_id,
        }


@dataclass(frozen=True, slots=True)
class UnitAssignment:
    """Complete one-to-one assignment, including misses and extras."""

    id_key: str
    gold_count: int
    prediction_count: int
    matches: tuple[UnitMatch, ...]
    unmatched_gold_indices: tuple[int, ...]
    unmatched_prediction_indices: tuple[int, ...]
    gold_ids: tuple[str, ...]
    prediction_ids: tuple[str, ...]
    split_gold_items: int = 0
    merged_prediction_items: int = 0

    @property
    def gold_to_prediction_id(self) -> dict[str, str]:
        return {match.gold_id: match.prediction_id for match in self.matches}

    @property
    def prediction_to_gold_id(self) -> dict[str, str]:
        return {match.prediction_id: match.gold_id for match in self.matches}

    @property
    def precision(self) -> float:
        if self.gold_count == self.prediction_count == 0:
            return 1.0
        return len(self.matches) / max(1, self.prediction_count)

    @property
    def recall(self) -> float:
        if self.gold_count == self.prediction_count == 0:
            return 1.0
        return len(self.matches) / max(1, self.gold_count)

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    @property
    def mean_score(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.score for match in self.matches) / len(self.matches)

    def to_dict(self) -> dict[str, object]:
        return {
            "id_key": self.id_key,
            "gold_count": self.gold_count,
            "prediction_count": self.prediction_count,
            "matched": len(self.matches),
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "mean_score": self.mean_score,
            "mean_iou": self.mean_score,
            "unmatched_gold_indices": list(self.unmatched_gold_indices),
            "unmatched_prediction_indices": list(self.unmatched_prediction_indices),
            "unmatched_gold_ids": [self.gold_ids[index] for index in self.unmatched_gold_indices],
            "unmatched_prediction_ids": [
                self.prediction_ids[index] for index in self.unmatched_prediction_indices
            ],
            "split_gold_items": self.split_gold_items,
            "merged_prediction_items": self.merged_prediction_items,
            "gold_to_prediction_id": self.gold_to_prediction_id,
            "prediction_to_gold_id": self.prediction_to_gold_id,
            "matches": [match.to_dict() for match in self.matches],
        }


def _stable_ids(items: Sequence[Mapping[str, object]], id_key: str, prefix: str) -> tuple[str, ...]:
    """Return unique stable IDs without mutating or trusting malformed duplicates."""

    raw_ids = [str(item.get(id_key, f"{prefix}-{index}")) for index, item in enumerate(items)]
    counts: dict[str, int] = {}
    result: list[str] = []
    for index, value in enumerate(raw_ids):
        count = counts.get(value, 0)
        counts[value] = count + 1
        result.append(value if count == 0 else f"{value}#duplicate-{index}")
    return tuple(result)


def match_units(
    gold_items: Sequence[Mapping[str, object]],
    prediction_items: Sequence[Mapping[str, object]],
    *,
    id_key: str,
    use_shared_ids: bool,
    polygon_key: str = "polygon",
    iou_threshold: float = 0.5,
) -> UnitAssignment:
    """Assign units by optional shared ID and then by thresholded polygon IoU.

    Shared IDs are considered only when ``use_shared_ids`` is true.  This is the
    crucial guard that prevents a blind submission from receiving privileged
    oracle correspondences merely because it guessed or copied a gold ID.
    """

    gold_ids = _stable_ids(gold_items, id_key, "gold")
    prediction_ids = _stable_ids(prediction_items, id_key, "prediction")
    matched_gold: set[int] = set()
    matched_prediction: set[int] = set()
    matches: list[UnitMatch] = []

    if use_shared_ids:
        gold_by_id = {value: index for index, value in enumerate(gold_ids)}
        prediction_by_id = {value: index for index, value in enumerate(prediction_ids)}
        for shared_id in sorted(set(gold_by_id) & set(prediction_by_id)):
            gold_index = gold_by_id[shared_id]
            prediction_index = prediction_by_id[shared_id]
            matched_gold.add(gold_index)
            matched_prediction.add(prediction_index)
            matches.append(
                UnitMatch(
                    gold_index=gold_index,
                    prediction_index=prediction_index,
                    method="shared_id",
                    score=1.0,
                    gold_id=gold_ids[gold_index],
                    prediction_id=prediction_ids[prediction_index],
                )
            )

    remaining_gold_indices = [
        index for index in range(len(gold_items)) if index not in matched_gold
    ]
    remaining_prediction_indices = [
        index for index in range(len(prediction_items)) if index not in matched_prediction
    ]
    remaining_gold = [gold_items[index] for index in remaining_gold_indices]
    remaining_prediction = [prediction_items[index] for index in remaining_prediction_indices]
    geometry = match_geometries(
        remaining_gold,
        remaining_prediction,
        polygon_key=polygon_key,
        iou_threshold=iou_threshold,
    )
    for geometry_match in geometry.matches:
        gold_index = remaining_gold_indices[geometry_match.gold_index]
        prediction_index = remaining_prediction_indices[geometry_match.prediction_index]
        matched_gold.add(gold_index)
        matched_prediction.add(prediction_index)
        matches.append(
            UnitMatch(
                gold_index=gold_index,
                prediction_index=prediction_index,
                method="geometry",
                score=geometry_match.iou,
                gold_id=gold_ids[gold_index],
                prediction_id=prediction_ids[prediction_index],
            )
        )

    # A stable gold-major ordering makes evidence and lockfiles reproducible.
    matches.sort(key=lambda item: (item.gold_index, item.prediction_index, item.method))
    return UnitAssignment(
        id_key=id_key,
        gold_count=len(gold_items),
        prediction_count=len(prediction_items),
        matches=tuple(matches),
        unmatched_gold_indices=tuple(
            index for index in range(len(gold_items)) if index not in matched_gold
        ),
        unmatched_prediction_indices=tuple(
            index for index in range(len(prediction_items)) if index not in matched_prediction
        ),
        gold_ids=gold_ids,
        prediction_ids=prediction_ids,
        split_gold_items=geometry.split_gold_items,
        merged_prediction_items=geometry.merged_prediction_items,
    )


def remap_prediction_graph(
    *,
    prediction_nodes: Sequence[str],
    prediction_edges: Sequence[Sequence[str]],
    assignment: UnitAssignment,
) -> tuple[list[str], list[list[str]], dict[str, object]]:
    """Map a prediction graph into gold identity space without dropping extras."""

    prediction_to_gold = assignment.prediction_to_gold_id
    unmatched_counter = 0
    unknown_counter = 0
    node_map: dict[str, str] = {}
    unmatched_nodes: list[str] = []
    unknown_nodes: list[str] = []

    def map_node(node: str, *, declared: bool) -> str:
        nonlocal unmatched_counter, unknown_counter
        value = str(node)
        if value in node_map:
            return node_map[value]
        if value in prediction_to_gold:
            mapped = prediction_to_gold[value]
        elif declared:
            mapped = f"__prediction_unmatched__:{unmatched_counter}:{value}"
            unmatched_counter += 1
            unmatched_nodes.append(value)
        else:
            mapped = f"__prediction_unknown__:{unknown_counter}:{value}"
            unknown_counter += 1
            unknown_nodes.append(value)
        node_map[value] = mapped
        return mapped

    declared_set = {str(node) for node in prediction_nodes}
    remapped_nodes = [map_node(str(node), declared=True) for node in prediction_nodes]
    remapped_edges: list[list[str]] = []
    malformed_edges = 0
    for edge in prediction_edges:
        if len(edge) != 2:
            malformed_edges += 1
            continue
        before, after = str(edge[0]), str(edge[1])
        mapped_before = map_node(before, declared=before in declared_set)
        mapped_after = map_node(after, declared=after in declared_set)
        remapped_edges.append([mapped_before, mapped_after])
        for mapped in (mapped_before, mapped_after):
            if mapped not in remapped_nodes:
                remapped_nodes.append(mapped)

    evidence: dict[str, object] = {
        "prediction_node_map": dict(sorted(node_map.items())),
        "unmatched_prediction_nodes": unmatched_nodes,
        "unknown_edge_nodes": unknown_nodes,
        "malformed_prediction_edges": malformed_edges,
        "remapped_prediction_edges": remapped_edges,
    }
    return remapped_nodes, remapped_edges, evidence
