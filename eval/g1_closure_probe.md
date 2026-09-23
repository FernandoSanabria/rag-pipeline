# G1 closure — §1 read-only probes (GATE 1)

Read-only investigation before any scored run. Every claim below is backed by pasted command output.
**All verdicts are PROPOSED** — the user decides at GATE 1. Nothing in this file changes code, config,
or a frozen artifact; the only writes this session are this file and a few gitignored scratch scripts.

## Headline (corrects the working premise)
The premise "`tool_exec` fired on **1/28** — row 8" is wrong on two counts, from the scored G1 result
file (`eval/results/eval_20260920T230921Z.json`, `fp_8ef2fa014c`):
1. **The tool fired on 2/28 rows — 8 AND 20**, not 1. The ledger (`METRICS_HISTORY.md:24`) missed row 20.
2. **Both "did it fire" and "did the model use the computed value" are run-to-run STOCHASTIC**, not
   fixed properties. Characterized below (row 8 fires 4/4 and uses the value 2/4; row 20 fires 3/4 and
   never uses it). So "N/28 fired" is a per-run draw, and "the model ignored the value" was one draw
   (2 of 4 re-runs incorporated the value) — not evidence of a phrasing defect.

---

## P1 — Row artifacts (rows 8 and 20)

### The tool result schema note
The scored result JSON's per-row schema is
`[user_input, retrieved_contexts, response, reference, faithfulness, answer_relevancy,
context_precision, context_recall, answer_correctness]` — it has **no `trace_notes`**. Trace notes
were captured via the sanctioned per-row **re-run** (`PIPELINE=agent`, semantic_v2, k=10; a fresh
`_compiled_graph().invoke(fresh_state(q))`; the re-run's fingerprint floats and need not match
`fp_8ef2fa014c`). Tool args/returns are taken from the scored run's synthetic chunks (verbatim below)
and corroborated by the re-run's structured `tool_results`.

### ROW 8 — `For anhydrous ammonia, how does the NIOSH IDLH compare to the EPA RMP toxic endpoint used in offsite consequence analysis?`

**Reference (verbatim):**
> NIOSH IDLH is 300 ppm (NIOSH Pocket Guide); the EPA RMP toxic endpoint for ammonia is lower at 200
> ppm (0.14 mg/L) (EPA RMP Ammonia Refrigeration guidance).

**Per-row scores (scored run vs the 2C reference run `eval_20260803T234054Z.json`, `fp_c881474fd1`):**

| metric | G1 (fp_8ef2fa014c) | 2C (fp_c881474fd1) |
|---|--:|--:|
| faithfulness | `null` (NaN) | 0.8000 |
| answer_relevancy | 0.8590 | 0.8591 |
| context_precision | 0.9573 | 0.7861 |
| context_recall | 1.0000 | `null` (NaN) |
| **answer_correctness** | **0.6333** | **0.6331** |

**The three synthetic tool chunks as they entered context (verbatim, scored run):**
```
[source_doc_id=tool:CompareThresholds page=None]
COMPUTED by CompareThresholds — not a document quote.
args={"value_a": 300, "unit_a": "ppm", "value_b": 200, "unit_b": "ppm", "substance": "anhydrous ammonia"} -> {"a_mg_m3": 208.9571, "b_mg_m3": 139.3047, "relation": "a>b", "assumptions": "Vm=24.45 L/mol (25 C, 1 atm)"}
Input value(s) sourced from the retrieved context above.

[source_doc_id=tool:ConvertExposureLimit page=None]
COMPUTED by ConvertExposureLimit — not a document quote.
args={"value": 200, "from_unit": "ppm", "to_unit": "mg/m3", "substance": "anhydrous ammonia"} -> {"value": 139.3047, "unit": "mg/m3", "molar_mass": 17.03, "basis": "curated molar mass for anhydrous ammonia (17.03 g/mol)", "assumptions": "Vm=24.45 L/mol (25 C, 1 atm)"}
Input value(s) sourced from the retrieved context above.

[source_doc_id=tool:ConvertExposureLimit page=None]
COMPUTED by ConvertExposureLimit — not a document quote.
args={"value": 300, "from_unit": "ppm", "to_unit": "mg/m3", "substance": "anhydrous ammonia"} -> {"value": 208.9571, "unit": "mg/m3", "molar_mass": 17.03, "basis": "curated molar mass for anhydrous ammonia (17.03 g/mol)", "assumptions": "Vm=24.45 L/mol (25 C, 1 atm)"}
Input value(s) sourced from the retrieved context above.
```

**Generated answer (scored run, `fp_8ef2fa014c`) — mg/m³ OMITTED:**
> The NIOSH IDLH for anhydrous ammonia is 300 ppm, while the EPA RMP toxic endpoint is 200 ppm.
> Therefore, the NIOSH IDLH is higher than the EPA RMP toxic endpoint. […] (answer is entirely in ppm)

**Trace notes (re-run) and answer (re-run, mg/m³ INCLUDED):**
```
router: direct
retrieve[direct]: dense_search(k=10, ns=semantic_v2) -> 10 chunks
tool_decide[iter 1]: requested 3 tool(s): CompareThresholds, ConvertExposureLimit, ConvertExposureLimit
tool_exec[CompareThresholds]: ok -> {"a_mg_m3": 208.9571, "b_mg_m3": 139.3047, "relation": "a>b", ...}
tool_exec[ConvertExposureLimit]: ok -> {"value": 139.3047, ...}
tool_exec[ConvertExposureLimit]: ok -> {"value": 208.9571, ...}
tool_decide[iter 2]: no tools needed -> generate
generate: 13 contexts -> answer_len=575
```
> The NIOSH IDLH … is 300 ppm … **which is equivalent to approximately 208.96 mg/m³**, whereas the
> toxic endpoint is 200 ppm, **equivalent to approximately 139.30 mg/m³**. […]

**Variance (N=4 fresh invokes at CAP=3):**
```
trial 1: fired=Y iters=1 tools=[Compare, Convert, Convert] answer_contains_tool_value=N
trial 2: fired=Y iters=1 tools=[Compare, Convert, Convert] answer_contains_tool_value=Y
trial 3: fired=Y iters=1 tools=[Compare, Convert, Convert] answer_contains_tool_value=N
trial 4: fired=Y iters=1 tools=[Compare, Convert, Convert] answer_contains_tool_value=Y
-> fired 4/4 ; value incorporated 2/4
```

**The unit point (as required):** the reference states the EPA endpoint as **0.14 mg/L**. The tool
computed 200 ppm → **139.3047 mg/m³**, and **139.30 mg/m³ ÷ 1000 = 0.1393 mg/L ≈ 0.14 mg/L** — the
tool's output is one trivial (÷1000) step from the exact unit the reference uses. So (i) **yes**, the
computed values would let the answer present the IDLH-vs-endpoint comparison in both units (the re-run
answer demonstrates it, in mg/m³), and (ii) the scored 0.6333 is one draw where the value was omitted,
statistically indistinguishable from 2C's 0.6331 — because that draw ignored the value.

**PROPOSED verdict — row 8 is NOT a clean spurious fire.** The tool fires reliably (4/4), does relevant
work (its 139.30 mg/m³ ≡ the reference's 0.14 mg/L), and the model incorporates it in 2 of 4 trials.
This is option (a)-flavoured, not (b): **Q2 stays alive** as a legitimate question — *can a `_tool_chunk`
phrasing change raise the incorporation count above 2/4?* — though its target is a **noisy** behaviour,
which weakens how cleanly any single-run "fix" can be validated (see §2/Q1's provider-noise caveat).

### ROW 20 — `Do the Airgas chlorine SDS and OSHA's air-contaminants table agree on the chlorine exposure ceiling?`

**Reference (verbatim):**
> Yes — both give an OSHA ceiling of 1 ppm: the Airgas SDS Section 8 states CEIL 1 ppm and 29 CFR
> 1910.1000 Table Z-1 lists chlorine at (C)1 ppm ((C)3 mg/m3).

**Per-row scores (scored G1 vs 2C):**

| metric | G1 (fp_8ef2fa014c) | 2C (fp_c881474fd1) |
|---|--:|--:|
| faithfulness | 1.0000 | 1.0000 |
| answer_relevancy | 0.7223 | 0.7217 |
| context_precision | 1.0000 | 1.0000 |
| context_recall | 1.0000 | 1.0000 |
| **answer_correctness** | **0.9573** | **0.9568** |

**The two synthetic tool chunks (verbatim, scored run):**
```
[source_doc_id=tool:CompareThresholds page=None]
COMPUTED by CompareThresholds — not a document quote.
args={"value_a": 1, "unit_a": "ppm", "value_b": 1, "unit_b": "ppm", "substance": "chlorine"} -> {"a_mg_m3": 2.8998, "b_mg_m3": 2.8998, "relation": "a==b", "assumptions": "Vm=24.45 L/mol (25 C, 1 atm)"}
Input value(s) sourced from the retrieved context above.

[source_doc_id=tool:CompareThresholds page=None]
COMPUTED by CompareThresholds — not a document quote.
args={"value_a": 3, "unit_a": "mg/m3", "value_b": 1.45, "unit_b": "mg/m3", "substance": "chlorine"} -> {"a_mg_m3": 3.0, "b_mg_m3": 1.45, "relation": "a>b", "assumptions": "Vm=24.45 L/mol (25 C, 1 atm)"}
Input value(s) sourced from the retrieved context above.
```

**Generated answer (scored run) — used the DOCUMENT's "3 mg/m³", not the tool's 2.90:**
> The Airgas chlorine SDS states that the OSHA PEL for chlorine is a ceiling of 1 ppm (3 mg/m³)
> [sds-airgas-chlorine p4]. The OSHA air-contaminants table also lists … 1 ppm (3 mg/m³)
> [niosh-pocket-guide p89]. Therefore, both documents agree …

**Re-run trace — tool did NOT fire this time, answer identical:**
```
router: direct
retrieve[direct]: dense_search(k=10, ns=semantic_v2) -> 10 chunks
tool_decide[iter 1]: no tools needed -> generate
generate: 10 contexts -> answer_len=359
```
**Variance (N=4):** `fired 3/4 ; value incorporated 0/4` (when it fires it calls CompareThresholds×2;
the tool's 2.90 mg/m³ never appears in any answer).

**The rounding point (as required):** the tool computed **2.8998 mg/m³** for 1 ppm chlorine; the SDS /
Table Z-1 tabulate **3 mg/m³**. `2.90 ≈ 3` is **agreement within rounding, not a contradiction** — an
*incidental external check* that the tool's arithmetic matches the published value. The answer correctly
uses the document's rounded "3 mg/m³".

**PROPOSED verdict — row 20 is a spurious-but-harmless fire.** The tool fires stochastically (3/4), its
output is redundant with the retrieved documents and is never used, and the answer is identical whether
or not it fires (0.9573 vs 0.9568, within noise). No `_tool_chunk` change is implicated by row 20.

---

## P2 — Is any row *load-bearing*? (read-only retrieval; embeddings only, no generation)

`MOLAR_MASS` converts only **ammonia / anhydrous ammonia, chlorine, acetone** (sodium hydroxide is a
particulate, present only to be *refused*), so candidates are limited to those three. For each candidate
we fetched top-10 `semantic_v2` chunks (`src.retrieve.dense_search`, k=10) and flagged, per chunk,
whether it carries a ppm↔mg/m³ **pair for the substance** (factor derivable without the tool) and
whether the **converted answer value** appears. Chunk id = `source_doc_id`+page (dense_search returns no
separate id). **A load-bearing row needs BOTH absent across all 10 chunks.**

| candidate (query) | factor/pair present? | converted value present? | BOTH-N? |
|---|:--:|:--:|:--:|
| "180 ppm of acetone … in mg/m3" (→427.6) | **Y** | N | no |
| "8 mg/m3 of chlorine … in ppm" (→2.76) | **Y** | Y | no |
| "62 ppm ammonia … to mg/m3" (→43.2) | **Y** | N | no |

The factor is present **in every case** because the **NIOSH Pocket Guide tabulates the per-chemical
conversion factor as a literal line**, retrieved in the top chunks:
```
acetone  : niosh-pocket-guide p33  "Conversion: 1 ppm = 2.38 mg/m3"
chlorine : niosh-pocket-guide p89  "Conversion: 1 ppm = 2.90 mg/m3"
ammonia  : niosh-pocket-guide p45  "Conversion: 1 ppm = 0.70 mg/m3"
```

**Finding — there is NO load-bearing row obtainable in this corpus for these substances.** The corpus
hands the model the exact conversion factor for every convertible chemical, so the tool is structurally
redundant here. This is the same reason the existing 2-row capability set came out mixed/within-noise.
**Consequence: §3's Q3 (add load-bearing capability rows) is not achievable — it drops.** §3 reduces to
the Q1 CAP=3-vs-CAP=0 like-for-like.

---

## P3 — The without-tool arm (CAP=0)

`agent/graph.py:269-276` (verbatim):
```python
def tool_decide_node(state: AgentState) -> dict:
    """Ask the model whether a tool is needed; emit the requested calls (or none). Cap-bounded (§5)."""
    if state["retrieval_error"]:  # retrieval already failed -> no tools (mirror generate's short-circuit)
        return {"tool_calls": [], "trace_notes": ["tool_decide[skipped]: retrieval_error -> generate"]}
    it = state["tool_iterations"]
    if it >= CAP:  # hard bound: stop looping, generate from what's available (countable signal, not swallowed)
        logger.warning("tool loop hit cap (%d) for %r -> generating from available context", CAP, state["question"])
        return {"tool_calls": [], "trace_notes": [f"tool_decide: CAP_REACHED ({CAP} iterations) -> generate"]}
```
With `AGENT_TOOL_MAX_ITERATIONS=0`, `CAP=0`; on the first visit `it = state["tool_iterations"] = 0`, so
line 274's `if it >= CAP:` is `0 >= 0` → **True** → returns before reaching the `_tool_llm().invoke()`
on line 279. Therefore CAP=0 makes **no tool-decision LLM call and no tool execution**, and `retrieved`
is never appended to → the contexts handed to `generate` are exactly the retrieved set.

**Inert — with one honest caveat:** the CAP=0 branch does make **no LLM call and no tool run**, and does
**not** alter `retrieved`/`answer`. It is not literally a no-op on state: it writes `tool_calls: []`
(already `[]` from `fresh_state`) and appends one observability breadcrumb
(`tool_decide: CAP_REACHED (0 iterations) -> generate`) to the `add`-reduced `trace_notes`, and emits a
`logger.warning` per request. None of these reach `generate` (which grounds only on `retrieved`), so
CAP=0 is the correct **without-tool arm**. This is a self-contained **HEAD CAP=3 vs HEAD CAP=0**
comparison; it is *not* claimed to equal the 2C path (2C numbers are reference only).

---

## P4 — Docs audit (nothing fixed here)

- **`eval/toolcall_PREDICTION.md` has no outcome section.** The file ends at the "Capability-set
  revision" section (line 54); there is no dated `## Outcome …` heading. Outcomes will be **appended**
  in §4.
- **`eval/KNOWN_LIMITATIONS.md` has no G1 entry.** `grep -niE "G1|tool"` returns nothing.
- **render_graph vs README:** `uv run python scripts/render_graph.py` diffed against the README mermaid
  block shows a diff, but it is **cosmetic / hand-curated, not structural**: same 6 nodes and same 9
  edges; the README block adds friendly edge labels (`direct` / `source_scoped` / `tools` / `done`) and
  drops the generator's `---config---` header, `<p>` node wrapping and `&nbsp;` label padding. The graph
  **topology matches**; the README diagram is semi-manually maintained despite its `<!-- regenerate -->`
  marker. (Observation only — not a G1 correctness issue, not fixed in §1.)
- **Repo-wide `1/28` search:** the erroneous *tool-fire* claim appears in exactly **one** tracked file —
  `eval/METRICS_HISTORY.md:24` (which says both "`tool_exec` fired on 1/28 — row 8" and "FALSIFIED
  (1/28)"). The other hits are unrelated: `blog/CONVENTIONS.md:78` and `blog/evaluation-first-rag.md:24`
  use "1/28 ≈ 0.036" as the **noise-floor arithmetic** (correct, leave alone); `uv.lock:887` is a
  coincidental URL hash. So §4's ledger correction is bounded to `METRICS_HISTORY.md:24`.

---

## Proposed pre-registration (for §2, pending GATE 1)
- **Q1 (always):** frozen-28 **CAP=3 vs CAP=0 interleaved in one process**, `system_fingerprint` recorded
  per row per arm. Predict: (a) on every row where `tool_exec` did **not** fire, the contexts passed to
  `generate` are **byte-identical** across arms (assert in code + count); (b) the rows whose contexts
  differ are **exactly** the fired rows, named; (c) all five aggregates within **±0.03**. Answer-text
  identity on non-firing rows is expected/recorded but **NOT** pass/fail (a text diff with identical
  contexts is provider noise). *Note the P1 caveat: which rows fire is itself stochastic, so the fired
  set is reported per arm, not assumed to be {8, 20}.*
- **Q2 (only if GATE 1 keeps it alive — recommended, given row 8):** change **only** `_tool_chunk`'s
  text (verbatim in the pre-reg); predict the row-8 incorporation count rises (measured over N trials,
  not one), and non-firing rows are byte-identical (`_tool_chunk` never runs on them). Falsifier: no rise
  → revert, record. Because the behaviour is noisy (2/4 incorporation), Q2 must be judged over **N
  repetitions**, not a single run.
- **Q3 — DROPPED** on P2 evidence: no load-bearing row exists in this corpus (the NIOSH Pocket Guide
  tabulates every factor). Recorded as the finding; no rows added to `capability_set.jsonl`.

## Cost (§1)
Read-only probes only. ~40 `gpt-4o-mini` decision/generation calls (trace re-run + N=4×2 variance) plus
~11 `text-embedding-3-small` query embeddings. Derived estimate **≈ $0.02 [d]**; a reconciled
`COST_LEDGER.md` entry for the whole G1-closure effort will be written at §4 (after §3's spend is known).

---

## GATE 1 — decisions requested
1. **Row 8 verdict:** accept "not a clean spurious fire; tool does relevant work; **keep Q2 alive**"? Or
   read it as (b) and drop Q2?
2. **Row 20 verdict:** accept "spurious-but-harmless; 2.90≈3 incidental check; no `_tool_chunk` implication"?
3. **Ledger premise:** confirm the correction to **2/28 (rows 8 & 20)** with the stochastic-firing note,
   applied in-place at `METRICS_HISTORY.md:24` with a dated bracketed disclosure in §4.
4. **Q3 drop:** accept that the corpus structurally precludes a load-bearing conversion row (P2), so §3 =
   Q1 (± Q2) only.
5. **§5 condition (4)** was truncated in the task prompt — please restate it so "closed" is fully defined
   before §3 runs.

---

# GATE-1 follow-ups (decisions received + P2b)

## GATE-1 decisions (recorded)
1. **Row 8** — accepted as *not a clean spurious fire*; **Q2 stays alive**, restructured into a 3-arm
   control (Arm 0 CAP=0 / Arm 1 CAP=3 current `_tool_chunk` / Arm 2 CAP=3 revised `_tool_chunk`), N=10,
   every trial RAGAS-scored, with ordered kill rules (see `eval/g1_closure_PREDICTION.md`).
2. **Row 20** — accepted as *spurious-but-harmless*; 2.90 mg/m³ ≈ the document's 3 mg/m³ is an incidental
   external check, not a contradiction; no `_tool_chunk` implication.
3. **Ledger** — confirmed. §4 corrects `METRICS_HISTORY.md:24` in place with a dated bracketed note
   ("was '1/28 — row 8'; the scored result file shows tool_exec fired on 2/28 — rows 8 and 20. Firing is
   stochastic: over N=4 fresh invocations row 8 fired 4/4, row 20 3/4."). Firing is reported as **counts
   with N**, never a rate.
4. **Q3** — kept **alive with ONE acetone row** (user ruling), on the P2b evidence below.
5. **§5** — the full "closed" definition (incl. condition 4) is written verbatim into
   `eval/g1_closure_PREDICTION.md` so it cannot be truncated again.

## P2b — load-bearing via the SOURCE-SCOPED path (read-only; embeddings only)
Source-scoping filters retrieval to one `source_doc_id`, **excluding** the NIOSH Pocket Guide's
`Conversion: 1 ppm = X mg/m3` line — so within a single document the factor may be absent. Tier-2/1 docs
mentioning a convertible substance were queried with `dense_search(..., source_doc_id=<doc>, k=10)`.

| doc (filtered) | substance | ppm in-doc? | mg/m³ in-doc? | clean BOTH-N cell? |
|---|---|:--:|:--:|---|
| `sds-nutrien-anhydrous-ammonia` | ammonia | Y | Y | no — both units printed (`25 ppm` / `17 mg/m³`) |
| `sds-airgas-chlorine` | chlorine | Y | Y | no — both units printed (`1 ppm` / `3 mg/m³`, etc.) |
| `epa-rmp-ammonia-refrigeration` | ammonia | Y | N | **contaminated** — prints `0.14 mg/L` (a mass conc), so mg/m³ is a trivial ×1000 away |
| **`sds-sigma-aldrich-acetone`** | acetone | **N** | Y | **YES** — mg/m³-only → asking in **ppm** has no in-doc factor once source-scoped |

**Finding — exactly ONE clean load-bearing cell:** `sds-sigma-aldrich-acetone`, mg/m³→ppm direction. The
other candidates print both units (or, for EPA-ammonia, a mass value in mg/L). Under the strict ≥2 rule
this is below threshold; per the GATE-1 ruling, Q3 proceeds with this **single** acetone row.

**Integrity note (must shape the Q3 question).** The acetone SDS's mg/m³ values are **modeled exposure
estimates, not an OEL** — retrieved verbatim they read e.g. `PROC15 ECETOC TRA Without Local Exhaust
Ventilation  Inhalation 84,58 mg/m³` (p27) and `PROC2 … Inhalation 0,02 mg/m³` (p22). The Q3 question
must quote the value **as the SDS labels it** (the PROC15 modeled inhalation concentration of
84.58 mg/m³), not call it "the exposure limit". Arithmetic (hand-verified): acetone factor =
58.08 ÷ 24.45 = 2.3755 mg/m³ per ppm → 84.58 ÷ 2.3755 = **35.61 ppm**. If, on extracting the SDS page,
no cleanly-labelled convertible value can be stated without fabrication → write `TODO_VERIFY` and stop.

## Item D — one judge-parse NaN per run (noted, not investigated)
On row 8, RAGAS returned `faithfulness = NaN` in the G1 run and `context_recall = NaN` in the 2C run —
one per-metric judge-parse failure per run, on the same row. Recorded here for the ledger's **per-metric
n** (a dropped cell, not a zero); not investigated this task.
