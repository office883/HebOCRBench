# Project status

HebOCRBench is maintained here as a **standalone private project**. This repository must not use unrelated repositories as storage, CI scratch space, or artifact transport.

Current release status: **1.0 Release Candidate — not yet a certified 1.0 dataset release**.

The evaluator, schemas, track contracts, converters, corpus registry, and release tooling are under active consolidation. A final `1.0.0` tag is permitted only after every item in `docs/RELEASE_CHECKLIST.md` passes from a clean commit and the materialized corpus produces fresh `FROZEN.json` and `CERTIFIED.json` evidence.

Large source archives, corpus images, private organizer packs, downloaded artifacts, and model outputs are deliberately excluded from Git. They must live in release assets or controlled object storage and remain bound to checksums and license records.
