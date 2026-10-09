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
| `enrich_eval.py` | Turns a raw `run_eval.py` output into a versioned metrics-history record (`eval/results/<prefix>_<ts>.json`, gitignored) + report: dual-model provenance, per-category aggregates, Δ vs a chosen baseline, a 3-way refusal-integrity audit (exact / near-miss / attempt), per-row correctness diff, and an optional prediction check. Reused each optimization round (v3/v4/…). See `--help` for args. |
| `mcp_client_probe.py` | Minimal MCP client for the G5 server (≤40 lines): initialize, list the tools, optionally call one; prints one JSON document. stdio by default (`uv run python scripts/mcp_client_probe.py [TOOL ARGS_JSON]`), or pass a URL first for streamable HTTP. Used for P1/P4/P5 in `eval/g5_PREDICTION.md`. |
| `mcp_parity_probe.py` | G5 P2/P3: for the five pre-registered queries at k=10, compares MCP `search_safety_docs` (stdio) with a direct `dense_search` (plus a direct-vs-direct control) and checks every result's provenance against the manifest. Prints ids, pages and counts only — never chunk text. ~15 embedding calls. |
| `guardrail_eval.py` | G6: runs the pre-registered P1–P6 and P3b of `eval/g6_PREDICTION.md` live, through the functions `api/main.py` wires: the input guard on the frozen 28 and `eval/guardrail_set.jsonl`, the output guard behind both pipelines, guarded vs guard-less pass-through, and the guards' cost and latency. Memoizes the query embedding for the process by patching `src.retrieve._embedder` at runtime (no `src/` change). Writes derived metrics only to `eval/guardrail_metrics.json`; raw answers stay in gitignored `eval/results/`. ~$0.30, ~30 min. `--parts` runs a subset (written to `eval/results/` unless `--out` names a path); the v2 input-guard re-run is `--parts P1,P2 --out eval/guardrail_metrics_v2.json`. |
| `fanout_eval.py` | G12: runs the pre-registered P1–P6 of `eval/g12_PREDICTION.md` live, comparing the fan-out graph with `agent/graph.py` at the branch point, loaded in the same process: dispatch on the four comparison rows (with both-sources, RAGAS context precision/recall and answer correctness, and latency read from the same runs), degradation with an injected failing branch, and byte-identical generate inputs on the other 24 rows (embedding, router and tool decisions memoized across arms; generation stubbed). Derived metrics only to `eval/fanout_metrics.json`; raw runs stay in gitignored `eval/results/`. ~$0.5. |
| `review_eval.py` | G10b: runs the pre-registered P1, P2, P3a, P4 and P5 of `eval/g10b_PREDICTION.md` live: the approval gate's fire set over the frozen 28, the capability set and the guardrail hard negatives (generation stubbed); approve, reject, amend and expired through the real endpoints in-process; byte-identical generate inputs across the pre-G10b graph and G10b with and without the SQLite checkpointer, with the checkpointer's latency; and a real uvicorn process paused, killed, restarted and resumed. Derived metrics only to `eval/review_metrics.json`; raw records stay in gitignored `eval/results/`. ~$0.15, ~9 min. |

## Notes
- `extract_pages.py` writes to `scratchpad/`, which is **gitignored** — the dumps are regenerable on
  demand from the PDFs + manifest and are never committed. Run `extract_pages.py` before
  `verify_eval_tokens.py`.
- These scripts read `.env` (via `load_dotenv()` / `src.config`) and may call OpenAI / Pinecone /
  LangSmith, so they require valid keys in `.env`.
