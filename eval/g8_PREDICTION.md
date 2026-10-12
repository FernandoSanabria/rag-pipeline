# G8 — Cost control, measured: pre-registration

This file is committed **before** any G8 code, as the first commit on the G8 branch. Outcomes are **appended** below
in later commits; the predictions are never edited. The companion design is [`g8_design.md`](g8_design.md). Recorded
2026-10-12.

**Evidence chain.** The PR is squash-merged, so this commit gets an annotated `prereg/g8` tag naming the PR before the
push.

**Rows are 1-based.** **HARD STOP: 2026-10-17.** Anything unmeasured by then is NOT TESTED.

**Each prediction states a hypothesis, its falsifier, and my forecast.** Where the forecast is FALSIFIED, the expected
failure is still a measurement.

## Shared setup
- **The judge** is `run_eval.py`'s (`gpt-4o-mini`), one sample at a time, with faithfulness and answer correctness.
- **The G10b review trigger is switched off for the eval passes,** as in G11, so rows 9–11 reach generation.
- **Counts come with N.** Verdicts are HOLDS, FALSIFIED or NOT TESTED, each with a reason.
- **Pass A, accounting.** Routing OFF, nothing memoized.
  - 28 rows × 3 trials through `api.main._answer` over `agent.graph.ask`, trial-major, so a row's repeats are a few
    minutes apart, as in G11.
  - **Independent captures,** for comparison with each request's cost record: `get_openai_callback` around the same
    request (OpenAI chat usage), and a counting embedder (embedding calls).
  - Measures **P1 and P2**.
- **Pass B, routing.** Two arms interleaved row by row, with which goes first alternating: **baseline** (routing OFF)
  and **routed** (routing ON, policy A, Nova Micro on eligible rows).
  - The query embedding, the router decision and the tool decision are memoized and shared across arms. 28 rows × 3
    trials.
  - **Hashing:** each OpenAI generation request body is hashed (sha256) by a recording transport.
  - **The judge** scores the eligible rows in both arms.
  - Measures **P3, P4, P5 and P7(b)**.
- **P6.** Row 25 under the `/ask` path's contexts (unscoped `dense_search` at k=10). Nova Micro runs through
  `generate_with(…, model_id="us.amazon.nova-micro-v1:0")`, with `gpt-4o-mini` as the control, 3 trials each.
- **Bedrock** is held at 15 RPM or less, backs off on throttling, and counts throttles.
- **Prices** are `agent/cost.py`'s `PRICES`, which equal [`COST_LEDGER.md`](COST_LEDGER.md)'s list prices:
  - `gpt-4o-mini`: $0.15 / 1M input, $0.075 / 1M cached input, $0.60 / 1M output;
  - `text-embedding-3-small`: $0.02 / 1M;
  - Nova Micro: $0.035 / 1M input, $0.00875 / 1M cache read, $0.14 / 1M output.

## P1 — Accounting
**Hypothesis.** On all 84 Pass-A requests:
- (a) the record's total equals the sum of its components, in tokens and in USD;
- (b) its OpenAI chat tokens (input, cached, output) equal the independent callback's for the same request;
- (c) its embedding calls equal the counting embedder's;
- (d) its USD equals the ledger's list prices × those tokens.

**Falsified by:** any request off by more than 1% on any of (a)–(d).

**Forecast:** HOLDS.

## P2 — The realized prompt-cache rate
**Hypothesis.** Across `gpt-4o-mini`'s calls in Pass A (router, tool decision and generation), cached input is at least
**40%** of input tokens on trials 2 and 3. G11's generation calls were 95.7% and 98.8%.
- Recorded per call site and per trial.
- **The router is expected near 0%:** its prompt, about 940 tokens, sits under OpenAI's documented 1,024-token
  minimum.

**Falsified by:** below 20% on trial 2 or trial 3.

**Forecast:** HOLDS. **This is a property of reruns minutes apart, not of traffic.**

## P3 — The policy's fire set
**Hypothesis.**
- In the routed arm, policy A routes exactly rows **{22, 23, 24, 25}**, in 3/3 trials each, and 0 of the 72 other
  row-trials.
- The baseline arm routes 0/84.
- **The smoke effect:** row 24, the only `/ask/agent` smoke row, is routed 3/3, and its retrieved pages are identical
  across the arms.

**Falsified by:** any row off the set, in either direction, or row 24's pages differing across the arms.

**Forecast:** HOLDS.

## P4 — Routed quality (G11's rule)
**Hypothesis.** On the eligible rows 22–25, Nova Micro against `gpt-4o-mini`, for faithfulness and for answer
correctness:
- **Per row:** no drop. A row-metric is FALSIFIED only if the Nova Micro mean lies **below** the `gpt-4o-mini` arm's
  three-trial minimum **and** more than **0.03** below its mean.
- **At the aggregate** (the mean of the 4 rows): Nova Micro is at least `gpt-4o-mini` − 0.03 on each metric.

**Falsified by:** as stated, per row and at the aggregate.

**Forecast: FALSIFIED per row,** on at least 1 of the 8 row-metrics.
- **Why:** G11's Nova 2 Lite fell on 2 of these 8 (row 23 faithfulness, row 24 correctness), and Nova Micro is the
  smaller model.
- **At the aggregate,** HOLDS on both metrics, with low confidence on faithfulness.
- **The sample is small:** 4 rows × 3 trials per arm.

## P5 — The cost delta
**Hypothesis**, from G11's token profile and the prices above:

| delta | baseline → routed | change |
|---|--:|--:|
| per routed request (rows 22–25), generation | $0.00087 → $0.00020 | −77% |
| per routed request, the whole request (router, tool decision, generation, embedding) | $0.00193 → $0.00126 | −35% |
| across the 28 under policy A, per pass, at list price | $0.0734 → $0.0707 | −3.7% |
| across the 28, as billed (cached tokens at the cached rate) | | −2.8% |

**The method.**
- Request totals come from Pass A's records (the mean of 3 trials per row).
- Generation costs per arm come from Pass B.
- List price counts every input token at the uncached rate. As billed applies the cached rate to cached tokens, so the
  eval's rerun cache shows separately.

**Falsified by:** any measured delta off its prediction by more than 2×, i.e. a measured-to-predicted ratio outside
[0.5, 2].

**Forecast:** HOLDS.

## P6 — Refusal on the cheap arm
**Hypothesis.** Nova Micro returns the **exact** refusal sentence ("The provided context does not contain the answer.")
on row 25 under the `/ask` path's contexts, in 3/3 trials. The control, `gpt-4o-mini`, is expected at 3/3. The gate is
`api.confidence.is_refusal`, which tests whole-answer equality.

**Falsified by:** any Nova Micro trial that is not the exact sentence.

**Forecast: FALSIFIED,** on G11's Nova 2 Lite result in the same family (0/3).

## P7 — Pass-through
**Hypothesis.**
- **(a) Routing OFF, the default, hermetic:** the generation request body is byte-identical to today's on all 28
  questions, across the frozen generator, the seam and `generate_node`.
- **(b) Routing ON, live:** on the 24 non-eligible rows × 3 trials, the routed arm's generation request body is
  byte-identical (sha256) to the baseline arm's: 72/72.

**Falsified by:** any difference.

**Forecast:** HOLDS.

## P8 — The shipping rule
**The rule.**
- **Cost accounting ships ON.** It is observability.
- **Routing ships OFF by default,** a documented, measured option. It turns on only if P4 and P6 both HOLD on the
  eligible set **and** the cost delta **across the 28 under the policy** is at least 30% (the GATE 1 ruling).
- **Even then, turning it on is a stop-and-ask:** it would change the G9 smoke snapshot (row 24's generation), and
  Render would need AWS keys it does not have.

**Forecast: routing stays OFF on cost, regardless of quality.** Under policy A, the delta across the 28 is about 3.7%,
below 30%. The run is therefore a pure measurement of P4 and P6 for the cheap arm, and both are forecast FALSIFIED.

## Spend
About $0.45 of the $3.00:
- Pass A: about $0.15–0.22 (84 unmemoized requests);
- Pass B: about $0.20 (decisions memoized; 24 judged answers);
- P6: about $0.01;
- a plumbing check before the run: about $0.02, disclosed and discarded.

Wall time is about 30 minutes.

## Outcome (recorded 2026-10-12)
**P1, P2, P3, P5 and P7 HOLD. P4 is FALSIFIED: the per-row forecast held, and the aggregate forecast of HOLDS missed on
both metrics, a forecast error. P6 is FALSIFIED, as forecast. P8: accounting ships ON and routing ships OFF; the delta
across the 28 is 3.6%.** (GATE 2 rulings, 2026-10-12.)

The predictions above are unchanged; this section only appends. Rows are 1-based; times are UTC.

**Run.** One process of [`cost_eval.py`](../scripts/cost_eval.py), 2026-10-12 15:36–15:50 UTC (run
`20261012T153609Z`). The runner was committed unchanged after the run.
- **The G8 build:** `bdd85c9`, tagged `evidence/g8-build`.
- **The design, in full:**
  - Pass A, 84 requests;
  - Pass B, 168 requests (84 per arm);
  - P6, 6 calls;
  - the judge, 24 answers (the eligible rows in both arms).
- **Clean:** 0 Bedrock errors or throttles. The memo made 28 router calls, 30 tool decisions and 28 query embeddings.
- **The G10b review trigger was switched off for the run,** as in G11.
- **Generation:** `gpt-4o-mini-2024-07-18` on `fp_0961e0f740` for 236 calls and on `fp_412505125b` for 7. The judge
  made 120 calls across four fingerprints.
- **A plumbing check ran first and was discarded:** rows 9 and 22, one trial; $0.014.
- **Derived metrics:** [`cost_metrics.json`](cost_metrics.json). Raw answers and contexts stay in the gitignored
  `eval/results/`.

### P1 — HOLDS: 84/84
Every Pass-A request's record matched the independent captures:
- (a) the sum of its components: worst relative error 0;
- (b) `get_openai_callback`'s input, cached and output tokens: 0;
- (c) the counting embedder's calls: 0;
- (d) the ledger's list prices: 2.1e-16.

0 calls were unpriced.

### P2 — HOLDS: 91.0% and 89.4%
| trial | router | tool decision | generation | all `gpt-4o-mini` calls |
|---|--:|--:|--:|--:|
| 1 | 0.0% | 10.5% | 6.5% | 37,504 / 462,083 = **8.1%** |
| 2 | 0.0% | 94.4% | 98.7% | 426,496 / 468,497 = **91.0%** |
| 3 | 0.0% | 91.4% | 98.4% | 412,544 / 461,511 = **89.4%** |

- **The router stays at 0%:** its prompt, a p50 of 934 tokens, sits under OpenAI's documented 1,024-token minimum for
  caching.
- **This measures reruns** of the same prompt minutes apart, **not traffic.**

### P3 — HOLDS: exactly {22, 23, 24, 25}
- The routed arm routed rows 22–25 in 3/3 trials each, and 0 of the 72 other row-trials.
- The baseline arm routed 0/84.
- Row 24's retrieved pages were identical across the arms in 3/3 trials.

### P4 — FALSIFIED per row and at the aggregate
| row | metric | `gpt-4o-mini` (3) | Nova Micro (3) | Δ of means | verdict |
|--:|---|---|---|--:|---|
| 22 | faithfulness | 0.67 0.67 0.67 | 0.67 0.50 0.67 | -0.056 | **FALSIFIED** |
| 23 | faithfulness | 1.00 1.00 1.00 | 1.00 1.00 1.00 | +0.000 | holds |
| 24 | faithfulness | 1.00 1.00 1.00 | 1.00 0.67 0.67 | -0.222 | **FALSIFIED** |
| 25 | faithfulness | 1.00 1.00 1.00 | 1.00 1.00 1.00 | +0.000 | holds |
| 22 | correctness | 0.77 0.77 0.77 | 0.79 0.56 0.79 | -0.060 | **FALSIFIED** |
| 23 | correctness | 0.90 0.90 0.75 | 0.76 0.76 0.76 | -0.096 | holds |
| 24 | correctness | 0.66 0.91 0.91 | 0.77 0.78 0.78 | -0.053 | holds |
| 25 | correctness | 0.72 0.72 0.72 | 0.66 0.66 0.66 | -0.052 | **FALSIFIED** |

- **Per row:** 4 of the 8 row-metrics fell (faithfulness on rows 22 and 24, correctness on rows 22 and 25).
- **Near misses:** row 23's correctness held only because the Nova Micro mean (0.759) sits just above the
  `gpt-4o-mini` minimum (0.754).
- **At the aggregate,** the mean of the 4 rows:
  - faithfulness 0.9167 → 0.8472
    (**-0.0694**);
  - answer correctness 0.7926 → 0.7274
    (**-0.0652**);
  - both below the −0.03 floor.
- **The forecast error.** The forecast was "FALSIFIED per row, on at least 1 of the 8; HOLDS at the aggregate". The
  per-row clause held. The aggregate clause missed on both metrics, at −0.069 and −0.065. This is recorded as a
  forecast error.

**Post-hoc readings** (from the raw answers, no model calls; **not part of any verdict**). The value is correct in all
12 routed answers.
- **Row 24: an unsupported attribution.** Nova Micro cites `sds-nutrien-anhydrous-ammonia page=9` in 3/3 trials, a page
  that isn't in its context. The boiling point is on page 6, which `gpt-4o-mini` cites.
- **Row 22 runs the other way.**
  - The pH value is on page 4. Nova Micro cites page 4 in 2 of 3 trials.
  - `gpt-4o-mini`, the shipped generator, cites `page=5` in 3/3 trials, a page that isn't in its context. That is
    recorded as an inline-misattribution instance in [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).
  - The routed drop comes from Nova Micro's third trial, which claims "page 5" in its prose (faithfulness 0.50).
- **Row 25: no content difference.** Both arms give the same value and method, with faithfulness 1.00 in all six
  answers. The correctness gap (0.665 against 0.717) is the judge reacting to wording.

### P5 — HOLDS: every delta within 0.97× of its prediction
| delta | predicted | measured | ratio |
|---|--:|--:|--:|
| generation, per routed answer | -77.0% | -74.95% | 0.97 |
| the whole routed request | -35.0% | -34.01% | 0.97 |
| across the 28, at list price | -3.7% | -3.58% | 0.97 |
| across the 28, as billed | -2.8% | -2.72% | 0.97 |

**Generation per routed answer** fell from $0.000871 to
$0.000218, a 4.0× cheaper model. A pass of the 28 costs
$0.0729 at list price and $0.0510
as billed.

### P6 — FALSIFIED: Nova Micro 0/3
Row 25 under the `/ask` path's contexts, byte-identical to G11's P6 contexts by sha256.
- **`gpt-4o-mini`, 3/3, byte-identical:** "The provided context does not contain the answer."
- **Nova Micro, 0/3.** None leaked a value. Trial 1, verbatim:

```text
The provided context does not contain the flash point of acetone per the Sigma-Aldrich SDS. The flash point information is not present in the given context. 

Source_doc_id: [sds-sigma-aldrich-acetone page=1]
```

**Trial 2, verbatim** (trial 3 is byte-identical):

```text
The provided context does not contain the flash point of acetone per the Sigma-Aldrich SDS. The flash point information is not present in the given context. 

The exact refusal sentence is: "The provided context does not contain the answer."
```

- **How these would be served:** checked with the run's contexts, all three pass the output guard and are served at
  0.9 "high", with every retrieved page cited.
- **The pattern:** this is the confidence-layer limitation G11 recorded, now with a second hedge shape: the instruction
  is echoed instead of followed. See [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).

### P7 — HOLDS: 28/28 hermetic, 72/72 live
- **(a) Hermetic** (`tests/test_routing.py`): with routing OFF, all 28 bodies are identical to the frozen generator's.
  With it ON, the 24 non-eligible rows are identical, and the 4 eligible rows send nothing to OpenAI.
- **(b) Live:** on the 24 non-eligible rows × 3 trials, the routed arm's generation request body was byte-identical
  (sha256) to the baseline arm's in 72/72.

### P8 — accounting ON, routing OFF
- **The cost condition fails on its own:** the delta across the 28 is 3.6%, below 30%.
- **Quality and refusal also fail:** P4 and P6.
- `ROUTING_ENABLED` stays `False`.

### The measured per-request bill (Pass A, N=84, at list price)
| component | per pass of 28 | share | p50 per call |
|---|--:|--:|---|
| tool decision | $0.03548 | 48.7% | 8,077 tokens in / 46 out (4 of 84 requests made 2 calls) |
| generation | $0.03322 | 45.6% | 7,505 in / 91 out |
| router | $0.00417 | 5.7% | 934 in / 13 out |
| query embedding | $0.00001 | under 0.1% | — |
| **total** | **$0.0729** (R1 estimated $0.0734) | | |

A request's p50 is **$0.00259 at list price** and $0.00168 as billed, with the eval's rerun cache. The range is
$0.00148–$0.00472.

### Supplementary (not registered)
- **Latency** on the eligible rows, 12 calls per arm, timed after the run on its stored contexts
  ([`cost_latency.json`](cost_latency.json)):
  - Nova Micro: p50 **0.67 s** (0.54–1.18), with Bedrock's `latencyMs` p50 at
    496 ms;
  - `gpt-4o-mini`: p50 **1.04 s** (0.91–1.89);
  - the design expected about 0.8 s for Nova Micro.
- **The tool-decision pre-filter (R7), re-measured:** the skipped rows' tool decisions cost $0.0294 of $0.0729 per pass,
  **40.3%** (the estimate was 39.7%).
- **Nova Micro's tokenizer** counts 6–8% more input tokens than `gpt-4o-mini`'s on the same prompts (rows 22–25).

### Hours and spend
- **Hours:** about 0.9 h from the G8 spec to GATE 2 (around 15:05–15:58 UTC), inside build-day 1 of 3. The run took 15
  min.
- **Spend:** about **$0.34** of the $3.00, from token counts at list price:
  - the run, $0.315:
    - Pass A $0.153 as billed;
    - Pass B generation $0.107;
    - Pass B decisions $0.023;
    - the judge $0.029;
    - P6 $0.003;
  - the plumbing check, $0.014;
  - the latency timing, about $0.01.
- **The breakdown** is in section I of [`COST_LEDGER.md`](COST_LEDGER.md).
