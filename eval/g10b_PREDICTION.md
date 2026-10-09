# G10b — Checkpointing and the approval gate: pre-registration

This file is committed **before** any gate code, as the first commit on the G10b branch. Outcomes are **appended**
below in later commits; the predictions are never edited. The companion design is [`g10b_design.md`](g10b_design.md).
Recorded 2026-10-09.

**Evidence chain.** The PR is squash-merged, so this commit gets an annotated `prereg/g10b` tag naming the PR before
the push.

**Rows are 1-based.**

## Shared setup
- **The trigger:** `exposure_limit_named(question) ∨ scoped_fallback`.
  - The first disjunct is the case-insensitive pattern `\b(exposure limits?|IDLH|PELs?|RELs?|TLVs?|STELs?)\b`.
  - The second is a source-scoped route downgraded to direct by the execution fallback.
  - The trigger is a policy on the question's wording, not a measurement of answer quality.
- **Durability:** `SqliteSaver` (option A), failing closed. The TTL is 24 h by default.
- **The resume ops:** approve, reject, and amend. Amend exposes removals only.
- **Unchanged:** `src/`, `semantic_v2` at k=10, and `gpt-4o-mini`.
- **Live runs are in-process,** via `scripts/review_eval.py`, against the real backends.
  - When two arms are compared, the query embedding, the router decision and the tool decision are memoized and shared
    across arms, so the comparison tests the layer rather than backend reproducibility (G12's P5 discipline).
- **Counts with N; never percentages alone.**
- **Verdicts** are HOLDS, FALSIFIED or NOT TESTED, each with a reason.

## P1 — The fire set
**Prediction:** the trigger fires on frozen rows **9, 10 and 11** in 3/3 trials each. It fires on **0/25** other
frozen rows, **0/8** G9 smoke rows, **0/3** capability rows and **0/9** guardrail hard negatives (rows 24–32 of
`eval/guardrail_set.jsonl`).

**Falsified by:** any row off the predicted set, in either direction.

## P2 — Four paths, live
**Setup:** rows **10 and 11**, 3 trials each.

**Predictions:**
- **approve:** the contexts handed to `generate` are byte-identical to an ungated run of the same question in the same
  process;
- **reject:** a refusal (`guard.stage = "review"`, `reason = "rejected"`) and **0** `generate` calls, read from the
  trace;
- **amend,** removing the lowest-ranked document chunk: the `generate` input is the paused set minus exactly that
  chunk;
- **expired,** with the TTL set to 5 s for the test: a refusal (`expired_or_lost`) and 0 `generate` calls.

**Falsified by:** any path deviating.

**Expected, not a deviation (refinement C).** Approve and amend run the output guard after generation. If the guard
withholds an approved answer, that is the correct composition: review is not a bypass. It is counted, and it does not
falsify P2.

## P3 — Restart survival
**P3a.** Both of:
- the hermetic subprocess test is green: graph A pauses on a SQLite file, and graph B, in a fresh process, resumes on
  it and answers;
- **and** a local live run answers: uvicorn with `REVIEW_DB_PATH` set; a pause; the process killed and restarted on
  the same file; a resume.

**P3b, after the merge** (the G5-closure pattern), under option A:
- one live pause on the Render service;
- one Render redeploy;
- then the resume returns the `expired_or_lost` refusal.

That is the fail-closed prediction. P3b is NOT TESTED at GATE 2, and its outcome is appended in a closure PR.

**Falsified by:** P3a failing, or an answer emitted for a thread the service should not know.

## P4 — Pass-through
**Setup:** the **25 non-trigger frozen rows** (the 8 smoke rows included) × 3 trials.

**Prediction:** the contexts handed to `generate` are byte-identical across three arms:
- the pre-G10b graph (`agent/graph.py` at the branch point, loaded as a module);
- G10b **without** a checkpointer;
- G10b **with** the `SqliteSaver`.

Generation is stubbed, and the decisions are memoized.

**Falsified by:** any difference.

## P5 — Latency
**Prediction:** the added p50 for a non-firing request with the checkpointer, against without, is **≤ 150 ms**,
measured over P4's runs.

**Falsified by:** more than **300 ms**.

## P6 — G9 stays green
**Prediction:** the G9 smoke stays green on this branch's PR runs, and row 24's context set is unchanged.

**Falsified by:** a smoke red attributable to the gate.

## Spend
About $0.30 of the $3.00:
- P1, about $0.06;
- P2, about $0.10;
- P4 and P5, with generation stubbed, about $0.05;
- the G9 PR runs.

## Outcome (recorded 2026-10-09)
**P1, P2, P3a, P4 and P5 HOLD. P6 is read on this PR's CI runs. P3b is NOT TESTED until after the merge.**

The predictions above are unchanged; this section only appends. Rows are 1-based; times are UTC.

**Run.** One process of [`review_eval.py`](../scripts/review_eval.py), 2026-10-09 07:03–07:12 UTC, measured:
- **The G10b build:** `180c875`, tagged `evidence/g10b-build`.
- **The pre-G10b arm:** `agent/graph.py` and `agent/state.py` at `b278f18`, loaded as modules.
- **Generation:** `gpt-4o-mini-2024-07-18`, `fp_2fb502e36f`.
- **Derived metrics:** [`review_metrics.json`](review_metrics.json). Raw records stay in the gitignored `eval/results/`.

### P1 — HOLDS: exactly {9, 10, 11}
- **Frozen rows 9, 10 and 11** paused in 3/3 trials each, all with reason `exposure_limit_named`.
- **Nothing else paused:** 0/75 runs of the 25 other frozen rows, 0 of the 8 smoke rows (which are among those 25),
  0/9 capability runs and 0/27 hard-negative runs.
- **The scoped-fallback disjunct fired 0 times live,** as predicted. It is proven hermetically.

### P2 — HOLDS: 24/24
| path | as predicted | what was checked |
|---|--:|---|
| approve | 6/6 | one `generate` call; its contexts byte-identical to the ungated run of the same question |
| reject | 6/6 | a refusal (`review`, `rejected`); 0 `generate` calls |
| amend (the lowest-ranked document chunk removed) | 6/6 | one `generate` call; its input was exactly the paused set minus that chunk |
| expired (TTL 5 s, resumed after 6 s) | 6/6 | a refusal (`review`, `expired_or_lost`); 0 `generate` calls |

**The paused evidence** equalled the ungated run's contexts in 24/24 pauses, and the 202's evidence keys matched the
checkpointed chunks.

**Refinement C's count:** the output guard withheld **0** of the 12 approved and amended answers.

### P3 — P3a HOLDS; P3b NOT TESTED (after the merge)
**Hermetically:** graph A paused on a SQLite file, and a fresh process compiled graph B on the same file, resumed and
answered (`tests/test_review.py`).

**Locally live,** the sequence was:
1. a real uvicorn process with `REVIEW_DB_PATH` set returned 202 for row 10;
2. it was killed with SIGTERM;
3. a new process started on the same file, and `GET /ask/agent/review/{thread}` still read `pending`;
4. `POST /ask/agent/resume` with approve returned 200 with an answer (no guard);
5. the status then read `approved`.

**P3b, the registered prediction under option A:** one live pause on Render, one redeploy, and the resume returns
`expired_or_lost`. It is recorded after the merge in a closure PR.

### P4 — HOLDS: 75/75
The contexts handed to `generate` were byte-identical across all three arms in every run (25 rows × 3 trials): the
pre-G10b graph, G10b without a checkpointer, and G10b with the SqliteSaver. The gate paused none of these runs.

### P5 — HOLDS: +2.4 ms
- **p50 wall time:** pre-G10b 0.158 s; G10b without a checkpointer 0.156 s; G10b with it 0.159 s.
- **The added p50,** with the checkpointer against without, was **2.4 ms**, both from the medians and paired per run.
- Decisions were memoized and generation stubbed, so the difference is the checkpoint write plus the thread delete.

### P6 — read on the PR
The G9 smoke on this branch's PR runs, with row 24's context set; recorded at GATE 3.

### Spend
- **Derived total:** **$0.148**, from token counts:
  - P1, $0.120 (253 chat requests: 120 live runs of the router and tool decision);
  - P2, $0.008;
  - P4 and P5, $0.020.
- P3a's separate uvicorn process isn't counted: one router call, one tool decision and one generation.
