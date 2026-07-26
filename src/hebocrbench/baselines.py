"""Deterministic baseline and fault-injection predictions.

These helpers serve two purposes: they provide smoke-test baselines for a new
benchmark installation, and they create deliberately broken outputs that prove
the evaluator reacts to Hebrew-specific failure modes.
"""

from __future__ import annotations

from copy import deepcopy
import unicodedata
from typing import Any, Callable, Mapping

from .unicode_utils import graphemes, is_hebrew_mark

Prediction = dict[str, Any]


def _prediction_shell(gold_page: Mapping[str, Any], *, name: str) -> Prediction:
    prediction: Prediction = {
        "schema_version": str(gold_page.get("schema_version", "1.0")),
        "page_id": str(gold_page["page_id"]),
        "regions": deepcopy(gold_page.get("regions", [])),
        "reading_order": deepcopy(gold_page.get("reading_order", {"edges": []})),
        "tables": deepcopy(gold_page.get("tables", [])),
        "form_fields": deepcopy(gold_page.get("form_fields", [])),
        "model": {"name": name, "version": "1"},
    }
    if "page_text" in gold_page:
        prediction["page_text"] = str(gold_page["page_text"])
    return prediction


def _map_text_fields(prediction: Prediction, transform: Callable[[str], str]) -> Prediction:
    for region in prediction.get("regions", []):
        for line in region.get("lines", []):
            line["text"] = transform(str(line.get("text", "")))
    if "page_text" in prediction:
        prediction["page_text"] = transform(str(prediction["page_text"]))
    for table in prediction.get("tables", []):
        for cell in table.get("cells", []):
            cell["text"] = transform(str(cell.get("text", "")))
    for field in prediction.get("form_fields", []):
        field["value_text"] = transform(str(field.get("value_text", "")))
        if "label_text" in field:
            field["label_text"] = transform(str(field.get("label_text", "")))
    return prediction


def perfect_prediction(gold_page: Mapping[str, Any]) -> Prediction:
    """Return a prediction that exactly transcribes the supplied gold page."""

    return _prediction_shell(gold_page, name="perfect")


def empty_prediction(gold_page: Mapping[str, Any]) -> Prediction:
    """Return all expected regions/lines but with empty recognized content."""

    prediction = _prediction_shell(gold_page, name="empty")
    return _map_text_fields(prediction, lambda _text: "")


def reverse_text_prediction(gold_page: Mapping[str, Any]) -> Prediction:
    """Reverse grapheme clusters, simulating visual-order Hebrew storage."""

    prediction = _prediction_shell(gold_page, name="reversed-graphemes")
    return _map_text_fields(prediction, lambda text: "".join(reversed(graphemes(text))))


def strip_marks_prediction(gold_page: Mapping[str, Any]) -> Prediction:
    """Remove Hebrew niqqud/cantillation while preserving base letters."""

    def strip(text: str) -> str:
        decomposed = unicodedata.normalize("NFD", text)
        return unicodedata.normalize(
            "NFC", "".join(ch for ch in decomposed if not is_hebrew_mark(ch))
        )

    prediction = _prediction_shell(gold_page, name="strip-hebrew-marks")
    return _map_text_fields(prediction, strip)


def swap_region_order_prediction(gold_page: Mapping[str, Any]) -> Prediction:
    """Keep recognition perfect but reverse the explicit region reading order."""

    prediction = _prediction_shell(gold_page, name="swap-region-order")
    regions = prediction.get("regions", [])
    ordered = sorted(
        regions,
        key=lambda region: (
            int(region.get("reading_index", 10**9))
            if region.get("reading_index") is not None
            else 10**9,
            str(region.get("region_id", "")),
        ),
    )
    reversed_ids = [str(region.get("region_id", "")) for region in reversed(ordered)]
    for index, region_id in enumerate(reversed_ids):
        for region in regions:
            if str(region.get("region_id", "")) == region_id:
                region["reading_index"] = index
                break
    prediction["reading_order"] = {
        "edges": [
            [reversed_ids[index], reversed_ids[index + 1]]
            for index in range(max(0, len(reversed_ids) - 1))
        ]
    }
    return prediction


def ascii_punctuation_prediction(gold_page: Mapping[str, Any]) -> Prediction:
    """Collapse Hebrew punctuation to common ASCII lookalikes."""

    mapping = str.maketrans({"־": "-", "׳": "'", "״": '"', "׃": ":"})
    prediction = _prediction_shell(gold_page, name="ascii-punctuation")
    return _map_text_fields(prediction, lambda text: text.translate(mapping))


BASELINE_FACTORIES = {
    "perfect": perfect_prediction,
    "empty": empty_prediction,
    "reverse_text": reverse_text_prediction,
    "strip_marks": strip_marks_prediction,
    "swap_region_order": swap_region_order_prediction,
    "ascii_punctuation": ascii_punctuation_prediction,
}


def generate_baseline_predictions(
    gold_pages: list[Mapping[str, Any]], kind: str
) -> list[Prediction]:
    """Generate one deterministic baseline prediction per gold page."""

    try:
        factory = BASELINE_FACTORIES[kind]
    except KeyError as exc:
        raise ValueError(
            f"Unknown baseline {kind!r}; choose from {', '.join(sorted(BASELINE_FACTORIES))}"
        ) from exc
    return [factory(page) for page in gold_pages]
