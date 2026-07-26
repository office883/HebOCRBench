# מרשם הקורפוסים של HebOCRBench 1.0

המקור הקנוני הוא `corpora/registry.yaml`; זהותו נעולה ב־`corpora/registry.lock.json`.

## מקורות רשמיים

### `modern-bidi-diagnostic-v1`

ערכת conformance מבוקרת ל־Unicode/BiDi. היא בודקת עברית עם מספרים, אנגלית, סוגריים, כתובות, מזהים ופיסוק. היא אינה תחליף למסמכים אמיתיים.

### `modern-public-documents-v1`

מסמכים ציבוריים בני־זמננו בעברית. כל מסמך נרשם במניפסט עם URL, SHA-256, מספר עמודים, תאריך, publisher, משפחת תבנית ודפי הערכה. converter מסוג `modern-pdf` מחייב הצלבת שכבות טקסט ובדיקת סדר לוגי.

### `modern-print-lines-development-v1`

שורות דפוס מודרני לצורכי פיתוח ובסיסי השוואה. אינן משמשות כטענה ל־hidden test ייצוגי.

### `modern-handwriting-lines-v1`

כתב יד אנושי מודרני בהרחבה נפרדת. החלוקה writer-disjoint והציון אינו מתערבב בדפוס.

## תנאי הכללה

מקור רשמי חייב לספק provenance, revision/checksum, שפה `he`, תקופה `modern`, מדיניות split ותנאי שימוש. מקור שכולל תתי־קורפוסים מחוץ לתחום נכנס רק דרך subset מפורש ונעול.

## שינוי מרשם

כל שינוי מחייב regeneration של registry lock, profile lock, corpus builds ו־suite lock. תוצאה המבוססת על fingerprint קודם נשארת תקפה לגרסה הישנה בלבד.
