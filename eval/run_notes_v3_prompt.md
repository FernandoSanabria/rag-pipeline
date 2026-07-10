# v3 (generation prompt) — WRITTEN PREDICTION, recorded BEFORE the eval

Change: **generation prompt only** (`src/generate.py` SYSTEM_PROMPT). Query-time only — reuses the
v2 `semantic` namespace; chunking, retrieval, and dataset unchanged; temperature=0, seed=42, model
fixed; exact refusal sentence preserved. Committed before running so the prediction is falsifiable.

## Goal / win condition
Recover the retrieval-solved-but-refused rows by allowing synthesis + two-source comparison, and
hold/raise faithfulness on multi-claim chunks — WITHOUT new confident-wrong answers. Win =
retrieval-solved rows recover AND no answer-absent row flips to a confident-wrong attempt.

## Predictions
- **Expect RECOVERY (retrieval-solved, currently refused):**
  1. Flowserve start sequence (ctx_recall 1.0 at v2, correctness 0.01 — steps retrieved, refused).
  2. Chlorine ceiling agreement (ctx_recall 1.0, ctx_precision 1.0, correctness 0.03 — perfect
     retrieval; both OSHA ceiling values ARE present, model must synthesize the agreement).
- **Expect faithfulness on procedure / fact to HOLD or RISE** (rule 4 = ground every claim; better
  handling of larger multi-claim chunks; v2 dipped procedure 0.75→0.50, fact 0.75→0.67).
- **Expect NO CHANGE / STILL REFUSE:** IDLH-vs-endpoint. Only ONE compared value is retrieved (EPA
  endpoint 200 ppm); the NIOSH IDLH 300 ppm is NOT in context. Rule 3 must keep this refused (or
  state only the present value). **If it flips to a confident WRONG two-part comparison → REGRESSION,
  flag loudly.**
- **Watch collateral:** any v2-passing row that flips to failing (e.g. a fact row that starts
  over-synthesizing / hallucinating from a larger chunk).

The report will confirm/falsify each, with a 3-way refusal-integrity audit (exact refusal /
near-miss refusal / genuine attempt), verbatim quotes for every refusal→attempt flip, and a
per-row correctness diff vs v2.
