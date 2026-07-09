# v2 (semantic chunking) — WRITTEN PREDICTION, recorded BEFORE the eval

Strategy: **semantic boundaries + variable/larger chunks vs fixed 500** (namespace `semantic`),
measured against v1 (`fixed_500_50`). k=5 unchanged. This is committed before running the eval so
the prediction is falsifiable, not fitted.

## Honest-comparison caveat
v2 changes chunk **SIZE and METHOD at once** (v1 = fixed 500 chars; SemanticChunker percentile =
variable, likely larger/fewer). Any gain therefore cannot be attributed to "semantic boundaries"
alone — larger chunks per k=5 slot may drive recall independent of boundary quality. The report
will show the chunk-size distribution so size effects are visible.

## Row-level prediction (the 7 failing rows from the Prompt-12 diagnostic)
- **Expect to RECOVER (fragmentation: right page, wrong 500-char slice):**
  1. NaOH pH
  2. NH3 boiling point
  3. NIOSH Case No. 1
  4. Flowserve start sequence
  Rationale: semantic chunking keeps SDS Section-9 property blocks, procedure step-lists, and
  multi-page case narratives intact, so the key value should land in a retrieved chunk.
- **Expect modest / no improvement (pure-recall / cross-doc: wrong pages fetched entirely):**
  5. RMP Program 1
  6. NIOSH 82% leading factor (+ OSHA requirement)
  7. IDLH-vs-EPA-endpoint
  Rationale: better boundaries won't necessarily rank a page dense search never surfaced. These are
  the hybrid-search target next.
- **Expect to STAY FAILED (generation/prompt, not chunking):**
  8. Chlorine ceiling agreement (the value was already retrieved at v1; it refused).

## Metric-SHAPE prediction
- `context_recall` **UP** and `answer_correctness` **UP**.
- `context_precision` possibly **DOWN** — larger semantic chunks carry more surrounding/irrelevant
  text per k=5 slot.
- `faithfulness` roughly flat-to-up.

The report will confirm or **falsify** each of these and flag any WRONG prediction.
