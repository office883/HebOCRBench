from __future__ import annotations

from hebocrbench.adapters.tesseract import run_tesseract_oracle_layout
from hebocrbench.io import load_jsonl
from hebocrbench.stress import generate_stress_suite


def test_oracle_layout_adapter_uses_images_but_not_gold_text(tmp_path):
    dataset = generate_stress_suite(
        tmp_path / "dataset", variants=("clean",), limit=1, include_structured=False
    )
    gold = load_jsonl(dataset.gold_path)
    seen_sizes = []

    def fake_recognizer(image, language, psm):
        seen_sizes.append(image.size)
        assert language == "heb"
        assert psm == 7
        return "פלט בדיקה"

    predictions = run_tesseract_oracle_layout(
        gold, dataset_root=dataset.root, recognizer=fake_recognizer
    )
    assert seen_sizes
    assert predictions[0]["regions"][0]["lines"][0]["text"] == "פלט בדיקה"
    assert predictions[0]["regions"][0]["lines"][0]["text"] != gold[0]["regions"][0]["lines"][0]["text"]
    assert predictions[0]["tables"] == []
    assert predictions[0]["form_fields"] == []
    assert predictions[0]["model"]["adapter"] == "tesseract_oracle_layout"
