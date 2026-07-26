# HebOCRBench 1.0 — Modern Hebrew OCR Benchmark

HebOCRBench is a reproducible benchmark for **OCR of contemporary Modern Hebrew documents**. It evaluates not merely whether a model found approximately the right letters, but whether it produced a document that can actually be searched, cited and processed.

The official scope is intentionally narrow:

- Modern Hebrew print and contemporary public documents;
- Modern Hebrew mixed with numbers, English, URLs, email addresses, identifiers and formulas;
- blind page segmentation, layout and reading order;
- tables and closed-schema forms;
- controlled scan/camera degradations;
- a separately reported Modern Hebrew handwriting extension.

The official v1 profiles **exclude** Yiddish, Biblical Hebrew, cantillated text, historical print and historical manuscripts.

## Non-negotiable RTL rule

Hebrew ground truth and predictions are stored in **logical Unicode order**. Images are never mirrored, strings are never reversed, and the scorer never chooses the better of a prediction and its reversed form.

For example, the visible RTL sentence:

```text
בשנת 2026 הופעלה גרסה OCR-v2.1.
```

is stored in normal logical order. `2026` and `OCR-v2.1` retain their internal LTR order. Coordinates remain ordinary image coordinates; reading order is represented explicitly.

## Official tracks

| Track | Purpose | Headline |
|---|---|---:|
| `modern-bidi-v1` | strict logical-order Unicode/BiDi conformance | yes |
| `modern-line-recognition-v1` | recognition on benchmark-provided line polygons | yes |
| `modern-page-ocr-v1` | blind end-to-end page OCR, layout and reading order | yes |
| `modern-tables-v1` | blind table presence, topology, cells and logical cell text | yes |
| `modern-robustness-v1` | degradation pairs derived from frozen modern pages | yes |
| `modern-forms-v1` | diagnostic closed-schema form extraction | no |
| `modern-handwriting-v1` | writer-disjoint real human handwriting extension | no |

The printed-document headline is a weighted geometric score over the five headline tracks. A model that fails BiDi conformance or completely collapses on one structural track cannot hide behind a good CER on easy lines.

## Headline weights

| Component | Weight |
|---|---:|
| blind page OCR | 34% |
| line recognition | 20% |
| table recognition | 17% |
| robustness | 17% |
| Unicode/BiDi | 12% |

Forms and handwriting are published separately.

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

### Tables and forms

- table presence F1;
- topology and cell-span F1;
- grid-slot accuracy;
- cell-position overlap;
- logical cell-text GCER;
- form field presence and exact value transcription.

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

Adds the modern-print line development source for model development and baseline support. It does not change the representative hidden page-OCR test claim.

### `modern-hebrew-handwriting-v1`

Separate writer-disjoint handwriting extension. It is never blended with print.

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
  --output modern-score.json
```

The command refuses to combine reports when their suite, profile, registry, model identity, track contract or gold hashes differ.

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
- table/form structure.

Only the image bytes change. The track records degradation family, severity and parameters for blur, compression, skew, perspective, low contrast, illumination, speckle and related realistic defects.

## Participant and organizer packs

The participant pack contains public train/dev gold and opaque test inputs. It never contains:

- hidden test text;
- original test source identifiers;
- private source filenames;
- the organizer HMAC key.

The organizer pack contains hidden gold and the private ID map. Both packs are bound to the same dataset and suite fingerprints.

## Release gate

`scripts/build_v1_release.py` refuses to create a release without a valid certified Modern Hebrew suite lock. A release must also pass:

- source and package data synchronization;
- registry/profile/track lock verification;
- full tests;
- Ruff and compile checks;
- wheel and sdist construction;
- isolated wheel installation;
- SBOM and SHA-256 manifest generation;
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
