# Standalone repository policy

## Scope

This repository contains only HebOCRBench source code, tests, schemas, normative track configurations, documentation, and small diagnostic fixtures.

## Prohibited cross-repository use

Unrelated repositories must not be used to:

- transport HebOCRBench source archives or GitHub Actions artifacts;
- host temporary workflows, payload chunks, corpus snapshots, or benchmark results;
- trigger HebOCRBench CI jobs;
- store private organizer data or withheld evaluation gold.

All HebOCRBench automation belongs in this repository or in explicitly documented storage owned by this project.

## Data policy

Do not commit materialized corpora, downloaded upstream archives, generated page images, prediction dumps, participant packs, organizer packs, wheels, release archives, or caches. The `.gitignore` rules enforce the common paths and extensions. Every distributed dataset artifact must have provenance, license metadata, an inventory, and cryptographic checksums.

## Release policy

A final release requires a clean Git tree, green test and lint suites, rebuilt lock files, isolated package installation, corpus freeze and certification, baseline evidence, leakage checks, and archive verification after extraction. `docs/RELEASE_CHECKLIST.md` is authoritative.
