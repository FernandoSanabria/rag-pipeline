# Structure-aware re-chunking — 2B+2C DESIGN + pre-registration (read-only design pass)

Root cause (see `eval/decomp_probe_RESULT.md`): the semantic chunker produced FAT MULTI-RECORD chunks
for tabular sources, diluting each fact's embedding below dense reach — the SAME failure for the 2B
target (IDLH, dataset idx 8) and the 2C target (acetone flash point, dataset idx **24** = line 25).
This design scopes structure-aware re-chunking. NOTHING re-ingested/embedded/scored here — design +
pre-registration only. Row indices are 0-based dataset indices throughout (acetone idx 24 == the
"row 25" 1-indexed line).

## 1. Tabular class — measured, not guessed
Per-doc chunk stats from the live index (namespace `semantic`). **Max chunk size is ~6000 for nearly
every doc** — that is the Phase-1 6000-char sub-split cap, so SIZE DOES NOT DISCRIMINATE tabular from
prose. The real discriminator is a **repeating-record schema with multiple records merged per chunk**.

| doc | chunks | avg | max | class / reason |
|---|--:|--:|--:|---|
| niosh-pocket-guide | 358 | 3441 | 6000 | **TABULAR** — per-chemical-entry records; 178/358 chunks merge ≥2 entries |
| sds-sigma-aldrich-acetone | 14 | 3405 | 5986 | **TABULAR** — 16-section SDS; flash point buried in a Sections-9–11 blob |
| sds-airgas-chlorine | 14 | 2113 | 5981 | TABULAR (SDS) — but see §2, out of scope |
| sds-nutrien-anhydrous-ammonia | 20 | 1998 | 5880 | TABULAR (SDS) — out of scope |
| sds-fisher-sodium-hydroxide | 8 | 2333 | 4281 | TABULAR (SDS) — out of scope |
| osha-1910-1000 | 98 | 2242 | 5991 | TABULAR (Z-1/Z-2/Z-3 air-contaminant tables) — out of scope |
| osha-1910-119 / -147 | 40 / 15 | ~2400 | ~6000 | PROSE (regulation text) |
| epa-rmp-general-guidance / -ammonia-refrigeration | 277 / 15 | ~2500 | ~6000 | PROSE guidance (some tables) |
| controls-hazardous-energies, niosh-alert-hazardous-energy | 65 / 18 | — | ~6000 | PROSE |
| osha-otm-iv-4-robots, osha-otm-v-2-excavations | 50 / 26 | — | ~6000 | PROSE |
| fisher-657/667, flowserve-mark3, micromotion, atlas-copco | 39/48/106/16/31 | — | ~5000–6000 | PROSE/procedure (spec tables, but no repeating-record failure) |

## 2. Scope decision — boundary = blast radius
Pre-registered rule: **re-chunk a tabular doc ONLY if it has oversized multi-record chunks AND a
failing/at-risk dataset row depends on it.** Applying it:
- **niosh-pocket-guide → IN** (IDLH idx 8 fails; 178/358 multi-record).
- **sds-sigma-aldrich-acetone → IN** (acetone idx 24 fails; flash point buried).
- **3 other SDS → OUT.** No failing row depends on them (chlorine idx 20/21, ammonia PEL idx 10,
  NaOH pH, etc. all pass today), AND their section headers are NOT cleanly detectable (see §3b) — a
  Sigma-tuned delimiter would mangle them. Excluding them removes the fragile multi-vendor regex.
- **osha-1910-1000 (Z-tables) → OUT.** No diagnosed fat-chunk failure; the chlorine-ceiling row's
  problem is generation flip-flop, not retrieval; re-chunking risks regressing it.
- **All prose docs → OUT** (don't perturb what works — e.g. the Fisher-657 torque row passes).

**Re-chunk scope = {niosh-pocket-guide, sds-sigma-aldrich-acetone} — exactly the two diagnosed
targets, both with clean detectable delimiters.**

## 3. Boundary rules — PROVEN on the real chunks + validated at corpus scale
### 3a. NIOSH → per-chemical-entry (with the headerless fallback specified)
**Operate on the RE-EXTRACTED full document, NOT on current chunks.** Honesty finding: **235/358
current chunks cut an entry at the chunk boundary** (an entry's head in one chunk, its value in the
next), so re-splitting the *existing* chunks would orphan values pervasively. The re-chunk therefore
re-reads the PDF (page-ordered) and splits the whole document; a reconstruction (page-ordered concat)
validated this: 677 `Formula:` occurrences = the guide's ~677 chemicals.

**Delimiter (refined):** an entry begins at its **NAME line**. Anchor on `Formula:`, but if `Formula:`
is line-initial (name on the previous line, e.g. `Arsenic\nFormula:` vs same-line `Ammonia Formula:`),
back up to the preceding non-empty line. Honesty finding: the naive "cut at the `Formula:` line" rule
**orphaned the name of 25/677 (3.7%) entries** (inconsistent PDF line-breaks); the name-anchored rule
fixes it → **0/677 orphaned**.
- **PROVEN on the real p44 chunk:** isolates **Ammonia (2046 chars, `IDLH: 300 ppm`, `Anhydrous
  ammonia`) into its own chunk ✓**; on the reconstructed doc the ammonia entry is **whole and unique**.

**Headerless characterization** (the 120/358 = 33.5% chunks with no `Formula:`): ~8 are continuation
TAILs (body of an entry whose header is in the prior chunk — an artifact of the current boundaries,
gone after re-extraction); the rest are **front-matter / index / the field-definition glossary /
appendix tables** (pp 1–14 intro, glossary, trailing appendices); **0 malformed**.

**FALLBACK RULE (explicit):** contiguous spans with no `<Name> Formula:` header — the front-matter
before the first entry, the trailing appendix/index region, and any single entry segment that exceeds
a size cap (~4000 chars) because it absorbed non-entry text — go to a **recursive size-based splitter
(~1500 chars)** and are labeled "prose" (page-tagged, low-priority, not chemical-fact answers). Entry
spans use the per-entry rule; only non-entry spans hit the fallback — so **no chemical entry is split
across a header/fallback boundary.**

**Effect (refined delimiter + fallback, measured on the reconstruction):** 676 whole per-entry chunks
(size min/median/max = 492/1476/2315 — all embed-clean) + 1 appendix-absorbed segment routed to
fallback + **46** front-matter prose chunks + **111** appendix prose chunks ≈ **834 total** (vs 358
now). Orphaned entries: **0**. Sane distribution — not 400 tiny, not 3 huge.

### 3b. SDS → per-section (acetone only)
Delimiter: `SECTION N:`. **VENDOR FORMATS DIVERGE — validated across all four SDS:** Sigma **16/16**
(`SECTION N:`, upper+colon), Airgas **1/16**, Nutrien **0/16**, Fisher **0/16** (they use `Section N.`
title-period, which collides with inline `Section N for…` cross-refs, or have no detectable header at
all). A single regex CANNOT safely section all vendors — a design finding surfaced now, not a runtime
surprise. Because scope is acetone-only (§2), we use the clean Sigma `SECTION N:` delimiter and do NOT
need a tolerant multi-vendor pattern.
- **PROVEN on the real acetone p7 blob (5487 chars, Sections 9–11):** splits into Section 9(1909,
  `Flash point … -17` ✓) + Section 10(1184) + Section 11(2299). **Per-section is sufficient**:
  Section 9 is a coherent 1909-char "physical & chemical properties" chunk that surfaces flash point
  cleanly — per-property would over-fragment and break cross-refs, so per-section is chosen.
- Expected acetone chunk count ≈ **16** (one per SDS section), up from 14 fat blobs; each ~0.5–2 KB.

## 4. AT-RISK vs INVARIANT (read-only, current index; frozen baseline saved)
For all 28 rows, `dense_search(k=10)`; a row is AT-RISK if any top-10 chunk is from an in-scope doc.
- **AT-RISK [9 rows]: 0, 8, 9, 10, 19, 21, 22, 23, 24** — can move.
  - **MAY-IMPROVE [7]: 0, 9, 10, 21, 22, 23, 24** — top-10 contains a fat multi-record chunk that will
    split (finer chunk → cleaner match). Both targets are at-risk; note idx 8 (IDLH) improves by a
    DIFFERENT mechanism — its answer (ammonia entry, currently rank ~20) RISES into top-10 once split,
    so it isn't flagged by the top-10-fat heuristic.
- **INVARIANT [19 rows]: 1,2,3,4,5,6,7,11,12,13,14,15,16,17,18,20,25,26,27** — never touch an in-scope
  doc → the controlled-experiment guard; expected UNCHANGED within noise.
- **Frozen baseline:** `scripts/prechunk_top10_baseline.json` (gitignored) records each row's current
  top-10 as `{source_doc_id, page, text_sha1, text_head}`. Re-chunking mints NEW chunk ids, so the
  post-re-chunk invariant check compares **content (source_doc_id + page + text_sha1)**, NOT ids —
  otherwise the invariant claim is unfalsifiable after the namespace changes.

## 5. Citation-page alignment (this lever may fix the v4 page-off)
Chunks are labeled by their START page. Today the answers live in the wrong-labeled chunk (ammonia
IDLH in the `p44` chunk vs gt p45; acetone flash point in the `p7` chunk vs gt p8). Per-entry /
per-section re-chunking labels a chunk by the true page where the record begins → the ammonia-entry
chunk should start ~p45 and the Section-9 chunk ~p8, **moving labels CLOSER to the dataset ground
truth.** `api/citations.py::derive_citations` reads `chunk.get("page")` from metadata, so re-chunking
CHANGES displayed citations. **Pre-registered direction: closer to ground truth.**

## 6. PRE-REGISTRATION (committed BEFORE any re-ingestion; this commit's hash is the pre-reg)
- **Targets:** IDLH (idx 8) and acetone (idx 24) answer chunks recover to **top-10** retrieval of the
  correct entry/section chunk.
- **At-risk set (may move): 0, 8, 9, 10, 19, 21, 22, 23, 24.** Predicted direction: 8 & 24 improve
  (targets); 0/9/10/21/22/23/24 may improve (fat chunk splits into a cleaner match); none predicted to
  regress (finer chunks of the same content).
- **Invariant set (unchanged within noise): 1,2,3,4,5,6,7,11,12,13,14,15,16,17,18,20,25,26,27.** This
  is the controlled-experiment guard — any invariant row that moves signals silent damage.
- **Blast-radius acknowledgment:** unlike query-time k=5→10, this RE-EMBEDS the corpus subset →
  the full 28-row eval + the intersection rule apply; the invariant set is the guard.
- **Reversibility:** re-chunk into a NEW namespace **`semantic_v2`**, NEVER overwrite the live
  `semantic` namespace. Revert = flip `RETRIEVAL_NAMESPACE` back to `semantic`; no rebuild-from-scratch.
  Do NOT overwrite the live namespace blind.

## Verification / next (NOT done here)
When built: re-ingest ONLY {niosh-pocket-guide, sds-sigma-aldrich-acetone} into `semantic_v2`
(per-entry with prose fallback for the 33.5% non-entry NIOSH pages; per-section for acetone); confirm
via a rank probe that the ammonia-entry and acetone-Section-9 chunks reach top-10; run the full 28-row
eval on `semantic_v2` vs the frozen baseline; verify the invariant set is unchanged (by content hash),
the at-risk set moved only as predicted, and the two targets recovered — over the intersection.

## GATE AMENDMENT (post-Gate-1)
The pre-reg said BOTH targets must reach top-10 before running Gate 2. Gate-1 (rank probe vs
`semantic_v2`, k=10/100, ×2) came back split: **IDLH cleared** (ammonia entry absent-from-top-100 →
**rank 8** for the combined eval question, **rank 2** for the focused sub-question, stable both runs);
**acetone did NOT** (107 → **rank 19**, stable — per-section chunking still bundles ~40 physical
properties, so flash point stays diluted). Amending the Gate-2 trigger: **IDLH cleared AND the
invariant guard is needed regardless**, so run the full eval to (a) read-verify the IDLH answer and
(b) prove the invariant set held. **Acetone is a recorded NEGATIVE going in, NOT a target of this
run** — its next lever (per-property chunking / scoped BM25 on the flash-point sub-question) is a
separate diagnosed finding, not a failure of this run.
