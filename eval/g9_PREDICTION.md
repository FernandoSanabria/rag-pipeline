# G9 — Evaluation in CI: pre-registration

This file is committed **before** any workflow or runner code, as the first commit on the G9 branch. Outcomes are
**appended** below in later commits; the predictions are never edited. The companion design is
[`g9_design.md`](g9_design.md). Recorded 2026-10-08.

**Evidence chain.** The PR is squash-merged, so this commit gets an annotated `prereg/g9` tag naming the PR before the
push. That keeps it resolvable for [`check_doc_citations.py`](../scripts/check_doc_citations.py).

**Rows are 1-based.**

## Shared setup
**The smoke set: 8 rows of the frozen `eval/dataset.jsonl`.**
- Rows 1, 4, 15, 20, 21, 25 and 26 run on `/ask`.
- Row 24 runs on `/ask/agent`, with the fan-out off as shipped.
- Each row goes through `api.main._answer`, the endpoints' one wiring point, so the judged answer is the served
  answer after the output guard.

**Unchanged:** `src/`, `semantic_v2` at k=10, `gpt-4o-mini` for generation, and the RAGAS judge exactly as in
`eval/run_eval.py`:
- `gpt-4o-mini` at temperature 0;
- `text-embedding-3-small`;
- faithfulness and answer correctness, taken from its five-metric set.

**The tiers, registered:**
- **T1 — HARD.** For each row, the `(source_doc_id, page)` set of document chunks equals the snapshot. On row 25, the
  served answer is also the whole refusal (`api.confidence.is_refusal`).
- **T2 — HARD.** Red iff faithfulness < snapshot − **0.2**. A judge NaN is retried once; still NaN → **NOT SCORED**,
  which is reported and not red.
- **T3 — reported only.** Answer correctness, as the delta against the snapshot.

**The cache** is content-keyed (`g9_design.md`, C1 note 5) and merges; it never replaces (refinement A).
- **A hit** is a row whose faithfulness came from the cache.
- Hits are counted per run from the run's artifact.

**Hard caps:** 8 rows, 10 generation calls and 120 judge calls per run. A breach exits 2.

**Snapshot:** `eval/smoke_snapshot.json`, written by one dispatched `baseline` run and committed. The characterization
runs and the negative proof run on the commit that adds it.

**Counts, not percentages.** Verdicts are **HOLDS**, **FALSIFIED** or **NOT TESTED**, each with a reason.

## P1 — No false red
**Prediction:** 5 `workflow_dispatch` runs on the same commit give **0 reds** on T1 and T2.

**Falsified by:** any red.

## P2 — A true red
**Prediction:** the negative proof (`simulate_regression=true`) goes red, with **both** failures named:
- T1 on row 1, whose retrieval runs at k=2;
- T2 on row 4, whose served answer is replaced by a canned unfaithful sentence before judging.

**Falsified by:** a green run, or only one of the two failures.

## P3 — Retrieval stability
**Prediction:** T1 set equality holds on **8/8 rows × 5 runs** (40/40).

**Falsified by:** any row's set varying.
- Any variation is recorded regardless.
- It is attributed to embedding non-reproducibility **only if** the chunk that entered or left was near-tied in that
  run's or the snapshot's top-11 scores. Those scores are advisory (refinement B).

## P4 — The cache
**Prediction:** **7 hits per run** in each of runs 1–5: rows 1, 4, 15, 20, 24, 25 and 26. Row 21 misses.

**Falsified by:** any run off by more than 2 (fewer than 5 hits).

**Recorded:** the saving in dollars, from the judge tokens of the misses.

## P5 — Cost and time
**Prediction:** **≤ $0.10 per run** and **≤ 6 min** per `smoke` job; about $0.01–0.02 and 3–4 min are expected.
- **Cost** is derived from token counts.
- **Time** is the job's duration from the Actions API, including `uv sync`.

**Falsified by:** either limit exceeded in any of the 5 runs.

## P6 — The hermetic CI is untouched
**Prediction:**
- `.github/workflows/ci.yml` is byte-identical to `main` (the diff goes in the GATE 2 packet);
- `test-and-build` still passes, with no secrets.

**Falsified by:** any diff, or any secret referenced by `ci.yml`.

## After P1 and P2: the required check and its demotion rule
**Becoming required.** If P1 **and** P2 both HOLD, the `smoke` check becomes a required check on `main` (GATE 1
decision 5).
- GitHub treats a **skipped** required check as satisfied.
- That covers fork PRs without secrets, PRs that touch none of the gated paths, and the state before a snapshot
  exists.

**The running false-red ledger (refinement D).** METRICS_HISTORY's G9 block keeps an append-only **"smoke reds"** list.
Each entry records the run, the row, the tier and a classification, decided from the artifacts:
- **noise**, if either:
  - the **base** commit is also red on that row and tier when run the same day. For a PR the base is its base branch;
    for a push it is the parent commit. The change under test did not cause the red;
  - it is a T1 **near-tie flip**: every chunk that entered or left the set lies within 0.001 of the rank-10 score in
    the snapshot's top-11.
- **true regression:** otherwise.

A re-run of the **same** commit cannot classify a red, because the cache replays the same judged score.

**The demotion rule, registered now:** **two noise reds in any twenty gated runs** → the check is demoted to
reported-only, and the threshold is re-derived from the accumulated runs.
- **What counts toward the twenty:** PR runs, push runs and the characterization dispatches.
- **What doesn't:** the baseline and the negative proof.

## Spend
About $0.20 of the $3.00: 7 workflow runs (the baseline, 5 characterization runs and the negative proof) at about $0.02
each, plus dev runs.
