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

## Result (fingerprint-matched, single interleaved run)
**28/28 rows shared `fp_c881474fd1`** — a true like-for-like (zero drift; the pre-registered ≥20-row
bar cleared). Per-metric v2 (semantic_v2) vs v4 (semantic), over the shared-fp intersection:

| metric | v2 | v4 | Δ | \|Δ\|≤0.03 | n |
|---|--:|--:|--:|:--:|--:|
| faithfulness | 0.9474 | 0.9397 | +0.0077 | YES | 19 |
| answer_relevancy | 0.8702 | 0.8351 | +0.0351 | no (UP) | 28 |
| context_precision | 0.7444 | 0.7630 | −0.0186 | YES | 28 |
| context_recall | 0.9833 | 0.9375 | +0.0458 | no (UP) | 20 |
| answer_correctness | 0.5932 | 0.5831 | +0.0101 | YES | 28 |

**Prediction verdict:** the "aggregates within v4's ±0.03 once fingerprints match" prediction HELD for
faithfulness, context_precision, answer_correctness — **confirming the Gate-2 faithfulness/precision
dips were fingerprint DRIFT, not the lever** (faith flipped −0.024→+0.008; precision −0.036→−0.019).
answer_relevancy (+0.035) and context_recall (+0.046) came in ABOVE the band — v2 is BETTER, not
worse (recall is a RAGAS-noisy metric over n=20, so directional). **No metric regressed beyond ±0.03.**

**Per-row reads (rows 8/20/24, all shared_fp):**
- **row 8 IDLH — full recovery reproduced:** correctness **0.363 → 0.974**; answer states "NIOSH IDLH
  … 300 ppm [niosh-pocket-guide page=45] … EPA RMP … 200 ppm" + the comparison. Citation p45 = gt.
- **row 20 chlorine — improved:** correctness **0.456 → 0.955** (a finer-NIOSH intrusion helped this
  "invariant" row; states OSHA ceiling 1 ppm / 3 mg/m³). Moved UP, not a regression.
- **row 24 acetone — still fails (recorded negative):** v2 refuses ("does not contain the answer");
  correctness 0.114 → 0.036 (honest refusal over v4's wrong value).

**2×-replicate:** pre-registered trigger NOT met — shared-fp = 28 (≥20) AND faithfulness + correctness
both in ±0.03. (The scratch script's all-5 `band_ok` flagged replicate, but that is stricter than the
pre-registered faith-OR-correctness trigger; not binding.) No replicate run.

**Promotion-bar mapping (`b38b026`):** IDLH read-verified recovered ✓ · 0 confident-wrong ✓ (acetone
refuses; changed rows moved up/held) · invariant COPY-integrity intact ✓ (0 copy bugs; but 3
"invariant" rows moved UP via finer-NIOSH intrusion) · **all-5-within-±0.03 ✗ — failed ONLY because
relevancy + recall IMPROVED beyond +0.03, not by any regression.** So the bar's letter fails on
over-performance; its spirit (no regression, quality held-or-better) passes. Promotion is a judgment
call for the reviewer, not an automatic disqualify. `semantic_v2` was NOT promoted *in this doc* — that
was a separate later decision, and it WAS made: under the corrected asymmetric bar (`cef7e24`) semantic_v2
was **PROMOTED** as the default `RETRIEVAL_NAMESPACE` (`8205164`), pinned in `render.yaml` (`4979002`). See
BAR CORRECTION below for the corrected criterion this promotion was decided against.

## BAR CORRECTION (spec fix, recorded before the promotion decision)
The pre-registered clause "all 5 metrics within ±0.03" was **mis-specified** — a symmetric band
wrongly treats a metric improving beyond +0.03 as a failure, which is incoherent for a promotion
gate. **Corrected clause (asymmetric): NO metric regresses beyond −0.03; improvements are
unbounded.** This correction was forced by relevancy +0.035 and recall +0.046 — both IMPROVEMENTS —
so it cannot be motivated reasoning toward a desired result; it would have made no difference had
those metrics moved down. Under the corrected bar: **promotion-eligible** (IDLH recovered ✅, 0
confident-wrong ✅, invariant intact ✅, no regression beyond −0.03 ✅ — precision −0.019 is the
largest and it's in-band).
