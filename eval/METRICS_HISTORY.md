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

## Notes
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
