# HebOCRBench — Public Full-Coverage Hebrew-Script OCR Benchmark

> **Current status:** 1.0 Release Candidate. The project is public-by-design, but the final `v1.0.0` tag is created only after the corpus, baselines, participant/organizer packs, release artifacts, and certification gates all pass from one frozen commit.

**HebOCRBench is a professional benchmark for OCR and HTR in Hebrew script: modern Hebrew, historical Hebrew, vocalized text, mixed-direction text, handwriting, complex page layouts, tables, forms, and additional Jewish languages written in Hebrew script.**

HebOCRBench does not ask only whether a system recovered approximately the right letters. It asks whether the output is usable:

- Hebrew is returned in **logical Unicode order**, not visual order;
- numbers, Latin runs, URLs, email addresses, formulas, and identifiers retain their internal LTR order;
- niqqud, cantillation, Hebrew punctuation, final letters, and combining marks are evaluated explicitly;
- page regions, lines, columns, tables, forms, and reading order are scored independently;
- model hallucinations, omitted pages, empty outputs, timeouts, and invalid BiDi controls remain visible in the score;
- historical Hebrew, modern Hebrew, Yiddish, Judeo-Arabic, Ladino, and other Hebrew-script languages are never collapsed into one misleading language score.

---

## Public-first and full-coverage policy

**Scientific inclusion is not limited by archive size, hosting cost, or distribution mode.** A large source is not a second-class source. A source is eligible when it contributes reliable ground truth, meaningful Hebrew-script coverage, and reproducible provenance.

The benchmark therefore separates two questions that are often confused:

1. **Is the source part of the scientific benchmark?**
2. **How are its bytes delivered to users?**

Licensing and archive size affect question 2, not question 1.

### Delivery classes

| Class | Meaning | Benchmark status |
|---|---|---|
| `bundled` | Bytes may be mirrored and are delivered through release assets or benchmark object storage | Full first-class source |
| `federated` | Public bytes are downloaded from the authoritative repository, API, IIIF endpoint, or archive and verified against locks | Full first-class source |
| `acceptance-required` | Public/research source requires explicit acknowledgement of upstream terms before acquisition | Full first-class source; terms remain attached |
| `access-controlled` | Metadata, conversion, split, and evaluation support are public, while users provide authorized access to source bytes | Full supported source when a reproducible build is possible |
| `metadata-only` | Source is catalogued but lacks sufficient public ground truth or stable access for an official scored track | Coverage candidate, not silently counted |

The Git repository is **not** the storage layer for multi-gigabyte corpora. Git contains code, schemas, manifests, source locks, acquisition recipes, checksums, attribution, split definitions, and audit evidence. Large immutable payloads are distributed through GitHub Releases, Git LFS/object storage where suitable, or fetched from their authoritative source. This is an engineering choice, not an exclusion policy.

See:

- [`docs/PUBLIC_DATA_POLICY_HE.md`](docs/PUBLIC_DATA_POLICY_HE.md)
- [`docs/DISTRIBUTION_ARCHITECTURE_HE.md`](docs/DISTRIBUTION_ARCHITECTURE_HE.md)
- [`docs/LICENSE_MATRIX_HE.md`](docs/LICENSE_MATRIX_HE.md)

---

## Official profile policy

HebOCRBench distinguishes the **scientific profile** from a **convenience transport subset**.

### `public-full-v1`

The canonical public scientific profile. It includes every registered v1 source that passes the relevant quality, provenance, conversion, split, and leakage gates, regardless of whether its bytes are bundled, federated, acceptance-required, or access-controlled.

Official leaderboard results should identify:

- benchmark version;
- profile fingerprint;
- track fingerprint;
- dataset fingerprint;
- exact source membership;
- evaluator version;
- model and runtime metadata.

### `redistributable-v1`

A convenience subset containing only source bytes that the benchmark may mirror directly. It is useful for zero-friction installation, but **it is not the canonical claim of Hebrew OCR coverage** and must not be presented as equivalent to `public-full-v1`.

### Language and task tracks

Results remain separated by language and task, including at minimum:

- modern Hebrew print;
- historical Hebrew print;
- modern Hebrew handwriting;
- historical Hebrew manuscripts;
- vocalized and cantillated Hebrew;
- mixed Hebrew–Latin BiDi;
- page OCR and reading order;
- tables and forms;
- isolated handwritten characters;
- Yiddish print;
- other Hebrew-script languages when sufficiently represented.

---

## Registered and planned source families

The registry is designed to grow. Large sources are welcome and should be added when they have stable provenance and evaluable ground truth.

| Source family | Primary contribution | Scale / status | Delivery principle |
|---|---|---:|---|
| Pinkas | Historical Hebrew handwriting, PAGE XML | 30 pages | Bundled/federated open source |
| BiblIA BnF | Medieval Hebrew manuscripts, ALTO | 132 complete image–GT pairs in the distributable BnF subset; broader metadata retained | Federated or release assets according to upstream terms |
| BiblIA BAV extension | Additional manuscript pages | 70 IIIF-linked pages | Federated IIIF acquisition; never discarded merely because bytes are not mirrored |
| Hebrew Wikisource validated pages | Validated Hebrew print with revision-bound transcription | Publicly reproducible collection | Federated MediaWiki/Commons acquisition |
| Jochre Yiddish | Historical Yiddish print in Hebrew script, ALTO to glyph level | Hundreds of pages | Federated/acceptance-required; scored separately from Hebrew |
| HHD v0.2 | Isolated modern handwritten Hebrew characters | Thousands of images | Separate character-recognition track |
| NetLay | Hebrew-script book layout classification | More than 1,300 pages | Separate layout track |
| Vaybertaytsh / YidTakNL | Historical Yiddish typography | Large supplementary corpus | Dedicated adapter and separate language track |
| Additional modern print, legal, governmental, newspaper, form, table, fax, camera, and handwriting sources | Coverage still needed for a definitive Hebrew benchmark | Expansion priority | Include by relevance and evidence, not by archive size |

The canonical source registry is [`corpora/registry.yaml`](corpora/registry.yaml). Locked identities and build inputs belong in the corresponding lock files. Source facts, attribution, and restrictions must remain machine-readable and human-readable.

---

## What “1.0” means

The final `v1.0.0` tag is not a decorative version number. It requires a complete evidence chain:

1. frozen evaluator, schemas, normalization rules, and official track contracts;
2. source registry and profile locks;
3. materialized real-world corpus with image and ground-truth hashes;
4. document-level split and leakage audit;
5. `FROZEN.json` and `CERTIFIED.json` generated from the same clean commit;
6. reproducible baseline bundles on the real test split;
7. participant and organizer packs with test leakage checks;
8. bootstrap confidence intervals and slice reports;
9. isolated wheel/source-archive installation tests;
10. release manifest, SBOM, checksums, and independent review evidence.

Until these gates pass together, the repository remains a Release Candidate even when individual components are already stable.

---

## Core evaluation principles

### Unicode and RTL

- Ground truth and predictions are compared in **logical order**.
- Images are never mirrored to “solve” RTL.
- The scorer never chooses the better of a prediction and its reversed string.
- Coordinates remain in normal image coordinates; reading order is represented explicitly.
- NFC is the strict canonical normalization. Lossy compatibility normalization is reported only as a secondary diagnostic.

### Text metrics

- code-point CER;
- grapheme-cluster CER;
- WER and exact-match rates;
- base-letter CER;
- diacritic and cantillation precision/recall/F1;
- Hebrew punctuation and final-letter confusions;
- exact accuracy for numbers, dates, currency, URLs, emails, identifiers, and LTR runs;
- hallucination, insertion, omission, empty-output, and failure rates.

### Layout and structure

- region and line detection;
- polygon/baseline geometry;
- split and merge errors;
- blind matching without gold IDs;
- reading-order edge and pairwise metrics;
- table topology, cell spans, position, and content;
- form labels, values, and label-to-value relations.

---

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,bidi]'
```

Run the test suite:

```bash
pytest -q
```

Inspect the CLI:

```bash
hebocrbench --help
hebocrbench --version
```

---

## Working with real corpora

List registered sources, terms, and profiles:

```bash
hebocrbench data list
hebocrbench data licenses
hebocrbench data profiles
```

Fetch a source through its official acquisition recipe:

```bash
hebocrbench data fetch \
  --source pinkas-v1 \
  --cache .hebocrbench-cache \
  --extract
```

Build a materialized corpus from verified roots:

```bash
hebocrbench data build \
  --source pinkas-v1 \
  --source-root pinkas-v1=.hebocrbench-cache/pinkas-v1/archive.extracted \
  --profile public-full-v1 \
  --benchmark-version 1.0.0 \
  --output builds/public-full-v1
```

Freeze and certify only after all declared profile sources are present and verified:

```bash
hebocrbench data freeze --build-root builds/public-full-v1
hebocrbench release certify --build-root builds/public-full-v1
```

A build must fail rather than silently shrink when a required source cannot be acquired, converted, licensed for the selected delivery action, or verified against its lock. A partial build receives a different profile fingerprint and cannot masquerade as `public-full-v1`.

---

## Data and release storage

The project uses the appropriate channel for each artifact:

- **Git:** code, specifications, small fixtures, schemas, registries, locks, manifests, attribution, and audit logic;
- **GitHub Releases:** versioned source archives, wheels, participant packs, redistributable corpus shards, manifests, and checksums;
- **object storage / Git LFS when appropriate:** very large immutable shards;
- **authoritative upstream services:** federated sources acquired through pinned URLs, APIs, repositories, or IIIF manifests;
- **private organizer storage:** hidden test ground truth and signing material, bound publicly to cryptographic commitments.

No large source is removed from the benchmark merely to keep the Git repository small.

---

## Licensing and public purpose

HebOCRBench is intended as public infrastructure for research, accessibility, preservation, and better Hebrew OCR. The project publishes the widest reproducible benchmark it can support.

Each source retains its own attribution and upstream conditions. The benchmark does not relicense third-party material under the software license and does not use one source’s terms to restrict unrelated sources. When direct mirroring is unavailable, the source remains supported through a public federated acquisition recipe and an exact lock.

The project’s software license applies to HebOCRBench code. Corpus licenses and source-specific notices travel with the corpus manifests and release assets.

---

## Repository hygiene

This is a standalone project. Unrelated repositories must never be used as CI scratch space, artifact transport, or corpus storage. Temporary bootstrap payloads and experimental transport files do not belong on the release branch.

Large generated outputs are intentionally excluded from Git history and must be published through the release architecture described above.

---

## Current release status

See [`STATUS.md`](STATUS.md) and [`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md).

The correct label remains **1.0 Release Candidate** until the complete public-full corpus, real baselines, release packs, and certification evidence are produced from a clean and reproducible build.