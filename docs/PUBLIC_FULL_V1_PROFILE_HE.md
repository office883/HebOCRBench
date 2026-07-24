# הפרופיל הקנוני `public-full-v1`

## מעמד

`public-full-v1` הוא פרופיל היעד המדעי של HebOCRBench 1.0. הוא אינו “חבילת הנתונים שהכי קל להוריד”, אלא ההגדרה המלאה של המקורות והמסלולים שעליהם אמור להישען דירוג ציבורי רציני.

ההגדרה המכונתית נמצאת ב־[`corpora/public-full-v1.profile.yaml`](../corpora/public-full-v1.profile.yaml).

## כלל חברות

מקור נכלל כאשר הוא עובר את השערים הבאים:

1. זהות upstream ניתנת לנעילה;
2. acquisition ניתן לשחזור או מתבצע מול root מורשה ומאומת;
3. קיימים image/GT pairs או labels תקפים למשימה;
4. converter/adapter רשמי מפיק schema קנוני;
5. ה־ground truth עובר validation;
6. ניתן לבצע split ברמת מסמך/כותב/מהדורה לפי המשימה;
7. duplicate and leakage audit עובר;
8. רישיון, attribution ו־citation מתועדים;
9. המקור משויך ל־track ול־score namespace נכונים.

הדברים הבאים **אינם** סיבת פסילה:

- archive גדול;
- מספר רב של shards;
- איסור על mirroring ישיר;
- NonCommercial;
- ShareAlike;
- צורך ב־IIIF/API;
- צורך באישור תנאים;
- צורך בגישה מוסדית או מורשית, כל עוד ניתן להגדיר build reproducible לבעלי הגישה.

## חברות v1

### Page OCR / recognition בעברית

- `pinkas-v1`
- `biblia-bnf-1.0`
- `he-wikisource-validated-v1`

### הרחבות עברית לאחר השלמת adapter/lock

- `biblia-bav-extension-v1`
- מקורות דפוס עברי מודרני ומסמכים ישראליים שיוספו ל־registry לאחר מעבר השערים

### כתב עברי בשפות אחרות

- `jochre-yiddish-1.0` — namespace יידי נפרד
- `vaybertaytsh-yidtaknl-v1` — לאחר adapter רשמי
- מקורות ערבית־יהודית ולדינו — לאחר כיסוי מספק

### משימות משלימות

- `hhd-v0.2` — isolated handwritten characters, לא page CER
- `netlay-v1` — layout classification, לא transcription CER

## מה אסור לעשות

- אסור להשמיט מקור חובה ולשמור את אותו profile ID.
- אסור לקרוא ל־`redistributable-v1` בשם “הבנצ׳מרק המלא”.
- אסור לערבב יידיש בציון השפה העברית.
- אסור לערבב character classification ב־page OCR.
- אסור להחליף מקור אמיתי חסר בנתונים סינתטיים.
- אסור למחוק עמודי timeout או failure מן המכנה.
- אסור לשנות source membership ללא profile fingerprint חדש.

## תוצאות רשמיות

כל תוצאה נושאת לפחות:

```text
benchmark_version
profile_id
profile_fingerprint
track_id
track_fingerprint
dataset_fingerprint
source_membership
model_id
model_version
runtime_fingerprint
prediction_bundle_sha256
```

Leaderboard רשמי משווה רק תוצאות בעלות אותה זהות profile/track/dataset. תוצאה על subset יכולה להתפרסם, אך אינה מתחרה באותה טבלה.

## מצב 1.0

הפרופיל מוגדר כעת, אך אינו מקבל חותמת `certified` עד שכל המקורות הנדרשים ממומשים באותו build, עוברים freeze, baselines, חבילות participant/organizer ובדיקת שחרור מלאה.