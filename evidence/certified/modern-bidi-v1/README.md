# `modern-bidi-v1` certification evidence

This directory records the first frozen and certified core track of HebOCRBench's Modern Hebrew benchmark.

- Dataset fingerprint: `4c76d51f0e8ea9ad4d7a760fe83907e5984c634490d772e82fa14c5ba46a3ba1`
- Registry fingerprint: `2958ab9868ce10754ff3ad4144b5abf95b4c32d4c35daf993057f73cf8fe730f`
- Certification SHA-256: `820ff22f393fbb91da90d22d984ca6f8a6d733eaeab1c7b7f80a679250440e67`
- Pages: `48`
- Certification gates: 12/12
- Canonical implementation commit: `8d31a81d579793f4e0e278a78ba5bfef01986e67`

The diagnostic source is CC0 and is reconstructed from the repository's locked Modern-Hebrew BiDi source. The generated images are intentionally not duplicated in Git history; reproducibility is enforced by the exact `gold.jsonl` and image hashes recorded by the certified build. A build is the certified track only when every hash, the dataset fingerprint and the certification marker match this directory.

`modern-bidi-v1` is a conformance and diagnostic track. It is a mandatory gate for official ranking, but it does not replace evaluation on real contemporary documents.
