# HebOCRBench 1.0 Release Candidate

> **Repository status:** standalone private project. The evaluator and protocol are being consolidated toward 1.0; the dataset release is not considered certified until the release checklist, corpus freeze, and certification gates all pass. See [`STATUS.md`](STATUS.md).

**בנצ׳מרק מקצועי ל־OCR בכתב עברי, עם סדר Unicode לוגי, BiDi, ניקוד, פריסה, סדר קריאה וקורפוסים אמיתיים הנבנים ממקורות נעולים.**

HebOCRBench אינו מסתפק בשאלה אם המודל “מצא בערך את האותיות”. הוא בודק אם התקבל מסמך שאפשר להשתמש בו: עברית בסדר לוגי, מספרים ומקטעים לטיניים בסדרם הפנימי, ניקוד ופיסוק מדויקים, עמודות בסדר הקריאה הנכון, אזורים ושורות במקום הנכון, וטבלאות וטפסים בלי ערבוב בין המבנה החזותי למבנה הלוגי.

גרסה **1.0.0** כוללת שלושה דברים נפרדים אך מחוברים:

1. **Evaluator קבוע ומגובה בבדיקות** — טקסט, Unicode/BiDi, ניקוד, layout, reading order, טבלאות, טפסים, סטטיסטיקה ודוחות ראיות.
2. **ערכת conformance דיאגנוסטית** — מקרים סינתטיים מבוקרים שנועדו לבדוק את המעריך ואת חוזה הפלט, לא להחליף קורפוס־אמת.
3. **מערכת פדרטיבית לקורפוסים אמיתיים** — registry נעול של מקורות PAGE/ALTO אמיתיים, הורדה בטוחה, בדיקת checksum או Git revision, רישוי מפורש, המרה, split חסין־דליפות, audit, freeze ו־release certification.

> קובצי המקור הגדולים אינם מועתקים אוטומטית לתוך חבילת התוכנה. כל build אמיתי נבנה מן המקור הרשמי, מאמת אותו מול `corpora/registry.lock.json`, ושומר את תנאי הרישיון של כל מקור. זו בחירה מכוונת: רישיון של קורפוס אחד אינו “צובע” את כל האחרים, וקישור ציבורי אינו אישור להעתיק הכול ללא תנאים.

## מה נחשב כאן ל־1.0

הגרסה יציבה ברמת **הפרוטוקול והכלים**:

- schema `1.0` ל־gold ולתחזיות;
- registry `1.0.0` עם fingerprint נעול;
- converters רשמיים ל־PAGE XML ול־ALTO 4;
- split ואודיט deterministic;
- corpus manifest, dataset lock ו־file inventory עם SHA-256;
- `FROZEN.json` ו־`CERTIFIED.json` הניתנים רק לאחר בדיקה חוזרת;
- CLI יציב לבנייה, הערכה ואימות;
- version `1.0.0` של חבילת Python.

“1.0” אינו טענה שכל מסמך עברי בעולם כבר נמצא במאגר. מסלולים שחסרה להם אמת־מידה ציבורית מספקת נשארים מסומנים כפערי כיסוי, ולא מתמלאים בעמודים סינתטיים בתחפושת.

## מקורות־אמת הרשומים ב־1.0

| מקור | משימה | סדר גודל | פורמט | רישיון | מעמד |
|---|---|---:|---|---|---|
| Pinkas | כתב־יד עברי היסטורי, end-to-end | 30 עמודים | PAGE XML | CC BY 4.0 | core פתוח |
| BiblIA | כתבי־יד עבריים מימי הביניים | 202 תמונות | ALTO 4.2 | CC BY-NC-SA 4.0 | core מחקרי |
| Jochre Yiddish | דפוס יידי בכתב עברי | 658 עמודים | ALTO 4 עד glyph | CC BY-NC-SA 4.0 | core מחקרי, ציון שפה נפרד |
| Vaybertaytsh.YidTakNL | דפוס וייברטייטש | 242 עמודי אימון מתועדים בפרסום | Transkribus export | CC BY 4.0 | supplementary; adapter ייעודי נדרש |
| HHD v2 | תווי כתב־יד מבודדים | 5,093 דוגמאות | תיקיות תמונה | CC BY 4.0 | supplementary; לא page OCR |
| NetLay | סיווג פריסת ספרים בכתב עברי | יותר מ־1,300 דפים | תמונות/labels | CC BY 4.0 | supplementary; לא transcription OCR |

ה־registry הקנוני הוא [`corpora/registry.yaml`](corpora/registry.yaml); הזהות הקפואה שלו נמצאת ב־[`corpora/registry.lock.json`](corpora/registry.lock.json). עובדות, תנאי שימוש ופערי רישוי מוסברים ב־[`docs/LICENSE_MATRIX_HE.md`](docs/LICENSE_MATRIX_HE.md).

הפרופילים הרשמיים והחברות המדויקת שלהם מוגדרים ב־[`corpora/profiles.yaml`](corpora/profiles.yaml) וננעלים ב־[`corpora/profiles.lock.json`](corpora/profiles.lock.json). `open-v1` כולל בדיוק את `pinkas-v1`; `research-nc-v1` כולל בדיוק את Pinkas, BiblIA ו־Jochre ודורש acceptance מפורש לשני מקורות ה־NC. builder ו־certifier אוכפים את אותה הגדרה.

## התקנה

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,bidi]'
```

אפשר גם להתקין את ה־wheel של המהדורה:

```bash
python -m pip install hebocrbench-1.0.0-py3-none-any.whl
hebocrbench --version
```

`python-bidi` אופציונלי ומשמש אבחון תצוגה בלבד. הציון הראשי מחושב תמיד על הרצף הלוגי ואינו “ניצל” באמצעות היפוך.

# עבודה עם קורפוסים אמיתיים

## 1. הצגת המקורות ותנאי הרישיון

```bash
hebocrbench data list
hebocrbench data licenses
hebocrbench data profiles
```

אפשר להחליף את ה־registry הקנוני:

```bash
hebocrbench data list --registry /path/to/registry.yaml
```

## 2. הורדה ואימות של מקור פתוח

לדוגמה, Pinkas:

```bash
hebocrbench data fetch \
  --source pinkas-v1 \
  --cache .hebocrbench-cache \
  --extract
```

הפקודה:

- מורידה באופן אטומי;
- בודקת גודל ו־MD5 לפי המקור הרשמי;
- מחשבת SHA-256 מקומי;
- מסרבת ל־ZIP traversal, symlinks, devices ו־archive bombs;
- כותבת `.hebocrbench-source.json` עם evidence שנקשר למקור.

## 3. מקור בעל תנאי מחקר/NC

BiblIA ו־Jochre אינם נכנסים בשקט לפרופיל פתוח. יש לקרוא את התנאים ולקבל אותם במפורש:

```bash
hebocrbench data fetch \
  --source biblia-1.0 \
  --accept biblia-1.0 \
  --cache .hebocrbench-cache \
  --extract
```

`--accept` אינו משנה את הרישיון ואינו מעניק זכויות חדשות; הוא רק יוצר עקבה לכך שהבונה לא דילג על השער.

## 4. בניית corpus materialized

לאחר החילוץ, מעבירים root לכל מקור:

```bash
hebocrbench data build \
  --source pinkas-v1 \
  --source-root pinkas-v1=.hebocrbench-cache/pinkas-v1/archive.extracted \
  --profile open-v1 \
  --benchmark-version 1.0.0 \
  --output builds/open-v1
```

ה־build יוצר:

```text
gold.jsonl
images/
stats.json
audit.json
attribution.jsonl
citations.bib
licenses/
source_reports/
dataset.lock.json
manifest.json
```

הוא נכשל אם XML אינו ניתן להמרה, תמונה חסרה, טקסט אינו תקין, split דולף, או מקור research-NC לא אושר.

## 5. Freeze ו־certification

```bash
hebocrbench data freeze --build-root builds/open-v1
hebocrbench release certify --build-root builds/open-v1
```

`release certify` קורא מחדש את כל הקבצים ומוודא:

- schema, hashes ו־file inventory;
- התאמה ל־registry fingerprint;
- evidence אמיתי של acquisition לכל core source;
- התאמת רישיון לפרופיל;
- gold validation;
- audit דליפה חוזר;
- statistics שנבנו מחדש;
- התאמה בין `manifest.json`, `dataset.lock.json` ו־`FROZEN.json`.

רק אז נכתב `CERTIFIED.json`. שינוי של בית אחד ב־`gold.jsonl` או בתמונה מבטל את ההסמכה. פירוט מלא: [`docs/BUILD_REAL_CORPUS_HE.md`](docs/BUILD_REAL_CORPUS_HE.md) ו־[`docs/REPRODUCIBILITY_HE.md`](docs/REPRODUCIBILITY_HE.md).

# ערכת Unicode/BiDi הדיאגנוסטית

```bash
hebocrbench generate \
  --output runs/diagnostic \
  --variants clean,blur,jpeg,low_contrast

hebocrbench validate \
  --gold runs/diagnostic/gold.jsonl \
  --dataset-root runs/diagnostic

hebocrbench sanity \
  --output runs/sanity \
  --variants clean \
  --limit 28
```

ה־sanity matrix מריץ בכוונה:

- prediction מושלם;
- פלט ריק;
- עברית הפוכה/visual order;
- ניקוד שהוסר;
- פיסוק עברי שקופל ל־ASCII;
- סדר אזורים שהוחלף;
- טקסט נוסף והזיות.

אם evaluator אינו מעניש כשל צפוי, הבדיקה עצמה נכשלת.

# הערכת מודל

תחזית מינימלית:

```json
{
  "schema_version": "1.0",
  "page_id": "doc-001-p001",
  "regions": [
    {
      "region_id": "pred-r1",
      "type": "body",
      "polygon": [[100, 100], [1500, 100], [1500, 300], [100, 300]],
      "base_direction": "rtl",
      "reading_index": 0,
      "lines": [
        {
          "line_id": "pred-l1",
          "polygon": [[120, 120], [1480, 120], [1480, 280], [120, 280]],
          "text": "בשנת 2026 הופעלה גרסה OCR-v2.1.",
          "base_direction": "rtl",
          "language": "he"
        }
      ]
    }
  ],
  "reading_order": {"edges": []},
  "timing_ms": 84.2,
  "model": {"name": "my-ocr", "version": "2026-07-23"}
}
```

הערכה:

```bash
hebocrbench evaluate \
  --gold builds/open-v1/gold.jsonl \
  --predictions runs/my-model/predictions.jsonl \
  --dataset-root builds/open-v1 \
  --config benchmark.yaml \
  --output runs/my-model/report
```

הפלט כולל `metrics.json`, `per_page.jsonl`, `errors.jsonl`, `summary.csv`, `report.html` ו־`run_manifest.json`.

## מדדים מרכזיים

- code-point CER ו־grapheme-cluster GCER;
- WER ו־exact match;
- Base-Letter CER;
- mark precision/recall/F1 לניקוד ולטעמים;
- אותיות סופיות ופיסוק עברי;
- LTR/numeric/URL/email/bracket exactness;
- visual-order suspicion ללא תיקון הציון;
- line score מול page-order score;
- region/line precision, recall, IoU, split ו־merge;
- reading-order edge F1 ו־pairwise precedence;
- טבלאות וטפסים;
- missing/extra pages והזיות;
- micro, macro page/document, slices, worst groups ו־bootstrap;
- latency, cost, retry ו־failure כאשר המגיש מספק אותם.

# כללים שאינם נתונים למשא ומתן

1. עברית נשמרת ומוערכת ב־Unicode logical order.
2. RTL אינו שיקוף של תמונה או מחרוזת.
3. רצפים לטיניים ומספריים נשארים בסדר LTR הפנימי שלהם.
4. NFC הוא פרופיל ה־strict; אין NFKC בציון הראשי.
5. ניקוד, טעמים, אותיות סופיות ופיסוק עברי אינם מתקפלים לצורכי “ציון יפה”.
6. אין לבחור `min(score(normal), score(reversed))`.
7. סדר קריאה הוא graph מפורש; קואורדינטות אינן תחליף.
8. עברית, יידיש ושפות נוספות בכתב עברי מקבלות ציונים נפרדים.
9. recognition-only עם oracle layout אינו end-to-end OCR.
10. build אמיתי ללא provenance ורישוי מאומת אינו release certified.

# תיעוד

- [כרטיס הבנצ׳מרק](docs/BENCHMARK_CARD_HE.md)
- [הנחיות אנוטציה](docs/ANNOTATION_GUIDE_HE.md)
- [הגדרות המדדים](docs/METRICS_HE.md)
- [Registry ומעמד המקורות](docs/CORPUS_REGISTRY_HE.md)
- [בניית קורפוס אמיתי](docs/BUILD_REAL_CORPUS_HE.md)
- [מטריצת רישיונות](docs/LICENSE_MATRIX_HE.md)
- [שחזור, freeze ו־certification](docs/REPRODUCIBILITY_HE.md)
- [ממשל, split ו־hidden test](docs/DATASET_GOVERNANCE_HE.md)
- [מדיניות leaderboard](docs/LEADERBOARD_POLICY_HE.md)
- [מיפוי PAGE/ALTO](docs/INTEROPERABILITY.md)
- [Checklist לשחרור](docs/RELEASE_CHECKLIST.md)

# רישיון

קוד HebOCRBench מופץ ברישיון MIT. טקסטי ה־diagnostic שנוצרו בפרויקט הם CC0-1.0. **אין רישיון־על אחד לקורפוסים החיצוניים**: לכל מקור נשמרים הרישיון, הייחוס והמגבלות שלו, והם גוברים על רישיון הקוד. קובצי פונט אינם מופצים.
