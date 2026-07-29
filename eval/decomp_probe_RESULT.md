# Step 8 — decomposition falsification probe: RESULT (outcome of the pre-registration)

Pre-registration: `scripts/decomp_probe_PREDICTION.md` @ commit `cc01954` (committed before the
probe ran). This file is the committed OUTCOME — a pre-registered prediction with no recorded
result is only half a receipt.

## Verdict: query decomposition is FALSIFIED as the IDLH lever
Read-only probe over the existing `dense_search` (no router/graph/decompose node built, no RAGAS).
Target: row 8, "how does the NIOSH IDLH compare to the EPA RMP toxic endpoint" for anhydrous
ammonia. Ground-truth chunks: NIOSH IDLH 300 ppm (`niosh-pocket-guide` p45), EPA endpoint 200 ppm
(`epa-rmp-ammonia-refrigeration` p4).

Manual sub-questions run through `dense_search` at k=10 and k=100, 2× back-to-back:
- **SQ1 (NIOSH IDLH) did NOT surface the ammonia answer chunk in top-10 — or top-100.** Strict
  exact-page match and a content scan (`ammonia`+`300`+`IDLH`) both confirm the ammonia IDLH-300
  entry is **absent from top-100** for SQ1 and for the combined query.
- **SQ2 (EPA endpoint) improved rank (27 → 13) but did NOT reach top-10 either.**
- **Prediction did NOT hold** (it was inverted/worse): predicted EPA in top-10 and NIOSH deep;
  actual = EPA never top-10, NIOSH absent from top-100.
None of the three pre-registered decision branches fires cleanly on the artifact-verified data:
decomposition alone does not surface either answer chunk into top-10. **Do NOT build query
decomposition for IDLH; do NOT build hybrid** (killed globally in Phase 1).

## Methodology finding: the tolerant matcher FALSE-POSITIVED; the artifact overrode the metric
The probe's mechanical `decision_branch` logged **FULL_LEVER** — SQ1 appeared to hit the NIOSH
target at rank 7. Reading the chunk overturned it: rank-7 was `niosh-pocket-guide` p44, an
**unrelated "Combustible Solid" entry**, not ammonia. The pre-registered tolerant matcher (page±1
AND `"IDLH"`/`"300"` present) misfired because those substrings are **ubiquitous** in the Pocket
Guide's per-entry format, so a page-adjacent chunk of the wrong chemical satisfied it. The
strict-exact-page rank plus a **chemical-name content scan** were the real arbiter, and they showed
absence. This is the project's "metric triages, artifact arbitrates" discipline in action: the
mechanical FULL_LEVER verdict was **overridden** by reading the retrieved text — and it is why a
loose lexical match is not a safe stand-in for reading the chunk.

## Root cause (ingestion audit, read-only): fat multi-record chunks — shared with acetone
Querying the index by metadata (not similarity) showed the ammonia IDLH-300 entry **IS ingested and
coherent** ("Ammonia … IDLH: 300 ppm … Anhydrous ammonia … NIOSH REL 25 / OSHA PEL 50") — but it is
buried inside a Pocket-Guide chunk that merges **~5 chemical entries**, so its embedding is an
average of five chemicals and dense retrieval can't surface it. The acetone flash-point value
(`-17,0 °C`, closed cup) is likewise present but buried in a **5,487-char SDS chunk spanning Sections
9–11** (~40 properties + reactivity + toxicology).

**One root cause, not two levers:** the semantic chunker produced FAT MULTI-RECORD chunks for
tabular sources, diluting each fact below dense reach — the same failure family for the 2B target
(IDLH) and the 2C target (acetone). The indicated lever is **structure-aware re-chunking of the
tabular document class** (per-chemical-entry for the NIOSH Pocket Guide; per-section for SDS docs) —
NOT decomposition, NOT hybrid. Scoped in the `feature/2bc-rechunk-design` design pass.

## Also recorded
Matching an answer chunk by exact (doc, page) fails when chunks span multiple pages and are labeled
by their START page: the ammonia answer lives in the "p44" chunk (not gt "p45"), acetone in "p7"
(not gt "p8"). Chunk page labels are misaligned with the dataset ground-truth pages — relevant to
the citation-alignment question the re-chunk pass pre-registers.
