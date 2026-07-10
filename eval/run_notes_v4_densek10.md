# v4 (dense k=10) — WRITTEN PREDICTION, recorded BEFORE the eval

Change: **retrieval depth only** — `RETRIEVAL_K=10` (was 5), query-time, over the v2/v3 `semantic`
namespace. Chunking, dataset, generation prompt, model (temp=0/seed=42) all unchanged. One knob.

**Why depth, not hybrid.** A read-only 3-arm rank sweep (zero eval cost) falsified the hybrid lever: a
dense k-bump (5→10) **strictly dominates** BM25+dense RRF fusion — depth recovers Flowserve *and*
ammonia-PEL, evicts **0** currently-retrieved answer chunks (raising k only *adds* ranks 6–10, never
evicts — a mathematical guarantee), whereas hybrid recovers only Flowserve and evicts 2 passing rows
(NH₃-autoignition, NIOSH-leading-factor) via BM25 re-ranking. Hybrid is dropped, not run — the 2nd
pre-registered retrieval hypothesis the sweep killed for free (1st: IDLH-fusion). Reporting the
falsification honors the pre-registration; a head-to-head hybrid eval to reconfirm it is sunk cost.

## RETRIEVAL predictions — PINNED (deterministic; verified by the sweep AND the shipped pipeline gates)
- **RECOVER into top-10:** `flowserve-mark3-pump-p40-60` (§5.7 ordered steps; dense rank 7) and
  `niosh-pocket-guide-p44-47` (dense rank 6). Gate confirmed both are **absent at k=5, present at k=10**.
- **0 EVICTIONS:** every answer chunk dense already ranked ≤5 remains in top-10 (guarantee; gate confirmed).
- **NOT recovered (out of top-10):** IDLH (`p44-47` at rank 20 for that query), acetone (`p7-6` rank 107).

## GENERATION predictions — the open question (retrieval-in-context ≠ correctness; v2 Flowserve: recall 0→1.0 yet correctness 0.013)
- **ammonia-PEL → PREDICT RECOVER, with a flagged EXTRACTION risk.** VERIFIED `niosh-pg-p44-47` carries
  ammonia's `NIOSH REL: TWA 25 ppm` AND `OSHA PEL†: TWA 50 ppm` in the "Ammonia Formula: NH3" block
  (both compared values present + labelled → no partial-comparison-guard block, unlike IDLH where 300 is
  never retrieved). CAVEAT: p44-47 is a **4-chemical** chunk (2-Aminopyridine REL/PEL **0.5 ppm**,
  Amitrole, Ammonia, Ammonium chloride) — the model must extract AMMONIA's 25/50, not a neighbour's 0.5.
  **Arbiter (READ):** response gives OSHA PEL **50** / NIOSH REL **25** for ammonia; a neighbour's 0.5 or
  a one-sided refusal falsifies RECOVER.
- **Flowserve → PREDICT retrieval-FIXABLE but NOT retrieval-FIXED (modest rise, no clean pass).** The
  §5.7 steps (`p40-60`: "a) Open the suction valve to full open position…") now enter context, but the
  pre-start CHECKLIST chunk v3 answered from is *also* still present (0-eviction) and v3 anchored on it.
  **Decisive arbiter = the RESPONSE'S FIRST STEP:** "open the suction valve fully" ⇒ §5.7 sequence won
  (fixed); "ensure the pump/motor are secured to the baseplate" ⇒ checklist won (not fixed). PREDICT
  checklist still wins or blends → correctness rises above 0.235 but does NOT cleanly pass. Real fix =
  generation-side checklist-vs-sequence disambiguation (v5/deferred). Informative either way.
- **IDLH / acetone → PREDICT NO CHANGE** (answer chunk out of top-10; still refuse/fail).
- **23 currently-passing rows → PREDICT mostly HOLD.** 0-eviction ⇒ every answer chunk stays in context;
  correct values remain available. Dominant regression MECHANISM if any = **over-elaboration/verbosity**
  from 2× context (answer_correctness penalises vs terse references), NOT wrong-value substitution.
  Per-watch-row (from reading the ranks-6–10 chunks k=10 newly injects):
  - **NH₃ autoignition (651 °C):** injects filler + PG ammonia entry (other temp fields) → low risk → HOLD.
  - **NH₃ boiling point (−33 °C):** injects one plausible-wrong neighbour (`niosh-pg-p228` "BP: 176 °F" of
    a *different* chemical) → HOLD; **the one row where a dip could be real wrong-value contamination** —
    if it moves, READ whether "176 °F"/"~80 °C" leaked into the answer (real regression) vs verbosity.
  - **NIOSH-152 (152/1,281):** injects pure filler (bibliography, address, unrelated EPA text) → HOLD.
  - **NIOSH leading-factor (82%/124):** injects on-topic LOTO narratives → verbosity risk → HOLD-but-watch.
  Net aggregate: within noise (±0.03); the per-row ledger is the signal.

## Methodology (standing)
- **RAGAS `context_recall` is UNRELIABLE as "was it retrieved"** — false 1.0s on acetone & Flowserve
  (both retrieval misses). Arbiter for retrieval = reading `retrieved_contexts`, never the metric.
- **Retrieval is deterministic** (no LLM) ⇒ the RETRIEVAL predictions confirm from ONE v4 run;
  replication (2× per config, spaced, fingerprint-tagged) is only for generation text + aggregate noise.
- Judge by READING (Flowserve first-step; ammonia 50/25; NH₃-bp 176-leak); verify any correctness
  regression against the raw `retrieved_contexts`; aggregate is context only, with ±0.03 / fp-drift bounds.
