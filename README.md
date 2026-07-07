# phase0

Evaluation-driven RAG over an industrial-equipment-safety document corpus.

## Prerequisites
- Python 3.11, managed by [uv](https://docs.astral.sh/uv/).
- A `.env` at the repo root with: `OPENAI_API_KEY`, `PINECONE_API_KEY`, `LANGCHAIN_API_KEY`,
  `LANGCHAIN_TRACING_V2`, `LANGCHAIN_PROJECT` (and optionally `COHERE_API_KEY`, `INDEX_NAME`,
  `LLM_MODEL`).

## Setup
```bash
uv sync
```
This creates the virtualenv, installs all pinned dependencies, **and editable-installs this
project** so `from src.pipeline import ask` resolves from any script with no `sys.path` tricks.

> **Required after every fresh clone.** The editable install lives in `.venv/` (gitignored), so
> `src` is not importable until `uv sync` has run. Any `uv run …` command auto-syncs, so running a
> script also works, but an explicit `uv sync` first is the clean way to set up.

## Usage
Run everything from the repo root via `uv run`:

```bash
# Ingest the corpus into Pinecone (namespace fixed_500_50)
uv run python src/ingest.py

# Evaluate the pipeline with RAGAS over eval/dataset.jsonl
uv run python eval/run_eval.py

# Quick end-to-end sanity check of ask()
uv run python scripts/smoke_test.py
```

See [`scripts/README.md`](scripts/README.md) for the developer tooling and
[`CLAUDE.md`](CLAUDE.md) for project conventions.
