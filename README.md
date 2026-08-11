# HebOCRBench 1.0 — Hebrew OCR Benchmark

HebOCRBench is a reproducible benchmark for Hebrew OCR. Its only composite
score is the five-track **Modern Hebrew printed-document headline**. Other
scientifically different families are carried in the same full-suite manifest
but are always reported separately.

The v1 coverage is explicit:

- five certified Modern Hebrew print roots covering Unicode/BiDi, line
  recognition, blind page OCR, tables and controlled degradations;
- separate real-data extensions for writer-disjoint Modern Hebrew handwriting,
  a narrow six-page Pinkas handwriting subset, and a 34-page HaZefira
  historical-press subset;
- held-out synthetic diagnostics for niqqud and a single Noto Rashi typeface;
- explicit missing-coverage entries for real Biblical text with cantillation,
  pure-Rashi historical print and forms with field-level ground truth.

The Modern Hebrew headline does not include Yiddish, Biblical Hebrew,
cantillated text, historical print or handwriting. No cross-family aggregate is
defined. The synthetic diagnostics are non-rankable and cannot be cited as real
Biblical, cantillation or historical-print results.

The fail-closed audit of real Biblical and pure-Rashi source candidates is
documented in [`docs/REAL_BIBLICAL_RASHI_SOURCE_AUDIT_HE.md`](docs/REAL_BIBLICAL_RASHI_SOURCE_AUDIT_HE.md).

## Non-negotiable RTL rule

Hebrew ground truth and predictions are stored in **logical Unicode order**. Images are never mirrored, strings are never reversed, and the scorer never chooses the better of a prediction and its reversed form.

For example, the visible RTL sentence:

```text
בשנת 2026 הופעלה גרסה OCR-v2.1.
```

is stored in normal logical order. `2026` and `OCR-v2.1` retain their internal LTR order. Coordinates remain ordinary image coordinates; reading order is represented explicitly.

## Official tracks and coverage targets

| Track | Evidence | Reporting |
|---|---|---|
| `modern-bidi-v1` | certified Unicode/BiDi conformance root | Modern headline |
| `modern-line-recognition-v1` | certified real public-document line root | Modern headline |
| `modern-page-ocr-v1` | certified real public-document page root | Modern headline |
| `modern-tables-v1` | certified real public-document table root | Modern headline |
| `modern-robustness-v1` | certified degradation-pair root | Modern headline |
| `modern-forms-v1` | **missing real field-level gold** | experimental, unscored |
| `modern-handwriting-v1` | real human handwriting | separate extension |
| `historical-pinkas-handwriting-v1` | 266 real public-fixed lines from six pages | separate narrow extension |
| `historical-hebrew-press-mixed-v1` | certified 34-page real public-fixed HaZefira root, mixed square/Rashi print | separate narrow extension |
| `biblical-niqqud-synthetic-diagnostic-v1` | 500 held-out synthetic niqqud lines; no cantillation | non-rankable diagnostic |
| `rashi-print-synthetic-diagnostic-v1` | 500 held-out synthetic Noto Rashi lines | non-rankable diagnostic |

The printed-document headline is a weighted geometric score over the five headline tracks. A model that fails BiDi conformance or completely collapses on one structural track cannot hide behind a good CER on easy lines.

## Headline weights

| Component | Weight |
|---|---:|
| blind page OCR | 34% |
| line recognition | 20% |
| table recognition | 17% |
| robustness | 17% |
| Unicode/BiDi | 12% |

Handwriting and historical press are published separately. `modern-forms-v1`
has a scoring contract but no eligible root: the audited modern corpus contains
zero `form_fields`, so v1 publishes no forms score.

## Core metrics

### Text

- code-point CER;
- grapheme-cluster CER;
- WER;
- exact line, word and page rates;
- base-letter CER;
- Modern Hebrew niqqud precision/recall/F1;
- final-letter and Hebrew-punctuation confusion matrices;
- exact accuracy for numbers, dates, currency, URLs, email, identifiers and LTR runs;
- hallucination, insertion, omission, empty-output and failure rates.

### Layout and order

- region and line precision/recall/F1;
- polygon IoU and baseline geometry;
- split/merge rates;
- reading-order edge F1;
- pairwise precedence accuracy;
- order-sensitive and order-tolerant text coverage.

### Tables

- table presence F1;
- topology and cell-span F1;
- grid-slot accuracy;
- cell-position overlap;
- logical cell-text GCER.

Form-field presence and exact-value metrics remain specified for a future root
with real field annotations. They are not evaluated or ranked in v1.

## Certified suite identity

A track YAML defines **how** a task is scored. A `modern-suite.lock.json` defines **which frozen bytes** were scored.

Every official report is bound to:

- benchmark version;
- registry fingerprint;
- profile fingerprint;
- suite fingerprint;
- track fingerprint;
- dataset fingerprint;
- SHA-256 of `gold.jsonl`;
- SHA-256 of the certification evidence.

Reports from different corpus revisions cannot be combined into one headline score.

## Official data profiles

### `modern-hebrew-print-v1`

Canonical public scientific profile:

- `modern-bidi-diagnostic-v1`;
- `modern-public-documents-v1`.

### `modern-hebrew-development-v1`

Adds modern-print line data for development and baseline support. It does not
change the public-fixed Modern Hebrew evaluation roots.

### `modern-hebrew-handwriting-v1`

Separate writer-disjoint handwriting extension. It is never blended with print.

### `historical-pinkas-handwriting-v1`

Separate narrow Pinkas historical-handwriting extension. Its 266 real lines are
public-fixed and page-disjoint from the cached training pages, but they come
from one collection and have no writer identities. Its score is never blended
with Modern Hebrew print or modern handwriting.

### `historical-hebrew-press-mixed-v1`

Separate real 34-page HaZefira historical-newspaper extension with 4,016 line
identities verified across paired PAGE and ALTO annotations. Its frozen root
passed all 12 certification gates. The source is public-fixed and includes both
square and Rashi print at corpus level, but has no region- or line-level Rashi
labels. It must not be reported as a pure-Rashi benchmark.

### Synthetic diagnostics

`biblical-niqqud-synthetic-diagnostic-v1` and
`rashi-print-synthetic-diagnostic-v1` are public-fixed, held-out probes. Each is
reported independently and is excluded from rankings and every headline. The
niqqud probe contains no cantillation marks; the Rashi probe uses one synthetic
font family and is not evidence about historical scans.

Inspect the machine-readable contracts:

```bash
hebocrbench data list
hebocrbench data profiles
hebocrbench tracks list
hebocrbench tracks verify
```

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,bidi]'
```

Run verification:

```bash
pytest -q
python -m compileall -q src scripts
ruff check .
ruff format --check .
hebocrbench tracks verify
```

## Evaluation

```bash
hebocrbench evaluate \
  --gold /data/track/gold.jsonl \
  --predictions predictions.jsonl \
  --track modern-page-ocr-v1 \
  --suite-lock modern-suite.lock.json \
  --dataset-root /data/track \
  --output report
```

The output directory contains the canonical JSON report, Markdown summary and run manifest. Missing pages and invalid outputs remain visible; the parser does not silently clean model prose or Markdown wrappers.

## Composite Modern Hebrew score

```bash
hebocrbench modern-score \
  --reports reports/by-track \
  --suite-lock modern-suite.lock.json \
  --track-root modern-bidi-v1=/data/modern-bidi-v1 \
  --track-root modern-line-recognition-v1=/data/modern-line-recognition-v1 \
  --track-root modern-page-ocr-v1=/data/modern-page-ocr-v1 \
  --track-root modern-tables-v1=/data/modern-tables-v1 \
  --track-root modern-robustness-v1=/data/modern-robustness-v1 \
  --output modern-score.json
```

The command independently verifies every certified root, reconstructs the suite,
re-evaluates the submitted predictions against the locked gold and refuses oracle-layout,
gold-assisted, incomplete or mismatched reports. It does not trust editable metric files.

## Full-suite manifest

`full-suite.lock.json` records the Modern headline, real extensions, synthetic
diagnostics and missing coverage in one tamper-evident manifest. It does not
define a score across those families:

```bash
hebocrbench full-suite build \
  --component-root modern-bidi-v1=/data/bidi \
  --component-root modern-handwriting-v1=/data/handwriting \
  --output full-suite.lock.json

hebocrbench full-suite verify \
  --lock full-suite.lock.json \
  --component-root modern-bidi-v1=/data/bidi \
  --component-root modern-handwriting-v1=/data/handwriting
```

## Building a frozen suite

Each headline track root must contain:

```text
gold.jsonl
images/
manifest.json
dataset.lock.json
FROZEN.json
certification.json
CERTIFIED.json
```

Build the suite lock:

```bash
hebocrbench modern-suite build \
  --profile modern-hebrew-print-v1 \
  --track-root modern-bidi-v1=/data/bidi \
  --track-root modern-line-recognition-v1=/data/lines \
  --track-root modern-page-ocr-v1=/data/pages \
  --track-root modern-tables-v1=/data/tables \
  --track-root modern-robustness-v1=/data/robustness \
  --output modern-suite.lock.json
```

Verify it independently:

```bash
hebocrbench modern-suite verify --lock modern-suite.lock.json
```

## Contemporary public-document corpus

The modern-PDF pipeline accepts only revision-locked manifests. It:

1. downloads the declared PDF;
2. verifies the expected SHA-256 and page count;
3. extracts text independently with PyMuPDF and Poppler;
4. checks NFC, Hebrew dominance, logical order and suspicious control characters;
5. rejects pages with unstable or incomplete text layers;
6. renders benchmark images at the locked DPI;
7. emits line/region geometry, reading order and table structure;
8. groups documents by template family before split assignment.

The official corpus target is at least:

- 100 independent documents;
- 500 scored pages;
- 50 template families;
- 25 table pages;
- 100 pages containing meaningful Hebrew–LTR interaction.

No source is included merely because its embedded text exists. Digital extraction must be cross-checked, and scanned pages requiring manual transcription remain outside the certified set until reviewed.

## Robustness track

Degradation pages are children of frozen source pages. They inherit:

- source page identity;
- document split;
- template family;
- transcription;
- reading order;
- table structure.

Only the image bytes change. The track records degradation family, severity and parameters for blur, compression, skew, perspective, low contrast, illumination, speckle and related realistic defects.

## Participant and organizer packs

The participant pack contains opaque evaluation inputs and no gold. It never
contains:

- withheld evaluation gold;
- original test source identifiers;
- private source filenames;
- the organizer HMAC key.

The organizer pack contains the withheld gold and private ID map. Both packs
are bound to the same dataset and suite fingerprints. The underlying sources
are public-fixed; opaque IDs and withheld pack contents are an evaluation
protocol, not a claim that the source material is unseen or uncontaminated.

## Release gate

`scripts/build_v1_release.py` refuses to create a release unless the Modern
suite lock, the full-suite lock, and every root marked certified by the
full-suite lock all re-hash to the same evidence. The release contains both
locks plus a path-free component proof. `scripts/verify_v1_release.py` requires
the release directory, its manifest, and the complete certified root mapping;
it verifies the proof and all artifact bytes. A release must also pass:

- source and package data synchronization;
- registry/profile/track lock verification;
- full tests;
- Ruff and compile checks;
- wheel and sdist construction;
- isolated wheel installation;
- complete SBOM, release manifest and SHA-256 membership verification;
- byte-for-byte source-archive completeness against the release source tree;
- source-tree hygiene and secret scanning.

## Repository layout

```text
src/hebocrbench/       evaluator and CLI
tracks/                official task contracts and lock
corpora/               source registry, profiles and locks
schemas/               normative JSON schemas
data/                  diagnostic cases and secondary registry
scripts/               corpus/release tooling
tests/                 unit and integration tests
docs/                  normative benchmark documentation
```

Large corpora do not belong in Git history. They are distributed as checksummed release/object-storage assets or fetched from authoritative sources through locked manifests.

## Citation

Cite the software and report the exact suite, profile, registry, track and dataset fingerprints. See [`CITATION.cff`](CITATION.cff).

## License

HebOCRBench code is MIT. Each corpus source retains its own terms and attribution; the software license does not relicense source documents.
