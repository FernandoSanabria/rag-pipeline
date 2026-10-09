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
