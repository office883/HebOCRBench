# מטריצת מקורות ותנאים

| מקור | תפקיד | מסירת bytes | תנאי מרכזי |
|---|---|---|---|
| Modern BiDi diagnostic | conformance | bundled | CC0-1.0 |
| Contemporary public documents | public-fixed page/line/table OCR | federated או asset מורשה | תנאי המסמך הציבורי והייחוס שלו |
| Modern print lines development | פיתוח | לפי provenance של כל דוגמה | אינו evaluation root |
| Modern human handwriting | הרחבת HTR | לפי provenance של המקור | נפרד מדפוס |
| Pinkas historical handwriting | הרחבה צרה של HTR היסטורי | asset מורשה | CC-BY-4.0, ייחוס, 266 שורות נעולות בלבד |
| HaZefira historical press | הרחבת דפוס היסטורי מעורב | federated-only | external review; revision ו־hash נעולים; אין טענת pure-Rashi |
| Synthetic niqqud diagnostic | diagnostic לא־מדורג | לפי provenance של השבר הנעול | 500 שורות held-out, ללא טעמים |
| Synthetic Noto Rashi diagnostic | diagnostic לא־מדורג | לפי provenance של השבר והגופן | 500 שורות held-out; אינו scan היסטורי |

## עיקרון

גודל אינו סיבה להוציא מקור. Git אינו storage layer לקורפוסים גדולים; bytes מופצים ב־release assets/object storage או נמשכים ממקור סמכותי ונבדקים מול lock.

קוד MIT אינו משנה את רישיון המסמכים. attribution, rights URI, source revision ו־hash נשמרים במניפסט הקורפוס.
