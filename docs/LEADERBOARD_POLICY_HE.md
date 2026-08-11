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

הדירוג הראשי דורש את חמשת מסלולי הליבה ואת שער BiDi. אין דירוג חלקי בטבלת
ה־Modern headline. כתב יד מודרני, Pinkas ודפוס HaZefira מדווחים כל אחד
בנפרד. אין דירוג ל־forms עד שיהיה gold אמיתי ברמת שדה, ואין דירוג
ל־diagnostics הסינתטיים של niqqud או Rashi. ציון cross-family אסור.

## הגינות

- evaluation gold אינו נכלל בחבילת המשתתף;
- מזהי evaluation בחבילת המשתתף opaque;
- מספר הגשות מוגבל;
- שמירת outputs לצורכי audit;
- הצהרת training contamination;
- כל refresh של gold יוצר suite fingerprint חדש;
- אין human correction של output לאחר הרצה.

המקורות הם public-fixed. הפרדת gold ומזהים נועדה לאכיפת פרוטוקול ההגשה;
אין לטעון על בסיסה שהחומר hidden, unseen או בהכרח נעדר מנתוני אימון.

## שוויון ואי־ודאות

הבדלים מוצגים עם paired document bootstrap. כאשר רווחי הסמך חופפים, אין להציג “ניצחון” מוחלט רק בשל אלפית CER.
