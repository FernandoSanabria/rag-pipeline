# Metrics History

Canonical evaluation is RAGAS with **five** metrics (see `CLAUDE.md`): faithfulness,
answer_relevancy, context_precision, context_recall, **answer_correctness** (vs the hand-verified
`reference`). Every row runs from a clean commit; full provenance lives in the matching
`eval/results/*.json` (gitignored).

| Row | Pipeline / strategy | faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness | commit | result file |
|---|---|--:|--:|--:|--:|--:|---|---|
| v0 | empty stub (baseline floor) | null | 0.0000 | 0.0000 | 0.0000 | not_measured | `9fd0dc7` | `baseline_v0_20260707T002946Z.json` |
| v1 | dense retrieval + generation, `fixed_500_50` chunking, k=5 | 0.7143 | 0.6335 | 0.7761 | 0.7173 | **0.4042** | `a76f09a` | `v1_fixed_500_50_20260709T033851Z.json` |

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
