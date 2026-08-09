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
