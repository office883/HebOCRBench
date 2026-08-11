# מרשם הקורפוסים של HebOCRBench 1.0

המקור הקנוני הוא `corpora/registry.yaml`; זהותו נעולה ב־`corpora/registry.lock.json`.

## מקורות רשמיים

### `modern-bidi-diagnostic-v1`

ערכת conformance מבוקרת ל־Unicode/BiDi. היא בודקת עברית עם מספרים, אנגלית, סוגריים, כתובות, מזהים ופיסוק. היא אינה תחליף למסמכים אמיתיים.

### `modern-public-documents-v1`

מסמכים ציבוריים בני־זמננו בעברית. כל מסמך נרשם במניפסט עם URL, SHA-256, מספר עמודים, תאריך, publisher, משפחת תבנית ודפי הערכה. converter מסוג `modern-pdf` מחייב הצלבת שכבות טקסט ובדיקת סדר לוגי.

### `modern-print-lines-development-v1`

שורות דפוס מודרני לצורכי פיתוח ובסיסי השוואה. הן אינן evaluation root ואינן
משמשות כראיה לייצוגיות של מבחן העמודים.

### `modern-handwriting-lines-v1`

כתב יד אנושי מודרני בהרחבה נפרדת. החלוקה writer-disjoint והציון אינו מתערבב בדפוס.

### `historical-pinkas-handwriting-v1`

הרחבה היסטורית צרה ונפרדת: 266 שורות אמתיות משישה עמודי Pinkas. ה־TAR הנעול
כולל גם חומר שומרוני, אך הממיר fail-closed מקבל רק
`source_dataset=zenodo/pinkas_dataset` וחוסם כל שורה אחרת. זהו holdout ציבורי
קבוע, page-disjoint מתת־קבוצת האימון שבמטמון, מאוסף יחיד וללא מזהי כותבים;
לכן אין טענת writer-disjoint או ייצוגיות לכל כתבי היד ההיסטוריים.

### `historical-hebrew-press-mixed-v1`

הרחבת דפוס היסטורי אמיתית ונפרדת: archive נעול של HaZefira ובו 34 תמונות,
34 קובצי PAGE XML ו־34 קובצי ALTO XML. הממיר דורש התאמה מלאה של 4,016
מזהי `TextLine` וטקסט ללא whitespace בין PAGE ל־ALTO; PAGE משמש gold ו־ALTO
משמש cross-check fail-closed. המקור כולל דפוס מרובע ו־Rashi ברמת הקורפוס,
אבל אין תיוג Rashi ברמת אזור/שורה ולכן `pure_rashi_claim=false`.

### `biblical-niqqud-synthetic-diagnostic-v1`

500 שורות niqqud סינתטיות מתוך test shard נעול, עם הפרדה ברמת item, source,
טקסט ו־font-file hash משבר האימון. נמצאו אפס טעמי מקרא. זהו diagnostic
public-fixed ולא־מדורג, ולא כיסוי של OCR מקראי אמיתי.

### `rashi-print-synthetic-diagnostic-v1`

500 שורות סינתטיות held-out בגופן Noto Rashi יחיד. זהו diagnostic
public-fixed ולא־מדורג; הוא אינו כולל סריקות היסטוריות ואינו מחליף
ערכת pure-Rashi אמיתית.

## תנאי הכללה

מקור רשמי חייב לספק provenance, revision/checksum, שפה `he`, תקופה מוגדרת,
מדיניות split ותנאי שימוש. מקור שכולל תתי־קורפוסים מחוץ לתחום נכנס רק דרך
subset מפורש ונעול. הרחבה היסטורית מדווחת בנפרד ואינה משנה את פרופילי הדפוס
המודרני. diagnostic סינתטי מסומן במפורש ואינו מדורג.

## שינוי מרשם

כל שינוי מחייב regeneration של registry lock, profile lock, corpus builds ו־suite lock. תוצאה המבוססת על fingerprint קודם נשארת תקפה לגרסה הישנה בלבד.
