# Project status

HebOCRBench is a standalone benchmark for **Modern Hebrew OCR**.

The evaluator, source registry, official track contracts and release tooling are versioned as 1.0.0. An official corpus release is publishable only when the five headline tracks are frozen, independently certified and bound by one `modern-suite.lock.json`.

## Certified now

- `modern-bidi-v1` — **frozen and certified, 12/12 gates passed**
  - dataset fingerprint: `4c76d51f0e8ea9ad4d7a760fe83907e5984c634490d772e82fa14c5ba46a3ba1`
  - evidence: [`evidence/certified/modern-bidi-v1`](evidence/certified/modern-bidi-v1)

## Still required for the official dataset release

- `modern-line-recognition-v1`
- `modern-page-ocr-v1`
- `modern-tables-v1`
- `modern-robustness-v1`
- one verified `modern-suite.lock.json`
- reproducible baselines from at least two independent OCR families
- participant and organizer packs
- final release verification and tag

The public-document acquisition pipeline is being calibrated as a checkpointed, resumable process. A timeout or a coverage shortfall remains a failed release gate; it is never converted into a smaller benchmark silently.

Yiddish, Biblical Hebrew, historical print and historical manuscripts are outside the official scope. Modern handwriting is a separate extension and is never blended into the printed-document score.
