# Metrics History

Canonical evaluation is RAGAS with **five** metrics (see `CLAUDE.md`): faithfulness,
answer_relevancy, context_precision, context_recall, **answer_correctness** (vs the hand-verified
`reference`). Every row runs from a clean commit; full provenance lives in the matching
`eval/results/*.json` (gitignored).

| Row | Pipeline / strategy | faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness | commit | result file |
|---|---|--:|--:|--:|--:|--:|---|---|
| v0 | empty stub (baseline floor) | null | 0.0000 | 0.0000 | 0.0000 | not_measured | `9fd0dc7` | `baseline_v0_20260707T002946Z.json` |
| v1 | dense retrieval + generation, `fixed_500_50` chunking, k=5 | 0.7143 | 0.6335 | 0.7761 | 0.7173 | **0.4042** | `a76f09a` | `v1_fixed_500_50_20260709T033851Z.json` |
| v2 | semantic boundaries + variable/larger chunks (1258 vs 7635; k=5) | 0.7401 | 0.7092 | 0.8134 | 0.8889 | **0.5152** | `87ae545` | `v2_semantic_20260710T165058Z.json` |
| Δ v2−v1 | | +0.0258 | +0.0757 | +0.0373 | +0.1716 | **+0.1110** | | |
| v3 | generation prompt (synthesis + comparison + ground-claims); semantic ns UNCHANGED, k=5 | 0.8309 | 0.7607 | 0.8258 | 0.9107 | **0.5128** | `6b416ed` | `v3_prompt_20260710T173937Z.json` |
| Δ v3−v2 | | +0.0908 | +0.0515 | +0.0125 | +0.0218 | **−0.0024** | | |
| v4 | dense retrieval, semantic ns, **k=10** (`RETRIEVAL_K`); prompt/chunking UNCHANGED | 0.9697 | 0.8489 | 0.7589 | 0.9374 | **0.5667** | `5e742d2` | `v4_densek10_20260710T235333Z` + `…000441Z` (k=10, 2× mean) |
| Δ v4−v3-dense(k5) | vs same-commit k=5 re-run (2× mean 0.828/0.763/0.818/0.911/0.534) | +0.1421 | +0.0863 | −0.0594 | +0.0267 | **+0.0322** | | |

## Notes
- **METHODOLOGY — run-to-run variance & backend drift (applies to every row).** temp=0/seed=42 is
  deterministic at a FIXED backend fingerprint — a controlled probe (same `_llm()`, one prompt, 10×
  back-to-back) gave 10/10 identical outputs under one fingerprint (`fp_6cc92eaef9`). But OpenAI's
  `system_fingerprint` DRIFTS between runs (this project: `fp_e1dafe7972`→`fp_db84271684`→
  `fp_6cc92eaef9`), and an identical-config rerun of v3 (~35 min later) produced different responses
  on **14 of 28 rows** — consistent with / strongly indicated by between-run fingerprint drift
  rather than within-run sampling (the probe shows generation is deterministic at a fixed fp; the
  two eval runs' own fingerprints weren't persisted, so drift is inferred, not directly confirmed —
  hence the persist-fingerprint fix). Observed noise are ONE-SAMPLE observations (LOWER BOUNDS, one
  redraw each): the correctness judge swung up to **~0.25 on a single row** in a single rerun even
  with a byte-identical answer (faithfulness swing 0.000); aggregate `answer_correctness` moved
  **+0.026** and `context_precision` **−0.030** IN ONE RERUN. These are observations, not
  thresholds/ceilings. Consequences: faithfulness deltas trustworthy; a single run's small per-row
  correctness (~0.25 scale) or aggregate correctness/precision (~0.03 scale) move is within
  variance, not signal; big moves survive (recall +0.17, faith +0.09). seed=42 verified passed
  (best-effort). GAP: per-run generation fingerprint wasn't persisted — persist it going forward.
  GOING FORWARD: replicate each config 2–3×, RECORDING each replicate's fingerprint; note that
  back-to-back replicates may share a fingerprint and understate cross-run drift, so space them (or
  at minimum tag by fingerprint so you know whether replicates actually spanned a drift).
- **answer_correctness for v1 is retro-defined.** At v1 capture, CLAUDE.md still mandated four
  metrics, so `run_eval.py` scored the canonical four and `answer_correctness` (0.4042) was computed
  in a **separate** `evaluate()` pass over the same 28 rows, recorded as a supplementary field in the
  v1 result JSON. It is promoted to canonical here and treated as v1's fifth metric so v1↔v2 (and
  later) comparisons use the same five-metric set. From v2 onward it is produced by `run_eval.py`'s
  single canonical `evaluate()` call (same metrics list, scored against `reference`).
- **v0 answer_correctness = not_measured** (the empty-pipeline baseline predates the metric; leave as
  `not_measured`, do not backfill a fabricated value).
- Key v1 signal (see the Prompt-12 diagnostic): answer_correctness (0.40) ≪ faithfulness (0.71) —
  the pipeline answers faithfully from retrieved text but often answers the wrong thing; 7 of 8
  failing rows were retrieval misses (4 chunk-fragmentation, 3 recall), 1 generation refusal, 0
  metric artifacts. Step 4 prioritizes retrieval (chunking, then hybrid).
- **v2 (semantic) — all 5 metrics improved; recall +0.17, correctness +0.11.** CAVEAT: v2 changed
  chunk SIZE and METHOD at once (1258 larger chunks vs 7635 fixed-500), so gains are "semantic
  boundaries + larger chunks", not boundaries alone. 139 oversized tabular chunks were sub-split at
  6000 chars. Depends on EXPERIMENTAL `langchain-experimental==0.3.4` (boundaries could shift on upgrade).
- **v2 prediction scorecard: 5 right / 3 WRONG** (recorded pre-run in `run_notes_v2_semantic.md`):
  - Right: NaOH pH, NH3 bp, NIOSH Case 1 recovered (fragmentation); IDLH & Chlorine stayed failed.
  - WRONG: (1) **NIOSH 82% factor** and (2) **RMP Program 1** were predicted "won't improve" (recall
    misses needing hybrid) but RECOVERED strongly (correctness 0.02→0.82, 0.04→0.35) — better chunking
    alone fixed pages that fixed-500 fragmented out of the ranking. (3) **Flowserve start** was
    predicted to recover but did NOT (correctness flat 0.013) even though its context_recall went
    0→1.0 — the right context is now retrieved but generation still fails: it FLIPPED from a retrieval
    problem to a generation problem.
  - Metric-shape: predicted context_precision DOWN (larger chunks) — FALSIFIED; precision rose
    +0.037. recall/correctness up as predicted.
  - Per-category: narrative recovered most (answer_correctness 0.15→0.73, faithfulness 0.33→1.0);
    fact & procedure faithfulness DIPPED (0.75→0.67, 0.75→0.50) — larger chunks introduce more
    claims RAGAS finds partly unsupported. One row returned NaN context_recall (27/28 numeric).
- Next lever (data-driven): remaining hard rows (IDLH cross-doc, Flowserve/Chlorine generation) are
  now GENERATION/cross-doc, not simple recall — points at prompt work + hybrid for the cross-doc pair.
- **v3 (generation prompt) — faithfulness win + honest-refusal win; flat correctness is an artifact,
  not a regression.** PRE-REGISTERED win condition ("recover the retrieval-solved rows WITHOUT new
  confident-wrong answers") MET: chlorine 0.03→0.66, Flowserve start 0.01→0.23, Fisher 657 torque
  0.03→0.38 recovered (exact_refusal→attempt); faithfulness +0.091 (procedure dip reversed
  0.50→1.00, fact 0.67→0.73); IDLH correctly HELD (exact_refusal, no fabrication — rule-3
  partial-comparison guard); refusal audit clean (3 refusal→attempt, 0 attempt→refuse, 0 near-miss).
  "No correctness regression" was NOT the pre-registered bar.
  - The apparent "7 correctness regressions" are NOT real quality losses. The arbiter was READING
    all 7 responses: **zero fabrications** — each is factually correct and grounded in its cited
    source. They are elaborate-but-true (answer adds correct, grounded detail that answer_correctness
    penalizes vs the terse hand-written reference) and/or within run-to-run variance — which lives in
    BOTH generation (3/7 had different rerun responses) AND the judge. Examples: NIOSH-152 "1,281 …
    of which 152 involved…" ✓; Flowserve cutwater "3 mm (0.125 in.) … may not prime" ✓; Airgas UN#
    "UN3304" ✓.
  - v3 LOCKED as a faithfulness + honest-refusal win. Flat aggregate answer_correctness is a known
    answer_correctness limitation (added-true-claims vs terse references) + run-to-run variance —
    documented, NOT fixed (no prompt metric-fitting).
  - Next lever = hybrid retrieval (v4), narrow target IDLH-vs-endpoint (NIOSH ammonia IDLH 300 ppm
    is in the Pocket Guide but term-blind dense/semantic retrieval misses it — BM25/hybrid should).
    Judge v4's IDLH target by READING whether its response flips refuse→attempt-with-correct-values
    (noise-immune for a narrow target), not only by aggregate correctness; AND replicate each config
    2–3×.
- **v4 (dense k=10) — FINAL Phase-1 retrieval entry; one knob (`RETRIEVAL_K` 5→10), adopted as the
  shipped retrieval depth (the config default flip lands with the Step-5 service).** Query-time only;
  semantic namespace, generation prompt, chunking and dataset all unchanged. Commit `5e742d2` (the
  RETRIEVAL_K feature) is cited as the metric-producing code; the runs executed at HEAD `d6d66ed`
  (= `5e742d2` + a docs-only pre-registration commit) — the same byte-identical pipeline `enrich`
  stamps into the result files. The aggregates above are 2×-replicate means. All four runs shared
  generation fingerprint `fp_6cc92eaef9` except v4b, whose persisted `generation_backends` caught 1 of
  28 calls spending on `fp_f26e464023` — a real mid-run backend drift, exactly what the
  persist-fingerprint fix (`46b47a9`) was built to surface. The replicates report the same primary
  fingerprint (`fp_6cc92eaef9`) yet **13 of 28 responses still differ** between them — so the reported
  `system_fingerprint` does NOT pin generation across time-separated runs (it held only for the earlier
  tight back-to-back probe). The within-config aggregate spread (e.g. answer_correctness 0.604 vs 0.529
  across the two k=10 runs) is therefore BOTH generation drift — the largest per-row swings (Fisher
  torque 0.71→0.16, ammonia-PEL 0.92→0.60) are on differing responses — AND judge noise on
  identical-response rows (e.g. the ammonia boiling-point row 0.91→0.66), within the standing ±0.03
  aggregate bound. The Δ is measured against a **same-commit k=5 re-run** (2× mean
  0.828 / 0.763 / 0.818 / 0.911 / 0.534), so it isolates k alone; that re-run reproduced the recorded
  v3 row within noise, confirming comparability.
  - **Measured result: two read-verified recoveries, zero real regressions.** ammonia-PEL flips
    refuse→correct (0.019→0.759) — the answer gives OSHA PEL 50 ppm and NIOSH REL 25 ppm, both read
    off the NIOSH-Pocket-Guide ammonia entry that k=10 pulls into context (dense rank 6). Flowserve
    recovers (0.202→0.559) — with both the pre-start checklist and the §5.7 sequence in context, the
    response leads with the ordered sequence ("open the suction valve fully…"). Every per-row
    correctness *dip* was checked by reading the response and is the terse-reference **verbosity
    artifact** — correct core value plus grounded elaboration, faithfulness 1.0 — not a wrong value
    (trench-egress 0.67→0.37 still says "4 ft or more"; Type-A soil 0.66→0.41 still says "1.5 tsf").
    faithfulness rose **+0.142**, trustworthy since its run-to-run floor is ~0; context_precision fell
    −0.06, the mechanical cost of grading twice the chunks. answer_correctness moved +0.03 in aggregate
    — within noise, masking the real structure (big recoveries offset by verbosity dips), so the
    per-row ledger, not the aggregate, is the signal. The retrieval side was proven safe *before* the
    run: raising k only adds ranks 6–10, so it cannot evict a chunk dense already ranked ≤5 (0
    evictions — a guarantee, confirmed by the shipped pipeline).
  - **Findings — the Phase-1 retrieval spine:**
    - **Hybrid (BM25+dense RRF) was FALSIFIED by a read-only rank sweep and never run.** Depth strictly
      dominates it: k=10 recovers 2 rows and evicts 0, whereas hybrid recovered only Flowserve and
      *demoted* 2 currently-passing rows (fusion re-ranks, so it can evict). This is the **2nd**
      retrieval hypothesis killed at zero eval cost (1st: IDLH BM25-fusion). Reporting the falsification
      honors the pre-registration; spending runs to reconfirm a lever the sweep already killed would be
      sunk cost.
    - **RAGAS `context_recall` is unreliable as "was it retrieved."** It returned 1.0 for acetone and
      Flowserve while the answer chunk was absent from the retrieved set. The arbiter for retrieval is
      reading `retrieved_contexts` / a rank probe — never the metric.
    - **Flowserve beat its pre-registered prediction, and that removed a deferred item.** We predicted
      "retrieval-fixable but not fixed — needs generation-side checklist-vs-sequence disambiguation
      (v5)." Wrong: with both chunks in context the model chose the sequence unaided, so the v3 failure
      was **pure non-retrieval** of the sequence. The v5 disambiguation item is removed.
    - **A row can gain +0.34 answer_correctness with its task behaviour unchanged.** IDLH moved
      0.024→0.362 yet still *refuses the comparison* — the k=10 response correctly states the EPA
      endpoint (200 ppm) then honestly declines the NIOSH side it still cannot retrieve. The metric
      partial-credits an honest partial answer; it is neither a recovery nor noise. Read the behaviour,
      not the number.
    - **Citation-accuracy flag (feeds Step 5).** The ammonia-PEL recovery is value-correct but cited
      `page=46` for a `page=44` value — right document, wrong page. Step 5's citation contract is
      {document, page}; page-level citation reliability is an open generation-side concern, flagged
      here, not fixed.
    - **Still unfixed by depth: IDLH and acetone.** Both are genuine retrieval misses at k=10 too — the
      NIOSH-300 ammonia entry sits at rank 20 for the IDLH query, the acetone flash-point chunk at rank
      107. Neither depth nor fusion surfaces them against a cross-source / tabular query; the real fix
      is query decomposition (per-source sub-lookups) or table-aware extraction, scoped to **Phase 2
      (agentic)** — not a retrieval-mode swap.
  - **Closing synthesis.** The Phase-1 retrieval arc was three orthogonal levers, each aimed at a
    diagnosed failure — semantic chunking (recall), the generation prompt (over-refusal + faithfulness),
    and retrieval depth (two cross-doc recoveries) — while two more-complex levers, IDLH BM25-fusion and
    full hybrid, were falsified by read-only probes before a single eval run was spent on them. The
    pipeline we ship — dense retrieval, semantic chunks, k=10, no lexical arm, no fusion — is *simpler*
    than the one originally planned; the simplification was earned by killing complexity with evidence
    rather than adding it on faith.
