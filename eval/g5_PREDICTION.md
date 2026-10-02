# G5 — MCP server: pre-registration

This file is committed **before** any build code; it's the first commit on the G5 branch, following the
falsify-before-you-run discipline. Outcomes are **appended** below in later commits, and the predictions are
never edited. The companion design is [`g5_design.md`](g5_design.md). Recorded 2026-10-01.

**Evidence chain.** The PR is squash-merged, so this commit won't be on `main`'s history. Before anything cites
it, the commit gets an annotated `prereg/g5` tag naming the PR. That keeps the hash resolvable for
[`scripts/check_doc_citations.py`](../scripts/check_doc_citations.py), the same convention as `prereg/g1-closure`.

**Shared setup.**
- Default settings: `RETRIEVAL_NAMESPACE=semantic_v2`, `RETRIEVAL_K=10`.
- Embeddings: `text-embedding-3-small`.
- No generation, no RAGAS and no scored eval. The only spend is about 20 embedding calls, roughly $0.

**Verdicts** are **HOLDS**, **FALSIFIED** or **NOT TESTED**, each with a reason. NOT TESTED is never reported
as a pass or a fail.

## P1 — Discovery

Two independent clients list the server over stdio:
- the MCP Inspector CLI, pinned: `npx -y @modelcontextprotocol/inspector@2.8.0 --cli …`, with the exact v2
  flags taken from `--help`;
- `scripts/mcp_client_probe.py`, a Python client on the pinned `mcp` SDK, at most 40 lines.

**Prediction:** each client lists exactly 2 tools, `search_safety_docs` and `lookup_document_metadata`. Their
`tools[]` JSON (name, inputSchema, outputSchema, annotations), with keys sorted, is byte-identical.

**Falsified by:** a tool count other than 2, a name mismatch, or any schema difference.

## P2 — Parity with a direct call

**Prediction:** for the 5 fixed queries below at `k = 10`, `search_safety_docs` over stdio returns the **same
ordered `(source_doc_id, page, text)` sequence** as an in-process
`src.retrieve.dense_search(query, k=10, source_doc_id=…)`. Text is included, since Option A returns it.

**Falsified by:** any difference in order, membership or text, on any query.

**Control (recorded, not a pass condition).** Each query's direct call is run twice. If P2 fails on a query
whose two direct calls also differ, the write-up attributes the failure to embedding non-determinism.
**Attribution never un-falsifies.**

The queries are question text only, copied verbatim from [`dataset.jsonl`](dataset.jsonl). No references are used.

| # | dataset row | query | `source_doc_id` |
|---|---|---|---|
| 1 | 1 | What is the RMP threshold quantity for anhydrous ammonia? | — |
| 2 | 16 | What is the required sequence of actions for applying lockout/tagout before servicing equipment? | — |
| 3 | 21 | Do the Airgas chlorine SDS and OSHA's air-contaminants table agree on the chlorine exposure ceiling? | — |
| 4 | 4 | What is the Maximum Diaphragm Casing Pressure for the Fisher 667 actuator size 30/30i? | — |
| 5 | 25 | What is the flash point of acetone per the Sigma-Aldrich SDS? | `sds-sigma-aldrich-acetone` |

## P3 — Provenance on every row

**Prediction:** over the P2 MCP transcript (5 × 10 rows), every row has:
- non-empty `text`;
- `kind == "document"`;
- `tier`, `license`, `title` and `publisher` equal to the manifest entry for its `source_doc_id`.

There are zero rows whose title equals a manifest filename or ends in `.pdf`. The transcript must contain at
least 1 tier-1 row **and** at least 1 tier-2 row. If it doesn't, P3 is NOT TESTED (not shown), not HOLDS.

**Falsified by:** any missing or mismatched field, or a filename where a title belongs.

## P4 — Error paths

**Prediction:** in **both** clients:
- (a) `lookup_document_metadata("not-a-doc")`, and `search_safety_docs` filtered to `"not-a-doc"`, each return
  `isError: true` with code `unknown_source_doc_id`;
- (b) a forced retrieval failure, with the server launched with `PINECONE_API_KEY=invalid`, returns
  `isError: true` with code `retrieval_failed`.

Every case returns zero rows.

**Falsified by:** a traceback or stack frame in text the client sees, any row, or `isError: false`.

## P5 — Live (after the deploy)

**Prediction:**
- (a) The automatic post-deploy wire-smoke run is green, including the `/mcp` `initialize` probe.
- (b) The dispatch-only negative proof, run **once**, goes red by design, with the MCP 406 path failing as
  required.
- (c) `scripts/mcp_client_probe.py https://equip-docs-rag-api.onrender.com/mcp`, run from a host other than
  the Render instance, completes `initialize`, `list_tools` and one search. The transcript records `uname -n`.

**Falsified by:** any of (a)–(c) failing. If it isn't attempted within budget, it's **NOT TESTED**, not failed.

If the HTTP step (C4) is severed into its own PR at GATE 2, P5 moves to that PR's pre-registration and is
NOT TESTED here.
