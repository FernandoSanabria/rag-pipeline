# phase0 — Project Conventions

Evaluation-driven RAG over an industrial-equipment-safety document corpus.
These conventions are binding for every task in this repo.

## Environment & dependencies
- Python 3.11, managed by **uv**.
- All dependencies are **pinned in `pyproject.toml`** — no unpinned or ad-hoc installs.

## Workflow: eval-first
- This is an **evaluation-driven** project. The eval harness is **built and run
  BEFORE** any retrieval or generation logic — the harness comes first, always.

## Evaluation
- Evaluation uses **RAGAS** with exactly four metrics, no more, no fewer:
  - faithfulness
  - answer relevancy
  - context precision
  - context recall

## Citations
- Citations in the final API render from the manifest **`title`** field
  (`data/manifest.json`) — **never** from raw filenames.

## Source documents & provenance
- **NEVER commit source PDFs.** PDFs under `data/` stay gitignored
  (`.gitignore` has `data/**/*.pdf`). Provenance lives in `data/manifest.json`.
- **NEVER fabricate a source URL or an evaluation ground-truth answer.**
  If you don't know, write `TODO_VERIFY` and **stop for human review**.

## Licensing tiers (treat as a hint, not a fact)
- `data/public/` = presumed public-domain (**tier 1**).
- `data/raw/` = presumed vendor-copyrighted (**tier 2**).
- The folder is a signal to **verify**, not proof. Confirm license/tier against
  `data/manifest.json` (and its `_verification_checklist`) before relying on it.
