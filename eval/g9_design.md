# G9 — Evaluation in CI: design (GATE 1)

Recorded 2026-10-08. This is the record of the read-only probes and the design, accepted at GATE 1 **before any
workflow code**. The pre-registration is [`g9_PREDICTION.md`](g9_PREDICTION.md).

**Status:** see the Outcome section of [`g9_PREDICTION.md`](g9_PREDICTION.md).

**What G9 is.** A smoke suite of 8 rows runs automatically. It must:
- fail the build on a real regression;
- not fail on the system's own measured noise;
- be proven red on purpose.

The full 28-row suite stays manual (`eval/run_eval.py`).

**Budget:** at most 4 build-days and $3.00, including the characterization runs.

**Frozen:**
- `src/**` and `eval/dataset.jsonl` (the smoke set references rows by index);
- `run_eval.py`'s metric set, the namespace and k;
- **`.github/workflows/ci.yml`**: hermetic, no secrets, no network, required for merge. G9 does not touch it.

**Rows are 1-based** (the G6 convention).

**Starting point.**
- G12 closed as PR #36, squash-merged as `9200676`. The push-triggered wire-smoke passed on that commit (2026-10-08).
- **A correction to my G12 plan:** I wrote that CI checks out with depth 1. In fact `ci.yml` uses `fetch-depth: 0`.
  Nothing committed relied on the claim.
- **Probe spend:** about $0.0001 (8 embedding calls and 8 Pinecone queries). Everything else was read from local files.

## GATE 1 decisions (accepted 2026-10-08)
- **Hard tiers:** T1, including the refusal-identity check on row 25, and T2 at δ_f = 0.2. T3 is reported only.
- **The cache:** build it.
- **Equipment row:** row 4.
- **Agent coverage:** row 24 on `/ask/agent`, with the fan-out off as shipped.
- **Required check:** yes, after P1 **and** P2 both HOLD, subject to the demotion rule (D).

## Refinements accepted at GATE 1
**A. The cache merges; it never replaces.**
- The runner reads the restored `.smoke-cache/judge.json`, merges its new entries in, and writes the union.
- It is saved under the key `smoke-judge-${{ github.run_id }}-${{ github.run_attempt }}`, restored by the prefix
  `smoke-judge-`, and the save step runs with **`if: always()`**:
  - a red run's scores are content-keyed and harmless;
  - the negative proof's canned answer gets its own key, which never matches a real row.
- A hermetic test covers the merge.
- **Cache scope:** runs on a branch ref share entries. PR runs (the merge ref) see their own entries and the base
  branch's. After the merge, `main`'s cache is visible to every PR.

**B. Snapshot scores.**
- `dense_search` drops Pinecone's score (as found in G5). So the top-11 scores come from a **direct Pinecone query in
  the runner**, outside `src/`, reusing the query vector the pipeline embedded.
- They are used **only** for P3's near-tie attribution. **The set is the gate; the scores are advisory.**

**C. Secrets surface.** One line goes in KNOWN_LIMITATIONS and the PR body:
- the keys reach `pull_request` runs from same-repo branches, so anyone with write access could read them by editing
  the workflow in a PR. That is acceptable for a single-owner repo, named as the trade-off;
- fork PRs never receive them; they skip, which is neutral;
- a skipped required check counts as satisfied.

**D. The running false-red ledger.**
- The G9 block in METRICS_HISTORY gets an append-only **"smoke reds"** list: every red on a gated run, with the row,
  the tier, and whether it was a true regression or noise, decided from the artifact.
- **Pre-registered demotion rule:** **two noise reds in any twenty runs** → the check is demoted to reported-only,
  and the threshold is re-derived from the accumulated runs.

**Secrets.** `OPENAI_API_KEY` and `PINECONE_API_KEY` exist as repository secrets. Before the baseline they are
confirmed **by name only**.

**Sequencing.**
- After the build, the branch is pushed so the workflow can run.
- GitHub dispatches only workflows it has registered. If a dispatch on the branch fails, the draft PR opens early, and
  its `pull_request` run registers the workflow.
- Before a snapshot exists, the smoke job **skips with a notice** ("no snapshot — baseline pending"), so the PR isn't
  red for a setup state.
- **Order of runs:** a dispatched baseline; the snapshot committed; 5 dispatched characterization runs on that same
  commit; then the negative proof.

## C1 notes: decisions the predictions depend on
These are written at C1, after GATE 1 and before any code. Each realizes an accepted decision; none changes a tier or
a threshold.

1. **What is under test.** The rows go through `api.main._answer`, the one wiring point both endpoints share:
   - `/ask`: `src.pipeline.ask`;
   - `/ask/agent`: `agent.graph.ask`.

   The judged answer is the **served** answer, after the output guard. The contexts are the ones the pipeline returned.
   The response model and the HTTP layer are not exercised; `tests/test_api.py` covers them.
2. **The T1 set** is the `(source_doc_id, page)` set of **document** chunks. Synthetic tool chunks (`page = None`)
   are excluded and counted separately (reported). On row 24 a router fallback to direct changes the set, so T1 sees
   it. The route is recorded but not gated.
3. **The repository is public, so its logs and artifacts are public.** The runner writes **derived values only**:
   - page sets, scores and hashes;
   - no answer, context or question text, in the log, the artifact or the snapshot.
4. **The paths filter is a gate job, not `on.paths`, on `pull_request` and `push`.** A required check behind an
   `on.paths` filter never reports on a PR that doesn't match, and so it blocks that PR.
   - The gate job (no secrets read, no network beyond the checkout) computes the changed files, checks that both
     secrets are present, checks that a snapshot exists, and writes a notice and a job summary saying why.
   - The `smoke` job runs only when all three allow it; otherwise its check shows **Skipped**.
   - `workflow_dispatch` ignores the paths, and a `baseline` dispatch ignores the missing snapshot.
5. **The cache key** is the sha256 of the JSON list:

   ```text
   [metric, judge model, embedding model, ragas version, question, answer, contexts, reference]
   ```

   - This is the packet's key plus three things that also change a score: the question, the embedding model
     (answer correctness's similarity half) and the ragas version.
   - **What is stored:** scores only, keyed by hash. NaN is never cached.
   - **Merge conflicts:** the entry already in the file wins.
6. **A cache hit (P4)** is a row whose faithfulness, the gated metric, came from the cache. Correctness hits are
   reported alongside.
7. **Caps:**
   - **generation calls** are calls to `src.generate.generate`, counted by its backend accumulator; the agent row's
     router and tool calls are reported, not capped, since the tool loop has its own bound of 3;
   - **judge calls** are completed calls on the judge model, counted by a callback.
8. **Exit codes:**
   - 0: green;
   - 1: a hard-tier failure (red), including the negative proof's intentional red;
   - 2: a cap breach or a configuration error (a row missing, a dataset hash mismatch, conflicting flags).
9. **The negative proof** replaces row 4's served answer after the pipeline and before judging. The output guard
   never sees it: the proof simulates an unfaithful answer being served. Row 1 runs with `retrieval_k = 2` through a
   patched settings object in the runner; `src/` is untouched.
10. **Cost and time, for P5:**
    - cost is derived from token counts: chat tokens by `get_openai_callback`, embedding tokens by tiktoken, priced
      from `eval/COST_LEDGER.md`;
    - time is the `smoke` job's duration from the Actions API, from start to completion, including `uv sync`.

## R1 — Choosing the smoke set from data, not taste
**Sources.** Only two **scored** runs of the promoted pipeline (`src.pipeline`, `semantic_v2`, k=10) exist:
- **run A:** the Gate-2 run, 2026-08-02 (`fp_c881474fd1`);
- **run B:** the like-for-like run's `semantic_v2` arm (`fp_c881474fd1`).

**Supplementary sources, labelled as such:**
- **Answers:** G6's 3 same-process `/ask` trials (2026-10-04, `fp_fb62ae2309`). That makes 5 `/ask` answer samples per
  row.
- **Retrieval sets:** the agent's direct path, which makes the identical `dense_search` call. That covers 2C, G1, the
  G1 closure's CAP=0 and CAP=3 arms, and G12's current arm. Source-scoped rows and tool chunks are excluded. This gives
  up to 8 observations per row, from 2026-08-02 to 2026-10-08.
- **Scores:** 2C and G1 agent scores, as a second view.

**What the data shows:**
- **Retrieval is stable.** All 28 rows have **1 distinct (source_doc_id, page) set** across every observation.
- **Full context lists** (order and text) are byte-identical across 3 files on every candidate row that was checkable
  (rows 1, 4, 15, 20, 21, 26).
- **Live gaps between ranks 10 and 11** run from 0.00056 (row 21) and 0.00093 (row 25) up to 0.018 (row 1). The
  embedding swap G5 recorded was between chunks 8e-06 apart, so even the tightest gap is about 70× larger.

**The 8 rows.** Every row's retrieval set was stable; "answers" counts distinct answers among 5 samples.

| row | path it covers | answers | faithfulness (A, B) | correctness (A, B) | supplementary |
|---|---|--:|---|---|---|
| 1 | EPA RMP fact (single document) | **1/5** | 1.0, 1.0 | 0.6827, 0.6827 | agent the same |
| 4 | **equipment manual** (Fisher 667) | **1/5** | 1.0, 1.0 | 0.7853, 0.7853 | agent the same |
| 15 | OSHA lockout/tagout regulation | **1/5** | 1.0, 1.0 | 0.453, 0.4947 | agent 0.4947 ×2 |
| 20 | SDS identifier (Airgas UN number); **touched by G6** (a false positive in v2) | **1/5** | 1.0, 1.0 | 0.3516, 0.3516 | agent the same |
| 21 | **comparison**; **touched by G12** | 4/5 | 1.0, 1.0 | 0.9568, 0.9546 | agent 0.9553–0.9573 (5 runs) |
| 25 | **the refusal row** on `/ask` (acetone flash point) | **1/5** (whole refusal) | 0.0, 0.0 | 0.0362, 0.0362 | — |
| 26 | NIOSH narrative (Alert Case 1) | **1/5** | 1.0, 1.0 | 0.4733, 0.4733 | agent 0.5767 ×2 |
| 24 | **source-scoped**, run through **`/ask/agent`** (Nutrien boiling point) | agent: identical | agent 1.0, 1.0 | agent 0.911, 0.911 | route `source_scoped` in 5/5 agent runs; scoped set stable in 4/4 |

**Excluded for variance:**
- **Row 9** (comparison; the G1/G12 row): 5/5 distinct answers, correctness 0.63–0.97 across 7 observations, and G12's
  judge variance on identical contexts.
- **Row 3** (equipment; the G6 v1 false positive):
  - 4/5 distinct answers;
  - faithfulness 0.8 vs 1.0, and 0.667 on the agent path;
  - correctness 0.16–0.77.
- **Row 11** (comparison): correctness 0.55–0.92.
- **Rows 2, 12, 16 and 27:** correctness spreads of 0.12–0.21. Row 27 also had 5/5 distinct answers.
- **Row 22** (source-scoped): agent faithfulness 0.667 ×2, and 3 distinct answers.
- **Row 10:** stable, but row 21 is tighter for the comparison path.

**Two places where the spec and the data conflicted** (both decided at GATE 1):
1. **Equipment row.** The spec named row 3, the noisiest equipment row. **Row 4** covers the same path and is
   perfectly stable.
2. **Source-scoped coverage.** `src.pipeline` (`/ask`) has no router, so "source-scoped" can only be covered on the
   agent path: **7 rows on `/ask`, and row 24 on `/ask/agent`**, with the fan-out off as shipped.

## R2 — The band, derived rather than assumed
**Fewer than 3 same-config scored reruns exist for every row:** there are 2 (runs A and B). The agent arm has 2 (2C and
G1).

**Per-row ranges for the 8, at n=2:**
- **Faithfulness:** 0.0 on every row.
- **Correctness:** 0.0 on every row except row 15 (0.0417) and row 21 (0.0022).

**Aggregate over the 8:**
- **Faithfulness mean:** 0.875 in both runs. Row 25's refusal scores 0.0 by construction.
- **Correctness mean:** 0.5812 against 0.5862.

**Judge NaNs.** On the promoted pipeline across all 28 rows, **7 rows have a faithfulness NaN in at least one run**
(rows 5, 6, 8, 9, 13, 17 had one scored value; rows 12 and 16 had none). **None of the 8 rows** had a NaN (0/16).

**Faithfulness dips on the promoted pipeline across all 28 rows:**
- one, on row 3: 0.8 vs 1.0, on differing answers;
- on the agent path, one more on row 3: 1.0 vs 0.667.

Both rows are excluded.

## R3 — Gate design: three tiers
**T1 — Retrieval** (deterministic; no judge, $0). **HARD.**
- **The check:** for each row, the SET of (source_doc_id, page) must equal the snapshot (set equality,
  order-insensitive).
- **Plus, on the refusal row only:** the answer must still be the whole refusal (`api.confidence.is_refusal`).
  Faithfulness is 0.0 by construction there and cannot see a change.
- **A regression means:** a row's retrieved evidence changed, or the refusal row stopped refusing.
- **Expected false-red rate:** about 0 per run.
  - There were 0 set variations in about 45 row-observations over two months.
  - The tightest rank 10/11 gaps (rows 21 and 25) are about 70× the observed embedding perturbation.
  - The snapshot also records the top-11 scores (advisory, refinement B), so P3's near-tie attribution can be checked.

**T2 — Faithfulness** (judged; the floor is about 0). **HARD.**
- **The rule:** red iff faithfulness < snapshot − **δ_f = 0.2**.
- **Judge NaN:** retry once; if it is still NaN, the row is **NOT SCORED** (reported, not red).
- **Why δ_f = 0.2:**
  - R2's own range for the 8 rows is 0.0, but n=2, which is too thin to set a floor.
  - 0.2 is the largest per-row faithfulness spread observed anywhere on the promoted pipeline (row 3), which is one
    unsupported statement in five.
- **A regression means:** the answer now makes claims its retrieved context doesn't support, beyond the one-statement
  noise level observed.
- **Expected false-red rate:**
  - There were 0 drops above 0.2 in the 19 promoted row-pairs (0 of 8 among the chosen rows), and 1 in the agent pairs
    (row 3, excluded).
  - With the cache (R4), the rows whose answers reproduce reuse their judged scores, so judge noise can't redden them.
  - The residual is about 0 per run. The rule-of-three bound per changed-row evaluation is under 16%, which is weak;
    **P1's five runs are the real number.**

**T3 — Answer correctness** (judged; noisy). **REPORTED only** (the spec forbids gating it at N=1).
- Shown as the delta against the snapshot.
- A 2-replicate mean would cost about +$0.013 per run and stay noisy (per-row swings up to about 0.25 in the ledger),
  so it is not built.

## R4 — The cache
**Key:** the sha256 in C1 note 5, stored with `actions/cache` under the merge semantics of refinement A. It is not a
committed file.

**Predicted hits:** about **7 of 8 rows per run** after the baseline: rows 1, 4, 15, 20, 24, 25 and 26.
- **The evidence:** their answers reproduced across every sample. Context lists were byte-identical across 3 files for
  rows 1, 4, 15, 20 and 26.
- **Weaker evidence for rows 24 and 25:** only page-set stability, since their byte-level lists weren't comparable.
- **Row 21's answers vary** (4/5 distinct), so it should miss.

**Saving:**
- in dollars, about 7 × $0.0016 ≈ **$0.011 per run**, at the ~$0.01 threshold, so not worth building for money alone;
- the stronger reason is **determinism**: on an unchanged row the gate compares the snapshot with itself, so judge
  variance can't cause a false red. Judge variance on identical inputs has been recorded twice already.

## R5 — Workflow shape and cost
**`.github/workflows/eval-smoke.yml`.** It is separate, and **`ci.yml` is not touched**.
- **Triggers:**
  - `pull_request` to main and `push` to main, filtered by the gate job (C1 note 4) on `src/**`, `agent/**`,
    `api/**`, `eval/smoke_*`, `scripts/smoke_eval.py`, `eval/run_eval.py`, `pyproject.toml`, `uv.lock` and the
    workflow itself;
  - `workflow_dispatch`, with `simulate_regression`, plus a `baseline` input that writes a snapshot artifact.
- **When secrets are absent** (fork PRs):
  - the gate job outputs whether `OPENAI_API_KEY` and `PINECONE_API_KEY` are present, and posts a `::notice::` and a
    job summary;
  - the `smoke` job runs only if they are present, so otherwise its check shows **Skipped**: neutral, never red, never
    a silent green.
- **Environment:**
  - `INDEX_NAME: equip-docs-rag` is a literal, as committed in `render.yaml`. Only the two secrets are needed.
  - Tracing is off.
- **Execution limits:**
  - concurrency: cancel-in-progress per PR;
  - timeout: 15 min;
  - setup: uv 0.11.25 via `astral-sh/setup-uv@v5`, as `ci.yml` does.
- **Required check:** only after P1 and P2 hold (GATE 1 decision 5).
- **Cost per run:**
  - generation: 7 × $0.0009 on `/ask`, plus about $0.002 for the agent row;
  - judge: 8 × about $0.0016, for faithfulness and answer correctness;
  - **total about $0.021 per run without the cache, about $0.010 with it.**
- **Wall time:** about 3–4 min, including `uv sync`.
- **Hard caps in the runner:**
  - at most 8 rows;
  - at most 10 generation calls;
  - at most 120 judge calls, counted by a callback.

  A breach aborts the run with exit 2 and names the cap.

## R6 — The negative proof (`workflow_dispatch`, `simulate_regression=true`)
**What it does:**
- **(a)** row 1 is retrieved with **k=2**, so its page set differs from the snapshot and **T1 goes red**;
- **(b)** row 4's answer is replaced, before judging, with a canned unfaithful sentence. It is a fabricated pressure
  and rating, a cache miss, and **T2 goes red**.

**The required result:** the job exits non-zero with **both** failures named. The run is named "SIMULATED REGRESSION
(intentional red)", mirroring the wire-smoke's "all intolerant paths went red as required".

**Scope:** the flag lives only in `scripts/smoke_eval.py`. No production code path changes.

## R7 — The snapshot
`eval/smoke_snapshot.json` holds:
- **per row:**
  - the retrieval set, plus the top-11 scores (advisory);
  - `is_refusal`;
  - faithfulness and correctness;
  - the answer's sha256.
- **per run:** the run id, the commit, the date, the generation and judge fingerprints, and **the ledger row** that
  justifies it.

**How it changes:**
- **Only** through a deliberate re-baseline commit, whose message names the ledger row.
- The runner prints a `::warning::` when a PR's diff touches both the snapshot and `src/`, `agent/` or `api/`.

## R8 — Characterization (the core of the pre-registration)
Five `workflow_dispatch` runs on the **same commit** (the branch head after the snapshot is committed), before any
required-check decision. **0 reds are predicted.** Each run's per-row values are recorded in METRICS_HISTORY's G9 block.

## After GATE 1 (appended 2026-10-09)
**Outcome.** G9 as registered: P1 FALSIFIED (2 of 5 characterization runs red, both T2 on row 24), and the check is
demoted to reported-only. See the Outcome section of [`g9_PREDICTION.md`](g9_PREDICTION.md). The G9b pre-registration
follows it there: T1 hard alone; T2 and T3 reported.

**What the smoke evaluates** (C1 note 1, stated plainly).
- **The path.** Each row goes through `api.main._answer`, the one wiring point of `/ask` and `/ask/agent`:
  - the input guard (off in the shipped app);
  - then the pipeline (`src.pipeline.ask`, or `agent.graph.ask` for row 24);
  - then the output guard and the response assembly.
- **The judged answer is the served answer.**
- **Not exercised:** the HTTP layer, the Pydantic response model and status codes. `tests/test_api.py` covers them
  hermetically.
- **So a red can catch:**
  - a change in a row's retrieved evidence (T1);
  - the refusal row no longer refusing (T1);
  - a less-faithful served answer (T2), including a guard change that withholds or passes an answer differently.
- **It cannot catch:**
  - a break in the response contract or in citation rendering;
  - anything on rows outside the 8;
  - the input guard, which is off.

**The breach key (`eval/smoke_pubkey.asc`).**
- **Purpose:** the maintainer can read the one thing the public artifact otherwise withholds, a breaching row's
  served answer, without publishing it.
- **What is encrypted, and when:**
  - only the answer text of a row with a T1 failure or a T2 floor breach;
  - never contexts, questions or other document text.

  It is written as one file per row, `breaches/row-<n>.asc`, in the run's artifact, next to the report's hashes.
- **The key:**
  - an OpenPGP key with primary fingerprint `0A23108E5E612C30A84874FC1A47B75AB89F83AE` (Ed25519) and encryption
    subkey `E6C4AFC0058EF625E2015BF435F761047666962B` (cv25519);
  - only the public key is in the repository;
  - the private key is held off-repo by the maintainer.
- **The failure mode:**
  - without gpg, or on any gpg error, nothing is written: never plaintext;
  - the runner also discards any output that contains the plaintext.
- **Rotation:** replace the `.asc` and `PUBKEY_FINGERPRINT` in `scripts/smoke_eval.py`. Older ciphertexts still need
  the older private key.
