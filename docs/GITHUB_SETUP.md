# GitHub repository setup

The canonical private remote is:

```text
office883/HebOCRBench
```

The repository was initialized on GitHub with a one-line placeholder README. The guarded publishing script is therefore permitted to replace **only** that exact initial commit:

```text
56ada5f679fd8f9f9ea33d2284b965efc8319953
```

It refuses unrelated repositories, dirty working trees, non-`main` branches, and any remote `main` that has changed since the placeholder was created:

```bash
./scripts/publish_private_repo.sh
```

The replacement uses `--force-with-lease`, not an unrestricted force push. Once the standalone history is published, ordinary protected-branch development must be used and force pushes should be disabled.

Recommended repository settings after the first push:

- keep visibility private until the 1.0 release audit is complete;
- protect `main` and require pull requests;
- require the Python 3.10/3.12/3.13 CI matrix;
- block force pushes and branch deletion;
- prevent GitHub Actions from writing repository contents unless a workflow explicitly needs it;
- store corpus credentials and organizer secrets only as encrypted environment secrets;
- publish large source snapshots and release products as release assets or controlled object storage, never as Git blobs.
