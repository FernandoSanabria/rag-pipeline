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

## Outcome (G9 as registered, recorded 2026-10-09)
**P1 is FALSIFIED (2 of 5 runs red). P2, P3, P4 and P5 HOLD. P6 holds at this commit and is re-checked on the final
head.**

The predictions above are unchanged; this section only appends. Rows are 1-based; times are UTC.

**The ruling.** The registered demotion rule fired, so **the check is demoted to reported-only and is not a required
check**. A new pre-registration, G9b, follows below.

**Runs.** All are `eval-smoke` runs on this branch. A "gated" run is one whose `smoke` job ran.

| run | trigger | created (UTC) | commit | role | result |
|---|---|---|---|---|---|
| #2 | workflow_dispatch | 2026-10-09 05:01 | `8856abd` | baseline; artifact lost (upload-artifact v4 skips hidden paths) | green |
| #3 | workflow_dispatch | 2026-10-09 05:04 | `1db5e6e` | **the baseline**, committed as `eval/smoke_snapshot.json` | green |
| #5 | pull_request | 2026-10-09 05:06 | `18b07c8` | first gated PR run (cold cache) | green |
| #6–#10 | workflow_dispatch | 2026-10-09 05:06–05:11 | `18b07c8` | **P1, P3, P4, P5: characterization** | #6 and #10 red |
| #11 | workflow_dispatch | 2026-10-09 05:38 | `18b07c8` | **P2: negative proof** | red, as required |

Runs #1 and #4 were `pull_request` runs before a snapshot existed; their `smoke` job skipped with "baseline pending".

### P1 — FALSIFIED: 2 of 5 runs red
Each cell is faithfulness, then correctness. "(judged)" marks a cache miss; bold marks a T2 red.

| row | snapshot | #6 | #7 | #8 | #9 | #10 |
|--:|---|---|---|---|---|---|
| 1 | 1, 0.6823 | 1, 0.6823 | 1, 0.6823 | 1, 0.6823 | 1, 0.6823 | 1, 0.6823 |
| 4 | 1, 0.7853 | 1, 0.7853 | 1, 0.7853 | 1, 0.7853 | 1, 0.7853 | 1, 0.7853 (judged) |
| 15 | 1, 0.4947 | 1, 0.4947 | 1, 0.4947 | 1, 0.4947 | 1, 0.4947 | 1, 0.4947 |
| 20 | 1, 0.3516 | 1, 0.3516 | 1, 0.3516 | 1, 0.3516 | 1, 0.3516 | 1, 0.3516 |
| 21 | 1, 0.9564 | 1, 0.9549 | 1, 0.9564 | 1, 0.9549 | 1, 0.9549 | 1, 0.9564 |
| 25 | 0, 0.0362 | 0, 0.0362 | 0, 0.0362 | 0, 0.0362 | 0, 0.0362 | 0, 0.0362 |
| 26 | 1, 0.4733 | 1, 0.4733 | 1, 0.5767 | 1, 0.4733 | 1, 0.4733 | 1, 0.5767 |
| 24 | 1, 0.911 | **0.75**, 0.5928 (judged) | 1, 0.911 | 1, 0.911 | 1, 0.911 | **0.75**, 0.5928 |

**Both reds are T2 on row 24** (`/ask/agent`, source-scoped). Row 24 served two answers:
- **A,** the snapshot's: 154 characters, faithfulness 1.0, correctness 0.911;
- **B:** 160 characters, faithfulness 0.75 (the judge found 1 of 4 statements unsupported), correctness 0.5928.

**What was identical between A and B:**
- byte-identical contexts;
- the same route (`source_scoped`, no tool chunk);
- the same generation fingerprint (`fp_2fb502e36f`, seed 42, temperature 0).

**How each red arose.** Run #6 judged B fresh; run #10 drew B again, and the cache replayed the same 0.75. The cache
makes a score deterministic per answer; it can't make generation choose A.

**Where B appears.**
- **On GitHub-hosted runners:** in 2 of the 7 runs that served row 24 (#3, #5–#10).
- **Locally, never:** 0 of 24 regenerations through the runner's own binding (2026-10-09, all with byte-identical
  contexts), and 0 of 23 historical row-24 answers in the gitignored `eval/results/`.
- **Its text has not been read.** The artifacts carry hashes only, by design. G9b adds a way to read it, and its
  label is recorded in G9b's outcome.

### P2 — HOLDS
Run #11 went red with exactly the two injected failures named:
- **T1 row 1:** the k=2 set kept 2 of 10 pages;
- **T2 row 4:** the canned answer's faithfulness was 0.0, against the snapshot's 1.0.

`negative_proof.held` was true. The other 6 rows passed.

### P3 — HOLDS: 40/40
T1 set equality held on 8 rows in each of 5 runs (48/48 including PR run #5).

**Run #10, row 4: a rank swap.**
- Pages 14 and 17 of the Fisher 667 manual swapped ranks 7 and 8; their snapshot scores were 8e-06 apart.
- This is the **third recorded instance of embedding non-reproducibility**, after G5's P2 swap and G5's N=5 post-hoc
  embedding check.
- T1 compares the set, order-insensitive, so it passed, as designed. The reordered contexts changed the cache key,
  so row 4 was re-judged (faithfulness 1.0).

### P4 — HOLDS: 7, 8, 8, 8, 7 hits
- **The predicted miss didn't occur.** Row 21's answers alternated between two variants, and both were already cached
  from runs #2 and #3.
- **The misses came elsewhere:** row 24 in #6 (answer B, first seen) and row 4 in #10 (the reordered contexts).
- **The saving:** a cold run (PR #5, 0 hits) cost $0.0164, and a fully cached run cost $0.0056. That is about
  **$0.0107 per run**.

### P5 — HOLDS
- **Cost:** $0.0056–0.0076 per run (limit $0.10).
- **`smoke` job time:** 31–49 s (limit 6 min).

### P6 — holds at this commit
`git diff origin/main -- .github/workflows/ci.yml` is empty, and `test-and-build` passed on every push.

### The smoke-reds ledger (refinement D) and the demotion
1. Run #6, row 24, T2: **noise.** The code was unchanged since the baseline; it is generation variance on identical
   input.
2. Run #10, row 24, T2: **noise.** The same answer B.

That is **2 noise reds in 6 gated runs** (#5–#10), so the registered rule fires: the check is demoted to reported-only,
and the threshold is to be re-derived from accumulated runs. The running list continues in
[`METRICS_HISTORY.md`](METRICS_HISTORY.md)'s G9 block.

**Spend so far:** about $0.14, comprising:
- the CI runs, $0.084;
- the local dev runs, $0.035;
- the local regeneration of row 24, $0.030.

## G9b pre-registration (recorded 2026-10-09)
This section is committed **before** any G9b code, on the G6 v1/v2 precedent: a falsified gate is recorded as it was,
and its successor is pre-registered separately. It gets its own annotated tag, `prereg/g9b`, pushed before any G9b
run. Outcomes are appended below it later. Rows are 1-based.

**Design: what changes from G9.**
- **T1 is the only hard tier:**
  - set equality on the `(source_doc_id, page)` set;
  - the refusal identity on row 25.
- **T2 and T3 are reported,** as deltas against the unchanged `eval/smoke_snapshot.json`.
  - A T2 breach, faithfulness < snapshot − 0.2, becomes a `::warning::` and a "below floor (reported)" row. It is
    never red.
- **Unchanged:** the caps, the content-keyed cache and the negative-proof mechanism. The negative proof now proves T1
  red via row 1 (k=2), and records T2's reported line on row 4 (the canned unfaithful answer).
- **No new T2 mechanism and no re-derived T2 threshold.** G9's row-24 data is not used for design.

**Diagnostics added in G9b.** None of them changes a verdict.
1. **Encrypt-on-breach.**
   - **When:** a row trips T1, or trips the reported T2 floor.
   - **What:** the runner encrypts **that row's answer text only**. Contexts, questions and other document text are
     never encrypted or written.
   - **How:** with the OpenPGP public key committed at `eval/smoke_pubkey.asc`. The ciphertext goes in the artifact
     next to the hashes. The private key is held off-repo by the maintainer.
   - **Without gpg,** no ciphertext is written: never plaintext.
2. **The generation call's response headers.** The runner logs `openai-organization`, and `openai-project` if present,
   in CI and locally. These identify the calling account, not a credential.
3. **A `rows` dispatch input,** for diagnostic runs on a subset of the 8. Those runs are labelled DIAGNOSTIC and are
   outside the demotion window.

**In-sample, stated plainly.** T1's 48/48 (G9's runs #5–#10) is in-sample. The 5 fresh characterization runs on the
G9b build commit are the out-of-sample test.

**Predictions:**

| prediction | what | falsified by |
|---|---|---|
| **P1b:** no false red | 5 `workflow_dispatch` runs on the G9b build commit give **0 reds** (T1 is the only red tier) | any red |
| **P2b:** a true red | the negative proof goes red, with **T1 row 1** named. The row-4 T2 reported line is recorded | a green run |
| **P3b:** retrieval stability | T1 set equality holds on **40/40** (8 rows × 5 runs) | any row's set varying |

**The required check** is turned on only if P1b **and** P2b hold, and the maintainer does that. The agent doesn't
change branch protection.

**The demotion rule** is registered again, unchanged: **two noise reds in any twenty gated runs** → reported-only, and
the threshold is re-derived.
- **The window** starts at G9b's first characterization run. PR and push runs on commits carrying the G9b build count.
- **Outside it:** the negative proof, diagnostic runs and baselines.

**Reading answer B (G9's row 24), registered.**
1. Read B from the ciphertext of G9b's 5 characterization runs and P2b. Its answer sha256 is `b558d1354cdd4937c4b0092c2d8f36b770cdcc3268831d4abe8727058f521c0c`.
2. If B doesn't appear, dispatch up to 5 `rows=24` diagnostic runs, stopping at the first B (cap $0.03).
3. If it still doesn't appear, B is recorded as unclassified: "B reproduced only on GitHub-hosted runners (2/7) and
   never locally (0/24 plus 23 historical); its text was not readable through the public artifact".
4. **Once read, B is labelled either:**
   - **(i)** an unsupported claim at temperature 0, naming the figure or claim that differs;
   - **(ii)** the judge penalizing a phrasing change.

   **Either way,** G9's two reds stay classified as noise, because the code was unchanged.

## G9b outcome (recorded 2026-10-09)
**P1b, P2b and P3b all HOLD.** The G9b pre-registration above is unchanged; this section only appends. Rows are
1-based; times are UTC.

**Runs.** All are on the G9b build, `8ed1667`.

| run | trigger | created (UTC) | role | result | faithfulness hits | T2 reported (not red) | cost | `smoke` job |
|---|---|---|---|---|--:|---|--:|--:|
| #14 | pull_request (head `8ed1667`) | 05:46 | gated PR run | green | 7 | — | $0.0069 | 45 s |
| #15 | workflow_dispatch | 05:46 | **P1b, run 1** | green | 6 | row 21 (0.3333) | $0.0093 | 48 s |
| #16 | workflow_dispatch | 05:47 | **P1b, run 2** | green | 8 | row 21 (0.3333) | $0.0056 | 33 s |
| #17 | workflow_dispatch | 05:48 | **P1b, run 3** | green | 7 | row 21 (0.3333) | $0.0069 | 37 s |
| #18 | workflow_dispatch | 05:49 | **P1b, run 4** | green | 7 | — | $0.0076 | 41 s |
| #19 | workflow_dispatch | 05:50 | **P1b, run 5** | green | 8 | — | $0.0056 | 36 s |
| #20 | workflow_dispatch | 05:52 | **P2b, negative proof** | red, as required | 8 | rows 4 and 21 | $0.0053 | 34 s |
| #21–#25 | workflow_dispatch, `rows=24` | 05:53–05:56 | DIAGNOSTIC (reading B) | green | 1 each | — | $0.0012 each | 22–28 s |

### P1b — HOLDS: 0 reds in 5 runs (#15–#19)

### P2b — HOLDS
- Run #20 went red with **T1 row 1** named (the k=2 set kept 2 of 10 pages).
- T2's reported line on row 4 was present (the canned answer's faithfulness was 0.0).
- `negative_proof.held` was true.

### P3b — HOLDS: 40/40
T1 set equality held on every row in every run. It is 48/48 counting PR run #14.

### Reported T2 breaches: row 21, in 3 of 5 runs, plus P2b
**The scores.** In #15, #16 and #17, row 21 served one of two new answer variants:
- faithfulness **0.3333**, against the snapshot's 1.0;
- correctness 0.9557 and 0.9565, against the snapshot's 0.9564.

**Under G9's hard T2, 3 of these 5 runs would have been red.**

**Read through the ciphertext** (local only; no text is committed). Both variants:
- conclude that the two documents agree on 1 ppm (3 mg/m³);
- attribute "the OSHA air contaminants table" value to the NIOSH Pocket Guide page whose entry reports OSHA's PEL.

OSHA Table Z-1's own chlorine line is not in row 21's single-query context (G12's finding).

**Not CI-only.** A low-faithfulness row-21 variant (0.3333) also appeared in a local dev run on 2026-10-08.

### Answer B (G9's row 24): unclassified, by the registered rule
**The rule's wording:** B reproduced only on GitHub-hosted runners (2/7) and never locally (0/24 plus 23 historical);
its text was not readable through the public artifact.

**Added, from the G9b runs:** after encrypt-on-breach existed, B never recurred.
- Row 24 served A in all 14 CI observations after 05:11 UTC (#11, #13–#25).
- Its two B observations (#6 at 05:07 and #10 at 05:11) were the only ones.
- Every local regeneration came after 05:11 (0/24 at about 05:25; 0/1 at about 05:45).
- **So the data cannot separate the calling environment from a time window.**

**The account headers are the same.** The generation call's `openai-organization` and `openai-project` headers were
identical in CI and locally, so an account difference doesn't explain B.

**The conditional G6 cross-reference is not added,** because B is unclassified.

### Encrypt-on-breach, observed
Every breach ciphertext from #15–#17 and #20 (6 files) decrypted with the off-repo key to text whose sha256 matched the
reported `answer_sha256`. No plaintext answer appeared in any downloaded artifact.

### The demotion window and the required check
- **G9b's window so far:** 6 gated runs (#14–#19), 0 reds. The diagnostic runs and the negative proof are outside it.
- **The required check:** P1b and P2b hold, so `eval-smoke / smoke` (and `gate`) may become required checks. The
  maintainer turns that on; the agent doesn't change branch protection.
