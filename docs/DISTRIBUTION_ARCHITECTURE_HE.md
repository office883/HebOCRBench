# ארכיטקטורת ההפצה של HebOCRBench

## מטרת הארכיטקטורה

HebOCRBench צריך להיות ציבורי, ניתן לשחזור ורחב־כיסוי גם כאשר מקורותיו גדולים, מפוצלים בין מארחים שונים או כפופים לתנאי הפצה שונים. לכן הפרויקט מפריד בין:

- source control;
- artifact distribution;
- federated acquisition;
- materialized corpus builds;
- hidden-test organizer storage.

המטרה אינה להקטין את הבנצ׳מרק כדי להתאים אותו ל־Git, אלא לבחור לכל סוג קובץ את ערוץ ההפצה הנכון.

## שכבה 1: Git repository

המאגר הציבורי מכיל:

- קוד evaluator ו־CLI;
- schemas;
- track definitions ו־locks;
- corpus registry ו־profile locks;
- materializers ו־converters;
- manifests ו־inventory schemas;
- attribution ורישיון לכל מקור;
- split declarations;
- audit and certification logic;
- small conformance fixtures;
- baseline runners;
- release automation.

המאגר אינו מכיל blobs גדולים שנוצרים או מורדים מחדש, משום שהם מנפחים היסטוריה לצמיתות ומקשים על clone, review ו־CI.

## שכבה 2: GitHub Releases

כל release רשמי יכול לכלול:

- wheel וחבילת מקור;
- participant pack;
- redistributable corpus shards;
- baseline bundles;
- source snapshot shards שמותר למרר;
- SBOM;
- `SHA256SUMS`;
- release manifest;
- public certification report;
- citations and attribution bundle.

כל asset מקבל:

- filename יציב;
- byte size;
- SHA-256;
- media type;
- role;
- source/profile/track fingerprint כאשר רלוונטי.

## שכבה 3: אחסון אובייקטים או Git LFS

כאשר Release Assets אינם מתאימים לגודל או למספר ה־shards, ניתן להשתמש באחסון אובייקטים ציבורי. הדרישות:

- כתובות HTTPS יציבות;
- versioned object keys;
- checksum מחוץ לשירות האחסון;
- תמיכה ב־range/resume;
- immutable release paths;
- mirror policy;
- inventory ציבורי.

Git LFS מתאים רק כאשר הוא אינו הופך clone רגיל לתלות יקרה או שבירה. ברירת המחדל לקורפוסים גדולים היא assets/object storage או acquisition פדרטיבי.

## שכבה 4: acquisition פדרטיבי

מקור נשאר אצל המארח הסמכותי ונמשך באמצעות adapter נעול.

כל מתכון acquisition כולל, לפי היכולת:

```yaml
source_id: example-v1
record_or_revision: exact-upstream-identity
urls:
  - authoritative-url
expected_size_bytes: 123
checksums:
  sha256: ...
delivery_mode: federated
license_id: ...
required_files: ...
```

עבור APIs ו־IIIF נשמרים גם:

- query parameters;
- revision IDs;
- timestamps;
- manifest hashes;
- page/canvas identifiers;
- rendition dimensions;
- text revision hashes.

Materializer חייב להיות idempotent ולהפיק אותו tree fingerprint מאותם inputs.

## שכבה 5: cache מקומי

ה־CLI משתמש ב־content-addressed cache:

- קובץ שה־hash שלו כבר קיים אינו מורד מחדש;
- הורדה נכתבת לקובץ זמני ורק לאחר אימות הופכת ל־verified;
- cache corruption גורם להורדה חוזרת;
- שני מקורות בעלי bytes זהים רשאים לחלוק blob, אך provenance נשמר בנפרד;
- ניקוי cache אינו משנה את fingerprint של corpus שנבנה מחדש.

## שכבה 6: materialized build

Build רשמי אינו אוסף מקרי של תיקיות. הוא מכיל לפחות:

```text
gold.jsonl
images/
stats.json
audit.json
attribution.jsonl
citations.bib
licenses/
source_reports/
dataset.lock.json
manifest.json
FILE_INVENTORY.json
```

ה־manifest מקשר כל page ל:

- source ID;
- source document ID;
- source revision;
- image hash;
- GT hash;
- split;
- language/script/style labels;
- license and attribution record;
- converter version;
- repair/audit record.

## Sharding

מקורות גדולים מחולקים באופן דטרמיניסטי:

- הגבול הוא document/source unit ולא crop אקראי;
- כל page מופיע ב־shard אחד בלבד;
- image ו־GT נשארים באותו shard או מקושרים במפורש;
- shard membership ננעל ב־manifest;
- סדר הקבצים בארכיון דטרמיניסטי;
- timestamps, owner/group ו־compression parameters מנורמלים ככל האפשר;
- hash לכל shard ו־hash ל־manifest הכולל.

## פרופיל מלא מול הורדה חלקית

ה־CLI יכול לאפשר הורדת track או split בלבד כדי לחסוך זמן, אך:

- `public-full-v1` נשאר מוגדר באמצעות membership מלא;
- הורדה חלקית מקבלת build state של partial materialization;
- אין להפיק `CERTIFIED.json` של full profile מחומר חלקי;
- תוצאה רשמית נמדדת רק על ה־test membership שהוגדר בפרופיל.

## Participant pack

החבילה הציבורית למשתתף כוללת:

- train/dev images and gold כאשר ניתן לפרסם;
- test images;
- opaque test IDs;
- submission schema;
- track configs and locks;
- source/profile fingerprints;
- attribution;
- sample submission;
- participant inventory and checksums.

היא אינה כוללת:

- test transcription;
- private source mapping המאפשר לחפש תשובות;
- organizer key;
- scorer secrets.

## Organizer pack

החבילה הפרטית כוללת:

- test gold;
- opaque-to-private ID map;
- signing/HMAC material;
- public participant-pack fingerprint;
- evaluation service configuration;
- private inventory.

ה־organizer pack אינו מפורסם ב־Git או ב־public Releases.

## Baseline bundles

כל baseline רשמי מופץ כ־bundle נפרד הכולל:

- predictions;
- report;
- model/runtime metadata;
- exact command;
- dependency versions;
- dataset/profile/track fingerprints;
- page-level failures and timings;
- bundle checksum.

כשל, timeout או OOM נשמרים ולא מסוננים.

## Release manifest

Release נחשב שלם רק כאשר manifest ציבורי קושר יחד:

- commit SHA;
- tag;
- evaluator version;
- track locks;
- source and profile locks;
- dataset fingerprint;
- participant-pack fingerprint;
- baseline bundle hashes;
- release asset hashes;
- certification report;
- external review reference.

## זמינות ומראות

כדי למנוע תלות במארח יחיד:

- registry יכול להכיל mirrors בסדר עדיפות;
- checksum הוא מקור האמת, לא שם המארח;
- mirror אינו רשאי לשנות bytes תחת אותה זהות;
- כשל של mirror אחד מוביל ל־mirror הבא;
- שינוי upstream מחייב revision חדש או בירור, לא עדכון שקט של lock.

## מסקנה

העיקרון פשוט:

> Git שומר את השיטה והראיות; ערוצי artifacts והמקורות הסמכותיים שומרים את ה־bytes.

כך ניתן לכלול מקורות גדולים באמת בלי לזהם את היסטוריית המאגר, בלי להקטין את הבנצ׳מרק ובלי לאבד יכולת שחזור.