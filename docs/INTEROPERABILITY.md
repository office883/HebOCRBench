# Interoperability

HebOCRBench uses JSONL as the canonical evaluator interface and supports conversion from PAGE XML, ALTO and revision-locked modern PDF manifests.

Required invariants:

- UTF-8, NFC and logical Unicode order;
- ordinary image coordinates;
- explicit region/line IDs and reading order;
- polygons or baselines in source coordinates;
- language `he` for Hebrew content and `en` for local LTR runs where annotated;
- modern-era metadata and source provenance.

Converters may preserve extra metadata, but they must not invent unreadable text, reverse Hebrew strings or infer hidden table/form semantics without evidence.
