# Project status

HebOCRBench 1.0 is a multi-profile Hebrew OCR benchmark with one guarded
composite: the Modern Hebrew printed-document headline. The evaluator, source
registry, track contracts, suite locks and release tooling are versioned.

## Modern headline corpus

The release candidate has five frozen and certified roots:

- `modern-bidi-v1`;
- `modern-line-recognition-v1`;
- `modern-page-ocr-v1`;
- `modern-tables-v1`;
- `modern-robustness-v1`.

Only those five roots may enter `modern-suite.lock.json` and the guarded Modern
headline. Certification proves corpus identity and gate results; it is not a
model-performance claim. Baseline metrics are published only from complete,
fingerprint-bound runs and are intentionally not copied into this status file.

## Separately reported families

- `modern-handwriting-v1` — real writer-disjoint Modern Hebrew handwriting;
- `historical-pinkas-handwriting-v1` — 266 real public-fixed lines from six
  pages in one Pinkas collection; page-disjoint from the cached training subset,
  without writer identities;
- `historical-hebrew-press-mixed-v1` — 34 real public-fixed HaZefira pages and
  4,016 line identities with PAGE/ALTO parity. It contains mixed square/Rashi
  print at corpus level but no pure-Rashi line or region labels. The frozen
  root passed 12/12 certification gates with dataset fingerprint
  `16aed8a8fc31ae1aaf957fe973b2b1dbdd0f911156f7539a78ceaafe56240143`;
- `biblical-niqqud-synthetic-diagnostic-v1` — 500 held-out synthetic niqqud
  lines, zero cantillation marks, non-rankable;
- `rashi-print-synthetic-diagnostic-v1` — 500 held-out synthetic single-font
  lines, non-rankable and not historical-scan evidence.

There is no cross-family score. Each real extension has its own report, and each
synthetic diagnostic remains outside every ranking and headline.

## Explicit gaps

- `modern-forms-v1` is `missing-real-gold`: the audited 700-page modern corpus
  contains zero `form_fields`; discovery signals are not field annotations and
  v1 has no forms score;
- no certified real Biblical/cantillation root is available;
- no certified pure-Rashi historical-print root with Rashi-specific labels is
  available. The mixed HaZefira extension does not close that gap.

## Still required before publication

- verify the Modern and full-suite locks against the final registry/profile
  fingerprints;
- complete reproducible baseline runs from at least two independent OCR
  families;
- verify participant and organizer packs;
- pass the final archive verifier and create the release tag.

The evaluation material is public-fixed. Withheld gold and opaque participant
IDs are protocol controls, not claims that the sources are hidden, unseen or
absent from model training.
