"""Cross-run comparison artifacts for HebOCRBench report directories."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import __version__
from .io import write_json


def _nested(value: Mapping[str, Any], *path: str) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _report_directories(root: Path) -> list[Path]:
    if (root / "metrics.json").is_file():
        return [root]
    return sorted(
        directory
        for directory in root.iterdir()
        if directory.is_dir() and (directory / "metrics.json").is_file()
    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _row(report_dir: Path) -> dict[str, Any]:
    metrics = _load_json(report_dir / "metrics.json")
    manifest_path = report_dir / "run_manifest.json"
    manifest = _load_json(manifest_path) if manifest_path.is_file() else {}
    model = manifest.get("model") if isinstance(manifest.get("model"), Mapping) else {}
    run_id = report_dir.name
    return {
        "run_id": run_id,
        "model_name": model.get("name") or run_id,
        "model_version": model.get("version"),
        "adapter": model.get("adapter"),
        "oracle_layout": bool(model.get("oracle_layout", False)),
        "evaluator_version": manifest.get("evaluator_version"),
        "conformance": _nested(metrics, "conformance", "status"),
        "certified_rank": None,
        "gold_pages": _nested(metrics, "coverage", "gold_pages"),
        "submitted_pages": _nested(metrics, "coverage", "submitted_prediction_pages"),
        "missing_pages": _nested(metrics, "coverage", "missing_prediction_pages"),
        "extra_pages": _nested(metrics, "coverage", "extra_prediction_pages"),
        "line_cer": _nested(metrics, "recognition", "line_cer"),
        "line_gcer": _nested(metrics, "recognition", "line_gcer"),
        "line_wer": _nested(metrics, "recognition", "line_wer"),
        "page_order_gcer": _nested(metrics, "recognition", "page_order_gcer"),
        "reading_order_penalty_gcer": _nested(metrics, "recognition", "reading_order_penalty_gcer"),
        "base_letter_cer": _nested(metrics, "recognition", "base_letter_cer"),
        "punctuation_error_rate": _nested(metrics, "recognition", "punctuation_error_rate"),
        "line_exact_rate": _nested(metrics, "recognition", "line_exact_rate"),
        "mark_recall": _nested(metrics, "diacritics", "mark_recall"),
        "mark_f1": _nested(metrics, "diacritics", "mark_f1"),
        "mark_error_rate": _nested(metrics, "diacritics", "mark_error_rate"),
        "ltr_run_exact_rate": _nested(metrics, "conformance", "ltr_run_exact_rate"),
        "numeric_exact_rate": _nested(metrics, "conformance", "numeric_exact_rate"),
        "bracket_exact_rate": _nested(metrics, "conformance", "bracket_exact_rate"),
        "visual_order_failure_count": _nested(metrics, "conformance", "visual_order_failure_count"),
        "order_pairwise_accuracy": _nested(metrics, "reading_order", "pairwise_accuracy"),
        "order_edge_f1": _nested(metrics, "reading_order", "edge_f1"),
        "region_f1": _nested(metrics, "layout", "regions", "f1"),
        "line_f1": _nested(metrics, "layout", "lines", "f1"),
        "table_grid_slot_accuracy": _nested(metrics, "tables", "grid_slot_accuracy"),
        "table_cell_text_gcer": _nested(metrics, "tables", "cell_text_gcer"),
        "form_value_exact_rate": _nested(metrics, "forms", "value_exact_rate"),
        "form_value_gcer": _nested(metrics, "forms", "value_gcer"),
        "latency_ms_p50": _nested(metrics, "operational", "latency_ms_p50"),
        "latency_ms_p95": _nested(metrics, "operational", "latency_ms_p95"),
        "throughput_pages_per_minute": _nested(
            metrics, "operational", "throughput_pages_per_minute"
        ),
        "report_path": str(report_dir / "report.html"),
    }


def collect_comparison_rows(report_root: str | Path) -> list[dict[str, Any]]:
    """Load report bundles and assign ranks only to conformant runs."""

    root = Path(report_root)
    if not root.is_dir():
        raise FileNotFoundError(f"Report root does not exist: {root}")
    directories = _report_directories(root)
    if not directories:
        raise ValueError(f"No report directories containing metrics.json under {root}")
    rows = [_row(directory) for directory in directories]
    certified = sorted(
        (row for row in rows if row.get("conformance") == "conformant"),
        key=lambda row: (
            float(row["line_gcer"]) if row.get("line_gcer") is not None else float("inf"),
            float(row["page_order_gcer"])
            if row.get("page_order_gcer") is not None
            else float("inf"),
            str(row["run_id"]),
        ),
    )
    for rank, row in enumerate(certified, start=1):
        row["certified_rank"] = rank
    return sorted(
        rows,
        key=lambda row: (
            row.get("certified_rank") is None,
            row.get("certified_rank") or 10**9,
            float(row["line_gcer"]) if row.get("line_gcer") is not None else float("inf"),
            str(row["run_id"]),
        ),
    )


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{100 * float(value):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _status_he(value: Any) -> str:
    return {
        "conformant": "תואם",
        "non_conformant": "לא־תואם",
        "not_evaluated": "לא הוערך",
    }.get(str(value), str(value))


def _comparison_html(rows: Iterable[Mapping[str, Any]]) -> str:
    body_rows: list[str] = []
    for row in rows:
        rank = row.get("certified_rank")
        model = html.escape(str(row.get("model_name") or row.get("run_id")))
        version = row.get("model_version")
        subtitle_parts = []
        if version:
            subtitle_parts.append(f"גרסה {html.escape(str(version))}")
        if row.get("adapter"):
            subtitle_parts.append(html.escape(str(row["adapter"])))
        if row.get("oracle_layout"):
            subtitle_parts.append("Oracle layout — זיהוי בלבד")
        subtitle = " · ".join(subtitle_parts)
        status = _status_he(row.get("conformance"))
        status_class = "ok" if row.get("conformance") == "conformant" else "bad"
        body_rows.append(
            "<tr>"
            f'<td class="rank">{rank if rank is not None else "—"}</td>'
            f"<td><strong>{model}</strong><small>{subtitle}</small></td>"
            f'<td><span class="badge {status_class}">{html.escape(status)}</span></td>'
            f"<td>{_pct(row.get('line_gcer'))}</td>"
            f"<td>{_pct(row.get('page_order_gcer'))}</td>"
            f"<td>{_pct(row.get('base_letter_cer'))}</td>"
            f"<td>{_pct(row.get('mark_recall'))}</td>"
            f"<td>{_pct(row.get('ltr_run_exact_rate'))}</td>"
            f"<td>{int(row.get('visual_order_failure_count') or 0)}</td>"
            f"<td>{_pct(row.get('order_pairwise_accuracy'))}</td>"
            f"<td>{_pct(row.get('region_f1'))}</td>"
            f"<td>{_pct(row.get('table_grid_slot_accuracy'))}</td>"
            f"<td>{_pct(row.get('form_value_exact_rate'))}</td>"
            f"<td>{_number(row.get('latency_ms_p50'))}</td>"
            "</tr>"
        )
    rows_html = "".join(body_rows)
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HebOCRBench — השוואת ריצות</title>
<style>
:root {{ --ink:#17191c; --muted:#626a75; --line:#d8dde4; --bg:#f4f6f8; --card:#fff; --ok:#176b3a; --bad:#a12721; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif; line-height:1.5; }}
main {{ max-width:1500px; margin:auto; padding:30px 20px 70px; }}
header {{ background:#14202a; color:#fff; border-radius:18px; padding:30px; box-shadow:0 12px 30px #0002; }}
header p {{ max-width:1000px; color:#dbe4eb; }}
.panel {{ margin-top:18px; background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:auto; box-shadow:0 3px 12px #0000000b; }}
table {{ border-collapse:collapse; width:100%; min-width:1450px; }}
th,td {{ padding:12px 10px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th {{ position:sticky; top:0; background:#edf1f4; z-index:1; }}
td small {{ display:block; color:var(--muted); white-space:normal; max-width:260px; }}
.rank {{ font-size:1.25rem; font-weight:800; text-align:center; }}
.badge {{ display:inline-block; padding:3px 9px; border-radius:999px; font-weight:750; }}
.badge.ok {{ color:var(--ok); background:#e9f6ee; }} .badge.bad {{ color:var(--bad); background:#fdebea; }}
.note {{ margin-top:16px; color:var(--muted); }}
</style>
</head>
<body><main>
<header>
<h1>HebOCRBench — השוואת ריצות</h1>
<p><strong>דירוג מאושר</strong> ניתן רק לריצות שעברו את שער התאימות. ריצה לא־תואמת נשארת מוצגת לצורכי אבחון, אך אינה מקבלת מקום בדירוג גם אם מספר יחיד נראה מחמיא.</p>
</header>
<section class="panel"><table>
<thead><tr><th>דירוג</th><th>מערכת</th><th>שער</th><th>GCER שורות ↓</th><th>GCER סדר עמוד ↓</th><th>CER אותיות בסיס ↓</th><th>Recall ניקוד ↑</th><th>דיוק LTR ↑</th><th>כשלים חזותיים ↓</th><th>סדר קריאה ↑</th><th>Region F1 ↑</th><th>טבלה ↑</th><th>טופס ↑</th><th>p50 ms ↓</th></tr></thead>
<tbody>{rows_html}</tbody>
</table></section>
<p class="note">Oracle layout מסומן במפורש ואינו בר־השוואה למערכת end-to-end בתחום הפריסה. טקסט נמדד בסדר Unicode לוגי; היפוך אינו מעניק נקודות.</p>
</main></body></html>"""


def write_comparison_artifacts(report_root: str | Path, output_dir: str | Path) -> dict[str, Path]:
    """Write JSON, CSV and self-contained HTML comparison artifacts."""

    rows = collect_comparison_rows(report_root)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": output / "comparison.json",
        "csv": output / "comparison.csv",
        "html": output / "comparison.html",
    }
    write_json(
        paths["json"],
        {
            "benchmark": "HebOCRBench",
            "evaluator_version": __version__,
            "ranking_policy": "Only conformant runs receive certified_rank; lower line_gcer wins.",
            "runs": rows,
        },
    )
    fieldnames = list(rows[0].keys())
    with paths["csv"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    paths["html"].write_text(_comparison_html(rows), encoding="utf-8")
    return paths
