# בניית קורפוס עברית מודרנית אמיתי

## שלבי עבודה

1. בחר profile מודרני רשמי.
2. רכוש/הורד כל מקור דרך manifest או revision נעול.
3. אמת checksum, מספר קבצים וזכויות.
4. המר ל־schema 1.0 בסדר Unicode לוגי.
5. פצל לפי document/template/writer — לעולם לא באקראי ברמת שורה.
6. הרץ schema validation, image validation ו־leakage audit.
7. הקפא עם `FROZEN.json`.
8. הסמך באופן עצמאי עם `CERTIFIED.json`.
9. בנה suite lock מכל חמשת roots המוסמכים.

## PDF מודרני

```bash
python scripts/build_modern_public_corpus.py \
  --manifest manifests/modern-public-documents-v1.yaml \
  --output builds/modern-public
```

כל PDF נבדק מול hash ומספר עמודים. הטקסט מופק בידי PyMuPDF ו־Poppler; אי־הסכמה מהותית, visual-order חשוד, תווי BiDi לא מאושרים, טעמי מקרא או שכבה חסרה גורמים לדחייה.

## יעדי מינימום

- 100 מסמכים;
- 500 עמודים;
- 50 משפחות תבנית;
- 25 עמודי טבלה;
- 100 עמודי mixed BiDi;
- פיצול train/dev/test זר ברמת template family.

## Freeze והסמכה

```bash
hebocrbench data freeze --build-root builds/modern-public
hebocrbench release certify --build-root builds/modern-public
```

המערכת נכשלת אם קובץ השתנה, מקור חסר, split דולף או evidence אינו תואם למרשם.
