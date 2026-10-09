# G12 — Parallel fan-out (dispatch-and-aggregate): pre-registration

This file is committed **before** any graph code, as the first commit on the G12 branch. Outcomes are **appended**
below in later commits; the predictions are never edited. The companion design is [`g12_design.md`](g12_design.md).
Recorded 2026-10-08.

**Evidence chain.** The PR is squash-merged, so this commit gets an annotated `prereg/g12` tag naming the PR before
the push. That keeps it resolvable for [`check_doc_citations.py`](../scripts/check_doc_citations.py).

**What is measured.** G12 is a **dispatch-and-aggregate node**, not a hierarchy of agents.
- **Claimed:** the mechanism (gated dispatch, concurrent branches, a join) and its resilience (degradation).
- **Not claimed:** a retrieval-metric gain. The repo's one decomposition probe was falsified, and context recall is
  already 1.0 on all four comparison rows (`g12_design.md`, R1).

**Rows are 1-based.**

## Shared setup
- **Two arms:**
  - **fan-out:** the G12 graph;
  - **current:** `agent/graph.py` as it stands on `origin/main` at the branch point, loaded in the same process as a
    baseline module.
- **Unchanged:**
  - `src/`;
  - `semantic_v2` at k=10;
  - `gpt-4o-mini`;
  - `text-embedding-3-small`.
- **Trials:** N=3 wherever an LLM is involved. Arms are interleaved per row, and each trial is a full pass over its
  rows.
- **Fingerprints:** recorded for the decomposer, generation and the RAGAS judge.
- **Reporting:** counts with N, never percentages alone.
- **Memoization:** only in P5, where the query embedding, router decision and tool decision are shared across arms.
  P1, P3, P4 and P6 run as production does.
- **RAGAS:** the same metric objects and judge as `eval/run_eval.py`, scored one sample at a time. Batch scoring
  failed in the G1 closure.
- **Budget:** $3.00 (`eval/COST_LEDGER.md`).

**Verdicts** are **HOLDS**, **FALSIFIED** or **NOT TESTED**, each with a reason.

## The mechanism, as registered
**1. The gate.** The router's route is `direct` and the question matches, case-insensitive:

```text
\b(compare[sd]?|comparison|versus|vs\.?|agree|disagree|differ|differs|difference|higher than|lower than|stricter than)\b
```

**2. The decomposer.**
- One call: `gpt-4o-mini`, temperature 0, structured output (`json_schema`).
- The prompt and schema below are **frozen**. They are never edited after the first scored run; any change is a new
  pre-registration.
- A hermetic test asserts that the copies in `agent/graph.py` are byte-identical to these.

```text decomposer-prompt
You split comparison questions for an industrial-safety document search. A comparison question asks how a value or requirement stated by one source compares with, differs from, or agrees with the same kind of value or requirement in another source (for example a NIOSH limit versus an OSHA limit, or an SDS versus a regulation).
Rules:
- If the question is a comparison, set comparison = true and write 2 or 3 sub-questions, ONE per compared source. Each sub-question must be self-contained (name the chemical or equipment), ask only for that one source's value or requirement, and must not ask for the comparison itself.
- For each sub-question, set source_doc_id to exactly one of the known ids below when it asks about exactly one of these documents; otherwise null.
- If the question is not a comparison, set comparison = false and sub_questions = []. A question with two parts that does not compare the same kind of value across sources is NOT a comparison.

Known documents (source_doc_id: title):
{catalog}

Question: {question}
```

`{catalog}` is the router's `doc_id: title` list from `data/manifest.json`, and `{question}` is the user's question.
The schema's field descriptions:

```text decomposer-schema
SubQuestion.question: a self-contained question asking for ONE source's value or requirement
SubQuestion.source_doc_id: the one known document this sub-question asks about, else null
Decomposition.comparison: true only if the question compares a value or requirement across two or more sources
Decomposition.sub_questions: 2 or 3 sub-questions when comparison is true, else an empty list
```

**3. Dispatch.** The gate fires only when `comparison=true` with 2 or 3 sub-questions. Then:
- one `Send` per sub-question;
- each branch runs `dense_search(sub_question, k=retrieval_k)`, scoped when a known `source_doc_id` is named.

Anything else falls back to today's single-query `retrieve`.

**4. The join.**
- It rank-interleaves the surviving branches in the decomposer's order and dedups by `chunk_content_key`.
- It writes `retrieved` once.
- If every branch failed, it takes the existing retrieval-error path (empty answer, scored 0.0 "no answer generated").

## P1 — Dispatch
**Prediction:** the fan-out fires in 3/3 trials on each of rows 9, 10, 11 and 21 (**12/12**), and in 0/3 on every other
frozen row (**0/72**).

**Falsified by:** any miss, in either direction.

The 0/72 is deterministic, because the wording pre-gate matches none of those rows. The 12/12 depends on the
decomposer, which R2 measured at 12/12 **in-sample**.

## P2 — Both sources in one pass
**Prediction:** in each of the four rows, chunks from both compared source documents are in the joined `retrieved` in
3/3 trials (**12/12**).

**Falsified by:** any trial missing a source.

Against R1's single-query baseline, this is a **gain on rows 10 and 11**, where the OSHA document was absent, and
**parity on rows 9 and 21**.

Source scoping makes the presence of a source nearly mechanical. So whether each source's *own value line* is present
is **recorded, not scored**; R2b found OSHA's line on rows 10, 11 and 21.

## P3 — Retrieval metrics on the four rows, fan-out against current
Both arms, 3 trials each, scored with RAGAS. **The acceptance is mechanism and resilience, not a metric gain.**

**Predictions:**
- **Context recall: no gain.** Δ ≥ −0.03 on every row, meaning no drop. It is already 1.0 on all four rows.
- **Context precision falls on rows 10 and 21 and rises on rows 9 and 11.**
  - The context doubles to 20 chunks.
  - Rows 10 and 21 are already precise single-query (0.82 and 1.0), and OSHA's value line ranks 9th–10th within its
    own document.
  - On rows 9 and 11, each branch's value chunk ranks 1st or 3rd. Single-query ranks are 6th–8th on row 9, and 2nd on
    row 11, where precision is 0.56.
- **Row 9's answer correctness:** row 9 is the G1 regression row. The fan-out mean must be at least the current mean
  minus 0.03, in the same run.

**The per-row band** (refinement A).
- **Band:** for each row and metric, the current arm's three trials define that row's observed range, [min, max].
- **Score:** the fan-out arm is scored by its mean over three trials.
- **FALSIFIED only if both:**
  1. the fan-out mean lies **outside the current arm's range, in the direction opposite to the prediction**;
  2. **and** the difference of the two means exceeds **0.03**.
- **The opposite direction:**
  - below the minimum, for "no drop" and the row-9 guard;
  - above the maximum, for a predicted fall;
  - below the minimum, for a predicted rise.
- **Why per row:**
  - The RAGAS judge scores byte-identical strings differently from run to run. The methodology block of
    [`METRICS_HISTORY.md`](METRICS_HISTORY.md) records single-row swings of up to about 0.25 with identical answers.
  - A band built from the trials actually observed is the honest noise floor at N=3.
  - **This is weaker than an aggregate ±0.03, by design.**
- **Reporting:** the outcome reports every per-row trial value, not only means.

## P4 — Degradation, live
**Setup.** The four rows, 3 trials each. At dispatch, the eval script appends a third sub-question scoped to
`no-such-doc`.
- **Where the failure happens:** that branch raises `ValueError` **at validation, before any retrieval call**.
- **What live P4 tests:** the join's tolerance of a failed branch *record*.
- **What it doesn't test:** an exception raised during retrieval. That case (`dense_search` raising inside a branch)
  is covered **hermetically only**, by R6 test 4.

**Prediction:** in **12/12**, the answer is generated from the two surviving branches. It is neither a whole-answer
refusal nor empty, and `trace_notes` names `fanout[3/3]` and `ValueError`.

**Falsified by:** a whole-answer refusal, an empty answer, or an exception.

## P5 — Pass-through on the 24 non-comparison rows
**Setup.** 3 trials. The query embedding, the router decision and the tool decision are memoized and shared across
both arms, so the comparison tests the layer rather than backend reproducibility (the G5 and G6 lesson). Generation is
stubbed, because only its inputs are compared.

**Prediction:** in all **72/72** cases, the contexts handed to `generate` are byte-identical between the fan-out graph
and the current one.

**Falsified by:** any difference.

## P6 — Latency
**Prediction:** over the 12 P1 trials on the four rows, the fan-out arm's p50 wall time is at most the current arm's
p50 **+ 1.0 s**, which is the decomposer call.

**Falsified by:** a difference of more than **+1.5 s**.

**Named risk:** the doubled context also slows `tool_decide` and `generate`; the estimate is +1.0 to +1.5 s.

**Recorded:** input tokens for `generate` in both arms.

## Spend
About $0.5 of the $3.00:
- 24 agent runs for P1, P3 and P6;
- 12 for P4;
- P5 with generation stubbed;
- about 500 RAGAS judge calls.

## Outcome (recorded 2026-10-08)
**P1, P2, P4 and P5 HOLD. P3 is FALSIFIED on 4 of its 9 per-row items, and P6 is FALSIFIED.**

The predictions above are unchanged; this section only appends. Rows are 1-based.

**The ruling.** The fan-out ships **switched off** (`FANOUT_ENABLED = False` in `agent/graph.py`). It is built and
measured, but not enabled. There is no revision and no second mechanism: the mechanism works, and its metric cost is
the finding.

**Run.** One process of [`scripts/fanout_eval.py`](../scripts/fanout_eval.py), 2026-10-08 22:22–22:42 -05.
- **The current arm** was `agent/graph.py` at the branch point, `afd81eb`.
- **Fingerprints** (all `gpt-4o-mini-2024-07-18`):
  - decomposer: `fp_551db23bc9` ×24;
  - generation: `fp_2fb502e36f` ×36;
  - RAGAS judge: `fp_4e22378d6e` ×372, `fp_8f7cd315de` ×48, `fp_b742a60b91` ×24, `fp_91ad5947c4` ×24,
    `fp_5b4bcc598a` ×12.
- **Derived metrics** are in [`fanout_metrics.json`](fanout_metrics.json). Raw runs stay in the gitignored
  `eval/results/`.

### P1 — HOLDS: 12/12 and 0/72
- **Rows 9, 10, 11 and 21:** the fan-out fired 3/3 each.
- **The other 24 rows, over 3 trials:** it fired 0/72. That count is read from P5's runs.
- In every fan-out run, the decomposer named the same two source documents per row as in R2.

### P2 — HOLDS: 12/12
| row | fan-out: both sources | current: both sources | OSHA's own value line (fan-out / current) |
|---|--:|--:|---|
| 9 | 3/3 | 3/3 | — |
| 10 | 3/3 | **0/3** | 3/3 / 0/3 |
| 11 | 3/3 | **0/3** | 3/3 / 0/3 |
| 21 | 3/3 | 3/3 | 3/3 / 0/3 |

The fan-out reached 12/12 against the current path's 6/12: a gain on rows 10 and 11, as predicted, and parity on rows
9 and 21.

### P3 — FALSIFIED on 4 of 9 items (refinement A's per-row band)
Every trial value is shown, current arm first, then the fan-out arm.

| row, metric | predicted | current | fan-out | current range | difference of means | verdict |
|---|---|---|---|---|--:|---|
| 9 recall | no drop | 1.0, 1.0, 1.0 | 1.0, 1.0, 0.6667 | [1.0, 1.0] | −0.1111 | **FALSIFIED** |
| 10 recall | no drop | 1.0, 1.0, 1.0 | 1.0, 1.0, 1.0 | [1.0, 1.0] | 0 | HOLDS |
| 11 recall | no drop | 1.0, 1.0, 1.0 | 1.0, 1.0, 1.0 | [1.0, 1.0] | 0 | HOLDS |
| 21 recall | no drop | 1.0, 1.0, 1.0 | 1.0, 1.0, 1.0 | [1.0, 1.0] | 0 | HOLDS |
| 9 precision | up | 0.886, 0.6974, 0.7159 | 0.4359, 0.6967, 0.6967 | [0.6974, 0.886] | −0.1567 | **FALSIFIED** |
| 10 precision | down | 0.7611, 0.7611, 0.7454 | 0.8649, 0.8115, 0.866 | [0.7454, 0.7611] | +0.0916 | **FALSIFIED** |
| 11 precision | up | 0.5595, 0.4206, 0.5587 | 0.5818, 0.6096, 0.6096 | [0.4206, 0.5595] | +0.0874 | HOLDS |
| 21 precision | down | 1.0, 1.0, 1.0 | 0.9281, 0.9201, 0.8492 | [1.0, 1.0] | −0.1009 | HOLDS |
| 9 answer correctness | no drop | 0.7549, 0.7886, 0.7228 | 0.6773, 0.7736, 0.6747 | [0.7228, 0.7886] | −0.0469 | **FALSIFIED** |

### P4 — HOLDS: 12/12
- **Setup:** the four rows, 3 trials each, with a third branch scoped to `no-such-doc` appended at dispatch.
- **Results:**
  - all 12 answers were generated, with 0 empty, 0 whole-answer refusals and 0 exceptions;
  - every trace names `fanout[3/3] … -> ERROR ValueError`, and the join reports `join: 2/3 branches ok; failed
    fanout[3/3] ValueError`;
  - the G6 output guard would have withheld 0 of 12.
- As registered, the failure was raised at validation, before any retrieval call.

### P5 — HOLDS: 72/72
The contexts handed to `generate` were byte-identical between the two arms in all 72 pairs (24 rows × 3 trials). The
embedding, router and tool decisions were shared across arms: 24 live router calls and 24 live tool calls.

### P6 — FALSIFIED: +1.98 s p50
- **Wall time at p50:** fan-out **6.93 s** against current 4.95 s, over 12 runs per arm. The fan-out ranged 5.56–12.35
  s, the current path 3.84–8.05 s.
- **Two causes:**
  - **the decomposer took 1.17–1.35 s live**, against 0.67 s at p50 in R2;
  - **the context roughly doubles.** At p50, context tokens handed to `generate` are 12,567 against 5,528, and
    chunks are 20–23 against 10–13. Prompt tokens per run, at p50, are 27,950 against 15,848.

### Attribution — post-hoc, not part of the verdict
- **Row 9 recall is judge variance.** Fan-out trials 2 and 3 had **byte-identical contexts**, and context recall
  depends only on the contexts and the reference. Yet they scored 1.0 and 0.6667.
- **Row 9 correctness is the G1 attention effect.**
  - The tool fired in all 6 of row 9's runs.
  - The fan-out answers add the tool's mg/m³ values ("≈ 208.96 mg/m³") and drop the document's 0.14 mg/L. That is the
    displacement recorded for this row in G1, where it is 0-based row 8 (see [`METRICS_HISTORY.md`](METRICS_HISTORY.md)).
  - The doubled context compounds it.
- **Row 21: an UNREGISTERED observation,** reported because the artifact showed it.
  - Answer correctness fell from 0.9553, 0.9572, 0.9553 to **0.5798, 0.5287, 0.4586**.
  - 1 of 3 fan-out answers wrongly concludes that the Airgas SDS and OSHA "do not agree".
  - All three render the ceiling as "3 mg/m³ and 1 ppm" rather than "1 ppm (3 mg/m³)".
  - The hypothesis, **untested**: OSHA Table Z-1's raw "(C)1 (C)3" line appears only in the fan-out context.

### Spend
- This run made 678 chat requests:
  - 2,227,580 prompt tokens, of which 1,322,752 were cached;
  - 59,408 completion tokens;
  - plus 84 query-embedding calls.
- **Derived cost: $0.2706.** By part: P1 $0.0699, P3 scoring $0.1211, P4 $0.0465, P5 $0.0330.
- The probes cost about $0.013, so G12's total is about $0.28 of $3.00.
