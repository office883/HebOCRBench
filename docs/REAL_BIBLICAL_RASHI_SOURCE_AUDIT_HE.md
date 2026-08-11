# ביקורת מקורות אמיתיים למקרא מנוקד ולדפוס רש״י

תאריך snapshot: 2026-08-11.

מסמך זה בודק שני פערי כיסוי מוגדרים ב־HebOCRBench:

1. סריקות או צילומים אמיתיים של עברית מקראית הכוללת גם ניקוד וגם טעמי
   מקרא, עם תעתיק מדויק המיושר לעמוד או לשורה;
2. סריקות אמיתיות של דפוס רש״י טהור, עם gold מסומן ברמת אזור או שורה.

המסקנה היא fail-closed: נכון לתאריך הבדיקה לא נמצא release ציבורי ומוכח
שעומד בכל הדרישות של אחד משני היעדים. יש חומרי גלם טובים לבניית corpus
ידני, אך אין להפוך אותם ל־official track באמצעות התאמה משוערת, טקסט ייחוס
לא מיושר, פלט OCR, או נתונים סינתטיים. מסמך זה אינו משנה registry, profile,
track או lock כלשהם.

## תנאי הקבלה

מקור מתקבל כ־gold אמיתי רק אם כל התנאים הבאים מתקיימים:

- הקלט הוא צילום או סריקה של מסמך פיזי, לא rendering של טקסט דיגיטלי;
- התעתיק הוא אנושי או תוקן ואושר כ־ground truth, ולא פלט OCR בלתי בדוק;
- לכל יחידת score יש התאמה חד־משמעית בין bytes של תמונה לבין תעתיק;
- alignment ברמת עמוד כולל גבולות מילה מדויקים גם כאשר פסוק נחתך בין
  עמודים; alignment ברמת שורה כולל polygon או baseline ותעתיק מלא לשורה;
- במקרא נשמרים בפועל סימני הניקוד וטעמי המקרא שבתמונה;
- ב־pure-Rashi כל שורת score מסומנת ככתב רש״י, בלי להסתמך על כך שהספר או
  העמוד כוללים כתב רש״י במקום כלשהו;
- קיימים revision או version, schema מתועד, הורדה ישירה או fetch יציב,
  manifest מלא ו־checksum לכל artifact שנכנס ל־root;
- כל חוסר, ambiguity, mismatch או artifact שאינו בר־שחזור גורם לדחייה.

לצורך בדיקות Unicode במסמך זה, ״טעמי מקרא״ נספרו בטווח
`U+0591–U+05AF`. ניקוד נספר מתוך סימני הניקוד העבריים הרלוונטיים בבלוק
Hebrew, בנפרד מאותיות הבסיס.

## החלטת accept/reject

| מקור | יעד | קבלה כחומר גלם | קבלה כ־official gold כעת | סיבת ההחלטה |
|---|---|---:|---:|---|
| UXLC 2.5 + תמונות לנינגרד ב־Sefaria | מקרא מנוקד ומוטעם | כן | **REJECT** | 924 תמונות אמיתיות וטקסט מלא, אך גבולות העמוד הם טווחי פסוקים משוערים; אין גבול מילה מדויק או line geometry |
| BIMA 2.x / Corpus Masoreticum | מקרא מנוקד ומוטעם | חלקית | **REJECT** | קיימים image-linked paths אמיתיים, אך geometry חלקי, export דינמי ולא קפוא, ו־TEI שנבדק אינו XML תקין |
| BiblIA v1.0 | מקרא מנוקד ומוטעם | לא ליעד זה | **REJECT** | alignment אמיתי וטוב, אך אפס טעמי מקרא ואפס ניקוד בעמודים המקראיים |
| Damascus Pentateuch, Library of Congress | מקרא מנוקד ומוטעם | כן | **REJECT** | 466 קובצי תמונה אמיתיים עם IIIF, אך אין תעתיק page/line-aligned או geometry |
| DiJeSt + CoDiAJe Meam Loez | pure-Rashi | מותנה בקבלת export מקורי | **REJECT** | יש הצהרה על 50 דפי GT, אך אין חבילת תמונה+PAGE XML ציבורית, אין hash/version, ואין labels שמבודדים שורות רש״י |
| HaZefira OmiLab locked export | pure-Rashi | כן, ל־mixed historical press | **REJECT** ל־pure-Rashi | 34 עמודים ו־4,016 שורות אמיתיות, אך כתב מרובע ורש״י מעורבים ואין label ברמת אזור או שורה |
| Mahpod–Keller / Bar-Ilan Responsa | pure-Rashi | לא נגיש | **REJECT** | המאמר מדווח על מיליוני אותיות מסומנות, אך אין dataset ציבורי או artifact שניתן לאמת |
| `isaacmg/qwen3-vl-8b-hebrew-rashi-merged` | pure-Rashi | לא | **REJECT** | model card מדווח על 65 דפי eval, אך ה־images וה־gold אינם מפורסמים |
| Vaybertaytsh.YidTakNL | pure-Rashi | כן, ל־Vaybertaytsh | **REJECT** | Vaybertaytsh הוא יעד טיפוגרפי נפרד ואינו הוכחה ל־pure-Rashi |
| Source Library Zohar | pure-Rashi | כן | **REJECT** | הסריקות אמיתיות, אך המקור עצמו אומר שאין להן canonical page coordinates ונדרשת התאמה אנושית |

אין שורת `ACCEPT` בעמודת official gold. זאת אינה קביעה שאין חומר אמיתי
בעולם, אלא שאין כרגע artifact ציבורי, קפוא ומוכח שאפשר להכניס ל־benchmark
בלי עבודת alignment או בלי קבלת data שלא פורסם.

## מקרא: UXLC ותמונות קודקס לנינגרד

### artifacts מאומתים

- דף טכני רשמי:
  <https://www.tanach.us/Pages/Technical.html>
- תיעוד הקישור לתמונות וה־page ranges:
  <https://www.tanach.us/Pages/LC%20images.html>
- ZIP ישיר של הטקסט:
  <https://tanach.us/Books/Tanach.xml.zip>
- index של עמודי הקודקס:
  <https://tanach.us/XSL/LCIndex.xml>
- תבנית URL לתמונה:
  `https://manuscripts.sefaria.org/leningrad-color/BIB_LENCDX_F{folio}.jpg`
- דוגמת תמונה ישירה:
  <https://manuscripts.sefaria.org/leningrad-color/BIB_LENCDX_F001B.jpg>

הזהות שפורסמה על ידי UXLC ונבדקה שוב מקומית:

| שדה | ערך |
|---|---|
| text version | UXLC 2.5 |
| site build | 27.6 |
| build timestamp | 31 Mar 2026 12:00 |
| `Tanach.xml.zip` size | 2,365,002 bytes |
| `Tanach.xml.zip` SHA-256 | `1bc6e006f43d3b18f2f718cefa3aa4774cac2c54092c28d173dd61996c43a050` |
| schema | TEI-flavoured XML, validated by `Tanach.xsd` |

תוצאת parse דטרמיניסטי של ה־ZIP שנבדק:

| פריט | count |
|---|---:|
| canonical book files | 39 |
| chapters | 929 |
| verses | 23,213 |
| `<w>` elements | 304,223 |
| `<k>` elements | 1,269 |
| `<q>` elements | 1,279 |
| samekh spacings | 1,981 |
| pe spacings | 1,181 |
| cantillation code points | 249,071 |
| niqqud code points | 1,000,514 |

ה־page index הוא קובץ HTML בשם `LCIndex.xml`. ב־snapshot שנבדק:

| פריט | ערך |
|---|---:|
| size | 78,992 bytes |
| local snapshot SHA-256 | `ba180365cb55e3b888a8e3ea4dc4f0e90e206c0af200ae942dab340a378e13b4` |
| range | folio 1v–463r |
| unique image page IDs | 924 |
| data rows | 954 |
| Masoretic-notes-only pages | 4 |

ה־SHA-256 של `LCIndex.xml` הוא receipt מקומי של snapshot מתאריך הביקורת;
הוא אינו checksum שפורסם על ידי UXLC. מספר rows גדול ממספר התמונות מפני
שעמוד יכול להכיל חומר משני ספרים.

בדיקת image URL לדוגמה, `BIB_LENCDX_F001B.jpg`:

| שדה | ערך |
|---|---|
| dimensions | 3683×4224 pixels |
| size | 5,652,297 bytes |
| local SHA-256 | `d2699be4391772321aa45a8bc9954bd42dd8504bbdaf05b59d4d8ce7b3d4cfaf` |

זהו hash של דוגמה אחת בלבד. לא נמצא manifest רשמי שמקבע את כל 924 קובצי
התמונה ואת ה־hash של כל אחד מהם.

### מדוע UXLC נדחה כ־GT מוכן

תיעוד UXLC אומר במפורש שטווח הטקסט של עמוד כולל את פסוק ההתחלה ואת פסוק
הסיום גם כאשר הם אינם נמצאים בשלמותם בתמונה. פסוק מפוצל מסומן רק ב־`a`
או `b`, ללא גבול מילה. מיקום טקסט בעמוד ניתן כאחוז משוער. נוסף לכך, תצוגת
qere/ketiv בטקסט אינה התאמה ליניארית פשוטה למה שמופיע בעמוד ובשוליים.

לכן בחירה אוטומטית של כל הפסוקים בטווח תוסיף טקסט שאינו בתמונה או תחסיר
טקסט שנמצא בה. חיתוך פסוק לשניים לפי מספר מילים יהיה heuristic ולא gold.
אין גם polygons או baselines של שורות. UXLC מתקבל כמקור ייחוס וכבסיס
לבנייה ידנית בלבד.

## מקרא: BIMA 2.x / Corpus Masoreticum

### endpoints ותכולה

- תיאור רשמי של image-linked coloured text paths ו־IIIF:
  <https://digi.ub.uni-heidelberg.de/en/corpusmasoreticum/index.html>
- DOI של המהדורה:
  <https://doi.org/10.11588/edition.corpusmasoreticum>
- ממשק BIMA:
  <https://bima2.corpusmasoreticum.de/>
- public catalog API:
  <https://api.dev.corpusmasoreticum.de/v1/manuscript/published>
- דוגמת page JSON:
  <https://api.dev.corpusmasoreticum.de/v1/manuscript-page/B32inf./009_B32inf_c.3r>
- export שנמצא בממשק:
  `POST https://api.dev.corpusmasoreticum.de/v1/manuscript-page/{page_uuid}/export-tei`

האתר הרשמי מכנה את המערכת BIMA 2.0. ה־TEI generator שנבדק מזהה את עצמו
כ־BIMA 2.2; אין מכאן release archive קפוא בשם 2.2. ה־API נמצא תחת host
`api.dev`, ולכן כל count להלן הוא snapshot ולא הבטחת version.

ב־2026-08-11 החזיר ה־catalog:

| פריט | count |
|---|---:|
| published manuscripts | 19 |
| published pages | 1,525 |
| readings | 115,644 |
| Corpus Masoreticum manuscripts | 15 |
| Corpus Masoreticum pages | 1,503 |
| Corpus Masoreticum readings | 108,623 |

בדיקה מלאה של 30 העמודים שפורסמו עבור `B32inf.` החזירה:

| פריט | count |
|---|---:|
| pages with `Biblical Main Text` | 30 |
| readings | 1,172 |
| feature records | 9,302 |
| feature records carrying path geometry | 1,427 |
| path-bearing records containing cantillation | 1,401 |
| path-bearing records containing niqqud | 1,421 |
| cantillation code points in the returned Unicode | 9,461 |
| niqqud code points in the returned Unicode | 31,009 |

### מדוע BIMA נדחה כ־GT מוכן

רק 1,427 מתוך 9,302 feature records נושאים path. לכן ה־JSON מספק עוגנים
או קטעים אמיתיים ומועילים, אך אינו geometry מלא לכל התעתיק המקראי בעמוד.

נוסף לכך, כל 30 קובצי ה־TEI שיוצאו בבדיקה נכשלו כ־well-formed XML בגלל
`&SEP...` גולמי בתוך כתובות IIIF; בחלקם נמצאו גם `xml:id` כפולים. רוב
ה־geometry שקיים ב־JSON אינו נשמר ב־TEI: בכל 30 ה־exports יחד נמצאו רק 27
רשומות `line`/`zone`/`path` של main context. לא פורסמו bulk archive,
checksum או schema-validated release שמקבעים את מצב ה־API.

מותר להשתמש ב־path-bearing fragments כחומר לבדיקת annotation ידנית. אסור
להציג את כל תעתיק העמוד כמיושר, או לתקן את ה־TEI מקומית ואז לכנות את התוצר
release רשמי של המקור.

## מקרא: BiblIA v1.0

### artifact והזהות שלו

- Zenodo record:
  <https://zenodo.org/records/5167263>
- DOI:
  <https://doi.org/10.5281/zenodo.5167263>
- הורדה ישירה:
  <https://zenodo.org/api/records/5167263/files/BiblIA_dataset.zip/content>

| שדה | ערך |
|---|---|
| version | 1.0 |
| published | 2021-08-06 |
| archive size | 546,198,414 bytes |
| publisher MD5 | `8cef38c1e501afd628d245a72f49bf05` |
| locally verified MD5 | `8cef38c1e501afd628d245a72f49bf05` |
| local SHA-256 | `aa87702a28ab0cd0c8a0c6a075dea8c2660c8dbf6fcc543b8eeffe6882e58e7a` |
| annotation schema | ALTO 4.2 XML |
| archive members | 340 |
| annotated page XML files | 202 |
| bundled JPG files | 132 |

70 התמונות הנותרות מתועדות באמצעות URLs של מוסדות המקור במקום להיכלל
ב־ZIP.

### בדיקת התוכן

| פריט | count |
|---|---:|
| all annotated lines | 12,461 |
| Biblical pages | 178 |
| Biblical lines | 11,301 |
| cantillation code points בכל ה־bundle | 0 |
| niqqud code points בעמודים המקראיים | 0 |
| isolated Hebrew point marks בכל ה־bundle | 6 |

ששת ה־point marks היחידים מופיעים בארבעה עמודים Rabbinic. לכן BiblIA הוא
מקור אמיתי, מסומן ושימושי ל־HTR עברי היסטורי, אך הוא אינו מכסה מקרא מנוקד
או טעמי מקרא ואינו יכול להיות proxy ליעד הזה.

## מקרא: מקורות image-only או reference-only

הפריט המוסדי
[`Damascus Pentateuch`, Library of Congress, LCCN 2021667535](https://www.loc.gov/item/2021667535/)
מספק 466 קובצי תמונה, PDF ו־ZIP, וכן
[`IIIF Presentation manifest`](https://www.loc.gov/item/2021667535/manifest.json).
ה־ZIP הישיר הוא
<https://tile.loc.gov/storage-services/service/gdc/gdcwdl/wd/l_/11/36/4/wdl_11364/wdl_11364.zip>.
הרשומה מתארת 229 דפי קלף, שלוש עמודות ו־20 שורות לעמודה, עם ניקוד מלא,
טעמים והערות מסורה. זו סריקה אמיתית מצוינת, אבל אין ברשומה תעתיק
page/line-aligned, PAGE/ALTO XML או geometry של שורות. גם מספר הקבצים כולל
surfaces מנהליים ולכן אינו שווה אוטומטית למספר דפי כתב־היד. הרשומה אינה
מפרסמת release version או checksum ל־ZIP; קיבוע bytes מקומי לבדו לא יוצר
gold מיושר ולכן המקור נשאר `REJECT`.

סריקות Aleppo Codex ומקורות ספרייה דומים הן אף הן חומר גלם אמיתי, אך לא נמצא
לצדן release עם תעתיק מדויק המיושר ל־bytes של כל עמוד או שורה. טקסט מקראי
דיגיטלי מקביל, גם אם הוא קרוב מאוד לעד, אינו GT עד שבודק אנושי הכריע את
גבולות העמוד, variant readings, qere/ketiv, תוספות מסורה והבדלים כתיביים.
לפיכך מקורות אלה נשארים מחוץ ל־benchmark הנוכחי.

## דפוס רש״י: DiJeSt ו־CoDiAJe

### מה קיים בפועל

- תיאור ה־ground truth של DiJeSt:
  <https://dijest.net/gtmodel/>
- DiJeSt 3.0 public model:
  <https://www.transkribus.org/models/dijest-30>
- CoDiAJe:
  <https://lindat.mff.cuni.cz/services/teitok-live/codiaje/index.php>

DiJeSt מצהיר על 30 דפי ground truth ממהדורת `Meam Loez` של Yaakov Culi
משנת 1730 ועל 20 דפים ממהדורת Yitzhak Magriso משנת 1753. התעתיקים תוקנו
והטקסט הועלה ל־CoDiAJe. דוגמאות לרשומות הציבוריות:

- `xmlfiles/Tests/lad410.xml` — Culi, 1730, folios 201r–211v;
- `xmlfiles/lad401a.xml` — Culi, 1730, folios 2r–19v;
- `xmlfiles/Proceso/lad424.xml` — Magriso, 1753, folios 1r–11r.

URL מלא לדוגמה:

`https://lindat.mff.cuni.cz/services/teitok-live/codiaje/index.php?action=file&cid=xmlfiles/Tests/lad410.xml`

הרשומה מציגה page breaks וטוקנים, אך metadata של `lad410.xml` מציין
`Private collection of Aldina Quintana`. בשלושת ה־records שנבדקו, קריאת
ה־facsimile API החזירה רשימה ריקה:

```text
GET .../index.php?action=ajax&data=facs&cid=xmlfiles/Tests/lad410.xml
{"cid":"xmlfiles/Tests/lad410.xml","facs":[""]}
```

אותה תוצאה התקבלה עבור `lad401a.xml` ועבור `lad424.xml`.

DiJeSt 3.0 עצמו הוא model מעורב, לא dataset של רש״י טהור:

| שדה | ערך |
|---|---|
| published | 2025-06-13 |
| model ID | 357765 |
| training pages | 2,853 |
| lines | 173,190 |
| words | 1,498,332 |
| validation CER | 1.79% |
| languages/material | Hebrew, Judeo-Arabic, Ladino, Yiddish; modern and historical Hebrew-script typefaces |

ה־CER הוא על validation של model מעורב ואינו ציון pure-Rashi.

### מדוע DiJeSt נדחה כ־GT מוכן

לא נמצא public Transkribus export שמכיל יחד את bytes של 50 התמונות ואת
PAGE XML או ALTO המתוקן. לא פורסמו collection/page IDs מלאים, export
revision, schema version, manifest או checksums. תצוגת CoDiAJe מספקת טקסט
עם page breaks, אך לא תמונות, line polygons או script labels. גם בעמודי
Meam Loez יש כתב מרובע, כותרות או קטעים מודגשים; עצם שיוך הספר למשפחת דפוס
רש״י אינו הופך כל שורה ל־pure-Rashi.

לכן זהו lead איכותי לקבלת data מן המחברים, לא artifact שניתן לצרף כעת.

## דפוס רש״י: HaZefira המעורב

ה־OmiLab archive שכבר משמש את הרחבת הדפוס ההיסטורי הוא מקור אמיתי וקפוא:

- project page:
  <https://www.openu.ac.il/en/omilab/pages/historicalnewspaper.aspx>
- repository:
  <https://github.com/omilab/historical_press>
- locked revision:
  `4908643b90bde64f56704b7375e49e13028fe049`
- direct locked artifact:
  <https://raw.githubusercontent.com/omilab/historical_press/4908643b90bde64f56704b7375e49e13028fe049/%20hazfirahGT2htr%2B.zip>

| שדה | ערך |
|---|---:|
| artifact size | 44,387,813 bytes |
| artifact SHA-256 | `775e77227cbd46099487d3294d8cfd449ced7c8b6eeb7865ba41f053fe1b0ea8` |
| images | 34 |
| PAGE XML files | 34 |
| ALTO XML files | 34 |
| matching line IDs | 4,016 |

המקור מתקבל כהרחבת historical press מעורבת. הוא נדחה רק לדרישת
pure-Rashi: ה־PAGE וה־ALTO אינם מסמנים כל `TextLine` או `TextRegion` ככתב
מרובע, רש״י או mixed. בחירת שורות לפי font classifier או לפי מראה היא
pseudo-labeling ולא gold.

## דפוס רש״י: מקורות נוספים שנדחו

### Mahpod–Keller / Bar-Ilan Responsa

המאמר [Auto-ML Deep Learning for Rashi Scripts OCR](https://arxiv.org/abs/1811.01290)
מדווח על יותר משלושה מיליון אותיות מסומנות, מתוך 170 ספרים בפרויקט
Responsa, ועל accuracy מעל 99.8%. לא נמצא repo, Zenodo record, direct
download, schema, version או checksum של התמונות וה־annotations. מספר
במאמר אינו תחליף ל־dataset נגיש ובר־אימות.

### מודל Hugging Face של `isaacmg`

- model:
  <https://huggingface.co/isaacmg/qwen3-vl-8b-hebrew-rashi-merged>
- metadata API:
  <https://huggingface.co/api/models/isaacmg/qwen3-vl-8b-hebrew-rashi-merged>
- audited model commit:
  `583c1af20eb82e07a9e82604e782246273c02539`

ה־README מדווח על 65 דפי Vilna Talmud held out ועל CER נפרד לאזורי Gemara,
Rashi ו־Tosafot. אין קישור ל־dataset, אין manifest של 65 העמודים ואין GT
להורדה. בזמן הביקורת החזיר
`https://huggingface.co/api/datasets?author=isaacmg&limit=100` את `[]`.
משקלי model ונתוני eval הם artifacts שונים; פרסום הראשון אינו מפרסם את
השני.

### Vaybertaytsh.YidTakNL

- Figshare v1:
  <https://doi.org/10.6084/m9.figshare.25422844.v1>
- Figshare API:
  <https://api.figshare.com/v2/articles/25422844>
- Transkribus baseline model:
  <https://www.transkribus.org/models/vaybertaytshyidtaknl-baseline>

| שדה | ערך |
|---|---:|
| Figshare version | 1 |
| published | 2024-03-16 |
| linked files | 1 external link-only entry |
| published file size/hash in Figshare API | 0 bytes / none |
| baseline training pages | 228 |
| baseline lines | 7,382 |
| baseline words | 60,036 |

זהו מקור אמיתי עם images ותעתיקים חיצוניים, אך היעד מוגדר במפורש
Vaybertaytsh/Tsene-Rene. זהו semi-cursive Ashkenazi typeface קרוב מבחינה
היסטורית, לא evidence לכתב רש״י טהור. הוא אינו מוחלף בשם ״רש״י״ לצורך
ה־benchmark.

### Source Library Zohar

העמוד <https://sourcelibrary.org/blog/rashi-ocr> מתאר 548 דפי Zohar
Cremona 1558 בדפוס רש״י צפוף וטקסט ייחוס ב־Sefaria. אותו עמוד אומר במפורש
שהסריקות אינן נושאות canonical reference coordinates ושנדרש קורא עברית
כדי לאמת את ה־alignment. לכן אין כאן GT עמודי מוכן; שילוב אוטומטי של
הסריקות וטקסט Sefaria יהיה gold-assisted alignment.

## Roadmap A: בניית GT ידני מ־UXLC ולנינגרד

UXLC הוא חומר הגלם המועדף לבניית מקטע מקראי אמיתי. השלבים הבאים נדרשים
לפני שינוי registry או יצירת track:

1. **קיבוע המקורות.** להוריד את `Tanach.xml.zip` בגרסה וב־hash המפורטים
   לעיל, לשמור snapshot של `LCIndex.xml`, ולהוריד כל תמונת Sefaria שנבחרה.
   עבור כל תמונה יש לרשום URL, retrieval timestamp, dimensions, byte size
   ו־SHA-256. שינוי bytes יוצר source revision חדש.
2. **בחירת scope דיפלומטי.** להגדיר אם ה־gold כולל רק את עמודות המקרא או
   גם מסורה, micrography וכותרות. ברירת המחדל המומלצת ל־track המקראי היא
   main Biblical text בלבד, עם labels נפרדים לכל חומר אחר.
3. **טרנסקריפציה מן התמונה.** UXLC משמש reference להצעת טקסט, אך annotator
   חייב לאמת כל אות, ניקוד וטעם מול התמונה. אין להשלים טקסט חסר מן ה־UXLC
   ואין לתקן את העד לפי מהדורה מודרנית.
4. **פתרון גבולות עמוד.** לסמן ידנית את המילה הראשונה והאחרונה בכל עמוד,
   לרבות פסוקים חצויים. `a/b` או חצייה לפי מספר מילים אינם מספיקים.
5. **alignment שורה.** ליצור PAGE XML עם `TextRegion`, `TextLine`, polygon
   או baseline, reading order ותעתיק לוגי מלא לכל שורה. split word בין
   שורות חייב לקבל policy מפורש ועקבי.
6. **מדיניות qere/ketiv ומסורה.** לתעתק את מה שנמצא באזור התמונה המסומן.
   qere שבשוליים אינו מוכנס לרצף של שורת ה־ketiv בלי region וקשר מפורשים.
7. **בקרת איכות כפולה.** שני קוראים בודקים מדגם משמעותי; מחלוקות עוברות
   adjudication. הבדיקות כוללות code-point diff, ספירת סימני ניקוד וטעמים,
   זיהוי שורה חסרה/כפולה ובדיקה חזותית של קצוות כל עמוד.
8. **מניעת leakage.** split נעשה לפי רצפים פיזיים של folios או blocks של
   ספרים, לא לפי שורות אקראיות. עמודים סמוכים וגרסאות crop של אותו עמוד
   נשארים באותו split.
9. **אריזה קפואה.** ה־release כולל לפחות `images/`, `pagexml/`,
   `gold.jsonl`, source manifest, annotation policy, QA receipt,
   `SHA256SUMS` ו־schema versions. כל ID נקשר ל־folio ול־hash התמונה.
10. **certification לפני רישום.** יש להוכיח שאין image חסר, transcript
    extra, boundary משוער, XML לא תקין או Unicode שאבד. רק release שעובר
    את כל השערים יכול להפוך בעתיד ל־real Biblical track.

אפשר לפרסם את העבודה ב־batches קפואים. כל batch חייב להיות עצמאי ובר־אימות;
״התחלנו ליישר את כל 924 העמודים״ אינו completion proof לעמודים שטרם נבדקו.

## Roadmap B: קבלת Transkribus export מ־DiJeSt

המסלול המועדף ל־pure-Rashi הוא השגת ה־50 pages שעליהם DiJeSt כבר מצהיר,
ולא שחזור ה־GT מתוך תצוגת CoDiAJe:

1. **לקבל export מקורי.** לבקש מבעלי DiJeSt/CoDiAJe את תמונות המקור ואת
   PAGE XML שנבחר ב־Transkribus במצב `Ground Truth`, יחד עם collection ID,
   document ID, page ID, export timestamp ו־schema version. פלט model אינו
   תחליף ל־GT.
2. **לקשור image ל־annotation.** לכל PAGE XML חייב להיות image filename,
   dimensions ו־SHA-256 תואמים. אם אין אפשרות להפיץ תמונות, נדרש fetch
   ציבורי יציב עם expected hash; private viewer ללא bytes יציבים אינו
   מספיק.
3. **להוכיח revision.** לפרסם Git tag, DOI או archive version עם manifest
   ו־checksums. יש לשמור גם את סטטוס התעתיק וזהות פעולת ה־export, כדי שלא
   יוחלף בשקט בגרסת OCR חדשה יותר.
4. **לסמן script ברמת שורה/אזור.** annotator מסמן `rashi`, `square` ו־mixed.
   רק `rashi` חד־משמעי נכנס ל־pure subset. כותרות, פסוקים מרובעים ושורות
   מעורבות נשארים מחוץ ל־score או מדווחים בנפרד.
5. **לשמור תעתיק דיפלומטי.** שדה ה־gold הוא הכתב העברי כפי שנדפס, בסדר
   Unicode לוגי, עם קיצורים וסימני פיסוק. Romanization, lemma או normalized
   Ladino יכולים להישמר כשדות עזר בלבד.
6. **QA חזותי.** לבדוק כל crop מול polygon וכל תעתיק מול התמונה; למדוד IAA
   ולבצע adjudication. יש לדחות שורות חתוכות, מכוסות או כאלה שסיווג הכתב
   שלהן אינו מוסכם.
7. **split לפי מקור פיזי.** לשמור pages מאותה מהדורה ורצפים סמוכים באותו
   group, ולחפש כפילויות image/text בין Culi, Magriso וכל training data של
   model שמוערך.
8. **לפרסם participant/organizer evidence.** public-fixed source נשאר
   public-fixed; אפשר להסתיר evaluation gold בחבילת organizer, אך אסור
   לטעון שהמקור unseen. כל report נקשר ל־dataset hash.
9. **שער fail-closed.** אם לא מתקבלים images, PAGE XML, pure-Rashi labels
   ו־checksums, הפער נשאר `missing_coverage`. אין להשלים אותו באמצעות טקסט
   CoDiAJe לא מיושר או באמצעות model-generated segmentation.

## מסקנת audit

- UXLC הוא בסיס הבנייה המועדף למקרא, לא benchmark מוכן.
- DiJeSt הוא lead הבנייה המועדף ל־pure-Rashi, בתנאי שיפורסם export מקורי
  ושיתווסף script labeling ידני.
- BIMA יכול לתרום fragments מסומנים לאחר audit ידני, אך לא עמודים מלאים
  במצב ה־API שנבדק.
- BiblIA, HaZefira ו־Vaybertaytsh הם datasets אמיתיים, אך כל אחד מחמיץ את
  היעד המדעי המסוים ולכן אינו proxy מותר.

עד להשלמת אחד ה־roadmaps, הכיסוי האמיתי לשני היעדים נשאר חסר במפורש;
ה־diagnostics הסינתטיים נשארים non-rankable ואינם מוצגים כתחליף.
