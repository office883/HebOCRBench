# מניפסט Dataset v1 — משפחות Hebrew OCR

## חמשת roots הראשיים

1. `modern-bidi-v1` — מקרי conformance מבוקרים.
2. `modern-line-recognition-v1` — שורות אמת ממסמכים מודרניים.
3. `modern-page-ocr-v1` — עמודים מלאים, פריסה וסדר קריאה.
4. `modern-tables-v1` — עמודי טבלה ומבנה תאים.
5. `modern-robustness-v1` — צמדי מקור/degradation.

כל root מכיל `gold.jsonl`, תמונות, `manifest.json`, `dataset.lock.json`, `FROZEN.json`, `certification.json` ו־`CERTIFIED.json`.

כל חמשת ה־roots האלה frozen ו־certified. רק הם משתתפים בציון ה־Modern
headline.

## Suite lock

`modern-suite.lock.json` מכיל hash של ה־gold וההסמכה בכל track, dataset fingerprint, registry/profile fingerprints ומעמד maturity. רק roots במעמד `certified` יכולים להרכיב את הציון הראשי.

## הרחבות אמיתיות נפרדות

- `modern-handwriting-v1` — כתב יד מודרני writer-disjoint;
- `historical-pinkas-handwriting-v1` — 266 שורות משישה עמודים, public-fixed,
  מאוסף יחיד וללא מזהי כותבים;
- `historical-hebrew-press-mixed-v1` — 34 עמודי HaZefira ו־4,016 מזהי שורה
  עם parity בין PAGE ל־ALTO; ה־root עבר 12/12 שערי certification. הדפוס
  מעורב מרובע/Rashi ברמת הקורפוס; אין תיוג Rashi ברמת שורה או אזור ולכן זו
  אינה ערכת pure-Rashi.

כל הרחבה מקבלת דוח משלה ואינה משנה את ה־Modern headline.

## Diagnostics ופערי כיסוי

- `biblical-niqqud-synthetic-diagnostic-v1` — 500 שורות niqqud סינתטיות
  held-out, ללא טעמי מקרא;
- `rashi-print-synthetic-diagnostic-v1` — 500 שורות סינתטיות ב־Noto Rashi
  יחיד, ללא סריקות היסטוריות.

שני ה־diagnostics אינם מדורגים ואינם מצטרפים לשום headline. קורפוס אמיתי
של מקרא עם טעמים וערכת pure-Rashi אמיתית נשארים פערים מפורשים.

`modern-forms-v1` הוא `missing-real-gold`: ב־700 עמודי המקור נמצאו אפס
`form_fields`. לכן אין root זכאי ואין ציון forms ב־v1.

## Full-suite lock

`full-suite.lock.json` מחבר זהויות והוכחות מכל המשפחות בלי לחשב ציון
cross-family. מדיניות הדיווח שלו נועלת `cross_family_score=forbidden`.
