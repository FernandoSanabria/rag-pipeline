# Known Limitations & Backlog

Precisely-stated limitations found during Phase 1/2, for the write-up and the backlog. These are not
ship-blockers (answers are correct); they are recorded exactly because the precision is the finding.

## Citation layer — array (retrieved) vs inline (model-used) divergence + value-page imprecision
**Mechanism (architectural, pre-existing).** `api/citations.py` derives the structured `citations`
array from **RETRIEVED-chunk metadata** — `source_doc_id` + `page`, deduped by `(document, page)` —
and **deliberately IGNORES the model's prose** (the generator was caught citing wrong pages). So the
two surfaces answer different questions:
- structured `citations` array = **"what was retrieved"** (a superset of the top-k chunks);
- inline body citations = **"what the model used"** (a subset, and fallible).
This is **PRE-EXISTING / not re-chunk-induced** — proven by the v4-era control (graph-v4, `semantic`
namespace, dataset row 8): its answer had **inline = []** yet a **10-item retrieved array**, the same
retrieval-derived design.

**Concrete limitation (live IDLH response on `semantic_v2`).** The answer VALUE is correct — NIOSH
IDLH **300 ppm**, EPA endpoint **200 ppm**, with the comparison — but the inline EPA attribution is
`epa-rmp-ammonia-refrigeration page=1` while the value chunk is **~page 4** (page 1 is the appendix
cover). So **a correct value can carry an imprecise page**, and **neither surface disambiguates the
value-bearing page**: the array **over-cites** (EPA Ammonia-Refrigeration pp 13/7/1/25 all appear),
the inline **under-specifies** (one page, possibly the wrong one). The structured array is the more
reliable *provenance* surface (it's what was actually retrieved); the inline is the model's fallible
attribution — which is exactly why `citations.py` routes around the prose.

**Re-chunk effect (precise, not a blanket claim).** `niosh-pocket-guide page=45` now appears in
**BOTH** surfaces (ground-truth aligned) where `semantic` had it **absent from top-100** — a genuine
improvement on the **NIOSH side**. The **EPA page imprecision is UNCHANGED**. So "citation improved"
is true for NIOSH specifically, not across the board.

**2D candidate (backlog, deferred).** Rank-weight / trim the citation array toward the chunks the
answer actually grounds on, or reconcile inline ↔ array. Not done now; `citations.py` is untouched.
Because the answer and its inline attribution are correct, this is a provenance-surface cleanup, not
a ship-blocker or a promotion concern.

## G1 tool loop — what it is and is not shown to do
Recorded 2026-09-23 from the G1 closure (`eval/g1_closure_probe.md`, `eval/g1_closure_PREDICTION.md`
commit `e0de6ec`, G1-closure block in `eval/METRICS_HISTORY.md`). **No promotion decision is at stake:**
`/ask` is the shipped default; `/ask/agent` is the richer path and was never promoted on these numbers.

**G1 as shipped:**
- **(i)** It fires **stochastically on 1–2 of the frozen 28** (rows 8, 20); on **row 8 it is a net
  regression** (−0.11 answer_correctness, N=10/arm, fp-matched) because the model stops reproducing the
  document's `0.14 mg/L` when tool chunks are present; on **row 20 it is spurious and harmless**.
- **(ii)** On a **source-scoped question whose document lacks a conversion factor** (acetone SDS,
  mg/m³→ppm), the tool is **load-bearing**: correct grounded answer **5/5 with tool vs 0/5 without**
  (3 refusals, 2 prior-knowledge computations — the latter a grounding violation, see below). Precise
  condition: the value `84.58 mg/m³` comes from the *question*; the document's role is only that
  source-scoping to it excludes the NIOSH Pocket Guide's conversion-factor line, so no factor is in context.
- **(iii)** **No load-bearing row exists on the full-corpus path** because the NIOSH Pocket Guide
  tabulates every factor (`Conversion: 1 ppm = X mg/m3` per chemical, always retrieved).
- **(iv)** The `tool_decide` prompt's "only if the value is not in context" instruction **does not prevent
  firing on comparison rows** — backlog, not fixed here.

**Backlog lines (not fixed):**
- **Prose-citation of tool chunks.** `agent/graph.py._tool_chunk`'s "COMPUTED … not a document quote"
  marker does **not** stop the model citing the chunk in prose — observed `[source_doc_id=tool:...]` in
  **5/5** with-tool acetone answers. The *structured* `citations` array still excludes tool chunks
  (`page=None`), so provenance is safe; only the in-prose text names the tool. Docstring corrected to state
  the observed behaviour; no code change.
- **Grounding violation without the tool.** On the source-scoped acetone question with the tool disabled,
  2/5 answers computed the conversion from **prior chemistry knowledge not in the retrieved context** (a
  grounding violation) rather than refusing. Worth a guard that a source-scoped answer cite only in-context
  values.
- **README graph block vs the generator.** The README's request-serving mermaid block carries a
  `<!-- regenerate: uv run python scripts/render_graph.py -->` marker, but the script's output is **not**
  what is committed (the edge labels are hand-curated; the script emits `<p>`/`&nbsp;`/unlabeled edges).
  Either make the script emit the labels or change the marker.
