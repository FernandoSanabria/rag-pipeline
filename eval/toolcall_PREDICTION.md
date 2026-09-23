# G1 tool-calling — pre-registration (written BEFORE the eval run)

Pre-registered per the project's falsify-before-you-run discipline (cf. `eval/decomp_probe_RESULT.md`). Recorded
before running any eval on the G1 tool loop; the result is compared against this, not fitted to it.

## The question
Does adding a tool-calling loop (unit conversion, document-metadata lookup) change **answer quality on the
frozen 28-row set**, or only change **which questions become answerable**?

## The prediction (falsifiable)
1. **The aggregate on the frozen 28 rows is FLAT** — every RAGAS metric within the ±0.03 noise band of the
   comparable prior agent-path run (`semantic_v2 + source-scoped router (2C, agent)` in `METRICS_HISTORY.md`),
   allowing for judge/fingerprint noise. Tool use is a *new capability*, not a re-answering of the existing
   rows.
2. **`tool_exec` fires on 0 of the 28 rows.** The dataset has no question that needs a unit conversion (all 4
   exposure-limit rows — 9, 10, 11, 21 — already quote both ppm and mg/m³ verbatim from the source) and none
   asks for a document's provenance. So the real `_tool_llm` should request no tool on any row, and the answers
   + contexts should be byte-for-byte the pre-G1 agent path.

## Why
The 28 rows were written for a retrieval/grounding eval, before unit-conversion or provenance tools existed.
The conversion tool's win is a capability the current rows do not exercise (Agent-2 corpus analysis: 0/28).

## How it will be validated (not assumed)
- Run the frozen 28 on `PIPELINE=agent` (semantic_v2, k=10); record the aggregate in `METRICS_HISTORY.md`.
- **Independently** loop `agent.graph.ask` over the 28 questions and grep each row's `trace_notes` for a
  `tool_exec` breadcrumb; report the count. **0 validates prediction 2**; any non-zero row is named and
  declared not comparable to the ledger (a spurious `tool_decide` firing changed that row's contexts).

## Outcomes and what each means
- **Flat + 0 tool_exec** → prediction holds; the capability is real but unexercised by this dataset. This is a
  legitimate, recorded result — the honest ledger entry is "no aggregate change; tool loop added; frozen-28
  unaffected."
- **Flat + some tool_exec fired** → the tool was invoked but didn't move the aggregate; name those rows and
  treat them as not-comparable.
- **Not flat** → investigate; a tool that changed a frozen-row answer is either a real improvement or a
  regression, read per-row.

## The capability, shown separately (not in the frozen set)
Two conversion rows live in `eval/capability_set.jsonl` (NEVER appended to `dataset.jsonl`), run via
`scripts/run_capability_eval.py`. They demonstrate the tool is load-bearing on questions the corpus does not
pre-tabulate; they are explicitly a different set and **not comparable** to the 28-row history.

## Capability-set revision (on the record — a revision, not tuning-to-pass)
The FIRST draft of the capability set asked "The NIOSH REL for acetone is 250 ppm. Express that in mg/m³."
A probe showed this was **invalid as a capability test**: the NIOSH Pocket Guide already tabulates acetone's
590 mg/m³, so the model answered "≈590 mg/m³" straight from retrieved context and `tool_exec` never fired — it
tested table-reading, not conversion. It was replaced **before any scored run** with non-tabulated *measured*
values the corpus does not print:
- "A workplace air sample shows 75 ppm of anhydrous ammonia. Express that in mg/m³." (→ 52.24 mg/m³)
- "A chlorine reading of 4 ppm … convert to mg/m³." (→ 11.60 mg/m³)
A probe confirmed the revised rows fire `ConvertExposureLimit`. Recorded here the same way the promotion-gate
mis-specification was — the revision is disclosed, dated before scoring, and its reason stated.

---

## Outcome (recorded 2026-09-23)

Scored against this pre-registration (not fitted to it). Full evidence: `eval/g1_closure_probe.md`,
`eval/g1_closure_PREDICTION.md` (commit `e0de6ec`), and the G1-closure block in `eval/METRICS_HISTORY.md`.

- **Prediction 2 ("`tool_exec` fires on 0/28") — FALSIFIED.** It fired on **2/28** on the scored run
  (`eval_20260920T230921Z.json`): row 8 (ammonia IDLH vs EPA endpoint) **and** row 20 (chlorine ceiling
  agreement) — both **comparison** rows, both calling `CompareThresholds`. The original prediction only
  considered the four exposure-limit rows (9/10/11/21); it missed that comparison questions trigger the
  compare tool. Firing is **stochastic**: N=4 re-invocations gave row 8 4/4, row 20 3/4; a later
  fp-matched like-for-like fired on row 8 only. "N/28 fired" is a per-run draw.
- **Prediction 1 ("aggregate flat") — not the story.** The tool loop does not re-answer the frozen 28
  neutrally: on **row 8 it is a net regression** (answer_correctness Arm CAP=3 0.8166 vs CAP=0 0.9277,
  Δ −0.1111, N=10/arm, fp-matched). Mechanism: the model **stops reproducing the EPA document's
  `0.14 mg/L`** (the reference's token) when the tool's synthetic chunks are in context — attention, not
  retrieval (the chunk stays present). Row 20's fire is spurious and harmless.
- **The capability the frozen 28 do not exercise — CONFIRMED, narrowly.** On a **source-scoped** question
  whose document lacks a conversion factor (acetone SDS, mg/m³→ppm), the tool is **load-bearing**: correct
  grounded answer **5/5 with tool vs 0/5 without** (3 refusals + 2 grounding violations). No load-bearing
  row exists on the full-corpus path — the NIOSH Pocket Guide tabulates every factor.
- **Net:** the tool as shipped is not shown to improve the frozen-28 and is a regression on the one row it
  reliably fires on; it is genuinely useful only when retrieval is scoped away from the corpus's tables.
  No promotion decision is at stake — `/ask/agent` was never promoted on these numbers.
