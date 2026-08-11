# חוזה ה־Full Suite

`full-suite.lock.json` הוא manifest מאחד לכל משפחות HebOCRBench, אך אינו ממציא
ציון מאחד. השדה `reporting_policy.cross_family_score` נעול לערך `forbidden`:

- חמשת מסלולי הדפוס המודרני מרכיבים רק את ה־Modern headline המוגן שלהם.
- כתב יד מודרני, כתב יד Pinkas ודפוס העיתונות ההיסטורי HaZefira הם הרחבות
  אמיתיות ומדווחות כל אחת בנפרד.
- niqqud ו־Rashi סינתטיים הם diagnostics לא־מדורגים ומדווחות בנפרד.
- אין כיום corpus אמיתי מוסמך של מקרא עם טעמים, ואין corpus אמיתי מוסמך של
  pure-Rashi עם תיוג Rashi ברמת שורה/אזור. הרחבת HaZefira כוללת 34 עמודים
  ו־4,016 שורות של דפוס היסטורי מעורב מרובע/Rashi, עם parity מלא בין PAGE
  ל־ALTO ו־root שעבר 12/12 שערי certification, אך אינה סוגרת את פער
  ה־pure-Rashi.
- `modern-forms-v1` נשאר `missing-real-gold` ו־experimental: אותות discovery
  בבחירת 700 העמודים אינם תיוג שדות, ובבדיקה נמצאו אפס `form_fields` אמיתיים.

כל root שסופק נדרש להיות frozen ו־certified. ה־lock קושר את fingerprint של
ה־dataset ואת hashes של manifest, gold, stats, dataset lock, freeze ו־certification.
אימות חוזר מזהה שינוי בבתים או שינוי בחוזה המשפחות.

```bash
hebocrbench full-suite build \
  --component-root modern-bidi-v1=/path/to/modern-bidi-v1 \
  --component-root modern-handwriting-v1=/path/to/modern-handwriting-v1 \
  --component-root historical-hebrew-press-mixed-v1=/path/to/historical-press \
  --output full-suite.lock.json

hebocrbench full-suite verify \
  --lock full-suite.lock.json \
  --component-root modern-bidi-v1=/path/to/modern-bidi-v1 \
  --component-root modern-handwriting-v1=/path/to/modern-handwriting-v1 \
  --component-root historical-hebrew-press-mixed-v1=/path/to/historical-press
```

אפשר להריץ את אותה מעטפת גם דרך `scripts/manage_full_suite.py`.
