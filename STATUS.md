# Project status

HebOCRBench is maintained as a **standalone, public-by-design benchmark project**. It must not use unrelated repositories as CI scratch space, artifact transport, or corpus storage.

## Current release state

**1.0 Release Candidate — not yet a certified `v1.0.0` dataset release.**

The evaluator, schemas, track contracts, converters, corpus registry, and release tooling are being consolidated around a public full-coverage profile. A final `v1.0.0` tag is permitted only after every item in [`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md) passes from one clean commit and the materialized corpus produces fresh `FROZEN.json` and `CERTIFIED.json` evidence.

## Inclusion policy

A source is not excluded because it is large, expensive to host, NonCommercial, ShareAlike, federated, IIIF-based, API-based, or access-controlled. These properties determine **how the source is delivered**, not whether it can be represented in the scientific benchmark.

The canonical scientific target is `public-full-v1`, containing every registered source that passes quality, provenance, conversion, split, and leakage gates. A smaller `redistributable-v1` package may be offered for convenience, but it is not allowed to masquerade as the complete benchmark.

## Storage policy

Large source archives, corpus images, generated builds, participant packs, organizer packs, model outputs, and baseline artifacts are deliberately excluded from ordinary Git history. They belong in:

- versioned GitHub Release assets;
- object storage or Git LFS when suitable;
- authoritative upstream repositories, APIs, or IIIF endpoints with pinned acquisition recipes;
- controlled organizer storage for hidden test ground truth and signing material.

This storage separation is not a coverage restriction. The Git repository retains the code, public manifests, source and profile locks, checksums, attribution, split declarations, and audit evidence needed to reproduce the benchmark.

## Gates still required for final 1.0

- materialize the complete declared `public-full-v1` source set;
- validate all images and ground truth against source locks;
- run document-level duplicate and leakage audits;
- freeze the dataset and generate cryptographic fingerprints;
- run reproducible real baselines and publish their complete bundles;
- generate participant and organizer packs and verify zero test-text leakage;
- build and install wheel/source archives in clean environments;
- publish release manifest, SBOM, checksums, and license/attribution bundle;
- obtain external review of the scorer, source policy, and release evidence;
- create the `v1.0.0` tag only after every gate passes on the same commit.

See [`docs/PUBLIC_DATA_POLICY_HE.md`](docs/PUBLIC_DATA_POLICY_HE.md) for the normative public-data policy.