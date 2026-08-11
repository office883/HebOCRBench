# HebOCRBench 1.0 release checklist

- [ ] registry/profile/track locks נבנו מחדש ומאומתים;
- [ ] חמשת מקורות ה־Modern headline הם עברית מודרנית; כל הרחבה/diagnostic
      משויכים ל־profile ולמשפחת דיווח נפרדים;
- [ ] חמשת roots הראשיים עברו build, audit, freeze ו־certify;
- [ ] `modern-suite.lock.json` נבנה מאותם roots ומאומת מול הפרופיל הקנוני;
- [ ] `full-suite.lock.json` מאומת, נועל `cross_family_score=forbidden`, וכל root
      שמסומן בו certified עבר re-hash מול ה־lock;
- [ ] אין leakage ברמת document/template/image/text/ancestry;
- [ ] Participant Pack אינו מכיל evaluation gold, source IDs או organizer secret;
- [ ] Organizer Pack קשור לאותו dataset fingerprint;
- [ ] forms מסומן `missing-real-gold` ואינו מקבל root או ציון;
- [ ] diagnostics סינתטיים מסומנים non-rankable ואינם מוצגים כראיה אמיתית;
- [ ] HaZefira אינו מוצג כ־pure-Rashi ללא תיוג מתאים;
- [ ] לפחות שני מנועי OCR עצמאיים הורצו כ־baselines;
- [ ] כל baseline כולל outputs, hashes, timings ו־bootstrap intervals;
- [ ] pytest עובר במלואו;
- [ ] Ruff lint ו־format check עוברים;
- [ ] compileall עובר;
- [ ] wheel/sdist נבנים ומותקנים בסביבה מבודדת;
- [ ] release verifier עובר עם release-dir, manifest וכל component roots;
- [ ] component proof תואם ל־Modern ול־full-suite fingerprints;
- [ ] SBOM, manifests ו־SHA256SUMS שלמים ומאומתים, לא רק קיימים;
- [ ] שני source archives מכילים בדיוק את עץ המקור המותר ובאותם bytes;
- [ ] source tree אינו מכיל secrets, fonts, caches או materializers היסטוריים;
- [ ] tag מצביע בדיוק ל־commit שעבר את כל השערים.
