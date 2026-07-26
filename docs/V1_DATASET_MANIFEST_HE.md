# מניפסט Dataset v1 — עברית מודרנית

## חמשת roots הראשיים

1. `modern-bidi-v1` — מקרי conformance מבוקרים.
2. `modern-line-recognition-v1` — שורות אמת ממסמכים מודרניים.
3. `modern-page-ocr-v1` — עמודים מלאים, פריסה וסדר קריאה.
4. `modern-tables-v1` — עמודי טבלה ומבנה תאים.
5. `modern-robustness-v1` — צמדי מקור/degradation.

כל root מכיל `gold.jsonl`, תמונות, `manifest.json`, `dataset.lock.json`, `FROZEN.json`, `certification.json` ו־`CERTIFIED.json`.

## Suite lock

`modern-suite.lock.json` מכיל hash של ה־gold וההסמכה בכל track, dataset fingerprint, registry/profile fingerprints ומעמד maturity. רק roots במעמד `certified` יכולים להרכיב את הציון הראשי.

## הרחבות

`modern-forms-v1` ו־`modern-handwriting-v1` מדווחות בנפרד ואינן משנות את headline print score.
