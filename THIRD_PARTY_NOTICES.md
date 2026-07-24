# Third-party notices

## Python and external software

HebOCRBench depends on open-source Python packages listed in `pyproject.toml`; their own licenses apply. The optional Tesseract adapter invokes a separately installed Tesseract binary and language data, neither of which is distributed by this project.

The diagnostic renderer uses a font discovered on the host system. **No font binary is bundled or redistributed.** Manifests may record only a basename and cryptographic hash for reproducibility.

## Federated corpora

HebOCRBench code is MIT, but corpus data is not relicensed as MIT. Materialized builds preserve source-specific notices under `licenses/` and `attribution.jsonl`.

Registered sources in 1.0 include:

- Pinkas — CC BY 4.0;
- BiblIA — CC BY-NC-SA 4.0, including institution-/item-level considerations for externally resolved images;
- Jochre Yiddish OCR Corpus — CC BY-NC-SA 4.0;
- Vaybertaytsh.YidTakNL — CC BY 4.0;
- HHD v2 — CC BY 4.0 according to the targeted Zenodo v2 record; older mirrors may state different terms;
- NetLay — CC BY 4.0;
- HebOCRBench diagnostic text — CC0-1.0.

The canonical machine-readable details, URLs, checksums and acceptance flags are in `corpora/registry.yaml` and `corpora/registry.lock.json`. Users remain responsible for complying with upstream terms and any rights attached to individual images.
