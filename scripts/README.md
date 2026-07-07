# scripts/

Developer tooling — **not** part of the `src/` runtime pipeline or the eval harness. These are
convenience utilities for smoke-testing and for auditing the eval dataset's grounding. The project
is installed editable (see `[build-system]` in `pyproject.toml`), so every script imports `src`
cleanly with no path manipulation. Run all of them from the repo root:

```bash
uv run python scripts/<name>.py
```

| Script | Purpose |
|---|---|
| `smoke_test.py` | End-to-end sanity check of `src.pipeline.ask()` on a few representative questions (including one hard multi-page case). Asserts the return shape `{"answer": str, "contexts": list[str]}` and prints retrieved `source_doc_id`/`page` + the answer. |
| `extract_pages.py` | Dumps per-page, NFKC-normalized text for every `data/manifest.json` doc into `scratchpad/pages/<doc_id>.txt` (with `===== <doc_id> PAGE n/N =====` markers). |
| `verify_eval_tokens.py` | Checks that each eval candidate's `key_value_token` actually appears on its cited page. Reads the dumps from `extract_pages.py`. Usage: `uv run python scripts/verify_eval_tokens.py <candidates.json>`. |

## Notes
- `extract_pages.py` writes to `scratchpad/`, which is **gitignored** — the dumps are regenerable on
  demand from the PDFs + manifest and are never committed. Run `extract_pages.py` before
  `verify_eval_tokens.py`.
- These scripts read `.env` (via `load_dotenv()` / `src.config`) and may call OpenAI / Pinecone /
  LangSmith, so they require valid keys in `.env`.
