# Like-for-like v2-vs-v4 delta (fingerprint-matched) — PRE-REGISTRATION

Committed BEFORE the runs (this commit's hash is the pre-reg). Gate-2 was drift-confounded
(`fp_c881474fd1` ≠ v4's `fp_6cc92eaef9`); this run produces a fingerprint-matched delta for the
promotion decision and honest article numbers. Do NOT promote; do NOT edit graph.py/ingest.py.

## Method (one variable = namespace; INTERLEAVED per row; per-ROW fingerprint pairing)
`run_eval.py` can't interleave two namespaces in one pass without surgery, so a custom script
(gitignored) interleaves per row: for each of the 28 questions, run `src.pipeline.ask` (PIPELINE=v4)
under `RETRIEVAL_NAMESPACE=semantic` then immediately under `=semantic_v2` (switched via
`get_settings.cache_clear()` between calls), so each row's v4/v2 pair shares a generation fingerprint
whenever possible. Capture per-row `v4_fp` / `v2_fp` by counter-delta on `generation_backends()` (as
in ab_repro_probe). Then RAGAS scores all 56 samples (28 v4 + 28 v2) in ONE judge pass.
- **The like-for-like intersection = rows where BOTH ran (non-NaN) AND shared a fingerprint.** Report
  how many of 28 qualify. Aggregate v2-vs-v4 is computed over that set only.
- Report per-RUN and per-ROW fingerprints.

## Predictions (pre-registered)
- **IDLH (row 8) holds recovered** — answer states NIOSH 300 ppm + EPA endpoint; correctness up vs v4.
- **Invariant holds** — the 19 invariant rows' retrieved content unchanged; 0 copy bugs.
- **Acetone (row 24) still fails** — refusal / answer chunk still outside top-10.
- **Aggregates within v4's ±0.03 band on the fingerprint-matched intersection** — i.e. Gate-2's
  faithfulness/precision dips were drift, not the lever.

## 2×-replicate TRIGGER (pre-registered — not a post-hoc read)
Go to 2×-replicate (run the interleaved script 2× total per side, compare MEANS over the intersection)
IFF: the shared-fingerprint per-row intersection is **< ~20 rows** OR **faithfulness or
answer_correctness lands outside ±0.03** on the first pass. Record every replicate's fingerprint.

## PROMOTION BAR (pre-registered — this eval maps directly onto it, no interpretation gap)
**PROMOTE-eligible IFF ALL of:** IDLH read-verified recovered AND 0 confident-wrong (no
Phase-1-passing row flips to a confidently wrong answer) AND invariant intact (copied content
unchanged, 0 copy bugs) AND **all 5 metrics within v4's ±0.03 on the fingerprint-matched
intersection.** (Blast-radius intrusions that don't regress a row do not by themselves disqualify;
a confident-wrong regression or an out-of-band metric does.)

## Result
(appended after the runs)
