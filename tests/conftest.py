from __future__ import annotations

from copy import deepcopy

import pytest


def _rect(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


@pytest.fixture
def gold_page():
    return {
        "schema_version": "1.0",
        "page_id": "doc1-p1",
        "document_id": "doc1",
        "split": "dev",
        "track": "bidi_diagnostic",
        "image": {
            "path": "images/doc1-p1.png",
            "width": 1200,
            "height": 400,
            "rotation_degrees": 0,
        },
        "metadata": {
            "languages": ["he"],
            "script": "Hebr",
            "script_style": "modern_square_print",
            "era": "modern",
            "document_type": "line_card",
            "layout_type": "single_line",
            "vocalization": "none",
            "source_type": "synthetic",
            "font_id": "NotoSansHebrew",
            "writer_id": None,
            "scribe_id": None,
            "template_id": "line-card-v1",
            "source_collection": "synthetic-seed",
            "license": "CC-BY-4.0",
        },
        "regions": [
            {
                "region_id": "r1",
                "type": "body",
                "polygon": _rect(40, 40, 1160, 360),
                "base_direction": "rtl",
                "reading_index": 0,
                "lines": [
                    {
                        "line_id": "l1",
                        "polygon": _rect(60, 100, 1140, 250),
                        "baseline": [[1140, 220], [60, 220]],
                        "text": "בשנת 2026 הופעלה גרסה OCR-v2.1.",
                        "base_direction": "rtl",
                        "language": "he",
                        "tags": ["bidi:mixed", "content:number", "content:latin"],
                    }
                ],
            }
        ],
        "reading_order": {"edges": []},
        "tables": [],
        "form_fields": [],
    }


@pytest.fixture
def prediction_page(gold_page):
    page = deepcopy(gold_page)
    for key in ["document_id", "split", "track", "image", "metadata", "reading_order"]:
        page.pop(key, None)
    page["model"] = {"name": "perfect", "version": "1"}
    return page
