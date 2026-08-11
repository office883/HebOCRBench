# שחזור תוצאות

## זהויות חובה

- commit של הקוד;
- גרסת Python ותלויות;
- evaluator version;
- track config fingerprint;
- registry/profile/suite/dataset fingerprints;
- hash של predictions והדוח;
- model weights/API version;
- prompt, temperature, resolution ו־retry policy.

## הרצה

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,bidi]'
hebocrbench tracks verify
hebocrbench modern-suite verify --lock modern-suite.lock.json
pytest -q
```

מודל סטוכסטי מורץ במספר seeds או לפחות על subset יציב כדי למדוד שונות. API מרוחק חייב לשמור תאריך וגרסת endpoint.

## Surya OCR 2 עם `llama-server` מתמשך

הרצה מלאה של Surya דרך `llama-cli` טוענת מחדש את קובצי ה־GGUF בכל פריט. לצורך
ה־baseline המלא יש להחזיק תהליך `llama-server` נפרד בחזית. ה־launcher כופה
`127.0.0.1`, מבטל UI והרשאות CORS חיצוניות, קובע `temperature=0` ו־`seed=1`,
ומפיק alias שמחייב את שני SHA-256 של המודל וה־mmproj. ה־adapter שולח את אותו
alias בכל קריאה ודוחה תשובה של מודל אחר.

טרמינל ראשון:

```bash
python scripts/run_surya2_llama_server.py \
  --model-path /absolute/path/to/surya-2.gguf \
  --mmproj-path /absolute/path/to/surya-2-mmproj.gguf \
  --port 8137 \
  --parallel 2 \
  --context-size 16384 \
  --image-max-tokens 2048 \
  --max-generation-tokens 4096
```

טרמינל שני, ללא `--max-pages` (כל 34,267 פריטי חמשת המסלולים):

```bash
python scripts/run_modern_baseline.py \
  --engine surya2-llamacpp \
  --surya-backend server \
  --surya-server-url http://127.0.0.1:8137 \
  --surya-model-path /absolute/path/to/surya-2.gguf \
  --surya-mmproj-path /absolute/path/to/surya-2-mmproj.gguf \
  --surya-max-tokens 4096 \
  --surya-image-max-tokens 2048 \
  --timeout-seconds 600 \
  --workers 2 \
  --suite-lock build/release/modern-suite.lock.json \
  --track-root modern-bidi-v1=build/roots/modern-bidi-v1 \
  --track-root modern-line-recognition-v1=build/derived/modern-line-recognition-v1 \
  --track-root modern-page-ocr-v1=build/derived/modern-page-ocr-v1 \
  --track-root modern-tables-v1=build/derived/modern-tables-v1 \
  --track-root modern-robustness-v1=build/derived/modern-robustness-v1 \
  --output build/baselines/surya2-server
```

ההרצה ניתנת לחידוש מאותו `--output`; cache keys כוללים את bytes התמונה, כל
הגדרות ה־backend, גרסת המנוע ושני hashes של המשקלים. ברירת המחדל הישנה
`--surya-backend cli` נשארה זמינה ל־smoke ולבדיקות תאימות.

## ארטיפקטים

כל baseline bundle כולל `model.json`, `command.json`, predictions, report, logs, timing, failures ו־SHA256SUMS. אין להסתפק בטבלה ידנית.
