# Blog conventions — shared source of truth for the article set

Five cross-linked write-ups share terms, numbers, and links. To keep them from drifting, this file is
the one place those are defined. Every article conforms to it. Numbers trace to
[`eval/METRICS_HISTORY.md`](../eval/METRICS_HISTORY.md); no article contains commit-hash tokens; every
relative link resolves to a tracked file.

## Canonical glosses (use verbatim on first use in each piece)
*Complete: every load-bearing term across the five pieces is glossed here once. Add a term here before using it in a piece.*

**Retrieval & indexing**
- **RAG** — retrieval-augmented generation: answer a question by retrieving the most relevant passages
  from a document corpus, then having an LLM answer *only* from them.
- **dense retrieval** — matching on *meaning* via embeddings; strong on paraphrase, blind to exact identifiers.
- **embedding / embedding dilution** — an embedding is one vector meant to carry a chunk's meaning; pack
  several records into a chunk and it encodes their *average*, so each fact sinks below what a query can reach.
- **semantic chunking** — splitting a document on topic/record boundaries instead of blind character counts,
  so each chunk is one coherent unit.
- **namespace** — a separate labelled partition of the vector store; you can build a new index alongside the
  live one and switch between them.
- **k / retrieval depth** — how many chunks each query pulls back (e.g. k=10).
- **BM25 / hybrid / RRF fusion** — BM25 is classic keyword search (matches exact terms); *hybrid* runs it
  alongside dense retrieval and fuses the two rankings (Reciprocal Rank Fusion), covering meaning and terms at once.
- **source-scoped router** — a per-query filter that retrieves within a single named document; fires only on
  single-document questions, so it can only narrow, never evict a passing row elsewhere.
- **cold start** — the delay when a spun-down free-tier container wakes for its first request (~30–60 s here); warm requests are fast.

**Evaluation**
- **RAGAS** — a library that uses an LLM to score your answers.
- **the five metrics** — *faithfulness* (is every claim supported by the retrieved text — did the model make
  anything up); *answer relevancy* (does the answer address the question); *context precision* (of the chunks
  retrieved, how many were relevant); *context recall* (did retrieval surface the answer chunk); *answer
  correctness* (does the answer match a hand-verified reference — catches faithful-but-wrong).
- **the correctness judge** — the LLM RAGAS uses to score answer correctness; because it's itself a model, it
  can score a byte-identical answer differently across runs.
- **0–1 score scale** — RAGAS metrics are 0–1 scores, not percentages; because the hand-written references are
  terse, a correct-but-elaborate answer is marked down, so a 0.57 can be a good system.
- **`system_fingerprint`** — OpenAI's identifier for the backend configuration that served your request; it
  changes when they change the infrastructure underneath you.
- **like-for-like** — comparing two variants row-by-row under the *same* generation fingerprint (interleaved),
  so a difference is the change and not backend drift.
- **promotion bar** — the pre-registered gate a change must clear to ship, specified *asymmetrically*: no metric
  regresses beyond −0.03, improvements are unbounded.
- **noise floor (±0.03)** — a single per-row or aggregate correctness move under ~±0.03 is treated as noise, not signal.

**Method**
- **pre-registration** — writing a falsifiable prediction to a committed file *before* running the thing that
  tests it, so it can't become a rationalization after the fact.
- **rank probe** — a read-only check of where a known answer chunk lands under some retrieval scheme; no
  generation, no scoring, nearly free.
- **wire-smoke** — a request against the *running* service (the live URL) rather than the code; the only thing
  that proves the deployed artifact actually works.

**Domain**
- **OSHA / NIOSH / EPA** — three different U.S. federal agencies; they publish different numbers for the same
  chemical, which is why a cross-agency comparison is a hard retrieval target.
- **IDLH / PEL / REL** — immediately dangerous to life or health; OSHA's legal (permissible) exposure limit;
  NIOSH's recommended exposure limit.
- **RMP** — the EPA's Risk Management Program (sets threshold quantities and toxic endpoints for hazardous chemicals).
- **SDS** — safety data sheet.

**Latency**
- **P50 / P95** — the median and the slow-tail 95th percentile.

## Canonical figures and their comparators (source of truth: `eval/METRICS_HISTORY.md`)
answer_correctness unless noted. **Never juxtapose numbers from different comparators as a delta.**

| figure | what it is | comparator / note |
|--:|---|---|
| 0.4042 | v1 — dense + `fixed_500_50` + k=5 | Phase-1 baseline (the "0.40" low end; v0 was not_measured) |
| 0.5152 | v2 — semantic chunks, k=5 | vs v1 |
| 0.5128 | v3 — generation prompt; chunks/ns unchanged | flat vs v2 (−0.0024) |
| 0.5667 | v4 — semantic + k=10 (**canonical v4**) | Phase-1 arc endpoint (the "0.57") |
| 0.5932 | shipped `/ask` (`semantic_v2`) | **like-for-like v2 arm; comparator is the fresh-v4 re-run 0.5831, Δ +0.0101 — NOT the canonical 0.5667** |
| 0.6305 | shipped `/ask/agent` (source-scoped router) | full-28 agent eval |
| 0.7143 → 0.97 | faithfulness v1 → v4 | the faithfulness arc |
| ±0.03 | single-move noise floor (correctness) | below this, treat as noise not signal |
| n=28 | hand-written eval pairs | 1 row flip ≈ 1/28 ≈ 0.036 > the ±0.03 floor |

Rule of thumb: the arc is **v1 → v4 (0.40 → 0.57)**; the **shipped** numbers (0.5932, 0.6305) are
reported as labelled absolutes and never sequenced against the canonical v4 (they were measured against
different baselines — see the ledger's disambiguation).


## Link graph
- **Target repo:** `rag-pipeline` (so `../eval/…` relative links are correct once an article is committed).
- **Entry point / reading order:** `evaluation-first-rag.md` (build) → `falsification-and-diagnosis.md`
  (diagnosis) → `article-b-shipping-the-fix.md` (fix) → `article-c-how-not-to-fool-yourself.md`
  (methodology) → `the-cost-of-rigor.md` (cost companion).
- **Rule:** every piece links the repository and cites `eval/METRICS_HISTORY.md` for its numbers.
- **Link surface (settled decision):** all cross-references use **repository-relative** links
  (`../eval/…`, `../scripts/…`, `../.github/…`). The **repo is the primary reading surface** — GitHub renders
  them and the citation/link guard validates them. **Consequence:** cross-posting to an external platform
  (dev.to / Medium / Substack / self-hosted) breaks them, and that is a known future **link-rewriting task**,
  not a surprise. (Absolute GitHub URLs would survive publication but fall outside the guard's scope —
  `http(s)` is skipped — trading the check for portability not needed while the repo is the surface.)
- The five drafts and `the-cost-of-rigor.md` now **commit together**, so every cross-link — including the cost
  article's two previously-dead links — resolves at set-commit; no article ships a dead relative link.
