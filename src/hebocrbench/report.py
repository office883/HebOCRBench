"""Machine-readable artifacts and a self-contained RTL evaluation report."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import html
from importlib import metadata as importlib_metadata
import json
import os
from pathlib import Path
import platform
import sys
import unicodedata
from typing import Any, Mapping

from . import __version__
from .evaluator import EvaluationRun
from .io import sha256_file, write_json, write_jsonl


def _library_versions() -> dict[str, str | None]:
    distributions = {
        "jsonschema": "jsonschema",
        "pillow": "Pillow",
        "pyyaml": "PyYAML",
        "rapidfuzz": "RapidFuzz",
        "referencing": "referencing",
        "regex": "regex",
        "scipy": "scipy",
        "shapely": "shapely",
        "python-bidi": "python-bidi",
    }
    versions: dict[str, str | None] = {}
    for key, distribution in distributions.items():
        try:
            versions[key] = importlib_metadata.version(distribution)
        except importlib_metadata.PackageNotFoundError:
            versions[key] = None
    return versions


def _flatten_scalars(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten_scalars(value[key], path))
    elif isinstance(value, (str, int, float, bool)) or value is None:
        rows.append((prefix, value))
    return rows


def _error_rows(run: EvaluationRun) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for page in run.pages:
        for line in page.details.get("line_results", []):
            rates = line["text"]["rates"]
            visual = line["bidi"]["visual_order"]
            hygiene = line["bidi"]["hygiene"]
            gcer = float(rates["gcer"])
            suspicious = bool(visual["visual_order_suspected"])
            hygiene_count = sum(
                int(hygiene.get(key, 0))
                for key in (
                    "bidi_control_count",
                    "unbalanced_embeddings",
                    "unbalanced_isolates",
                    "zero_width_count",
                    "replacement_character_count",
                    "private_use_count",
                    "presentation_form_count",
                )
            )
            if gcer == 0.0 and not suspicious and hygiene_count == 0:
                continue
            errors.append(
                {
                    "page_id": page.page_id,
                    "document_id": page.document_id,
                    "track": page.track,
                    "gold_line_id": line.get("gold_line_id"),
                    "prediction_line_id": line.get("prediction_line_id"),
                    "matched_by": line.get("matched_by"),
                    "reference": line["reference"],
                    "prediction": line["prediction"],
                    "cer": rates["cer"],
                    "gcer": gcer,
                    "wer": rates["wer"],
                    "base_letter_cer": rates["base_letter_cer"],
                    "visual_order_suspected": suspicious,
                    "visual_order_gain": visual["visual_order_gain"],
                    "hygiene": hygiene,
                    "reference_codepoints": line["reference_codepoints"],
                    "prediction_codepoints": line["prediction_codepoints"],
                }
            )
    errors.sort(
        key=lambda item: (
            bool(item["visual_order_suspected"]),
            float(item["gcer"]),
            float(item["cer"]),
        ),
        reverse=True,
    )
    return errors


def _pct(value: Any) -> str:
    try:
        return f"{100 * float(value):.2f}%"
    except (TypeError, ValueError):
        return "—"


def _number(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _codepoint_string(points: list[Mapping[str, Any]]) -> str:
    return " · ".join(f"{point.get('codepoint', '')} {point.get('name', '')}" for point in points)


def _html_report(run: EvaluationRun, errors: list[dict[str, Any]]) -> str:
    m = run.metrics
    recognition = m["recognition"]
    conformance = m["conformance"]
    status = str(conformance.get("status", "unknown"))
    status_he = {
        "conformant": "תואם",
        "non_conformant": "לא־תואם",
        "not_evaluated": "לא הוערך",
    }.get(status, status)
    status_class = (
        "ok" if status == "conformant" else "bad" if status == "non_conformant" else "warn"
    )

    cards = [
        ("GCER שורות", _pct(recognition.get("line_gcer"))),
        ("CER שורות", _pct(recognition.get("line_cer"))),
        ("WER", _pct(recognition.get("line_wer"))),
        ("GCER סדר עמוד", _pct(recognition.get("page_order_gcer"))),
        ("דיוק שורות מלא", _pct(recognition.get("line_exact_rate"))),
        ("כשל סדר חזותי", _pct(m["bidi"].get("visual_order_failure_rate"))),
        ("דיוק מקטעי LTR", _pct(m["bidi"].get("ltr_run_exact_rate"))),
        ("Recall ניקוד", _pct(m["diacritics"].get("mark_recall"))),
    ]
    card_html = "".join(
        f'<div class="card"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(value)}</div></div>'
        for label, value in cards
    )

    failed = conformance.get("failed_checks", [])
    failed_html = (
        "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in failed) + "</ul>"
        if failed
        else "<p>כל בדיקות השער שנדרשו עברו.</p>"
    )

    slice_rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{int(values.get('pages', 0))}</td>"
        f"<td>{_pct(values.get('line_gcer'))}</td>"
        f"<td>{_pct(values.get('page_order_gcer'))}</td>"
        f"<td>{_pct(values.get('line_exact_rate'))}</td>"
        "</tr>"
        for name, values in sorted(
            m.get("slices", {}).items(),
            key=lambda item: float(item[1].get("line_gcer", 0.0)),
            reverse=True,
        )
    )

    error_blocks: list[str] = []
    for index, error in enumerate(errors[:50], start=1):
        error_blocks.append(
            f"""
            <article class="error-card">
              <header><strong>#{index} · {html.escape(str(error["page_id"]))}</strong>
                <span>GCER {_pct(error["gcer"])}</span>
                {'<span class="pill bad">חשד לסדר חזותי</span>' if error["visual_order_suspected"] else ""}
              </header>
              <div class="comparison">
                <section><h4>אמת</h4><p class="sample" dir="rtl">{html.escape(str(error["reference"]))}</p></section>
                <section><h4>פלט</h4><p class="sample" dir="rtl">{html.escape(str(error["prediction"]))}</p></section>
              </div>
              <details>
                <summary>קוד־פוינטים בסדר הלוגי</summary>
                <p class="codepoints" dir="ltr"><b>REF:</b> {html.escape(_codepoint_string(error["reference_codepoints"]))}</p>
                <p class="codepoints" dir="ltr"><b>PRED:</b> {html.escape(_codepoint_string(error["prediction_codepoints"]))}</p>
              </details>
            </article>
            """
        )
    errors_html = "".join(error_blocks) or "<p>לא נמצאו שורות שגויות.</p>"

    embedded_metrics = html.escape(json.dumps(m, ensure_ascii=False, indent=2))
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HebOCRBench — דוח הערכה</title>
<style>
:root {{ color-scheme: light; --ink:#15171a; --muted:#59616b; --line:#d9dde3; --bg:#f5f6f8; --card:#fff; --ok:#176b3a; --bad:#a12721; --warn:#8b5a00; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif; background:var(--bg); color:var(--ink); line-height:1.55; }}
main {{ max-width:1240px; margin:auto; padding:32px 22px 80px; }}
h1,h2,h3 {{ line-height:1.2; }}
.hero {{ background:linear-gradient(135deg,#101820,#263746); color:white; padding:34px; border-radius:18px; box-shadow:0 12px 32px #0002; }}
.hero p {{ max-width:850px; color:#e2e9ef; }}
.status {{ display:inline-block; margin-top:8px; padding:8px 14px; border-radius:999px; font-weight:800; background:white; }}
.status.ok {{ color:var(--ok); }} .status.bad {{ color:var(--bad); }} .status.warn {{ color:var(--warn); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:20px 0; }}
.card,.panel,.error-card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; box-shadow:0 3px 12px #0000000b; }}
.card {{ padding:18px; }} .card .label {{ color:var(--muted); font-size:.92rem; }} .card .value {{ font-size:1.7rem; font-weight:850; direction:ltr; text-align:right; }}
.panel {{ padding:22px; margin-top:18px; overflow:auto; }}
table {{ border-collapse:collapse; width:100%; min-width:680px; }} th,td {{ border-bottom:1px solid var(--line); padding:10px; text-align:right; }} th {{ background:#f1f3f5; }}
.error-card {{ padding:18px; margin:14px 0; }} .error-card header {{ display:flex; gap:12px; flex-wrap:wrap; justify-content:space-between; }}
.comparison {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }} .comparison section {{ background:#fafbfc; border-radius:10px; padding:12px; border:1px solid #e8ebef; }}
.sample {{ font-size:1.12rem; white-space:pre-wrap; unicode-bidi:plaintext; }}
.codepoints {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:.78rem; overflow-wrap:anywhere; background:#111820; color:#dbe7ef; padding:12px; border-radius:8px; text-align:left; }}
.pill {{ border-radius:999px; padding:2px 9px; font-size:.82rem; }} .pill.bad {{ background:#fde8e7; color:var(--bad); }}
pre {{ direction:ltr; text-align:left; overflow:auto; max-height:520px; background:#111820; color:#dbe7ef; padding:14px; border-radius:10px; }}
footer {{ color:var(--muted); margin-top:26px; }}
@media (max-width:760px) {{ .comparison {{ grid-template-columns:1fr; }} .hero {{ padding:24px; }} }}
</style>
</head>
<body><main>
<section class="hero">
<h1>HebOCRBench — דוח הערכה</h1>
<p><strong>עקרון ההערכה:</strong> הטקסט נמדד בסדר Unicode לוגי וב־NFC. דמיון לפלט חזותי או למחרוזת הפוכה משמש לאבחון בלבד ואינו משפר שום ציון.</p>
<span class="status {status_class}">שער BiDi: {html.escape(status_he)}</span>
</section>
<section class="grid">{card_html}</section>
<section class="panel"><h2>שער התאימות</h2>{failed_html}</section>
<section class="panel"><h2>תוצאות לפי slice</h2><table><thead><tr><th>Slice</th><th>עמודים</th><th>GCER</th><th>GCER סדר עמוד</th><th>Exact שורה</th></tr></thead><tbody>{slice_rows}</tbody></table></section>
<section class="panel"><h2>שגיאות מובילות</h2><p>ה־diff מוצג גם ברמת קוד־פוינט כדי שתצוגת BiDi יפה לא תסתיר סדר אחסון שגוי.</p>{errors_html}</section>
<section class="panel"><details><summary><strong>JSON מלא של המדדים</strong></summary><pre>{embedded_metrics}</pre></details></section>
<footer>נוצר באמצעות HebOCRBench {html.escape(__version__)}. קבצי הפונט אינם חלק מהדוח.</footer>
</main></body></html>"""


def write_evaluation_artifacts(
    run: EvaluationRun,
    output_dir: str | Path,
    *,
    gold_path: str | Path | None = None,
    predictions_path: str | Path | None = None,
    model_manifest: Mapping[str, Any] | None = None,
    suite_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Write the canonical result bundle and return artifact paths."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "metrics": output / "metrics.json",
        "per_page": output / "per_page.jsonl",
        "errors": output / "errors.jsonl",
        "summary": output / "summary.csv",
        "html": output / "report.html",
        "run_manifest": output / "run_manifest.json",
    }
    write_json(paths["metrics"], run.metrics)
    write_jsonl(paths["per_page"], [page.to_dict() for page in run.pages])
    errors = _error_rows(run)
    write_jsonl(paths["errors"], errors)

    with paths["summary"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["metric", "value"])
        writer.writeheader()
        for metric, value in _flatten_scalars(run.metrics):
            writer.writerow({"metric": metric, "value": value})

    paths["html"].write_text(_html_report(run, errors), encoding="utf-8")
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch:
        created = datetime.fromtimestamp(int(source_date_epoch), tz=timezone.utc)
    else:
        created = datetime.now(timezone.utc)
    manifest: dict[str, Any] = {
        "benchmark": "HebOCRBench",
        "evaluator_version": __version__,
        "created_at_utc": created.isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "unicode_data_version": unicodedata.unidata_version,
        "libraries": _library_versions(),
        "configuration": run.configuration,
        "conformance_status": run.metrics["conformance"]["status"],
        "model": dict(model_manifest or {}),
        "inputs": {},
    }
    if suite_evidence is not None:
        manifest["benchmark_suite"] = dict(suite_evidence)
    for label, path in (("gold", gold_path), ("predictions", predictions_path)):
        if path is not None:
            source = Path(path)
            manifest["inputs"][label] = {
                "path": str(source),
                "sha256": sha256_file(source) if source.is_file() else None,
            }
    write_json(paths["run_manifest"], manifest)
    return paths
