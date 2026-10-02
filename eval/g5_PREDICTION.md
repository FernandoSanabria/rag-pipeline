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

## Outcome — P1–P4 (stdio), recorded 2026-10-02

Run over stdio against the commit that added `mcp_server/`, and scored against the predictions above, which are
unchanged. The transcripts were pasted at the gate review and are not committed, because they contain tier-2
chunk text; this section records ids, pages and counts only.

| | Verdict | Basis |
|---|---|---|
| P1 | **HOLDS** | Both clients list exactly 2 tools; the four-field `tools[]` JSON is byte-identical. |
| P2 | **FALSIFIED** | Row 4's MCP sequence differs from the direct call at index 6. |
| P3 | **HOLDS** | 50 rows, 0 violations, both tiers present. |
| P4 | **HOLDS** | Both error codes in both clients, `isError: true`, zero rows, no traceback in any client-received result. |
| P5 | **NOT TESTED** here | It needs the HTTP transport deployed, which follows the merge. |

### P1 — HOLDS

The Inspector CLI (2.8.0) and `scripts/mcp_client_probe.py` each list exactly `search_safety_docs` and
`lookup_document_metadata`. Normalized to the four pre-registered fields (name, inputSchema, outputSchema,
annotations) with keys sorted, the two JSON documents are byte-identical (4,516 bytes each; empty diff). For
information only: the full `tools[]` entries, descriptions included, are byte-identical too.

### P2 — FALSIFIED

`scripts/mcp_parity_probe.py`, k=10, the five queries above:

| row | filter | n MCP/direct | MCP == direct | control: direct == direct |
|---|---|---|---|---|
| 1 | — | 10/10 | True | True |
| 16 | — | 10/10 | True | True |
| 21 | — | 10/10 | True | True |
| 4 | — | 10/10 | **False** | True |
| 25 | `sds-sigma-aldrich-acetone` | 10/10 | True | True |

The first difference on row 4, verbatim from the probe:
`index 6: ('fisher-667-actuator', 14) vs ('fisher-667-actuator', 17) (text equal: False)`.

The control: row 4's two direct calls matched each other, so the pre-registered attribution rule (attribute to
embedding non-determinism when the two direct calls also differ) does not apply. The verdict is FALSIFIED.

**Post-hoc, not part of the verdict.** Five embeddings of the row-4 query in one process produced 4 identical
vectors and 1 differing by up to 9.2e-05 per component. Ranks 7 and 8 score 0.578180 vs 0.578172, a gap of
8.0e-06, and the differing vector swaps them. In the parity run, the MCP call got the common order and both
direct calls got the rarer one. The measurement is recorded in the ledger's methodology block
([`METRICS_HISTORY.md`](METRICS_HISTORY.md)).

**Design critique — by the reviewer, who authored this pre-registration.** The n=2 control had no power against
a ~1-in-5 event and passed by coincidence. The property P2 was meant to establish — that the MCP layer passes
`(query, k, source_doc_id)` through unchanged — is shown deterministically by hermetic test 5
(`tests/test_mcp_server.py`); the live parity test conflated layer transparency with backend reproducibility.

### P3 — HOLDS

Over the P2 MCP transcript: 50 rows (5 × 10) and 0 violations. Every row has non-empty text,
`kind == "document"`, and tier, license, title and publisher equal to the manifest; no title is a filename or
ends in `.pdf`. Both tiers are present.

### P4 — HOLDS

In both clients:
- `lookup_document_metadata("not-a-doc")` and `search_safety_docs` filtered to `"not-a-doc"` return
  `isError: true` with code `unknown_source_doc_id`;
- with the server launched with `PINECONE_API_KEY=invalid`, a search returns `isError: true` with code
  `retrieval_failed` and the message "retrieval raised UnauthorizedError; no results are returned".

Every case returned zero rows, and no client-received result contained a traceback. The falsifier is "text the
client sees" in the MCP result. The server's stderr is the operator channel (CONTRACT.md, "Error contract"):
under stdio, a client that forwards that stream shows the server's log, tracebacks included, by design. A grep
of that log from the forced-failure runs found neither the key value nor any Authorization header.
