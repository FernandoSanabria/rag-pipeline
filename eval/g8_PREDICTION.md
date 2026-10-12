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
