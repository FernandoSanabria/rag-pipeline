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
