"""Evaluator self-tests using deliberately broken OCR systems."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .baselines import generate_baseline_predictions
from .evaluator import evaluate_dataset
from .io import load_jsonl, write_json, write_jsonl
from .report import write_evaluation_artifacts
from .stress import DEFAULT_VARIANTS, generate_stress_suite

SANITY_SYSTEMS = (
    "perfect",
    "empty",
    "reverse_text",
    "strip_marks",
    "ascii_punctuation",
    "swap_region_order",
)


def run_sanity_matrix(
    output_dir: str | Path,
    *,
    variants: Sequence[str] = DEFAULT_VARIANTS,
    limit: int | None = 28,
    seed: int = 20260722,
    font_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate a seed suite and prove that known faults are detected."""

    output = Path(output_dir)
    dataset_dir = output / "dataset"
    prediction_dir = output / "predictions"
    report_dir = output / "reports"
    prediction_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    generated = generate_stress_suite(
        dataset_dir,
        seed=seed,
        variants=variants,
        limit=limit,
        font_path=font_path,
        include_structured=True,
    )
    gold_pages = load_jsonl(generated.gold_path)
    systems: dict[str, dict[str, Any]] = {}
    runs = {}
    for kind in SANITY_SYSTEMS:
        predictions = generate_baseline_predictions(gold_pages, kind)
        prediction_path = prediction_dir / f"{kind}.jsonl"
        write_jsonl(prediction_path, predictions)
        run = evaluate_dataset(gold_pages, predictions)
        runs[kind] = run
        write_evaluation_artifacts(
            run,
            report_dir / kind,
            gold_path=generated.gold_path,
            predictions_path=prediction_path,
            model_manifest={"name": f"sanity:{kind}"},
        )
        systems[kind] = {
            "conformance": run.metrics["conformance"]["status"],
            "line_cer": run.metrics["recognition"]["line_cer"],
            "line_gcer": run.metrics["recognition"]["line_gcer"],
            "page_order_gcer": run.metrics["recognition"]["page_order_gcer"],
            "base_letter_cer": run.metrics["recognition"]["base_letter_cer"],
            "punctuation_error_rate": run.metrics["recognition"]["punctuation_error_rate"],
            "visual_order_failure_rate": run.metrics["bidi"]["visual_order_failure_rate"],
            "mark_recall": run.metrics["diacritics"]["mark_recall"],
        }

    checks = {
        "perfect_has_zero_strict_error": systems["perfect"]["line_gcer"] == 0.0,
        "perfect_passes_bidi_gate": systems["perfect"]["conformance"] == "conformant",
        "empty_is_heavily_penalized": systems["empty"]["line_gcer"] >= 0.95,
        "reversal_is_penalized": systems["reverse_text"]["line_gcer"] > 0.25,
        "reversal_is_flagged": systems["reverse_text"]["visual_order_failure_rate"] > 0.0,
        "mark_stripping_preserves_base_letters": systems["strip_marks"]["base_letter_cer"] == 0.0,
        "mark_stripping_hurts_strict_score": systems["strip_marks"]["line_gcer"] > 0.0,
        "punctuation_folding_hurts_strict_score": systems["ascii_punctuation"][
            "punctuation_error_rate"
        ]
        > 0.0,
        "order_swap_preserves_line_recognition": systems["swap_region_order"]["line_gcer"] == 0.0,
        "order_swap_hurts_page_order": systems["swap_region_order"]["page_order_gcer"] > 0.0,
    }
    result: dict[str, Any] = {
        "passed": all(checks.values()),
        "checks": checks,
        "systems": systems,
        "dataset": generated.to_dict(),
    }
    write_json(output / "sanity_matrix.json", result)
    return result
