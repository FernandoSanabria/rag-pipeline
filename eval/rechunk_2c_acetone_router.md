# 2C acetone recovery — source-scoped ROUTER: PRE-REGISTRATION

Committed BEFORE building (this hash is the pre-reg). Phase win already met (2B IDLH); this recovers
the SECOND target and gives the write-up a two-lever story. Do NOT promote. Branch
`feature/2c-acetone-router`.

## Read-only probe result (zero eval; vs current `semantic_v2`)
Acetone Section-9 flash-point chunk baseline rank **19** (17 NIOSH entries + Section-1 outrank it).
- **(a) SOURCE-SCOPED** (dense_search filtered to `source_doc_id=sds-sigma-aldrich-acetone`):
  Section-9 → **rank 2** (top-3 ✅). Filter key `source_doc_id` PROVEN correct — the filtered query
  returned acetone chunks (a wrong key returns EMPTY; it did not).
- **(b) SCOPED BM25** ("flash point" over the acetone doc): Section-9 → **rank 1**.
Both recover it. **Chosen lever: source-scoped retrieval via a router** (metadata filter, no lexical
index; the question literally says "per the Sigma-Aldrich SDS").

## The distinction (SCOPED, not the globally-killed hybrid)
Scoped per-query source-filtered retrieval fires ONLY on source-anchored questions and retrieves
ONLY within that one document → **cannot evict a passing row elsewhere**, unlike the global BM25+dense
RRF fusion that demoted 2 passing rows (why hybrid was killed). Same idea family, opposite blast
radius, because of scoping.

## Router trigger — NARROW (protects the shipped 2B IDLH win)
`source_scoped` fires **ONLY for a question anchored to a SINGLE named document** ("per the
Sigma-Aldrich SDS", "per the Nutrien SDS"). **Multi-source COMPARISON questions are NOT source-scoped
→ route DIRECT.** Critically, the promoted **IDLH row 8 ("NIOSH IDLH vs EPA RMP endpoint") is a
TWO-source comparison → MUST route DIRECT** — a single-doc filter would drop one side and silently
halve the shipped answer. Multi-source scoping = a future list-channel lever, NOT this one. This
constraint is stated in the router prompt AND pinned by a hermetic test (verbatim IDLH question →
route=direct, source_doc_id="").

## Router design
```
START → router_node → (source_scoped) → source_scoped_retrieve → generate → END
                    ↘ (direct)         → retrieve (v4 path)     ↗
```
- `router_node`: fast low-temp **structured-output** LLM (gpt-4o-mini, temp 0,
  `with_structured_output(method="json_schema")`) given the manifest doc list (doc_id+title). Returns
  `{source_scoped: bool, source_doc_id: str|null}` — true + a known doc_id ONLY for single-document
  questions; false for everything else incl. comparisons. Branch on the boolean.
- `source_scoped_retrieve`: `dense_search(question, k, source_doc_id=...)` — ADDITIVE optional metadata
  filter (default None = current v4 behavior byte-for-byte). Returns the SAME
  `{text, source_doc_id, page}` dict contract (hermetic-tested).
- `generate`: unchanged.

## Predictions (pre-registered)
- **Acetone (row 24) recovers to a read-verified −17 °C answer** (Section-9 → top-3 via source-scope →
  generator states the flash point).
- **IDLH (row 8) UNCHANGED** — routes DIRECT (comparison), answer stays the promoted 300/200 result.
- **Regression guard (stronger than "unchanged"):** every non-source-anchored (invariant) row takes
  `route=direct` AND its answer is **byte-identical to the pre-router v4/semantic_v2 path**. A router
  that routes correctly but PERTURBS the direct path is still a regression. Asserted via the
  `trace_notes` route + an answer diff on the invariant set.
- **Router cost:** the router LLM call is paid on EVERY question (incl. simple ones that then go
  direct) — record its per-question latency/token cost from the eval run.

## Guards
Additive `dense_search(source_doc_id=None)`; router + source-scoped path in `agent/`; hermetic
stubbed-LLM router tests incl. the IDLH-direct pin + filtered-dict-shape test. Full-eval gate reads
acetone −17 °C by artifact, not delta; intersection rule. Do NOT promote.
