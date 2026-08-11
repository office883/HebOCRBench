# Interoperability

HebOCRBench uses JSONL as the canonical evaluator interface and supports
conversion from PAGE XML, ALTO, revision-locked modern PDF manifests and the
locked historical PAGE/ALTO pair used by the HaZefira extension.

Required invariants:

- UTF-8, NFC and logical Unicode order;
- ordinary image coordinates;
- explicit region/line IDs and reading order;
- polygons or baselines in source coordinates;
- language `he` for Hebrew content and `en` for local LTR runs where annotated;
- an explicit era, coverage scope and source provenance.

Converters may preserve extra metadata, but they must not invent unreadable
text, reverse Hebrew strings or infer unannotated table/form/script semantics.
In particular, corpus-level mixed square/Rashi metadata is not a line- or
region-level pure-Rashi label.
