# מטריצת מקורות ותנאים

| מקור | תפקיד | מסירת bytes | תנאי מרכזי |
|---|---|---|---|
| Modern BiDi diagnostic | conformance | bundled | CC0-1.0 |
| Contemporary public documents | hidden page OCR | federated או asset מורשה | תנאי המסמך הציבורי והייחוס שלו |
| Modern print lines development | פיתוח | לפי provenance של כל דוגמה | אינו hidden test רשמי |
| Modern human handwriting | הרחבת HTR | לפי provenance של המקור | נפרד מדפוס |

## עיקרון

גודל אינו סיבה להוציא מקור. Git אינו storage layer לקורפוסים גדולים; bytes מופצים ב־release assets/object storage או נמשכים ממקור סמכותי ונבדקים מול lock.

קוד MIT אינו משנה את רישיון המסמכים. attribution, rights URI, source revision ו־hash נשמרים במניפסט הקורפוס.
