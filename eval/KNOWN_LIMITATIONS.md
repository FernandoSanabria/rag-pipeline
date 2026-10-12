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

**Inline misattribution the array does not expose** (G9, recorded 2026-10-09).
- **The instance:** on G9 row 21, the served answer's inline citation sources a claim about OSHA's air-contaminants
  table to `niosh-pocket-guide page=89`. This happened in 5 of 8 G9b-build runs; see the G9 block of
  [`METRICS_HISTORY.md`](METRICS_HISTORY.md).
- **Why the array can't show it:** the structured array lists the retrieved pages of both documents, so it can't
  show that the inline attribution is wrong.
- **A second `gpt-4o-mini` instance, on G8 row 22** (post-hoc, recorded 2026-10-12).
  - The shipped generator cites `sds-fisher-sodium-hydroxide page=5` in 3/3 trials, on a row whose context holds no
    page 5. The pH value is on page 4, which Nova Micro cites in 2 of 3 trials.
  - Faithfulness 0.67 ×3 is the judge partly seeing it ([`g8_PREDICTION.md`](g8_PREDICTION.md), P4).
  - Both instances here are the shipped generator's. The Nova instances (G11 row 21, G8 row 24) are in those gates'
    sections below.

**An answer that contains the refusal sentence but is not it is served at high confidence** (G11, recorded
2026-10-12).
- **The mechanism.** `api/confidence.py`'s `is_refusal` is whole-answer equality, by design, so that "a partial answer,
  then the refusal sentence" never counts as a refusal.
  - The design was right about what not to call a refusal, and silent about what to call it instead.
  - Such an answer is scored 0.9, "high: answer generated from retrieved context", and `api/citations.py` cites every
    retrieved page.
- **Observed on frozen row 25 under the `/ask` path's contexts** (G11's P6):
  - 3/3 on Nova 2 Lite, and 0/3 on `gpt-4o-mini`, which returned the exact sentence;
  - across the 84 agent-path answers per arm, 0 in either arm.
  - Each Nova answer said the Sigma-Aldrich SDS excerpt lacks the flash point, gave the NIOSH Pocket Guide's 0°F, and
    ended with the exact sentence. The output guard passed all three.
  - So a question scoped to one document is served another document's value at high confidence.
- **Observed again in G8, on Nova Micro** (G8's P6, on contexts byte-identical to G11's): 0/3 exact. **Two hedge shapes
  are now named:**
  - **Nova 2 Lite** leaked another document's value, then appended the sentence;
  - **Nova Micro** echoed the instruction ("The exact refusal sentence is: …") instead of following it, and leaked no
    value.
  - All six answers pass the output guard and are served at 0.9 "high". `gpt-4o-mini` returned the exact sentence in
    6/6 across the two gates ([`g8_PREDICTION.md`](g8_PREDICTION.md), P6).
- **Not a property of one provider.** It applies to any model that hedges, a future OpenAI model included.
- **The fix needs its own gate.** A "hedged" tier at low confidence would change `api/confidence.py`, which `/ask`, the
  default path, shares. Not done in G11 ([`g11_PREDICTION.md`](g11_PREDICTION.md), P6).

**2D candidate (backlog, deferred).** Rank-weight / trim the citation array toward the chunks the
answer actually grounds on, or reconcile inline ↔ array. Not done now; `citations.py` is untouched.
Because the answer and its inline attribution are correct, this is a provenance-surface cleanup, not
a ship-blocker or a promotion concern.

Near-empty table-of-contents chunks can outrank content: the G5 tier-1 demo query's rank-1 hit was a
47-character ToC fragment (controls-hazardous-energies p3). Observed 2026-10-02 via search_safety_docs; not
measured on the frozen 28; chunking backlog, not G5.

The NIOSH Pocket Guide's two-column exposure limits are flattened in extraction.
- **On `niosh-pocket-guide page=45`** (anhydrous ammonia), the chunk puts the NIOSH REL's "ST 35 ppm" continuation line
  after the OSHA PEL. In the PDF it sits in the NIOSH REL column, judged by its x-position against another entry on the
  same page.
- **The effect:** on frozen row 11, `gpt-4o-mini` attributed that short-term limit to OSHA in 3/3 trials and was scored
  faithfulness 1.00.
- **Observed** 2026-10-12, in G11's post-hoc reading. The extent across the guide is not measured. Chunking backlog,
  not G11.

**Chunking backlog** (collected here; each item stays recorded where it was found):
- near-empty table-of-contents chunks outranking content (G5; the note above);
- chunk hygiene for OSHA Table Z-1's raw "(C)1 (C)3" line (G12, untested; the G12 section below);
- the NIOSH Pocket Guide's flattened two-column limits (G11; the note above): faithfulness is scored against the chunk,
  so a flattened two-column page rewards the wrong attribution.

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
- The doc-guard reads any run of 7 or more lowercase hex digits as a commit hash.
  - **Actions run IDs:** the 11-digit IDs are caught, so docs cite runs by trigger, timestamp and head commit, and run
    URLs live in PR bodies.
  - **UUIDs:** RFC 4122 UUIDs are caught by their 8- and 12-character hex groups, so G10b's review thread ids are
    written in upper case, which RFC 4122 allows.
  - **AWS case IDs:** the 15-digit AWS support-case numbers are all digits, so they read as hex. G11 keeps them in
    [`g11_aws_gates.txt`](g11_aws_gates.txt), which the guard doesn't scan (it reads `*.md` only).
  - **The candidate improvement, not done here:** exempt `/actions/runs/<id>` URLs and RFC 4122 UUIDs in
    `scripts/check_doc_citations.py`, with a test.

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
- **A non-numeric misattribution passes** (recorded 2026-10-09). The output guard checks numeric traceability only.
  - **Observed on G9 row 21:** in 5 of the 8 G9b-build runs that served it (#15–#17, #20, #26), the served answer
    sourced a claim about OSHA's air-contaminants table to the NIOSH Pocket Guide page (faithfulness 0.333,
    correctness 0.956).
  - **Once locally:** a variant at the same faithfulness also appeared once, in a local dev run on 2026-10-08. Its
    text was not recorded.
  - **The ledger:** the G9 block of [`METRICS_HISTORY.md`](METRICS_HISTORY.md).
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
- Every `json_schema` structured-output call prints a Pydantic serializer warning
  (`PydanticSerializationUnexpectedValue`, `field_name='parsed'`). **Corrected 2026-10-08:**
  - G12's R2 probe showed it appears **with tracing off too, with or without `include_raw`**. This line previously
    said it appeared only with tracing on.
  - So the production router emits it as well.
  - It is cosmetic.

## G12 parallel fan-out — built, measured, not enabled
Recorded 2026-10-08.
- The design is [`g12_design.md`](g12_design.md). The pre-registration and its outcome are in
  [`g12_PREDICTION.md`](g12_PREDICTION.md), whose pre-registration commit is tagged `prereg/g12` at PR time.
- It is a **dispatch-and-aggregate node**, not a hierarchy of agents. It ships with `FANOUT_ENABLED = False`, so the
  served graph is the pre-G12 one.
- This is **the same pattern as G6's input guard** (above): built, measured, not shipped.
- Rows are 1-based.

**Shown, on the four comparison rows (9, 10, 11, 21) and N=3 per arm:**
- the gate and decomposer dispatched on 12/12, and on 0/72 other rows (P1);
- both compared sources were retrieved 12/12, against 6/12 single-query (P2);
- an injected failing branch was tolerated and named 12/12 (P4);
- the other 24 rows passed through byte-identical, 72/72 (P5);
- the branches overlap in time, visible in the trace intervals.

**Why it is not enabled.**
- **Four of the nine pre-registered per-row metric predictions were falsified (P3):**
  - row 9 context recall fell 0.11. Trials 2 and 3 had byte-identical contexts and scored 1.0 and 0.667, so this is
    judge variance;
  - row 9 context precision fell 0.157 where a rise was predicted;
  - row 10 context precision rose 0.092 where a fall was predicted;
  - **row 9 answer correctness fell 0.047.** Row 9 is the IDLH comparison and G1's regression row (G1's 0-based
    row 8).
- **Latency (P6, falsified):** +1.98 s at p50.
  - The decomposer took 1.17–1.35 s live.
  - The context handed to generation roughly doubles: 12,567 tokens against 5,528 at p50.
- **Row 21 regressed (unregistered).** Answer correctness fell from 0.955–0.957 to 0.46–0.58, and 1 of 3 answers
  wrongly concluded that the Airgas SDS and OSHA disagree.
  - The hypothesis, **untested**, is OSHA Table Z-1's raw "(C)1 (C)3" line, which appears only in the fan-out
    context.

**A finding about this corpus.**
- Context recall is already saturated at k=10 on all four comparison rows, so a second retrieval pass has nothing to
  recover.
- What the fan-out adds is context: the sources' own value lines, plus about 10 more chunks.
- That extra context hurt 2 of 4 rows (9 and 21).
- This is consistent with the decomposition prior ([`decomp_probe_RESULT.md`](decomp_probe_RESULT.md)), which found no
  retrieval lever in decomposition either.

**Untested follow-ons:**
- per-branch depth of k/n, keeping the total at k;
- a join that keeps only each branch's top-m chunks;
- chunk hygiene for OSHA Table Z-1.

Any of them needs a new pre-registration before the switch is turned on.

## G9 smoke evaluation in CI — what it is and is not shown to do
Recorded 2026-10-09.
- **Sources:**
  - the design is [`g9_design.md`](g9_design.md);
  - the pre-registrations and outcomes are in [`g9_PREDICTION.md`](g9_PREDICTION.md), G9 then G9b, tagged `prereg/g9`
    and `prereg/g9b`;
  - the run ledger is the G9 block of [`METRICS_HISTORY.md`](METRICS_HISTORY.md).
- **Rows are 1-based.**

**What it is.** An 8-row smoke evaluation, [`eval-smoke.yml`](../.github/workflows/eval-smoke.yml), on every PR and
push to `main` that touches the gated paths. It sits next to the hermetic `ci.yml`, which it does not touch.
- **G9 gated T1 and T2 hard. It was falsified:**
  - P1 failed, with 2 of 5 runs red on T2 for row 24;
  - the registered demotion fired.
- **G9b gates T1 alone:**
  - T1 is retrieval set equality, plus the refusal identity on row 25;
  - faithfulness (T2) and answer correctness (T3) are reported;
  - P1b, P2b and P3b hold.

**What it covers, and what it doesn't.**
- **The served path.** It gates the served path through `api.main._answer`, so retrieval, generation and the output
  guard (with the input guard off, as shipped). The judged answer is the served one.
- **Hermetic only:** the HTTP layer and the response contract (status codes, the Pydantic model, citation rendering)
  are covered by `tests/test_api.py`, not by the smoke.
- **The scope is the 8 rows.** Nothing outside them is evaluated in CI; the full 28 stay a manual `run_eval.py` run.

**Faithfulness is reported, not gated.**
- **Generation varies on identical input.** Row 24 served answer B (faithfulness 0.75) in 2 of 7 G9 runs, with
  byte-identical contexts and the same fingerprint.
- **Row 21 also varies.** It served two new variants at faithfulness 0.3333 in 3 of 5 G9b runs. Read through the
  breach ciphertext, both conclude that the documents agree, but attribute the OSHA table's value to the NIOSH Pocket
  Guide page that reports OSHA's PEL.
- **Under a hard T2, both would be reds** with no code change.

**Answer B is unclassified.**
- B reproduced only on GitHub-hosted runners (2/7) and never locally (0/24 plus 23 historical); its text was not
  readable through the public artifact.
- **It never recurred** in the 14 CI observations after its last one (05:11 UTC). The local attempts all came after
  that time, so calling environment and time window aren't separable.
- **The account headers match.** The generation call's organization and project headers are identical in CI and
  locally.

**Secrets surface.**
- The two keys are available to `pull_request` runs from same-repo branches, which means anyone with write access can
  read them by editing the workflow in a PR. That is acceptable for a single-owner repo, named as the trade-off.
- Fork PRs never receive them (skip = neutral), and a skipped required check counts as satisfied.

**The breach key (`eval/smoke_pubkey.asc`).**
- **Purpose:** the maintainer can read the one thing the public artifact otherwise withholds, a breaching row's
  served answer, without publishing it.
- **What is encrypted, and when:**
  - only the answer text of a row with a T1 failure or a T2 floor breach;
  - never contexts, questions or other document text.

  It is written as one file per row, `breaches/row-<n>.asc`, in the run's artifact, next to the report's hashes.
- **The key:**
  - an OpenPGP key with primary fingerprint `0A23108E5E612C30A84874FC1A47B75AB89F83AE` (Ed25519) and encryption
    subkey `E6C4AFC0058EF625E2015BF435F761047666962B` (cv25519);
  - only the public key is in the repository;
  - the private key is held off-repo by the maintainer.
- **The failure mode:**
  - without gpg, or on any gpg error, nothing is written: never plaintext;
  - the runner also discards any output that contains the plaintext.
- **Rotation:** replace the `.asc` and `PUBKEY_FINGERPRINT` in `scripts/smoke_eval.py`. Older ciphertexts still need
  the older private key.

**The cache, the snapshot and the paths.**
- **A PR's first run is cold.** Caches are scoped per ref, and a PR's merge ref can't see its branch's dispatch
  caches. So the first run of a PR re-judges every row (PR #5: 0 hits, $0.016) until `main` has a cache.
- **The snapshot changes only through a deliberate re-baseline commit.** A diff that touches both the snapshot and
  `src/`, `agent/` or `api/` gets a `::warning::`.
- **`data/manifest.json` is not a gated path,** although the router's document catalog comes from it. A manifest-only
  change doesn't run the smoke.

**The required check.** P1b and P2b hold, so the maintainer may make `eval-smoke / smoke` and `gate` required. The
agent doesn't change branch protection. The registered demotion rule still applies: two noise reds in any twenty gated
runs.

**Commits after the squash.** The measured commits are tagged so the docs' citations resolve after the merge:
- `evidence/g9-build`;
- `evidence/g9-baseline`;
- `evidence/g9-characterization`;
- `evidence/g9b-characterization`.

## G10b approval gate — what it is and is not shown to do
Recorded 2026-10-09.
- **Sources:**
  - the design is [`g10b_design.md`](g10b_design.md);
  - the pre-registration and its outcome are in [`g10b_PREDICTION.md`](g10b_PREDICTION.md), whose pre-registration
    commit is tagged `prereg/g10b` at PR time;
  - the run is in the G10b block of [`METRICS_HISTORY.md`](METRICS_HISTORY.md).
- **Rows are 1-based.**

**What it is.** `/ask/agent` pauses before generation on a trigger policy (`agent/review.py`), returns HTTP 202 with
the evidence, and resumes on approve, reject or amend.
- **Shown, live:**
  - it fired on exactly frozen rows 9, 10 and 11 (P1);
  - approve, reject, amend and expired were each 6/6 (P2);
  - a local uvicorn restart kept the pause and answered on approve (P3a);
  - non-firing inputs to `generate` were byte-identical in 75/75 runs (P4);
  - the checkpointer added 2.4 ms at p50 (P5).

**No authentication.** Anyone who can reach the service can approve a paused safety answer, or remove its evidence.
The follow-on is a reviewer token. It is not built.

**Amend is removals only.**
- Additions and `Reset()` are implemented and tested at the reducer and graph level (`tests/test_review.py`) but not
  exposed by the API.
- **The reason:** unauthenticated injection of citable text. On a public API with no reviewer identity, an "addition"
  would let anyone put text into an answer's evidence under a real manifest document's `source_doc_id`, and the answer
  would then cite it.

**The trigger is a policy on the question's wording, not a measurement of answer quality.**
- It names exposure limits (`exposure limit`, `IDLH`, `PEL`, `REL`, `TLV`, `STEL`), or a source-scoped fallback. It
  says nothing about whether an answer would be right.
- **Two named misses:**
  - "immediately dangerous to life or health" spelled out (guardrail hard negative 25);
  - "exposure ceiling" (frozen row 21).

  Widening the pattern to catch either would fire on a smoke row or a hard negative.
- **Tool failures are not a trigger,** because their fire set could not be predicted from local result files.
- **Scope:** the trigger applies only on `/ask/agent`; `/ask` never pauses.
- **Consequence on the live service:** every `/ask/agent` question that names an exposure limit returns 202 until
  someone resolves it.

**Durability is option A: SQLite, failing closed.**
- **Where a pause survives:** on a host with a persistent filesystem, the pause survives a process restart (P3a).
- **On Render's free plan,** the filesystem is replaced on every redeploy, restart and idle spin-down, so a pause is
  lost. Every merge to `main` that changes code redeploys.
- **Never silently.** The resume then returns the `expired_or_lost` refusal, never an answer.
  - **P3b HOLDS, observed live on 2026-10-09:** a pause at 07:25:11Z, a manual redeploy of `ed8a423` (live by
    07:26:42Z), and a resume at 07:28:49Z that returned `expired_or_lost` with no answer.
  - Spin-down is excluded (3 min 38 s elapsed). The record is in [`g10b_PREDICTION.md`](g10b_PREDICTION.md).
- **The fix, if the gate must survive deploys:** option B, a Postgres checkpointer. That means a `DATABASE_URL` secret,
  the `langgraph-checkpoint-postgres==3.0.5` pin, and one network write per `/ask/agent` request.

**The evidence a reviewer reads can be mostly noise** (a limitation of the review experience, not of the gate).
- On the live pause of row 11 (anhydrous ammonia, OSHA against NIOSH), 3 of the 10 evidence chunks concerned ammonia.
  The rest were other substances' Pocket Guide entries and appendix text.
- The gate shows exactly what `generate` would receive, so retrieval's known precision problem lands on the reviewer.
- Amend (removals) is the tool for it, one key at a time.

**Single worker.** Concurrent resumes of one thread are serialized by an in-process lock. That holds for the one
uvicorn worker the Dockerfile runs. Several workers or instances would need a database-level guard.

**Fail-closed edges:**
- with no checkpointer configured (`REVIEW_DB_PATH` unset), a triggering question is refused (`review_unavailable`);
- a SQLite error at the pause refuses the request the same way, even for a question that would not have paused, because
  the error surfaces at the run's single checkpoint write;
- the startup sweep deletes expired threads, plus any thread with no review record.

**Tests run with the trigger off by default** (`tests/conftest.py`), so existing tests exercise the non-firing path;
`tests/test_review.py` switches the real policy on. The G9 smoke and `run_eval.py` run with no checkpointer, where
non-firing rows are unchanged; row 24, the smoke's `/ask/agent` row, never fires.

## G11 provider seam — what it is and is not shown to do
Recorded 2026-10-12.
- **Sources:**
  - the design is [`g11_design.md`](g11_design.md);
  - the pre-registration and its outcome are in [`g11_PREDICTION.md`](g11_PREDICTION.md), whose pre-registration
    commit is tagged `prereg/g11` at PR time;
  - the run is in the G11 block of [`METRICS_HISTORY.md`](METRICS_HISTORY.md).
- **Rows are 1-based.**

**What it is.** `agent/generation.py` puts the agent path's generation behind `GENERATION_PROVIDER`.
- `openai`, the default, is the frozen `src.generate.generate` itself, so its request body is byte-identical to the
  pre-G11 one on all 28 questions (P1).
- `bedrock` sends the same two messages to Amazon Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`, us-east-1) through
  Bedrock's Converse API, with boto3.

**Bedrock is measured and not recommended.** It fails the pre-registered shipping rule (P7) on all three counts:
- **per-row quality (P3):** faithfulness fell on 9/28 rows and answer correctness on 8/28, though both aggregates held;
- **refusal (P6):** 0/3 exact refusals, so its hedged answer is served at high confidence (the citation-layer entry
  above). **P6 is the blocker;**
- **cost:** 2.54× per answer at p50 ($0.00294 against $0.00116).

**What Bedrock cannot record.**
- Converse returns no `system_fingerprint` and no model version, and Nova has no seed parameter.
- Recorded instead: the requested profile id (its only version signal), the AWS request id, `latencyMs`, `stopReason`
  and token usage. `serviceTier` came back absent on 84/84 calls.
- Without a seed, Nova's three answers were byte-identical on 2/28 rows (`gpt-4o-mini`: 10/28).

**Only generation moves.**
- The router, the tool decision, the decomposer (off), the input-guard classifier (off), the judge and the embeddings
  stay on OpenAI.
- `/ask` (`src/`) stays on OpenAI by design. A deployment without an OpenAI key is not possible from this seam.

**Nova's inline citations differ.** It used the exact `source_doc_id=` marker in 18–22 of 28 answers per trial
(`gpt-4o-mini`: 28/28) and cited no doc id at all in 2–5. Structured citations are unaffected, because they derive from
chunk metadata.

**Row 21 misattributes differently.** Nova cites `osha-1910-1000 page=52` for the chlorine ceiling, a page that doesn't
carry it (3/3). `gpt-4o-mini` cites the NIOSH page instead (3/3, the G9 instance in the citation-layer section above).

**AWS account gates,** recorded 2026-10-12, verbatim in [`g11_aws_gates.txt`](g11_aws_gates.txt):
- **Anthropic models:** the Marketplace subscription is active, but the applied on-demand quota is 0, and the increase
  request was routed to AWS Sales as above the support threshold. Not obtainable inside the time box.
- **Claude Haiku 5.5** is not available to this account tier at all.
- **Nova 2 Lite** carries its default quota, 20 RPM. The eval held 15 RPM or less, with 0 throttles.

**No `langchain-aws`.** Its last line compatible with `langchain-core` 0.3 (0.2.35) requires `numpy<2` on Python 3.11,
and a dry resolve downgraded numpy 2.4.6 → 1.26.4 project-wide. The adapter is boto3 only (`boto3==1.43.103`), imported
only when Bedrock is selected.

**Credentials stay local.** The AWS keys live in `.env` only (the names are in [`.env.example`](../.env.example)), not
in repository secrets and not in `render.yaml`. The deployed service cannot select Bedrock, and CI never calls it.

**Not deployed to AWS.** Render stays the host; an AWS deployment was out of scope.

## G8 cost control — what it is and is not shown to do
Recorded 2026-10-12.
- **Sources:**
  - the design is [`g8_design.md`](g8_design.md);
  - the pre-registration and its outcome are in [`g8_PREDICTION.md`](g8_PREDICTION.md), whose pre-registration commit
    is tagged `prereg/g8` at PR time;
  - the run is in the G8 block of [`METRICS_HISTORY.md`](METRICS_HISTORY.md).
- **Rows are 1-based.**

**What it is.** Every request is accounted for, by `agent/cost.py`, and this ships ON.
- **The record:** one per request. `_answer` opens it for both endpoints, and the resume endpoint opens its own.
- **What it holds:** tokens and USD at list price for each component (embedding, router, tool decision, generation),
  with cached input kept separate.
- **Where it goes:** one INFO log line per request, and a trace note on the agent path.
- **Measured:** the record matched an independent capture on 84/84 requests (P1), with a worst relative error of
  2.1e-16.
- **No response field,** by the GATE 1 decision.

**There is no traffic.** Every cache figure is a property of reruns of the frozen 28, not of users. As the roadmap
puts it: *"caching needs traffic, routing needs query diversity. Neither exists at 19 docs."*

**No answer or semantic cache is built.** This is a probe-decided non-build, like BM25 fusion.
- **On the frozen 28,** an answer cache's hit rate is 100% from the second trial, by construction.
- **A semantic cache** would serve one question's safety answer to a different question judged similar.
- **What already exists, at zero code,** is OpenAI's automatic prompt cache:
  - 91% and 89% of `gpt-4o-mini` input tokens were cached on the second and third trials, and 8% on the first;
  - the router's prompt (p50 934 tokens) sits under the 1,024-token minimum and is never cached.
- **Real traffic is what would measure it.** The first 24 hours of it would, and the cost log is what would show it.

**Routing is measured and OFF** (`agent/routing.py`, `ROUTING_ENABLED = False`).
- **The policy:** a single-document question with no tool, no comparison and no exposure limit. It is eligible on
  rows 22–25 of the 28.
- **The pre-registered rule fails on all three counts:**
  - quality fell on 4 of 8 row-metrics, and at the aggregate (faithfulness −0.069, correctness −0.065);
  - the exact refusal held 0/3;
  - the cost delta across the 28 is 3.6%, against the 30% the rule requires.
- **Turning it on would also need** AWS keys that Render does not have, and it would change the G9 smoke's row 24.
  **Routing is a policy, not a quality signal.**

**Nova Micro's recorded limits, as the cheap arm:**
- **Refusal:** it echoes the instruction instead of returning the sentence (3/3), and is served at high confidence
  (the citation-layer entry above).
- **Attribution:** row 24 cites a page that isn't in its context (`sds-nutrien-anhydrous-ammonia page=9`, 3/3) for a
  value on page 6.
- **Tokenizer:** it counts 6–8% more input tokens than `gpt-4o-mini`'s on the same prompts.
- **Latency:** 0.67 s p50 on the eligible rows (supplementary, unregistered).

**The accounting's own limits:**
- **Embeddings are derived:** a tiktoken count of the query, because the embeddings API's usage never reaches
  LangChain.
- **Usage is what each API reports,** reconciled with LangChain's independent callback but not with the OpenAI or AWS
  bills.
- **A paused request is billed across two log lines:** the router, the tool decision and the embedding on
  `/ask/agent`, and generation on `/ask/agent/resume`.
- **The trace note** records the bill up to generation. The output guard that follows costs nothing.

**The named follow-on: a pre-filter on `tool_decide`.** This is the largest controllable line item: the tool decision
is 48.7% of a request's measured cost, and 26 of the 28 rows need no tool. The R7 probe found:
- **The predicate:** the comparison gate, or the exposure-limit pattern, or a unit-conversion pattern.
- **Its fire set** on the frozen 28 is {9, 10, 11, 21}, which includes 9 and 21, the rows where tools fired. On the
  capability set it fires 3/3, and on the guardrail hard negatives 0/9.
- **The saving:** skipping the decision on the other 24 rows would save **40.3%** of a pass at list price, as measured.
- **Not built in G8:** it is a second mechanism, it touches the G1 path, and it needs its own pre-registration,
  including a check that no tool-needing question is skipped.
