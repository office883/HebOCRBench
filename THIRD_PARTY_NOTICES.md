# Third-party notices

HebOCRBench software is licensed under MIT. Corpus content is **not** relicensed by the software license.

The official Modern Hebrew profiles may refer to:

- contemporary Israeli public documents, acquired through revision-locked manifests and authoritative public APIs;
- `ssdataanalysis/hebrew-ocr-corpus`, modern-print configurations only, for development and baseline support;
- `ssdataanalysis/hebrew-htr-curated-v1`, real-human stage-3 holdout only, for the separately reported handwriting extension.
- the Pinkas dataset at Zenodo record 3569694, CC-BY-4.0, through an exact
  266-line, six-page filter from a revision- and checksum-locked mixed
  WebDataset TAR, for the separately reported historical handwriting extension.
- the OmiLab/Open University of Israel HaZefira historical-press ground truth,
  through the revision- and checksum-locked 34-page PAGE/ALTO archive, for a
  separately reported mixed square/Rashi historical-print extension;
- the locked synthetic foundation shards and Noto Rashi font provenance used
  only for the two separately reported, non-rankable diagnostics.

Every frozen corpus build must retain per-source attribution, acquisition URL
or repository revision, file hashes, rights evidence and any source-specific
conditions. None of these sources is blended into the Modern Hebrew headline.
HaZefira is a narrow mixed square/Rashi extension, not a pure-Rashi benchmark;
the synthetic niqqud data contains no cantillation and neither synthetic
diagnostic is evidence from real historical material.
