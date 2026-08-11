# מדדי HebOCRBench וגבולות אגרגציה

המדדים משותפים כאשר המשימה זהה, אבל יחידת הדיווח אינה משותפת: רק חמשת
מסלולי הדפוס המודרני נכנסים ל־Modern headline. הרחבות אמיתיות מדווחות כל
אחת בנפרד, diagnostics סינתטיים אינם מדורגים, ו־forms אינו מקבל ציון בלי
gold אמיתי ברמת שדה.

## 1. ייצוג קנוני

הציון הקפדני מחושב לאחר NFC בלבד, בסדר Unicode לוגי. אין reverse fallback, אין NFKC בציון הראשי, אין תיקון איות ואין איחוד אוטומטי בין פיסוק עברי ל־ASCII.

## 2. CER ו־GCER

```text
CER = (substitutions + deletions + insertions) / reference units
```

CER משתמש בקוד־פוינטים; GCER משתמש ב־Extended Grapheme Clusters. שניהם מדווחים, משום שניקוד מודרני עשוי להיות combining marks אך אות בסיס שגויה חמורה יותר מסימן חסר.

## 3. WER ו־Exact Match

מדווחים WER, line exact rate, word exact rate ו־page exact rate. הטוקנייזר קבוע בגרסת evaluator ואינו מבצע ניתוח מורפולוגי של תחיליות עבריות.

## 4. Modern Hebrew niqqud

כאשר ניקוד מופיע במקור, מדווחים precision/recall/F1 לפי vowels, dagesh/mapiq
ו־shin/sin dot. ב־Modern headline, סימני מקרא שאינם ב־gold נספרים
כהוספה/הזיה. ה־diagnostic הסינתטי של niqqud מכיל אפס טעמי מקרא ומדווח
בנפרד; הוא אינו benchmark למקרא עם טעמים.

## 5. BiDi

- strict logical line exact rate;
- LTR-run exact rate;
- numeric exact rate;
- bracket semantic accuracy;
- pairwise word-order accuracy;
- visual-order failure count;
- invalid/unbalanced BiDi controls.

כישלון בשער conformance מונע official rank.

## 6. פריסה וסדר קריאה

- region/line precision, recall, F1;
- polygon IoU;
- split/merge errors;
- reading-order edge F1;
- pairwise precedence accuracy;
- order-sensitive page GCER;
- order-tolerant coverage כמדד אבחוני בלבד.

## 7. טבלאות

- table presence F1;
- topology similarity;
- cell-span F1;
- grid-slot accuracy;
- cell-position overlap;
- cell-text GCER.

התאמת טבלאות היא עיוורת ואינה תלויה ב־gold table IDs.

## 8. robustness

מדווחים עקומת איכות לפי degradation family/severity ואת ההפרש מן העמוד המקורי. צאצא degradation תמיד קשור למקורו ונשאר באותו split.

## 9. אגרגציה

ה־Modern headline הוא ממוצע גאומטרי משוקלל של חמשת מסלולי הליבה. מדווחים
גם micro, macro-page, macro-document, worst-slice, median, p90/p95
ו־bootstrap confidence intervals ברמת מסמך. אין אגרגציה בין Modern print,
כתב יד, דפוס היסטורי או diagnostics סינתטיים.
