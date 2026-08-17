# Changelog

## Unreleased

- versioned `modern-bidi-v1` to track contract `1.1.0`;
- separated recognition-quality targets from hard Unicode/BiDi conformance;
- kept strict line, LTR-run, numeric and bracket exactness inside the scored
  BiDi component instead of using them to disqualify otherwise valid OCR runs;
- made the visual-order detector require both a material gain over logical order
  and a close match to the visual or reversed reference, reducing noise-driven
  false positives without allowing confirmed visual-order storage;
- allowed directional marks and balanced isolates while continuing to reject
  directional embeddings, overrides and unbalanced controls;
- exposed BiDi quality warnings in the guarded Modern score and bumped the
  canonical track-lock version to `1.1.0`; frozen dataset and suite identities
  remain unchanged, while newly generated reports carry the corrected track
  fingerprint.

## 1.0.0 — Hebrew OCR benchmark suite

- established five frozen, certified Modern Hebrew print roots and a guarded
  five-track geometric headline score;
- added cryptographic Modern and full-suite locks binding reports to frozen
  gold, certification evidence and registry/profile identities;
- made `cross_family_score=forbidden`: handwriting, historical material and
  synthetic diagnostics can never be blended into the Modern headline;
- added strict logical-order RTL/BiDi conformance gates and blind region,
  reading-order and table matching without gold IDs;
- added a revision-locked modern-PDF converter with independent text-layer
  cross-checking, template-family grouping and degradation ancestry checks;
- added separately reported real-data extensions for writer-disjoint Modern
  Hebrew handwriting and the narrow six-page Pinkas handwriting subset;
- added the 34-page, 4,016-line HaZefira historical-press extension with
  fail-closed PAGE/ALTO parity checks; it is mixed square/Rashi material and is
  explicitly not a pure-Rashi benchmark;
- added 500-line held-out synthetic niqqud and Noto Rashi diagnostics, marked
  non-rankable and excluded from every headline;
- recorded that the synthetic niqqud diagnostic contains no cantillation and
  that the single-font Rashi diagnostic is not historical-scan evidence;
- marked `modern-forms-v1` as `missing-real-gold` and unscored after the audited
  modern corpus yielded zero `form_fields`;
- added resumable baseline runners, participant/organizer pack tooling and
  fail-closed release verification;
- removed hidden/unseen claims for the public-fixed evaluation sources.
