# ARCHITECTURE — orientation map for this repo

> **What this file is.** A stable map for anyone (human or coding agent) picking up a task here: how
> the system is laid out, how a request flows, what the naming schemes mean, and where the
> authoritative details live. It is deliberately **orientation, not a status board** — every fact
> that changes (pinned versions, metric values, env-var defaults, the current milestone) is a
> *pointer* to its source of truth, not a copy. The test applied to every line below: *if this
> changes and nobody updates this file, is the file now wrong?* If yes, it's a link, not a claim.
>
> Binding rules live in [`CLAUDE.md`](CLAUDE.md) — this file explains and orients; `CLAUDE.md`
> governs. If the two ever disagree, `CLAUDE.md` wins.

---

## 1. What this project is

An **evaluation-first RAG pipeline** that answers questions over a corpus of industrial-equipment-
safety documents — OSHA / EPA / NIOSH regulations, chemical safety data sheets, and equipment
manuals. Local project name: `phase0` (see [`pyproject.toml`](pyproject.toml)). GitHub repo:
`rag-pipeline`. A deployed instance and a copy-paste `curl` live in the **Live demo** section of
[`README.md`](README.md).

**What it actually demonstrates** is a *method*, not just a pipeline: every change was
**pre-registered** (a falsifiable prediction committed before the eval ran), made **one variable at a
time**, and judged against the **raw artifact** — the actual retrieved contexts and generated
answers — rather than the aggregate score, which repeatedly misled. Two more-complex retrieval levers
were **falsified by read-only probes before any eval spend**, so the shipped pipeline is *simpler*
than the one first planned. The narrative version is [`README.md`](README.md); the long-form essays
are under [`blog/`](blog/CONVENTIONS.md).

## 2. How to read this repo (start here)

This table is the spine of the document. It points, so it does not go stale.

| Read this… | when you need… |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | the **binding** conventions — the five metrics, provenance/citation rules, eval-first, licensing tiers. Non-negotiable. |
| [`README.md`](README.md) | the public narrative, the live-demo `curl`, the results tables, the rendered request-graph diagram, setup & reproduction commands. |
| [`eval/METRICS_HISTORY.md`](eval/METRICS_HISTORY.md) | the **metrics ledger** — every measured change, its delta, commit, result file, per-row findings. The source of truth for any number. |
| [`eval/replay_safety_design.md`](eval/replay_safety_design.md) | how `AgentState` behaves under replay/checkpointing; the design pass for the future approval gate (G10b). |
| [`eval/toolcall_PREDICTION.md`](eval/toolcall_PREDICTION.md) | the G1 tool-loop pre-registration and what it predicted vs. what happened. |
| [`eval/decomp_probe_RESULT.md`](eval/decomp_probe_RESULT.md) | a worked example of *falsifying* a lever (query decomposition) with a read-only probe. |
| [`eval/rechunk_2bc_likeforlike.md`](eval/rechunk_2bc_likeforlike.md) · [`eval/rechunk_2bc_design.md`](eval/rechunk_2bc_design.md) · [`eval/rechunk_2c_acetone_router.md`](eval/rechunk_2c_acetone_router.md) | the design + promotion basis for the re-chunk (2B) and the source-scoped router (2C). |
| [`eval/run_notes_v2_semantic.md`](eval/run_notes_v2_semantic.md) · [`eval/run_notes_v3_prompt.md`](eval/run_notes_v3_prompt.md) · [`eval/run_notes_v4_densek10.md`](eval/run_notes_v4_densek10.md) | the pre-registered prediction for each Phase-1 retrieval step. |
| [`eval/KNOWN_LIMITATIONS.md`](eval/KNOWN_LIMITATIONS.md) · [`eval/COST_LEDGER.md`](eval/COST_LEDGER.md) | the honest backlog, and the (metrics-only) cost accounting. |
| [`scripts/README.md`](scripts/README.md) | the developer-tooling index (smoke test, eval enrichment/audit, grounding checks). |
| [`blog/CONVENTIONS.md`](blog/CONVENTIONS.md) | the shared source-of-truth for the five write-ups (canonical terms, figures, link rules). |

## 3. Repository layout

```
phase0/
├── src/        # the frozen v4 runtime pipeline (retrieve → generate)
├── agent/      # LangGraph orchestration layer (the /ask/agent path)
├── api/        # FastAPI service wrapping both paths
├── mcp_server/ # G5 MCP server: two read-only tools over the corpus (contract: mcp_server/CONTRACT.md)
├── eval/       # the evaluation harness, datasets, the metrics ledger, design/result docs
├── scripts/    # developer tooling + read-only probes (NOT runtime, NOT the harness)
├── data/       # corpus PDFs (gitignored) + committed manifest.json (provenance)
├── tests/      # hermetic pytest suite (no secrets, no network)
├── blog/       # five cross-linked write-ups + CONVENTIONS.md
├── .github/    # CI + eval-smoke + keepalive + post-deploy wire-smoke workflows
└── main.py     # a stub — NOT the app entry point
```

- **[`src/`](src/pipeline.py)** — the runtime pipeline, treated as a frozen contract (see §7):
  `pipeline.py` (`ask()` → `{answer, contexts, chunks}`), `retrieve.py` (dense Pinecone search +
  the one canonical context formatting), `generate.py` (grounded, cite-or-refuse generation),
  `config.py` (the settings object — the home of every runtime default), `ingest.py` (guarded
  Pinecone ingestion).
- **[`agent/`](agent/graph.py)** — the LangGraph layer; every node wraps an existing `src/`
  capability and reimplements nothing. `graph.py` (compiled graph + the `ask()` entry adapter),
  `state.py` (the `AgentState` channels, reducers, and `fresh_state()`), `tools.py` (the G1 tools). `graph.py` also
  holds the G12 fan-out nodes (`decompose`, `branch_retrieve`, `join`), compiled in only when `FANOUT_ENABLED` is set;
  it ships off. The served graph adds G10b's approval gate (`review_gate` → `review_wait`) immediately before
  `generate`. `review.py` holds its trigger policy, the SQLite checkpointer factory (`REVIEW_DB_PATH`), the TTL and the
  per-thread lock. `state.py`'s `retrieved` reducer appends exactly as `operator.add` and also accepts the
  `RemoveChunk`/`Reset` sentinels a reviewer's amendment uses.
- **[`api/`](api/main.py)** — FastAPI: `main.py` (`GET /health`, `POST /ask`, `POST /ask/agent`, and
  `POST /mcp`, the G5 MCP server over streamable HTTP; G10b adds `POST /ask/agent/resume` and
  `GET /ask/agent/review/{thread_id}`, and `/ask/agent` answers 202 when the approval gate pauses),
  `citations.py`, `confidence.py`, `schemas.py`, and `guards.py` (G6: the output guard, applied to both endpoints
  in the shared assembly; the input guard is built but switched off by `INPUT_GUARD_ENABLED`). `agent.graph` is imported lazily, so an agent-side import
  failure can break only `/ask/agent`; `api.main` imports `mcp_server` eagerly (a route must exist at startup),
  which constructs `Settings` at import and therefore requires both API keys to be present before
  `import api.main` — every existing entry point already supplies them.
- **[`mcp_server/`](mcp_server/CONTRACT.md)** — the G5 MCP server (`search_safety_docs`,
  `lookup_document_metadata`); it calls `src.retrieve.dense_search` and the G1 metadata tool and
  reimplements nothing. Schemas, licensing policy and error contract: [`mcp_server/CONTRACT.md`](mcp_server/CONTRACT.md).
- **[`eval/`](eval/run_eval.py)** — `run_eval.py` (the harness), `dataset.jsonl` (28 frozen rows),
  `capability_set.jsonl` (the tool-capability set — rows/provenance in that file; not comparable to the
  frozen 28), `results/` (gitignored per-run JSON), and
  the markdown ledger + design/result docs listed in §2. The G9 smoke suite lives here too: `smoke_set.json` (the 8
  rows), `smoke_snapshot.json` (what CI compares against) and `smoke_pubkey.asc` (the breach-encryption public key),
  run by `scripts/smoke_eval.py` (§5).
- **Entry point.** The deployed app is `api.main:app` (run by `uvicorn`). The `main.py` at the repo
  root is a leftover stub — do **not** treat it as the entry point.

## 4. Request flow — two serving paths

There are **two endpoints over one shared assembly layer**. Both return the same typed contract
(`answer`, `citations`, `confidence_score`, `confidence_basis`, `guard`); the agent path adds routing fields.
The shared assembly also holds the G6 output guard: an answer with a figure that traces to nothing retrieved is
withheld whole, on both endpoints (see *Guardrails* in [`README.md`](README.md)).

**`POST /ask` — the shipped default.** `src.pipeline.ask`: dense retrieval → context formatting →
grounded generation. No router, no per-request LLM tax beyond the answer itself. This is the
promoted path.

**`POST /ask/agent` (`PIPELINE=agent`) — the richer path.** `agent.graph.ask`, a compiled LangGraph:

```
router → {direct: retrieve | source_scoped: source_scoped_retrieve} → tool_decide ⇄ tool_exec
       → review_gate → [review_wait] → generate
```

- **router** classifies whether a question is anchored to one named document; on any error it falls
  back to `direct`.
- **source_scoped_retrieve** metadata-filters retrieval to that one document, and on a filter failure
  *or* an empty result falls back to the full-corpus query (and reports the route it actually ran).
- **tool_decide ⇄ tool_exec** is the **G1 bounded tool loop** (see below), capped and with per-tool
  timeouts; when no tool is needed it passes straight to `generate`.
- **review_gate → review_wait** is the **G10b approval gate**.
  - It fires on a pre-registered policy on the question's wording, or on a source-scoped fallback; when it doesn't
    fire it is a no-op.
  - When it fires, `review_wait` pauses with LangGraph's `interrupt()`. The thread is checkpointed (a fresh uuid4 per
    request), and the API answers 202 with the evidence.
  - A reviewer resolves it through `POST /ask/agent/resume`. With no checkpointer configured, the request is refused,
    never answered unreviewed.
- **generate** wraps the frozen `src.generate.generate` and short-circuits to an empty answer on a
  retrieval error (no LLM cost), mirroring `pipeline.ask` exactly.

**Why `/ask/agent` is *richer*, not *better*.** It pays a `gpt-4o-mini` router call and a tool-
decision call on **every** request, including the majority that then route direct and get the
*identical* `/ask` answer. It buys single-document scoping plus a transparency payload (`route` /
`source_doc_id` / `routing_reason`) at a fixed per-request cost — not a strict upgrade. Use `/ask`
when you don't need scoping. The current rendered graph and the cost discussion are in
[`README.md`](README.md) (see the diagram note below).

**The synthetic-tool-chunk mechanism (important, and non-obvious).** The frozen `generate` node
grounds **only** on the retrieved-context channel; it is never modified to "see" tool output
directly. So a tool result reaches the answer by being appended to that channel as a **synthetic,
self-describing chunk** with `source_doc_id="tool:<name>"` and `page=None`. Because citations are
derived from chunk metadata and a `None` page is skipped, tool chunks are **excluded from citations**
by construction — a tool output is a *transformation of a cited source, not a source itself*. This is
what lets the tool loop feed the generator without touching the frozen contract. The channel/reducer
details are documented in [`agent/state.py`](agent/state.py); the loop itself in
[`agent/graph.py`](agent/graph.py).

> **Diagram — one copy, on purpose.** The request-serving graph is rendered in
> [`README.md`](README.md) under *"Request-serving graph"*; its **topology is checked against
> `scripts/render_graph.py` output, but the README's edge labels are hand-curated** — the committed
> block is not the script's verbatim output (see the backlog line in
> [`eval/KNOWN_LIMITATIONS.md`](eval/KNOWN_LIMITATIONS.md)). This file does **not** keep a second copy —
> a duplicated diagram is two things to keep in sync.

## 5. The evaluation harness

The harness ([`eval/run_eval.py`](eval/run_eval.py)) runs a selected pipeline over a dataset and
scores it with RAGAS, writing a timestamped JSON into `eval/results/` (gitignored).

- **Pipeline selector.** The `PIPELINE` env var chooses the pipeline (default = the v4 `src` path;
  `agent` = the LangGraph path). An unrecognized value errors rather than silently falling through.
- **Datasets.** `eval/dataset.jsonl` is **28 hand-verified rows** (question + a hand-verified
  `reference` + provenance) — the frozen eval set. `eval/capability_set.jsonl` is a **separate**
  tool-capability set (rows/provenance in that file; measured values the corpus does not tabulate); it is **never**
  appended to the 28 and is **not comparable** to the ledger history.
- **The five metrics.** Exactly five, no more and no fewer (this is binding — see
  [`CLAUDE.md`](CLAUDE.md)): faithfulness, answer relevancy, context precision, context recall, and
  answer correctness (scored against the hand-verified `reference`; it guards against faithful-but-
  wrong answers). **Values live only in [`eval/METRICS_HISTORY.md`](eval/METRICS_HISTORY.md)** — this
  file names the metrics but quotes no scores.
- **Fingerprints & like-for-like.** The generation model is a floating alias, so OpenAI's
  `system_fingerprint` drifts between runs. A **like-for-like** compares two variants row-by-row under
  the *same* fingerprint (interleaved) so a difference reflects the change, not backend drift; a
  mismatched-fingerprint comparison is *not* a confirmed result. Treat a single answer-correctness
  move under **~±0.03** as noise, not signal — and settle close calls by **reading the per-row
  artifact**, not the aggregate.
- **The CI smoke (G9/G9b).** [`scripts/smoke_eval.py`](scripts/smoke_eval.py) runs 8 of the 28 rows through the
  served path (`api.main._answer`) on every PR that touches the gated paths
  ([`eval-smoke.yml`](.github/workflows/eval-smoke.yml)). It fails only on retrieval: a row's
  `(source_doc_id, page)` set differs from `eval/smoke_snapshot.json`, or the refusal row stops refusing.
  Faithfulness and answer correctness are reported against the snapshot, behind a content-keyed judge cache. Gating
  faithfulness was pre-registered and falsified (G9), because generation varied on identical input. A breaching row's
  answer is attached only as OpenPGP ciphertext. It complements the harness and does not replace it: the full 28 stay
  a manual run.
- **The promotion gate.** A change ships only if it clears a **pre-registered, asymmetric** bar (no
  metric regresses beyond the noise floor; improvements are unbounded), judged on a fingerprint-
  matched like-for-like. The re-chunk (2B) was promoted this way; the write-up is
  [`eval/rechunk_2bc_likeforlike.md`](eval/rechunk_2bc_likeforlike.md).

## 6. Milestone / naming decoder

The project's history uses a few overlapping naming schemes. This decoder is stable; for *what is
currently in flight* consult the ledger and the PR list, not a status line here.

- **Phase-1 retrieval arc — versions `v0`→`v4`.** `v0` empty baseline → `v1` dense + fixed chunks →
  `v2` semantic chunking → `v3` generation prompt → `v4` retrieval depth `k=10` (the Phase-1 final;
  current runtime defaults live in [`src/config.py`](src/config.py)). Two further levers (a BM25-fusion fix, then full hybrid retrieval)
  were **falsified** by read-only probes and never run. Full arc: [`README.md`](README.md) +
  [`eval/METRICS_HISTORY.md`](eval/METRICS_HISTORY.md).
- **"Step-N" — the Phase-1 workflow steps.** Notably **Step-5 = the API service** (the
  `{document, page}` citation contract + the confidence layer, i.e. the FastAPI layer). When a doc
  says "Step 5," it means the API/citation service.
- **Phase-2 agentic arc — `2A`→`2D`.** `2A` a LangGraph skeleton that byte-reproduces v4; `2B` the
  structure-aware re-chunk → the `semantic_v2` namespace (recovered the IDLH-vs-EPA comparison,
  promoted as the live default); `2C` the source-scoped router (recovered the acetone flash-point
  lookup); `2D` the deployed `/ask/agent` + routing transparency.
- **"G-N" gates — the agent-capability arc.** These labels are defined *inline in the code and design
  docs*, not in a central roadmap file:
  - **G1 = the bounded tool-calling loop** (unit-conversion + document-metadata tools). Built and
    merged; defined in [`agent/graph.py`](agent/graph.py); pre-registration + outcome in
    [`eval/toolcall_PREDICTION.md`](eval/toolcall_PREDICTION.md). Current verdict and its evidence live
    in the G1-closure rows of [`eval/METRICS_HISTORY.md`](eval/METRICS_HISTORY.md) and the G1 section of
    [`eval/KNOWN_LIMITATIONS.md`](eval/KNOWN_LIMITATIONS.md) — this file does not carry a result the
    ledger owns.
  - **G5 = the MCP server** ([`mcp_server/`](mcp_server/CONTRACT.md)). Built; it re-exports the G1
    metadata tool straight from the deliberately dependency-light [`agent/tools.py`](agent/tools.py).
    Contract in [`mcp_server/CONTRACT.md`](mcp_server/CONTRACT.md); design, pre-registration and
    outcomes in [`eval/g5_design.md`](eval/g5_design.md) and [`eval/g5_PREDICTION.md`](eval/g5_PREDICTION.md)
    — this file does not carry a result those files own.
  - **G6 = guardrails** ([`api/guards.py`](api/guards.py)). The output guard shipped; the input guard is built,
    measured and switched off. Design in [`eval/g6_design.md`](eval/g6_design.md); the v1 and v2
    pre-registrations and their outcomes are in [`eval/g6_PREDICTION.md`](eval/g6_PREDICTION.md). This file does not
    carry a result those files own.
  - **G12 = the parallel fan-out for comparison questions** (dispatch-and-aggregate, in
    [`agent/graph.py`](agent/graph.py)). Built, measured and switched off. The design is in
    [`eval/g12_design.md`](eval/g12_design.md); the pre-registration and its outcome are in
    [`eval/g12_PREDICTION.md`](eval/g12_PREDICTION.md). This file does not carry a result those files own.
  - **G10b = a checkpointer + human-approval gate** ([`agent/review.py`](agent/review.py), the `review_gate` and
    `review_wait` nodes in [`agent/graph.py`](agent/graph.py)). Built and shipped: `/ask/agent` pauses before
    generation on a pre-registered trigger and resumes on approve, reject or amend (removals only). Pre-designed in
    [`eval/replay_safety_design.md`](eval/replay_safety_design.md); the design is in
    [`eval/g10b_design.md`](eval/g10b_design.md), and the pre-registration and its outcome are in
    [`eval/g10b_PREDICTION.md`](eval/g10b_PREDICTION.md). This file does not carry a result those files own.

## 7. Conventions & guardrails

These are the invariants a change must respect. [`CLAUDE.md`](CLAUDE.md) is the binding statement;
this section explains the *why* and points there.

- **Dependencies.** Python 3.11, managed by **uv**; every dependency **pinned** in
  [`pyproject.toml`](pyproject.toml) — no unpinned or ad-hoc installs. `uv sync` editable-installs the
  project so `from src.pipeline import ask` resolves anywhere.
- **Eval-first, pre-registered, one variable at a time.** The harness precedes retrieval/generation
  logic. Every change lands with a committed, falsifiable prediction *before* the run (the
  `run_notes_*` / `*_PREDICTION.md` files), and moves exactly one variable so a delta has one cause.
  The arbiter is the artifact, not the aggregate.
- **Citations.** Structured citations render from the manifest **`title`** field (never a raw
  filename) and are derived from **retrieved-chunk metadata** (`source_doc_id` + `page`, deduped) —
  **never parsed from the model's prose** (the generator was caught mis-citing its own pages). A
  refusal/empty answer yields no citations; tool chunks (`page=None`) are excluded. See
  [`api/citations.py`](api/citations.py).
- **Provenance — never fabricate, never commit PDFs.** Source PDFs stay gitignored
  (`.gitignore: data/**/*.pdf`); provenance lives in [`data/manifest.json`](data/manifest.json).
  **Never fabricate a source URL or an evaluation ground-truth answer** — write `TODO_VERIFY` and
  **stop for human review**. Tools follow the same rule (they return `null` rather than a guessed
  value).
- **Licensing tiers are a hint, not a fact.** `data/public/` = presumed tier-1 (public-domain);
  `data/raw/` = presumed tier-2 (vendor-copyrighted, gitignored). The folder is a signal to
  **verify** against the manifest's checklist, not proof.
- **The frozen contract.** `src/generate.py` and its inputs are treated as **untouched**: the agent's
  `generate` node and the direct no-tool path **byte-reproduce v4** exactly (retrieval depth read
  from settings, not hardcoded; the `{answer, contexts, chunks}` shape the harness reads is frozen).
  Out-of-scope to edit during feature work: `src/generate.py`, `src/pipeline.py`, `src/retrieve.py`,
  `render.yaml`, and CI. This is *why* new capability (like the G1 tools) enters through the synthetic-
  chunk mechanism in §4 rather than by modifying the generator.
- **CI guards** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). On push/PR to `main`:
  `scripts/check_doc_citations.py` fails the build on (a) any cited commit hash that doesn't resolve
  and (b) any **dead relative markdown link** in a tracked doc; then a hermetic `pytest` run (no
  secrets, no network); then a `docker build` (no push) to catch a broken Dockerfile. A second gated
  workflow, [`eval-smoke.yml`](.github/workflows/eval-smoke.yml) (§5), holds the repository's only secrets
  (`OPENAI_API_KEY`, `PINECONE_API_KEY`). It is a separate file, so `ci.yml` stays hermetic, and without the secrets
  (fork PRs) its `smoke` job is skipped, never red. Two companion workflows: a keepalive ping (fights free-tier
  spin-down) and a post-deploy wire-smoke (polls the live service after a deploy). **Note:** once this file is committed, its own relative links enter
  the doc-guard's scope — keep them few and correct. Cited hashes must be reachable from main or from a
  prereg/* or evidence/* tag. `prereg/*` marks a pre-registration commit; `evidence/*` marks a measured build or
  baseline the ledger cites. Both use the same annotated-tag format with the PR number in the message (e.g.
  `prereg/g1-closure`, `evidence/g9-baseline`). PRs are squash-merged, so the tags keep those commits resolvable
  once their branch is gone.

## 8. Quick start

Run everything from the repo root via `uv run`. (Commands verified against [`README.md`](README.md)
and [`scripts/README.md`](scripts/README.md); the canonical, fuller versions live there.)

```bash
uv sync --dev                 # create the venv, install pinned deps, editable-install the project
uv run pytest -q              # the hermetic test suite (no secrets, no network)
uv run uvicorn api.main:app   # serve the API locally (GET /health, POST /ask, POST /ask/agent, POST /mcp)

uv run python eval/run_eval.py               # evaluate the shipped v4 path over the 28-row set
PIPELINE=agent uv run python eval/run_eval.py # evaluate the LangGraph agent path instead
uv run python scripts/smoke_test.py          # quick end-to-end sanity check of ask()
```

The live-service `curl` example (a deployed instance you can hit without any setup) is in the
**Live demo** section of [`README.md`](README.md). Runtime knobs (retrieval namespace/depth, the
pipeline selector, tool-loop cap/timeout) and their **defaults** live in
[`src/config.py`](src/config.py) — read them there rather than trusting a copied table.
