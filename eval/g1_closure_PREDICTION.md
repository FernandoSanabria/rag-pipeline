# G1 closure — pre-registration (§2)

Committed **before** any scored run in §3, per the project's falsify-before-you-run discipline. Results are
compared against this, not fitted to it. Companion to `eval/g1_closure_probe.md` (§1 read-only findings).
All firing/incorporation figures are **counts with N**, never rates. Date: 2026-09-22.

Shared setup for every scored run: `PIPELINE=agent`, `RETRIEVAL_NAMESPACE=semantic_v2`, `k=10`, judge
`gpt-4o-mini`, embeddings `text-embedding-3-small`, the canonical five RAGAS metrics, `raise_exceptions=False`.
Arms differ **only** by `AGENT_TOOL_MAX_ITERATIONS` (patched via the `agent.graph.CAP` module global, reset
after each arm) and, for Arm 2, by the `_tool_chunk` text. `system_fingerprint` is recorded per trial/row/arm.

---

## Q1 — frozen-28 CAP=3 vs CAP=0 like-for-like (always runs)
Both arms interleaved row-by-row in one process (fingerprints pair). **Predictions:**
- **(a)** On every frozen-28 row where `tool_exec` did **not** fire, the contexts handed to `generate` are
  **byte-identical** across the two arms. Asserted in code (`contexts_cap3 == contexts_cap0`) and counted.
- **(b)** The rows whose contexts **differ** across arms are **exactly** the rows where `tool_exec` fired in
  the CAP=3 arm — named per arm, **not** assumed to be {8, 20} (firing is stochastic per §1).
- **(c)** All five aggregates agree within **±0.03** between arms.
- Answer-text identity on non-firing rows is **recorded but NOT pass/fail** — an answer-text diff on a row
  with byte-identical contexts is provider non-determinism, labelled as such.

**Verdict rule:** HOLDS if (a) holds exactly, (b) holds exactly, and (c) holds for all five. Any (a)
violation (a non-firing row with differing contexts) FALSIFIES and is investigated. A (c) miss on a row
whose fingerprints differ between arms excludes that row from the delta (named), per the drift rule.

## Q2 — row 8, does a `_tool_chunk` phrasing change raise value incorporation / correctness?
Three arms, **N=10** fresh invocations each, **every trial RAGAS-scored** (five metrics), fingerprint per trial.
- **Arm 0** — `CAP=0`, current code. Baseline: does the model convert unaided? (Row 8 routes *direct*, so
  the NIOSH `1 ppm = 0.70 mg/m3` line is in-context even with no tool.)
- **Arm 1** — `CAP=3`, current `_tool_chunk`. The tool as shipped.
- **Arm 2** — `CAP=3`, **revised `_tool_chunk`** (text below, verbatim). The single phrasing variable.

Per trial record: `fired` Y/N; `mass_unit_in_answer` Y/N (answer states the IDLH or the endpoint in mg/m³
**or** mg/L); `answer_correctness`; `faithfulness`; `fingerprint`.

**Predicted counts / moves (falsifiable):**
- Arm 0 incorporation (`mass_unit_in_answer`): **≤ 3/10** (unaided, the model rarely converts for a
  ppm-comparison question).
- Arm 1 incorporation: **≥ 4/10** (§1 observed 2/4).
- Arm 1 − Arm 0 mean `answer_correctness`: **within ±0.03 (no material move)** — the reference's scored
  content is the two ppm values plus `0.14 mg/L`; the tool emits **mg/m³** (208.96 / 139.30), *not* the
  reference's mg/L token, so incorporating it need not raise correctness. (This makes kill rule (ii) the
  predicted outcome.)
- Arm 2 vs Arm 1: incorporation **≥ 6/10**; `answer_correctness` still **within ±0.03** of Arm 1.

**KILL RULES (applied in order):**
1. If Arm 0 incorporation is **within 2** of Arm 1 → the tool is doing nothing on this row → **do NOT run
   Arm 2**; record "no tool effect on row 8" and close row 8.
2. Else if Arm 1 > Arm 0 on incorporation **but** Arm 1 − Arm 0 `answer_correctness` does **not** move
   > 0.03 → record "behavioural effect without metric effect" and **STOP before Arm 2**, unless a
   one-sentence mechanism justifies why the phrasing would move the metric when incorporation alone did not.
   *(Candidate mechanism, pre-registered: if Arm 2 makes the model additionally express the endpoint in
   **mg/L** — matching the reference's `0.14 mg/L` token — correctness could rise; the current tool output
   is mg/m³, so this is not guaranteed.)*
3. Arm 2 runs **only** if both gaps exist (Arm 1 ≫ Arm 0 incorporation AND a correctness move > 0.03, or
   the mechanism in (2) is invoked). Arms 0/1 interleaved trial-by-trial; Arm 2 interleaved with a fresh
   Arm 1 slice if it runs.

**Arm 2 — revised `_tool_chunk` text (the ONE variable, verbatim; applied only if Arm 2 is earned):**
```python
    text = (
        f"COMPUTED VALUE from {name} — derived from the retrieved context above. "
        f"Use it in your answer where relevant; do not cite it as a document page.\n"
        f"inputs={json.dumps(args, default=str)} -> result={json.dumps(result, default=str)}"
    )
```
The `return {"text": text, "source_doc_id": f"tool:{name}", "page": None}` is **unchanged** — the
citation-exclusion property (page=None) is preserved; only the prose changes. The hermetic test asserting
the chunk text will be updated to the new string in the same commit as the code change.

## Q3 — one acetone capability row (source-scoped, load-bearing)
Append **one** row to `eval/capability_set.jsonl` (NEVER `dataset.jsonl`). The `sds-sigma-aldrich-acetone`
document prints acetone concentrations only in **mg/m³** (a PROC15 ECETOC-TRA modeled inhalation value,
`84.58 mg/m³`); source-scoping excludes the NIOSH conversion line, so converting to **ppm** has no in-doc
factor. Question quotes the value **as the SDS labels it** (not "the exposure limit"):
> "Per the Sigma-Aldrich acetone SDS, the PROC15 modeled worker inhalation concentration is 84.58 mg/m³.
> Express that concentration in ppm."

Hand-verified reference (arithmetic shown): acetone MW 58.08 g/mol, Vm 24.45 L/mol → factor 2.3755 mg/m³
per ppm → 84.58 ÷ 2.3755 = **35.61 ppm**. *If, on extracting the SDS page in §3, the value/label cannot be
stated without fabrication, write `TODO_VERIFY` and stop (no fabricated ground truth).*

Two arms, **N=5** each (firing is stochastic): with-tool `CAP=3`, without-tool `CAP=0`.
**Predictions (both must hold):**
- with-tool mean `answer_correctness` exceeds without-tool by **> 0.03**; and
- the converted value (**≈ 35.6 ppm**) appears **only** in the with-tool answers (without-tool, source-
  scoped, the model has no factor → it must refuse or omit ppm; producing 35.6 ppm without the tool would
  be an ungrounded guess).
- Routing pre-check: a single **dry router-node invoke** (no generation) is expected to return
  `route=source_scoped`, `source_doc_id=sds-sigma-aldrich-acetone`; if it routes *direct*, the row is not a
  clean source-scoped test — recorded and the prediction re-scoped.

## §5 — definition of CLOSED (verbatim, so it cannot be truncated)
G1 is **closed** when **all four** hold:
1. The CAP=3-vs-CAP=0 comparison (Q1) is **confirmed within ±0.03 on matched fingerprints**, or recorded
   **NOT TESTABLE** with the reason.
2. Rows 8 and 20 are **explained from the artifact**, with **at most one** pre-registered single-variable
   fix attempted (Q2 Arm 2) and its verdict recorded.
3. The capability question (Q3) has a **recorded answer** — either a per-row gain that survives noise, or
   the explicit finding "the conversion tool is not load-bearing [on this corpus / on this row]".
4. `eval/toolcall_PREDICTION.md` has its **appended dated outcome**; `eval/KNOWN_LIMITATIONS.md` has a G1
   entry stating **exactly what is and is not shown**; `eval/METRICS_HISTORY.md` is **corrected with
   disclosure** (the 1/28 → 2/28 in-place note); `eval/COST_LEDGER.md` carries the **closure spend**; and
   **CI is green**.

(Items 1–3 are produced by §3 and reported at GATE 2; item 4 is the §4 records step, after GATE 2.)
