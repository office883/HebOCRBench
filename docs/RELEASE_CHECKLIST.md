# HebOCRBench 1.0 release checklist

- [ ] registry/profile/track locks נבנו מחדש ומאומתים;
- [ ] כל המקורות הרשמיים הם עברית מודרנית בלבד;
- [ ] חמשת roots הראשיים עברו build, audit, freeze ו־certify;
- [ ] suite lock נבנה מאותם roots ומאומת מול הפרופיל הקנוני;
- [ ] אין leakage ברמת document/template/image/text/ancestry;
- [ ] Participant Pack אינו מכיל test text, source IDs או organizer secret;
- [ ] Organizer Pack קשור לאותו dataset fingerprint;
- [ ] לפחות שני מנועי OCR עצמאיים הורצו כ־baselines;
- [ ] כל baseline כולל outputs, hashes, timings ו־bootstrap intervals;
- [ ] pytest עובר במלואו;
- [ ] Ruff lint ו־format check עוברים;
- [ ] compileall עובר;
- [ ] wheel/sdist נבנים ומותקנים בסביבה מבודדת;
- [ ] release verifier עובר עם suite lock;
- [ ] SBOM, manifests ו־SHA256SUMS נוצרו;
- [ ] source tree אינו מכיל secrets, fonts, caches או materializers היסטוריים;
- [ ] tag מצביע בדיוק ל־commit שעבר את כל השערים.
