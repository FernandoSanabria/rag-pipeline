# G11 — The provider seam with AWS Bedrock (Nova 2 Lite): pre-registration

This file is committed **before** any seam code, as the first commit on the G11 branch. Outcomes are **appended**
below in later commits; the predictions are never edited. The companion design is [`g11_design.md`](g11_design.md).
Recorded 2026-10-12.

**Evidence chain.** The PR is squash-merged, so this commit gets an annotated `prereg/g11` tag naming the PR before the
push.

**Rows are 1-based.** **HARD STOP: 2026-10-17.** Anything unmeasured by then is NOT TESTED.

## Shared setup
**Two arms, one variable.** Generation is either `openai` (`gpt-4o-mini`, the frozen `src.generate.generate`) or
`bedrock` (`us.amazon.nova-2-lite-v1:0`, us-east-1, through the boto3 Converse adapter).
- **Shared by both arms:**
  - the agent graph;
  - retrieval (`semantic_v2`, k=10);
  - the router, tool decision and output guard;
  - the judge (`gpt-4o-mini`, `run_eval.py`'s RAGAS faithfulness and answer correctness, one sample at a time);
  - OpenAI embeddings.
- **Interleaving.** Both arms run row by row in one process, alternating which goes first.
- **Memoized and shared:** the query embedding, the router decision and the tool decision. The contexts handed to
  generation are therefore byte-identical, which is asserted and counted (P2).
- **N=3 trials** per arm per row.
- **Inference settings in both arms:** temperature 0, with `topP` and `maxTokens` unset. Bedrock has no seed.

**Recorded per call:**
- **openai:** the `system_fingerprint`;
- **bedrock:** the requested profile id, the AWS request id, `latencyMs`, `stopReason` and `serviceTier`;
- **both:** usage tokens and wall-clock latency.

**Cost** is computed from usage at **uncached list price** in both arms: Nova $0.33 / 1M input and $2.75 / 1M output;
`gpt-4o-mini` $0.15 and $0.60. The cached OpenAI figure is reported beside it.

**Bedrock is held at 15 RPM or less** (quota 20 RPM), backs off on `ThrottlingException`, and counts throttles.

**Counts with N.** Verdicts are HOLDS, FALSIFIED or NOT TESTED, each with a reason.

## P1 — Pass-through (hermetic)
**Prediction:** with `GENERATION_PROVIDER=openai`, the generation request body (model, messages, temperature, seed)
is byte-identical to today's on all 28 questions. Three paths are compared through a recording fake transport:
`src.generate.generate`, the seam, and the graph's `generate_node`.

**Falsified by:** any field differing.

## P2 — Context identity
**Prediction:** across **28 rows × 3 trials**, the contexts handed to generation are byte-identical across the two
arms.

**Falsified by:** any difference.

## P3 — Quality: non-inferiority, per row
**The rule.** For faithfulness and for answer correctness, per row: the OpenAI arm's three trials define its observed
[min, max]. The prediction is **no drop**.
- **FALSIFIED only if** the Bedrock mean lies **below** the OpenAI minimum **and** the difference of means exceeds
  **0.03**.
- **At the aggregate,** the prediction is Bedrock ≥ OpenAI − 0.03 on each metric (the noise floor).

**Why one-sided.** A two-sided "within the band" would falsify almost surely on a model swap, because different models
write different answers and the judge varies even on identical text (`METRICS_HISTORY.md`, methodology). What the
shipping rule (P7) needs is non-inferiority.

**Named rows:**
- **Row 9,** the G1 attention-effect row. The tool chunk is shared, so any difference is the model's attention.
  Predicted: no drop on answer correctness.
- **Row 21,** the misattribution row. OSHA Table Z-1's chlorine line is missing from both arms' context. Predicted: no
  drop on faithfulness. The answers' inline misattribution (the OSHA table's value cited to the NIOSH page) is counted
  per arm.

## P4 — Cost and latency
**Predicted:**
- **OpenAI arm:** about $0.0012 per answer at list price, and a p50 of about 1.7 s.
- **Nova arm:** about 8,000 input tokens (its tokenizer counts within ±25% of `o200k`) and about 120 output tokens, so
  **about $0.0030 per answer**, with a **p50 of about 1.2 s**.

**Falsified by:** Nova's measured p50 $ per answer outside **[$0.0015, $0.0060]**, or its p50 latency outside
**[0.6 s, 2.4 s]**.

## P5 — Citations and the output guard
**Prediction:**
- the share of answers citing at least one doc id (an exact marker or a bare id) is within ±2 rows per trial across
  arms;
- output-guard withholds are 0–1 per trial per arm.

**Falsified by:** withholds differing by more than 2 rows per trial, or 0 structured citations on a row that had them
in the OpenAI arm.

## P6 — Refusal behaviour
**The instrument** (corrected at GATE 1).
- **Why not the agent path:** on `/ask/agent`, no frozen row refuses (0/28 in the G1 closure's two arms, and 0/84 in
  G6's agent arm). Row 25 is source-scoped there and answers.
- **Where row 25 refuses:** on `/ask`, unscoped, 3/3 in G6.
- **So:** row 25 runs with the `/ask` path's unscoped contexts (`dense_search` at k=10, memoized embedding), fed to
  both providers' generation through the seam, 3 trials each.

**Prediction:** both arms return the **exact** refusal sentence ("The provided context does not contain the
answer.") in 3/3 trials. The frozen gate depends on whole-answer equality (`api.confidence.is_refusal`).

**Falsified by:** any Bedrock trial whose refusal is not the exact sentence.

**Recorded as well:** any agent-path refusal across the 28 rows in either arm. Predicted: 0.

## P7 — The shipping rule
**The rule.** The default provider stays `openai` unless Bedrock passes P3 on both metrics, passes P6, **and** costs
≤ 1.5× per answer.

**Otherwise** Bedrock ships as a configured, measured alternative:
- `GENERATION_PROVIDER=bedrock`, documented and off by default;
- no repository secrets;
- no `render.yaml` change.

**Refinement A.** This PR ships with `openai` as the default **in every outcome**. If the measurement would make Bedrock
the default, that is a stop-and-ask: the switch would change the G9 smoke snapshot, Render's configuration and the
secrets surface, each with its own gate.

**Predicted:** `openai` stays the default, on cost alone (about 2.5×).

## Spend
About $0.6 of the $5.00:
- 87 Bedrock calls at about $0.003;
- 87 OpenAI generations;
- about 171 judged answers at about $0.0013;
- the memoized router and tool calls.

Wall time is about 30 minutes.

## Outcome (recorded 2026-10-12)
**P1, P2 and P4 HOLD. P3 is FALSIFIED per row and HOLDS at the aggregate. P5 HOLDS on its registered falsifier. P6 is
FALSIFIED. P7's outcome HOLDS, and its "on cost alone" clause is FALSIFIED.** The default stays `openai`. Bedrock ships
configured, off by default and **not recommended**, with P6 named as the blocker (GATE 2 rulings, 2026-10-12).

The predictions above are unchanged; this section only appends. Rows are 1-based; times are UTC.

**Run.** One process of [`provider_eval.py`](../scripts/provider_eval.py), 2026-10-12 13:31–14:06 UTC (run
`20261012T133104Z`). The runner was committed unchanged after the run.
- **The G11 build:** `144e26b`, tagged `evidence/g11-build`.
- **The design, in full:** 28 rows × 3 trials × 2 arms through `api.main._answer` over `agent.graph.ask`, then P6 at
  3 trials per arm.
- **Generation:**
  - `gpt-4o-mini-2024-07-18` on `fp_0961e0f740` for 83 of the 84 agent-path calls, and on `fp_412505125b` for one
    (row 24, trial 2);
  - Nova 2 Lite on `us.amazon.nova-2-lite-v1:0` for 84/84, `stopReason` `end_turn` on all 84. Converse returns no
    fingerprint, so the profile id is Nova's only version signal.
- **The judge:** `gpt-4o-mini-2024-07-18`, 840 calls across four fingerprints.
- **Clean:**
  - 0 generation errors, 0 retries, 0 throttles, 0 empty answers, and no score missing after the judge's retry;
  - the contexts each generator received equalled the graph's returned contexts in 168/168;
  - the memo made 28 router calls, 31 tool decisions and 28 query embeddings.
- **The G10b review trigger was switched off for the run.** G11 measures generation, and rows 9–11 would otherwise
  pause before it.
- **A plumbing check ran first and was discarded:** rows 9 and 21, one trial, P6 once; $0.025.
- **Derived metrics:** [`provider_metrics.json`](provider_metrics.json). Raw answers and contexts stay in the gitignored
  `eval/results/`.

### P1 — HOLDS: 28/28
`tests/test_generation.py`: the request bodies sent by the frozen generator, by the seam with
`GENERATION_PROVIDER=openai`, and by the graph's `generate_node` were byte-identical on all 28 questions, with the
model, temperature 0 and seed 42 asserted. The suite passes 322/322.

### P2 — HOLDS: 84/84
The contexts handed to generation were byte-identical across the arms in all 84 trial-row pairs. They were also
identical across the three trials (28/28 rows) and across P6's 6 calls.

### P3 — FALSIFIED per row; HOLDS at the aggregate
| metric | per row: FALSIFIED (the Nova mean below OpenAI's minimum, and more than 0.03 below OpenAI's mean) | aggregate, the mean of 28 rows: OpenAI → Nova | aggregate |
|---|---|---|---|
| faithfulness | 9/28: rows 5, 6, 7, 9, 11, 20, 21, 23, 26 | 0.9794 → 0.9537 (−0.0258) | HOLDS |
| answer correctness | 8/28: rows 6, 11, 13, 15, 21, 24, 27, 28 | 0.6458 → 0.6476 (+0.0018) | HOLDS |

- **Named rows:**
  - **Row 9, correctness: HOLDS.** OpenAI 0.59, 0.72, 0.97; Nova 0.78, 0.79, 0.78 (means 0.762 → 0.786).
  - **Row 21, faithfulness: FALSIFIED.** OpenAI 1.00 ×3; Nova 0.93, 0.93, 0.85 (mean 0.903).
- **The other direction:** Nova's mean was above OpenAI's maximum on 2 faithfulness rows (8 and 12) and on 13
  correctness rows.
- **The band was a single point on most faithfulness rows.** OpenAI scored 1.00 in all three trials on 24 of the 28
  rows, so on those rows any Nova mean below 0.97 falsifies, and one trial at 0.86 is enough (row 9). This is a property
  of the rule as registered; it is stated here, not reopened.
- **The per-row table** is in the G11 block of [`METRICS_HISTORY.md`](METRICS_HISTORY.md).

**Post-hoc readings of the rows that fell on both metrics, plus row 11** (read from the raw answers, no model calls;
**not part of any verdict**):
- **Row 6: an unsupported inference, a real but small faithfulness defect** (faithfulness 1.00 ×3 → 0.67, 0.83, 0.60;
  correctness 0.59 ×3 → 0.59, 0.25, 0.43).
  - In two of three trials, Nova gives the purge limit a reason the context does not ("could affect the sensor's
    calibration and performance"). The manual ties calibration error to a sensor case left above atmospheric pressure
    during operation, not to the purge limit.
  - All three state the limit as "7.25 psi", where the manual and OpenAI say "below 7.25 psi". The third trial pastes
    the purge-time table instead, which the correctness judge marks down (0.25).
- **Row 21: a contradiction plus an unsupported attribution** (faithfulness 1.00 ×3 → 0.93, 0.93, 0.85; correctness
  0.96 ×3 → 0.37, 0.54, 0.40).
  - Each Nova answer opens "do not fully agree" and concludes "agree".
  - Each cites `osha-1910-1000 page=52` for the chlorine ceiling, a page whose only chlorine mention is a PCB
    cross-reference.
  - OpenAI answers "agree" consistently and cites the value to the NIOSH page (the registered misattribution).
- **Row 11: mostly an extraction artifact, not a Nova defect** (faithfulness 1.00 ×3 → 0.78, 1.00, 0.71; correctness
  0.84, 0.77, 0.86 → 0.47, 0.61, 0.63).
  - `niosh-pocket-guide page=45` is a two-column page flattened in extraction, so in the chunk "ST 35 ppm" follows the
    OSHA PEL. In the PDF it sits in the NIOSH REL column, judged by the line's x-position against another entry on the
    same page.
  - OpenAI gives that short-term limit to OSHA in 3/3 trials and scores 1.00. Nova (trials 1 and 3) gives it to both
    agencies, which is right about NIOSH by the PDF and unsupported by the chunk. Its trial-1 claim that "both agencies
    set the same STEL" is unsupported either way.
  - The judge grades against the chunk ([`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md), the chunking backlog).

### P4 — HOLDS: $0.00294 and 1.39 s
| | gpt-4o-mini | Nova 2 Lite | predicted, Nova | band, Nova |
|---|--:|--:|--:|--:|
| $ per answer, p50, uncached list price | $0.00116 | **$0.00294** | about $0.0030 | $0.0015–0.0060 |
| $ per answer, p50, as billed | $0.00075 (prompt cache) | $0.00294 | | |
| latency, p50 (p10–p90) | 1.35 s (0.93–2.40) | **1.39 s** (0.87–2.44) | about 1.2 s | 0.6–2.4 s |
| input tokens, p50 (range) | 7,505 (4,055–11,055) | 7,453 (4,031–10,876) | about 8,000 | |
| output tokens, p50 (range) | 90 (33–372) | 133 (41–616) | about 120 | |

- **Latency** is the client-side wall time of the generation call; for Bedrock, the successful Converse call, without
  the rate spacer. Nova's server-side `latencyMs` p50 was 1.29 s.
- **The ratio:** 2.54× at p50, 2.46× by mean.
- **The OpenAI arm's predictions** (about $0.0012 and about 1.7 s) carry no falsifier. Measured: $0.00116 and 1.35 s.

### P5 — HOLDS on its registered falsifier
- **Withheld by the output guard:** 0 in all six arm-trials (predicted 0–1).
- **Structured citations:** the same count in both arms on 84/84 pairs, so no row lost them. They derive from chunk
  metadata only.
- **The inline clause missed.** This is an unregistered observation; per the GATE 2 ruling, no falsifier is added after
  the fact. Trials 1 and 3 fall outside the predicted ±2:

| trial | answers citing a doc id: OpenAI / Nova | answers with an exact `source_doc_id=` marker | Nova rows citing none |
|---|---|---|---|
| 1 | 28 / 24 | 28 / 22 | 7, 8, 16, 27 |
| 2 | 28 / 26 | 28 / 21 | 8, 27 |
| 3 | 28 / 23 | 28 / 18 | 2, 7, 8, 16, 27 |

### P6 — FALSIFIED: Nova 0/3
Row 25 under the `/ask` path's contexts (unscoped `dense_search` at k=10, identical in all 6 calls):
- **gpt-4o-mini, 3/3, byte-identical:** "The provided context does not contain the answer."
- **Nova 2 Lite, 0/3.** Each answer said the SDS excerpt lacks the value, gave the NIOSH Pocket Guide's 0°F, and ended
  with the exact sentence. Trial 1, verbatim:

```text
The provided context does not include the flash point of acetone from the Sigma-Aldrich SDS. The SDS excerpt only lists product identification details, supplier information, and emergency contact numbers, but does not mention physical properties such as flash point.

However, the NIOSH Pocket Guide entry for acetone (page 33) states:

**Flash Point:** 0°F

Since the question specifically asks for the flash point "per the Sigma-Aldrich SDS" and this information is not present in the SDS excerpt, we cannot provide the value from that source. The NIOSH data shows the flash point is 0°F, but this is not from the requested SDS.

**Answer:** The Sigma-Aldrich SDS provided does not contain the flash point of acetone. The NIOSH Pocket Guide lists acetone’s flash point as 0°F, but this is not from the SDS requested.

The provided context does not contain the answer.
```

- **How each Nova answer would be served:** the output guard passes all three, `score_confidence` gives 0.9 "high:
  answer generated from retrieved context", and every retrieved page is cited. That is a property of the confidence
  layer, recorded in the citation-layer section of [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).
- **Agent-path refusals,** recorded as well: 0/84 in each arm (predicted 0).

### P7 — the outcome HOLDS; "on cost alone" is FALSIFIED
- **The rule selects `openai`.** Bedrock fails all three conditions: P3 on both metrics (per row), P6, and cost
  (2.54× > 1.5×).
- **"On cost alone" is FALSIFIED.** The cost ratio matched the prediction (about 2.5×), but P3 and P6 fail too.
- **Refinement A was not triggered.** The PR ships `openai` as the default.

### Unregistered observations
- **Row 21 misattributes in two ways:**
  - OpenAI, 3/3: OSHA's table ceiling cited to `niosh-pocket-guide page=89`, the G9 instance; Nova shows this type 0/3;
  - Nova, 3/3: the same value cited to `osha-1910-1000 page=52`, a page that does not carry it.
- **Determinism, on byte-identical inputs:** OpenAI's three answers were byte-identical on 10/28 rows and Nova's, with no
  seed, on 2/28 (the 2026-10-12 methodology line in [`METRICS_HISTORY.md`](METRICS_HISTORY.md)).
- **Row 24's second fingerprint** (`fp_412505125b`, trial 2) produced an answer byte-identical to the row's other two
  trials.
- **`serviceTier`** was absent from all 84 Converse responses.
- **Nova writes longer answers:** a p50 of 133 output tokens against 90.

### Hours and spend
- **Hours:** about 3.4 h from the G11 spec to GATE 2 (10:46–14:10 UTC), inside build-day 1 of 5. The run took 35 min:
  generation 7.2, P6 0.2, judging 27.3.
- **Spend:** about **$0.73** of the $5.00, from token counts at list price: the run cost $0.709 as billed ($0.808 at
  uncached list price) and the plumbing check $0.025. The breakdown is in section H of
  [`COST_LEDGER.md`](COST_LEDGER.md).
