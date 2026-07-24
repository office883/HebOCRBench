# Changelog

## 1.0.0 — 2026-07-23

First stable benchmark-protocol and federated real-corpus release:

- locked real-source registry for Pinkas, BiblIA, Jochre Yiddish and supplementary Hebrew-script resources;
- license tiers, explicit acceptance gates, attribution and per-source notices;
- safe atomic HTTP/file acquisition, checksum verification, locked Git checkout, Git LFS detection and guarded archive extraction;
- PAGE XML and ALTO 4 converters preserving Unicode logical order, geometry and explicit reading order;
- deterministic document-/writer-/scribe-aware split assignment;
- image, document, writer, scribe, source-page, text and template leakage audits;
- corpus coverage statistics, deterministic manifests, dataset fingerprints and source reports;
- `data list|licenses|fetch|verify|convert|build|stats|audit|freeze` CLI lifecycle;
- independent `release certify` gates and atomic `CERTIFIED.json` marker;
- packaged default registry, registry lock, benchmark configuration and runtime schemas;
- v1 documentation for licensing, corpus construction, reproducibility and release governance;
- all evaluator, report, diagnostic and fault-injection behavior from 0.1 retained.

The software release does not silently redistribute every upstream corpus. Materialized data releases are built from official locked sources under their own licenses and receive their own dataset fingerprint.

## 0.1.0 — 2026-07-22

Initial evaluator and diagnostic implementation:

- Unicode logical-order Hebrew OCR evaluation with NFC strict profile;
- CER, GCER, WER, exact-match, confusions and Hebrew punctuation/final-letter diagnostics;
- niqqud and cantillation metrics by mark class;
- BiDi conformance gate, LTR/numeric/bracket checks and non-rescuing visual-order diagnosis;
- region/line geometry, explicit reading order, logical RTL tables and forms;
- schema validation, Unicode hygiene and split-leakage auditing;
- deterministic synthetic diagnostic generator and fault-injection sanity matrix;
- Tesseract oracle-layout recognition adapter;
- per-run RTL reports and certified cross-run comparison artifacts;
- document bootstrap, slices, worst cases and operational metrics.
