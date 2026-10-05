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

Near-empty table-of-contents chunks can outrank content: the G5 tier-1 demo query's rank-1 hit was a
47-character ToC fragment (controls-hazardous-energies p3). Observed 2026-10-02 via search_safety_docs; not
measured on the frozen 28; chunking backlog, not G5.

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
- **Prose-citation & misattribution of tool-derived values.** `agent/graph.py._tool_chunk`'s
  "COMPUTED … not a document quote" marker does **not** stop the model citing the chunk in prose —
  observed `[source_doc_id=tool:...]` in **5/5** with-tool acetone answers (docstring corrected to state
  this; no code change). Structured citations exclude tool chunks via `page=None`, which produces a
  **MISATTRIBUTION** rather than safe provenance: on the acetone capability question, `/ask/agent` returned
  35.61 ppm with confidence 0.9 ('high') and nine citations to `sds-sigma-aldrich-acetone` pages — none of
  which contain the value or a conversion factor. The number came from `ConvertExposureLimit`; the
  citations point at the pages the model read, not at the computation that produced the answer. The
  confidence basis does not mention the tool. **Backlog:** a tool-derived value needs its own attribution
  (a 'computed' citation kind, or a `confidence_basis` entry naming the tool and args); until then a
  high-confidence tool-derived answer looks fully document-sourced to the caller. Raw response:
  ```json
  {
    "answer": "The PROC15 modeled worker inhalation concentration of 84.58 mg/m³ is equivalent to approximately 35.61 ppm. This conversion is based on the molar mass of acetone (58.08 g/mol) and standard conditions (25°C, 1 atm) for the conversion factor. \n\n[source_doc_id=tool:ConvertExposureLimit]",
    "citations": [
      {"document": "Sigma-Aldrich Safety Data Sheet — Acetone", "page": 27},
      {"document": "Sigma-Aldrich Safety Data Sheet — Acetone", "page": 25},
      {"document": "Sigma-Aldrich Safety Data Sheet — Acetone", "page": 21},
      {"document": "Sigma-Aldrich Safety Data Sheet — Acetone", "page": 22},
      {"document": "Sigma-Aldrich Safety Data Sheet — Acetone", "page": 1},
      {"document": "Sigma-Aldrich Safety Data Sheet — Acetone", "page": 26},
      {"document": "Sigma-Aldrich Safety Data Sheet — Acetone", "page": 24},
      {"document": "Sigma-Aldrich Safety Data Sheet — Acetone", "page": 20},
      {"document": "Sigma-Aldrich Safety Data Sheet — Acetone", "page": 19}
    ],
    "confidence_score": 0.9,
    "confidence_basis": "high: answer generated from retrieved context",
    "route": "source_scoped",
    "source_doc_id": "sds-sigma-aldrich-acetone",
    "routing_reason": "Question attributed to a single named document: Sigma-Aldrich Safety Data Sheet — Acetone"
  }
  ```
- **Grounding violation without the tool.** On the source-scoped acetone question with the tool disabled,
  2/5 answers computed the conversion from **prior chemistry knowledge not in the retrieved context** (a
  grounding violation) rather than refusing. Worth a guard that a source-scoped answer cite only in-context
  values. → **G6 built that guard**: the output guard, shipped 2026-10-04, which covers every answer and not only
  source-scoped ones. Its live catch is NOT TESTED (0/5 such answers at P4); the evidence is hermetic. See the G6
  section below.
- **README graph block vs the generator.** The README's request-serving mermaid block carries a
  `<!-- regenerate: uv run python scripts/render_graph.py -->` marker, but the script's output is **not**
  what is committed (the edge labels are hand-curated; the script emits `<p>`/`&nbsp;`/unlabeled edges).
  Either make the script emit the labels or change the marker.

## G5 MCP server — what it is and is not shown to do
Recorded 2026-10-02 from the G5 build: the design is `eval/g5_design.md`; the pre-registration and its
outcomes are in [`g5_PREDICTION.md`](g5_PREDICTION.md), whose pre-registration commit is tagged `prereg/g5` at
PR time. The server's contract is [`mcp_server/CONTRACT.md`](../mcp_server/CONTRACT.md).

**Shown (pre-registered):** P1 discovery, P3 provenance on every row and P4 structured error paths HOLD over
stdio. P5 HOLDS over HTTP (recorded 2026-10-04): the deployed `/mcp` passed the post-deploy wire-smoke, the
negative proof and an external client. **P2 live parity is FALSIFIED** on one of five queries — see the Outcome
sections of [`g5_PREDICTION.md`](g5_PREDICTION.md). P2 fell to embedding non-reproducibility on a near-tied pair; the
measurement is recorded in the ledger's methodology block (METRICS_HISTORY.md).

**Not shown or deferred (backlog, not fixed):**
- **`ask_safety_question`** — an MCP tool wrapping `src.pipeline` — is deferred; the spec is two tools.
- **The G1 convert / compare tools are not exported.** They produce computed values, and exporting them needs
  results of `kind: "computed"` that carry their own attribution — the G1 misattribution above is what
  happens without it.
- **`controls-hazardous-energies`: the public-domain label is unverified** (state agency work; the federal PD
  rule does not apply); MCP reports labels verbatim and does not certify them.
- **CI cannot see a missing `COPY`.** CI's `docker build` never starts the app, so a package the API imports
  but the image lacks fails only at container start (the original 404 incident). A candidate fix is an
  in-image import step in CI.
- **`/mcp` has no authentication.** The tools are read-only and a search costs one embedding call — the same
  exposure class as `/ask` — so none was added; recorded as a follow-on.
- The doc-guard reads 11-digit GitHub Actions run IDs as commit hashes, so docs cite runs by trigger, timestamp
  and head commit, and run URLs live in PR bodies. Exempting `/actions/runs/<id>` URLs in
  `scripts/check_doc_citations.py` (with a test) is a candidate improvement, not done here.

## G6 guardrails — what they are and are not shown to do
Recorded 2026-10-04.
- The design is [`g6_design.md`](g6_design.md).
- The pre-registrations (v1, then v2) and their outcomes are in [`g6_PREDICTION.md`](g6_PREDICTION.md). Its two
  pre-registration commits are tagged `prereg/g6` and `prereg/g6-v2` at PR time.
- Rows here are 1-based. The G1 section above is 0-based, so its row 8 is G6's row 9.

**Shipped: the output guard.** It lives in `api/guards.py` and is applied to both endpoints in the shared assembly
in `api/main.py`.
- **Shown:**
  - 0/84 answers withheld on each endpoint across the frozen 28 × 3 trials (P3; in-sample, see below);
  - retrieval passes through unchanged, with contexts identical in 81/81 pairs (P5);
  - p50 3.2 ms (P6);
  - a replay over 26 local result files (678 answers) found 0 false positives and 1 true catch.
- **The A′ trade-off: arithmetic the model does in its head is refused.**
  - On the capability set, `/ask` had 6/9 answers withheld and `/ask/agent` 2/9.
  - The agent's two were trials where the conversion tool fired, but the model ignored its value and multiplied
    the document's factor itself. That is the same attention failure as G1's row 8 (above).
  - A computed value passes only when the model states the tool's output.
- **Not shown: a live catch.** P4's five acetone answers at CAP=0 were all whole refusals, so the guard never met
  a prior-knowledge computation live (NOT TESTED). The evidence is hermetic: in `tests/test_guards.py`, the
  recorded G1 answer (0.855 ppm) is withheld against a stand-in for the live contexts.

**Known false-negative classes (not fixed):**
- **Units are not checked.** Values match regardless of meaning.
  - On `/ask`, the chlorine conversion 4 × 2.90 = 11.6 passed 3/3, because an unrelated "IP: 11.55 eV" (phosgene,
    NIOSH Pocket Guide page 283) was in the retrieved context.
  - A unit-aware check is a follow-on.
- **Signs and integers are lenient.** A context number also counts by its magnitude, so a sign error can pass.
  An integer in the answer matches any context number that rounds to it.
- **Only figures are checked.** A wrong claim built from traceable numbers passes, and so does a wrong name or
  unit.
- **In-sample caveat.** The source-side reading rules (digit boundaries, magnitude, both readings of a lone comma)
  were developed against recorded answers to the frozen 28. So P3's 0/84 and the replay's 0 false positives are
  not out-of-sample numbers.

**Not shipped: the input guard. Built, measured, not shipped: two prompts, both falsified.**
- **v1:**
  - refused frozen row 3 (Fisher 657 torque) in 3/3 trials;
  - let injection rows 10 and 11 through in 3/3 trials each;
  - so P1 and P2 are FALSIFIED.
- **v2** was the one allowed revision: one sentence appended to each of two prompt lines, with 13 held-out rows
  committed first.
  - **What it fixed and held:** both v1 failures; the held-out equipment rows (18/18 allowed); the look-alike
    controls (9/9 blocked).
  - **Where it failed:** it refused frozen row 20 (the Airgas chlorine UN number) in 3/3 trials, a false positive
    v1 did not have. It also let held-out row 42 (an injection inside a configuration block) through in 3/3.
  - So P1′ and P2′ are FALSIFIED.
- Both versions blocked 0/27 hard negatives. The ruling allowed one revision, so there is no v3.
- **Where it stands.** `check_input`, the v2 prompt and their tests stay in the repo behind
  `INPUT_GUARD_ENABLED = False` (`api/main.py`). Turning it on needs a new pre-registration.
- **Cost if it were on:** p50 607 ms and $0.000081 per request (v2).
- **Consequence.** Out-of-scope, injection, harmful and personal-information requests reach the pipeline. What
  remains between them and an answer is the generator's cite-or-refuse prompt and the output guard, and neither
  was measured against those categories.

**Other gaps (backlog, not fixed):**
- The MCP `search_safety_docs` tool is not guarded; that was out of scope for G6. It returns retrieved chunks, not
  generated answers.
- These were never measured: injection carried inside corpus documents, non-English or encoded input, and
  multi-turn attacks.
- The guardrail set and both classifier prompts were written by the same author. It is a regression set, not a
  benchmark.
- With LangSmith tracing on, every classifier call prints a Pydantic serializer warning
  (`PydanticSerializationUnexpectedValue`, `field_name='parsed'`). It is cosmetic and appears only with tracing on.
  With the input guard off, the service makes no classifier call.
