# מדיניות Leaderboard

## דרישות הגשה

כל תוצאה רשמית חייבת לכלול:

- model identity וגרסה;
- command/prompt/config מלאים;
- suite, profile, registry, track ו־dataset fingerprints;
- predictions מלאים;
- latency, חומרה, זיכרון ועלות כאשר רלוונטי;
- failure/timeout counts.

## דירוג

הדירוג הראשי דורש את חמשת מסלולי הליבה ואת שער BiDi. אין דירוג חלקי בטבלת ה־headline. תוצאות forms ו־handwriting מופיעות בטבלאות נפרדות.

## הגינות

- test gold נסתר;
- מזהי test opaque;
- מספר הגשות מוגבל;
- שמירת outputs לצורכי audit;
- הצהרת training contamination;
- refresh יוצר suite fingerprint חדש;
- אין human correction של output לאחר הרצה.

## שוויון ואי־ודאות

הבדלים מוצגים עם paired document bootstrap. כאשר רווחי הסמך חופפים, אין להציג “ניצחון” מוחלט רק בשל אלפית CER.
