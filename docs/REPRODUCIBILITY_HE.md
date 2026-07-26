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

## ארטיפקטים

כל baseline bundle כולל `model.json`, `command.json`, predictions, report, logs, timing, failures ו־SHA256SUMS. אין להסתפק בטבלה ידנית.
