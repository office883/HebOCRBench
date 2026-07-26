"""Explicit reading-order graph handling."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence


class ReadingOrderCycleError(ValueError):
    pass


def _edge_tuples(edges: Iterable[Sequence[str]]) -> set[tuple[str, str]]:
    return {(str(edge[0]), str(edge[1])) for edge in edges if len(edge) == 2}


def topological_order(
    nodes: Sequence[str],
    edges: Iterable[Sequence[str]],
    *,
    fallback_key: Callable[[str], object] | None = None,
) -> list[str]:
    unique_nodes = list(dict.fromkeys(nodes))
    node_set = set(unique_nodes)
    edge_set = _edge_tuples(edges)
    invalid = [(a, b) for a, b in edge_set if a not in node_set or b not in node_set]
    if invalid:
        raise ValueError(f"Reading-order edges reference unknown nodes: {invalid}")

    outgoing: dict[str, set[str]] = defaultdict(set)
    indegree = {node: 0 for node in unique_nodes}
    for before, after in edge_set:
        if after not in outgoing[before]:
            outgoing[before].add(after)
            indegree[after] += 1

    original_index = {node: i for i, node in enumerate(unique_nodes)}

    def sort_key(node: str) -> tuple[object, int]:
        return (
            fallback_key(node) if fallback_key is not None else original_index[node],
            original_index[node],
        )

    ready = sorted((node for node in unique_nodes if indegree[node] == 0), key=sort_key)
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for successor in sorted(outgoing[node], key=sort_key):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort(key=sort_key)
    if len(order) != len(unique_nodes):
        raise ReadingOrderCycleError("Reading-order graph contains a cycle")
    return order


def transitive_precedence(
    nodes: Sequence[str], edges: Iterable[Sequence[str]]
) -> set[tuple[str, str]]:
    edge_set = _edge_tuples(edges)
    outgoing: dict[str, set[str]] = defaultdict(set)
    for before, after in edge_set:
        outgoing[before].add(after)
    precedence: set[tuple[str, str]] = set()
    for start in nodes:
        queue = deque(outgoing[start])
        seen: set[str] = set()
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            precedence.add((start, current))
            queue.extend(outgoing[current])
    return precedence


def reading_order_metrics(
    *,
    gold_nodes: Sequence[str],
    gold_edges: Iterable[Sequence[str]],
    prediction_nodes: Sequence[str],
    prediction_edges: Iterable[Sequence[str]],
) -> dict[str, float | int | list[str]]:
    gold_edge_set = _edge_tuples(gold_edges)
    pred_edge_set = _edge_tuples(prediction_edges)
    edge_correct = len(gold_edge_set & pred_edge_set)
    edge_precision = edge_correct / max(1, len(pred_edge_set))
    edge_recall = edge_correct / max(1, len(gold_edge_set))
    edge_f1 = (
        0.0
        if edge_precision + edge_recall == 0
        else 2 * edge_precision * edge_recall / (edge_precision + edge_recall)
    )

    gold_precedence = transitive_precedence(gold_nodes, gold_edge_set)
    try:
        pred_order = topological_order(prediction_nodes, pred_edge_set)
    except ReadingOrderCycleError:
        pred_order = list(prediction_nodes)
    pred_position = {node: index for index, node in enumerate(pred_order)}
    correct_pairs = 0
    for before, after in gold_precedence:
        if (
            before in pred_position
            and after in pred_position
            and pred_position[before] < pred_position[after]
        ):
            correct_pairs += 1
    pairwise_accuracy = correct_pairs / max(1, len(gold_precedence))
    return {
        "gold_edges": len(gold_edge_set),
        "prediction_edges": len(pred_edge_set),
        "edge_precision": edge_precision,
        "edge_recall": edge_recall,
        "edge_f1": edge_f1,
        "comparable_pairs": len(gold_precedence),
        "correct_pairs": correct_pairs,
        "pairwise_accuracy": pairwise_accuracy,
        "prediction_order": pred_order,
    }
