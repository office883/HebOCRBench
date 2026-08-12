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

## Release work completed

- The final Modern and full-suite locks were independently verified against the
  registry and profile locks. Their suite fingerprints are
  `c68250ec4320485e243171b7d3f86c9b3b526f8ada317eda592cd7289f4df5ea`
  and
  `6d2b847121d307b225ec7e785ded7060f40da20b1d8dee28982ef7da06e032d4`.
- The participant and organizer packs were verified. The public participant
  pack contains 34,267 Modern evaluation images, has fingerprint
  `957e3b4f05707155db407d6707d742c7fe459365f575febb963e9d5732c3913c`,
  and is published on
  [Hugging Face at tag `v1.0.0`](https://huggingface.co/datasets/ssdataanalysis/hebocrbench-v1/tree/v1.0.0/participant-v1.0.0).
  The organizer pack remains non-public and has fingerprint
  `a9c9e376469c0fd813febbb9b87c3b525e6b23a2bbdb78957f778257f0bcc984`.
- The complete Tesseract 5.5.3 baseline finished all five Modern tracks and all
  separately reported extension/diagnostic roots with zero inference or API
  failures. Its Modern result is `non_conformant`, with no headline score,
  because mandatory BiDi gates failed.
- The complete Surya OCR 2 baseline finished all 34,267 Modern inputs and all
  2,264 separately reported extension/diagnostic inputs with zero inference or
  API failures. Artifact hashes, component roots, full coverage, blind inputs
  and recomputed metrics were independently verified. Its Modern result is also
  `non_conformant`, with no headline score, because mandatory BiDi gates failed.
- Exact aggregate metrics and score files for both complete Modern and separate
  extension/diagnostic runs are
  public on [Hugging Face at tag
  `v1.0.0`](https://huggingface.co/datasets/ssdataanalysis/hebocrbench-v1-results/tree/v1.0.0).
  The compact results pack fingerprint is
  `1e67e1f92c899029f13e8a69387aa0561ada503fea32889eb0c105d57d2a6301`;
  raw predictions, gold, images, organizer maps and key material are omitted.
- A deterministic local v1 release candidate was built and passed the archive
  verifier. The final archive will be rebuilt from the merged release commit.

## Publication gates still pending

- publish the minimal five-file public source mirror, pin its exact 40-character
  revision in the release workflow, and obtain a green
  `modern-v1-release.yml` run;
- create the GitHub `v1.0.0` tag and release after the two independent
  baselines and release workflow are complete. The Hugging Face participant-pack
  tag `v1.0.0` is already public.

The evaluation material is public-fixed. Withheld gold and opaque participant
IDs are protocol controls, not claims that the sources are hidden, unseen or
absent from model training.
