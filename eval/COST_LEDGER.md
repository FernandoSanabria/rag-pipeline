# Cost ledger — OpenAI + LangSmith spend for the `equip-docs-rag` pipeline

Metrics only. **No answer or context text** (same licensing discipline as
`eval/likeforlike_perrow_metrics.json`). This file is the checkable backing for the cost figures in
`blog/the-cost-of-rigor.md`.

Every figure is tagged **[m]** measured (read from a dashboard) or **[d]** derived (computed from a
measured token count × a published list price). List prices used for derivations:

| model | input | cached input | output |
|---|--:|--:|--:|
| `gpt-4o-mini` | $0.15 / 1M | $0.075 / 1M | $0.60 / 1M |
| `text-embedding-3-small` | $0.02 / 1M | — | — |
| `text-embedding-ada-002` | $0.10 / 1M | — | — |

## Provenance
- **OpenAI usage dashboard** — read 2026-08-08, range 2026-07-01 → 2026-08-08. Gives totals and
  per-model / per-token-type cost. It does **not** split spend by function (generation vs judging).
- **LangSmith** — project `equip-docs-rag` (`LANGCHAIN_TRACING_V2=true`, `LANGCHAIN_PROJECT` in
  `.env`). Traces every completion call and tags it by function; the generation-vs-judging split is
  computed there.
- **On-disk** — `eval/results/*.json` carry `generation_backends.n_calls` (generation calls only,
  from the v4 pipeline onward). Gitignored (RAGAS scores only); counts summarized here.

---

## A. Build-phase snapshot — as of ~2026-07-13 (the article's basis)
The state when `blog/the-cost-of-rigor.md` was written, after the v0–v4 baseline sweep (~14 passes).

> These are the author's dashboard / LangSmith readings **at that time**. Today's live dashboard is
> cumulative through 2026-08-08 (§B), so §A is re-verifiable only by filtering the dashboard back to
> the 2026-07-01 → 2026-07-13 window.

| item | value | flag |
|---|--:|:--|
| Total OpenAI spend | ~$1.53 | m |
| — chat completions (`gpt-4o-mini`) | ~$1.25 | d |
| — embeddings | ~$0.28 | d |
| Embedding tokens | ~11.05M | m |
| `gpt-4o-mini` calls (LangSmith-traced) | 5,204 | m |
| — generation | 315 | m |
| — RAGAS judging | 4,889 | d (5,204 − 315) |
| call share — judging / generation | 94% / 6% | d |
| token share — judging / generation | ~82% / 18% | m |
| billed chat requests (OpenAI) | ~3,766 | m |
| avg tokens per answer | ~4,600 | m |
| eval passes | ~14 | counted (run artifacts) |
| chat cost per full pass | ~$0.09 | d ($1.25 ÷ ~14) |

**Call-count reconciliation (three sources, slightly different populations).** How many answers got
*generated* reads three ways, and they don't quite agree:

| source | count | what it counts |
|---|--:|:--|
| on-disk `generation_backends.n_calls` | 280 | generation only, **v4 pipeline onward** (misses earlier runs) |
| LangSmith-traced generation | 315 | all pipelines + read-only probe generations + retries |
| question-evaluations | ~310 | 28 questions × the passes |

None is wrong — they count different populations. The counter can't see pre-v4 generations
(280 < 315); LangSmith additionally captures probe/retry generations that never became a scored row
(315 vs ~310). The ~30-call gap is not attributed call-for-call; at this scale it didn't need to be.

---

## B. Cumulative — through 2026-08-08 (after Phase-2 evaluation work)
Same corpus and pipeline, more evaluation. Chat ~2× the snapshot (more eval runs); embeddings ~flat
(the corpus is embedded once). Fully reconciled against the 2026-08-08 dashboard.

| item | value | flag |
|---|--:|:--|
| Total OpenAI spend | ~$3.00 | m |
| — chat completions (`gpt-4o-mini`) | ~$2.77 | d (sum of buckets) |
| &nbsp;&nbsp;· cached input | $0.371 | m |
| &nbsp;&nbsp;· input | $1.891 | m |
| &nbsp;&nbsp;· output | $0.507 | m |
| — embeddings | ~$0.29 | d (sum of buckets) |
| &nbsp;&nbsp;· `text-embedding-3-small` | $0.231 | m |
| &nbsp;&nbsp;· `text-embedding-ada-002` (RAGAS fallback) | $0.058 | m |
| chat input tokens | ~17.55M (of which ~4.95M cached) | d |
| chat output tokens | ~0.85M | d |
| embedding tokens | ~12.1M | m |
| chat requests (OpenAI) | 7,322 | m |
| embedding requests (OpenAI) | 6,528 | m |
| monthly budget (personal) | $100 (Aug-MTD $1.12) | m |

Token derivation (why the buckets back out cleanly): input $1.891 ÷ $0.15/1M = 12.6M uncached +
cached $0.371 ÷ $0.075/1M = 4.95M → ~17.55M input (matches the endpoint card); output $0.507 ÷
$0.60/1M = 0.85M. Chat $2.77 + embeddings $0.29 = $3.06, rounding to the dashboard's $3.00.

---

## Per-question RAGAS fan-out [m]
Counted from one `ragas evaluation` trace (LangSmith project `equip-docs-rag`, run 2026-08-02), `row 0`
= the question *"What is the RMP threshold quantity for anhydrous ammonia?"* (reference "10,000
pounds"; `retrieved_contexts` = 10 items → k=10). `gpt-4o-mini` calls per metric:

| metric | calls | sub-runs observed |
|---|--:|:--|
| context_precision | 10 | one `context_precision_prompt` per retrieved context (k=10) |
| answer_correctness | 3 | 2× `statement_generator` (answer + reference) + `correctness_classifier` (+ `answer_similarity`, embeddings, no chat call) |
| faithfulness | 2 | `statement_generator_prompt` + `n_l_i_statement_prompt` |
| answer_relevancy | 1 | `response_relevance_prompt` |
| context_recall | 1 | `context_recall_classification` |
| **total** | **17** | one answer, one pass (project average ≈ 16) |

Structural, so representative across runs — the July build-phase traces have aged out of the 14-day
retention; this is a later run of the identical RAGAS 0.4.3 metric set.

---

## Notes
- The **generation-vs-judging split** (§A) is the load-bearing figure for the article's "94% of calls
  were the harness judging" finding. It is computed in LangSmith; the OpenAI dashboard cannot see it
  (it bills tokens, not functions).
- The finding is **directionally corroborated** in the cumulative data: on the 2026-07-08 eval cluster
  LangSmith shows ~282 `generate` runs against ~3,130 successful LLM calls — ~91% non-generation.
- **Calls vs. bill.** 94% is a share of *calls*; because cost tracks tokens, the *bill* share of
  judging is nearer ~82% (token-weighted). Both point the same way.
- **Retries inflate traced vs. billed.** LangSmith counts attempts (incl. rate-limited 429 retries);
  OpenAI bills successes. That is the 5,204-traced vs. ~3,766-billed gap in §A, and it is visible in
  the cumulative error volume (2026-07-08: ~1.24K errored calls atop ~3.13K successes).

---

## C. G1 closure (2026-09-22 → 09-23) [d, estimated from call counts — not yet dashboard-reconciled]
Spend for the G1-closure controls (Q1 like-for-like, Q2 row-8 3-arm, Q3 acetone) plus the §1 probes.
All `gpt-4o-mini` + `text-embedding-3-small`; no new corpus embedding. Call counts are counted from the
run scripts; cost is **derived** at list price (≈1.5K input / 0.2K output tokens per judge call) and is an
**estimate pending a dashboard read**, not a measured figure.

| item | ~calls | flag |
|---|--:|:--|
| §1 probes (trace re-run, N=4 variance, retrieval probes) | ~40 chat + ~8 embed | d |
| Q2 generation (row 8, 20 invokes) | ~70 | d |
| Q2 sequential answer_correctness (the scoring that worked) | ~90 | d |
| Q1 generation (28×2 invokes) | ~180 | d |
| Q1 scoring (2nd batch — relevancy+recall usable, 3 metrics NaN) | ~900 | d |
| Q3 (acetone, 10 invokes + 1 router) | ~31 | d |
| **subtotal (useful work)** | **~1,320 chat** | d |
| **WASTE — RAGAS scoring failures re-run** | | |
| — Q2 batch score attempt 1 (timeout storm, n=6/10) | ~320 | d |
| — Q2 rescore batch attempt 2 (all-NaN) | ~120 | d |
| — Q1 batch score attempt 1 (scored then LOST to a print crash) | ~900 | d |
| **waste subtotal** | **~1,340 chat** | d |
| **total** | **~2,700 chat + ~8 embed** | d |
| **estimated cost** | **~$0.90** (≈$0.6 input + ≈$0.3 output) | d |

**Waste finding:** the RAGAS multi-step judge (faithfulness / context_precision / answer_correctness)
failed repeatedly under batch concurrency this session (timeouts / all-NaN), and one crashed batch was
scored then lost to a formatting bug before save. The failed/lost scorings (~1,340 calls, ~half the spend)
roughly **doubled** the closure cost. Sequential single-sample scoring was the only reliable path for
answer_correctness. Mitigation for next time: score sequentially (or save raw per-sample scores before any
aggregation), and treat batch RAGAS as best-effort.

## D. G6 guardrails (2026-10-04) [d, from measured token counts × list price; not dashboard-reconciled]
Token counts are measured per run by LangChain's OpenAI callback in `scripts/guardrail_eval.py`. Cost is
**derived** at the list prices above. There was no RAGAS judging.

| item | chat requests | prompt tokens (cached) | completion tokens | cost | flag |
|---|--:|--:|--:|--:|:--|
| design probes (acetone at CAP=0, ×3) | — | — | — | ~$0.003 | d (estimate) |
| v1 run — P1, input guard, frozen 28 × 3 | 84 | 37,095 (0) | 507 | $0.0059 | d |
| v1 run — P2, input guard, guardrail set × 3 | 90 | 39,429 (0) | 591 | $0.0063 | d |
| v1 run — P3, output guard, frozen 28 × 3 × 2 endpoints | 339 | 2,026,401 (1,475,840) | 25,058 | $0.2083 | d |
| v1 run — P3b, capability set × 3 × 2 endpoints | 45 | 219,009 (160,256) | 2,868 | $0.0226 | d |
| v1 run — P4, acetone at CAP=0 × 5 | 10 | 25,635 (20,480) | 155 | $0.0024 | d |
| v1 run — P5, pass-through, frozen 28 × 3 pairs | 249 | 1,305,234 (1,220,352) | 17,117 | $0.1145 | d |
| v1 run — embeddings | 31 calls | 613 tokens | — | <$0.0001 | d |
| v2 run — P1′ | 84 | 43,647 (0) | 507 | $0.0069 | d |
| v2 run — P2′ (45 rows) | 129 | 66,939 (0) | 834 | $0.0105 | d |
| **total** | **1,030** | | | **~$0.38** of the $2.00 budget | d |

**Cache note.** Memoizing the query embedding made the repeated trials of a question send byte-identical
generation prompts. OpenAI's prompt cache therefore served 79% of the v1 run's prompt tokens (2,876,928 of
3,652,803) at the cached price.

## E. G12 parallel fan-out (2026-10-08) [d, from measured token counts × list price; not dashboard-reconciled]
| item | chat requests | prompt tokens (cached) | completion tokens | cost | flag |
|---|--:|--:|--:|--:|:--|
| R2 decomposer probe (28 rows × 3) | 84 | 75,399 (—) | 1,461 | $0.0122 | d |
| R2b retrievals and the warning check | 2 | — | — | <$0.001 | d (estimate) |
| run — P1, four rows × 3 × 2 arms | 96 | 653,076 (436,352) | 7,799 | $0.0699 | d |
| run — P3, RAGAS scoring of the 24 P1 answers | 480 | 925,944 (598,656) | 45,177 | $0.1211 | d |
| run — P4, injected failing branch, 12 runs | 54 | 436,362 (287,744) | 4,434 | $0.0465 | d |
| run — P5, 24 rows × 3 × 2 arms, generation stubbed | 48 | 212,198 (0) | 1,998 | $0.0330 | d |
| run — query embeddings | 84 calls | 1,451 tokens | — | <$0.0001 | d |
| **total** | **764** | | | **~$0.28** of the $3.00 budget | d |

RAGAS answer-correctness embedding calls are not counted; they are negligible at this list price.

## F. G9 smoke evaluation in CI (2026-10-08 → 10-09) [d, from measured token counts × list price; not dashboard-reconciled]
**Sources:** each CI run's report artifact (its `spend` block) and the local runs' printed spend. The run numbers are
`eval-smoke` runs; see METRICS_HISTORY's G9 block.

| item | chat requests | prompt tokens (cached) | completion tokens | embedding tokens | cost | flag |
|---|--:|--:|--:|--:|--:|:--|
| GATE 1 probes (8 embeddings, 8 Pinecone queries) | — | — | — | — | ~$0.0001 | d (estimate) |
| local dev run, baseline mode (all 8 judged) | 50 | 156,657 (6,144) | 6,183 | 893 | $0.0268 | d |
| local dev run, gate mode | — | — | — | — | $0.0085 | d (printed total) |
| G9 baseline #2 (artifact lost; from its log) | — | — | — | — | $0.0163 | d (printed total) |
| G9 baseline #3 | 20 | 90,108 (85,376) | 2,190 | 441 | $0.0084 | d |
| G9 PR runs #5 and #13 | 65 | 237,963 (211,968) | 7,548 | 1,182 | $0.0244 | d |
| G9 characterization #6–#10 | 60 | 365,598 (347,136) | 4,133 | 904 | $0.0313 | d |
| G9 negative proof #11 | 20 | 75,872 (7,936) | 1,501 | 276 | $0.0117 | d |
| local regeneration of row 24 (24 attempts) | 72 | — | — | — | $0.0304 | d (printed total) |
| G9b local `rows=24` run (headers) | — | — | — | — | $0.0012 | d (printed total) |
| G9b PR run #14 | 15 | 77,730 (74,368) | 1,378 | 315 | $0.0069 | d |
| G9b characterization #15–#19 | 70 | 384,076 (349,184) | 5,922 | 1,301 | $0.0350 | d |
| G9b negative proof #20 | 10 | 59,723 (52,352) | 511 | 152 | $0.0053 | d |
| G9b diagnostics #21–#25 (`rows=24`) | 15 | 72,770 (67,840) | 490 | 85 | $0.0061 | d |
| **total** | | | | | **~$0.21** of the $3.00 budget | d |

**Not derived:**
- PR run #12 was cancelled by #13 (concurrency) and left no artifact. At most it would have been one cold run, about
  $0.016.
- PR runs #1 and #4 skipped before a snapshot existed, at no cost.

**What the cache is worth.** A fully cached run costs about $0.0056, generation only. A cold run (PR #5) cost $0.0164.

## G. G10b approval gate (2026-10-09) [d, from measured token counts × list price; not dashboard-reconciled]
**Source:** the `spend` block of `eval/review_metrics.json`.

| item | chat requests | prompt tokens (cached) | completion tokens | cost | flag |
|---|--:|--:|--:|--:|:--|
| GATE 1 probes (local files, installed packages, three documentation pages) | 0 | — | — | $0 | d |
| P1: 40 questions × 3, gate decision only (generation stubbed) | 253 | 1,077,193 (639,744) | 10,773 | $0.1201 | d |
| P2: rows 10 and 11 × 3, four paths plus an ungated arm | 16 | 76,057 (61,696) | 1,519 | $0.0077 | d |
| P4 and P5: 25 rows × 3 × 3 arms (generation stubbed, decisions memoized) | 51 | 224,954 (198,400) | 2,106 | $0.0201 | d |
| P3a: one live pause, a restart and a resume (a separate uvicorn process) | about 3 | — | — | <$0.002 | d (estimate; not counted) |
| **total** | | | | **~$0.15** of the $3.00 budget | d |

The G9 smoke runs on this PR's pushes are not included; their spend is in each run's artifact.
