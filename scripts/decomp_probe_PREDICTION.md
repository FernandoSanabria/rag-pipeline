# Step 8 — decomposition falsification probe: PRE-REGISTERED prediction

Committed BEFORE the probe (`scripts/decomp_probe.py`) is written or run. The commit hash of this
file is the pre-registration. Read-only probe over the existing `dense_search`; no router, no graph,
no decompose node, no RAGAS, no build.

## Target (dataset row 8 — IDLH cross-source comparison)
Question: *"For anhydrous ammonia, how does the NIOSH IDLH compare to the EPA RMP toxic endpoint
used in offsite consequence analysis?"*
Known answer chunks (ground truth):
- NIOSH IDLH 300 ppm @ `niosh-pocket-guide` p45
- EPA endpoint 200 ppm @ `epa-rmp-ammonia-refrigeration` p4

Manual sub-questions (router does not exist — this tests whether decomposition COULD work):
- `SQ1_NIOSH` = "What is the NIOSH IDLH for anhydrous ammonia?"
- `SQ2_EPA` = "What is the EPA RMP toxic endpoint for ammonia in offsite consequence analysis?"

## Hypothesis (from METRICS_HISTORY + the 5b row-8 drop)
Decomposition **PARTIALLY** recovers IDLH. SQ2 (EPA endpoint) surfaces
`epa-rmp-ammonia-refrigeration` p4 in top-10 (v4 already states the EPA side at k=10). SQ1 (NIOSH
IDLH) does **NOT** surface `niosh-pocket-guide` p45 in top-10 even decomposed — that value is a
term-blind dense-retrieval miss (v3 note), a **LEXICAL** problem, not a two-facts-in-one-query
problem, so isolating it as its own sub-question won't fix it. Predicted: EPA rank@10 ≤ ~5; NIOSH
rank@10 still deep (>10), possibly recovered by k=100. → decomposition alone = PARTIAL recovery (EPA
stated, NIOSH still refused), which `answer_correctness` partial-credits (~0.02→~0.36) WITHOUT full
recovery.

## Match definition (PRE-REGISTERED — part of the target, not a post-hoc rescue)
- `NIOSH_IDLH_300ppm` matches `niosh-pocket-guide`, page ∈ {44,45,46} AND chunk text contains
  "300" or "IDLH".
- `EPA_endpoint_200ppm` matches `epa-rmp-ammonia-refrigeration`, page ∈ {3,4,5} AND chunk text
  contains "200".
Rationale: v4 notes show the pocket-guide value/page can sit off-by-one from the chunk boundary, and
the semantic namespace sub-splits a page — so a page hit whose chunk lacks the value isn't the answer
chunk. The **STRICT exact-page** rank is reported alongside for the record, but the DECISION RULE
reads the **tolerant** (±1-page AND value-present) match. Rank = BEST (lowest) 1-indexed position
among matching chunks; "not in top-N" otherwise. The probe runs 2× back-to-back; a rank crossing the
top-10 boundary between runs is reported BOUNDARY-UNSTABLE (Pinecone tie-order, proven in 5a row 6),
not a clean hit/miss.

## Decision rule (PRE-REGISTERED)
- **SQ1 tolerant-matches p45±1 in top-10 where the combined query did NOT** → decomposition is the
  FULL lever; build 2B as scoped.
- **SQ1 still misses in top-10 while SQ2 gets its EPA chunk** → PARTIAL recovery; the NIOSH side needs
  a SCOPED per-sub-question BM25 arm on SQ1 ONLY — NOT the globally-killed hybrid (a per-branch
  lexical lookup cannot evict passing rows outside its branch). Record as the implied next lever; do
  NOT build hybrid.
- **NEITHER sub-question beats the combined query's ranks** → decomposition is not the lever; stop and
  reconsider before any build.

## Acetone reference probe (SEPARATE — does NOT feed the IDLH decision)
Row-25 acetone question verbatim ("What is the flash point of acetone per the Sigma-Aldrich SDS?") at
k=10 and k=100; rank the flash-point chunk (`sds-sigma-aldrich-acetone`, p8±1, value "-17"/"17"
present). This is the OTHER 2B target — a TABULAR-retrieval miss, not a decomposition one. Recording
its rank now tells us whether acetone needs table-aware extraction (deep rank) or is already
retrievable — informational, not part of the decomposition verdict.
