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
