# ממשל נתונים

## Scope governance

הוועדה/maintainers שומרים את v1 בתחום עברית מודרנית בלבד. הוספת שפה, תקופה או משימה חדשה מחייבת track ו־profile נפרדים ואינה משנה בדיעבד את הציון הראשי.

## Split governance

- מסמך, template family, writer ו־source ancestry אינם חוצים splits.
- כל degradation יורש את split המקור.
- test gold נשמר בנפרד מחבילת המשתתף.
- refresh של hidden test יוצר suite fingerprint חדש.

## שינויים ותיקונים

תיקון transcription לאחר release מתועד ב־errata. שינוי gold מחייב גרסת dataset חדשה; אין עריכה שקטה של bytes תחת אותו fingerprint.

## פרטיות

אין לכלול PII רגיש ללא הצדקה, בסיס שימוש ותהליך הסרה/אנונימיזציה. Hidden test אינו הופך מסמך רגיש למותר.
