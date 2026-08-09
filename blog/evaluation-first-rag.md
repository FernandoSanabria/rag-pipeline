# Evaluation-first RAG: what happened when my own metrics lied to me

Anyone can build a retrieval-augmented-generation demo in an afternoon: chunk some documents, embed them, wire a vector search to an LLM, ship a chat box. The hard part isn't the demo. It's knowing whether it's any *good* — and the trap underneath that question is that your evaluation metrics will lie to you, confidently, to four decimal places.

This is the story of building a RAG question-answering system **evaluation-first** — the measurement harness before any retrieval or generation code — over a corpus of industrial-equipment-safety documents. Across the four measured changes of the build phase, answer-correctness went from 0.40 to 0.57 and faithfulness from 0.71 to 0.97. (These are 0–1 scores, not percentages: because the hand-written references are terse, a correct-but-*elaborate* answer gets marked down, so a 0.57 can be a genuinely good system — a trap I'll come back to.) But the numbers aren't the interesting part. The interesting part is the handful of times the measurement apparatus told me one thing and reading the actual output told me the opposite — and the two retrieval features I **killed with read-only probes before spending a single evaluation run**, shipping a pipeline *simpler* than the one I planned.

The false starts are the point. Here they are.

## The one decision that shaped everything

The corpus is 19 documents an industrial safety engineer might actually need: OSHA, EPA, and NIOSH regulations; chemical safety data sheets (SDS); equipment manuals from vendors like Flowserve and Emerson. Ten are public-domain government sources; nine are vendor-copyrighted (their PDFs never touch git — only provenance does).

RAG, briefly: you can't fit 19 documents in a prompt, so you **retrieve** the handful of passages most relevant to a question, then ask an LLM to answer *only* from those passages — grounding — and to refuse if the answer isn't there. Retrieval is the hard part. Feed the model the wrong passage and it will faithfully, fluently, tell you something wrong.

The decision that shaped the whole project: I built the **RAGAS** evaluation harness — RAGAS is a library that uses an LLM to score your answers — **before** writing a line of retrieval or generation logic. Five metrics, scored on 28 hand-written question/answer pairs:

- **faithfulness** — is every claim in the answer supported by the retrieved context? (i.e. no hallucination)
- **answer relevancy** — does the answer address the question?
- **context precision / recall** — did retrieval surface the right passages?
- **answer correctness** — does the answer actually match a hand-verified reference? This is the one that catches *faithful-but-wrong*.

Why first? Because you cannot improve what you cannot measure, and because it forces discipline: every change after this gets a verdict, from a clean commit, changing **one variable at a time**, with a falsifiable prediction written down *before* the run. A prediction committed after you've seen the result isn't a prediction; it's a rationalization.

**A word on those 28 pairs, because a small eval set is a fair thing to attack.** They span all 19 documents and are stratified deliberately — by *type* (single-document lookups vs. cross-document comparisons) and by *category* (fact, conditional, procedure, narrative) — with the hard cross-source rows included on purpose, because those are where retrieval breaks. I wrote and verified the references myself, which is the real limitation: one author is one point of failure, and I'd harden it with a second reviewer and a stratified expansion, tracking correctness per-category rather than only in aggregate. But note the arithmetic that makes 28 *workable*: a single row flipping from 0 to 1 moves aggregate correctness by ~1/28 ≈ **0.036** — larger than the ±0.03 I treat as noise. One row is worth more than the noise floor. That isn't a weakness to hide; it's the whole reason a per-row read has to arbitrate while the aggregate can only triage.

## The arc: v1 → v4

### v1 — the baseline, and the diagnosis

The baseline is textbook: split each document into ~500-character chunks, embed each chunk into a vector with OpenAI's `text-embedding-3-small`, store them in Pinecone. At query time, embed the question, pull the 5 nearest chunks by cosine similarity, hand them to `gpt-4o-mini` with instructions to answer only from that context or reply with an exact refusal sentence.

The numbers:

| metric | v1 |
|---|--:|
| faithfulness | 0.7143 |
| answer correctness | **0.4042** |

There's the whole story of the project in two numbers. Faithfulness 0.71, correctness 0.40. The pipeline answers *faithfully* — it doesn't make things up — but it's often faithfully reporting the **wrong retrieved text**. Reading the failures confirmed it: of 8 failing rows, 7 were retrieval misses, 1 was a generation refusal, and **zero** were metric artifacts. The bug wasn't the LLM. It was that I was handing the LLM the wrong pages. Fix retrieval first.

### v2 — semantic chunking, and a prediction that was one-third wrong

Fixed 500-character chunks are dumb about meaning: they slice a table row in half, or cut a lockout/tagout procedure between step 3 and step 4. **Semantic chunking** cuts on topic boundaries instead, keeping an SDS property block or a multi-page case narrative intact. That collapsed 7,635 fixed chunks into 1,258 semantic ones.

Every metric improved — context recall by +0.17, correctness by +0.11 (0.40 → 0.52). But I'd written the honest caveat into the [pre-registration](../eval/run_notes_v2_semantic.md) before running: v2 changed chunk **size and method at once**, so the gain is "semantic boundaries *plus* larger chunks," not boundaries alone. You don't get to claim the clean attribution you didn't earn.

More useful than the win was the **prediction scorecard: 5 right, 3 wrong**. Two rows I'd predicted *wouldn't* improve without fancier search — a NIOSH statistic and an EPA program-eligibility question — recovered strongly on better chunking alone (correctness 0.02 → 0.82 and 0.04 → 0.35). Better boundaries surfaced pages that fixed-500 had fragmented out of the ranking entirely.

And one row I was sure would recover — the Flowserve pump start sequence — **didn't**. Its context recall went from 0 to 1.0 (the right steps were now being retrieved) but its correctness stayed flat at 0.013. That's not a null result; it's a *relocation*. The Flowserve row had flipped from a retrieval problem to a generation problem. A wrong prediction that tells you where the bug moved is worth more than a clean win that tells you nothing.

### v3 — a better prompt, and the first lie

v3 changed only the generation prompt: permit synthesis, permit comparing two sources, and ground every claim. Same retrieval, same chunks.

Faithfulness jumped +0.09 (0.74 → 0.83). Several refusals became correct answers: the chlorine exposure-limit row went 0.03 → 0.66, Flowserve 0.01 → 0.23, a Fisher valve-torque spec 0.03 → 0.38. The pre-registered win condition — *recover the retrieval-solved rows without introducing any confident-wrong answers* — was met, and a refusal-integrity audit confirmed it: three refusals became genuine attempts, and **zero** answerable-only-in-theory rows flipped to fabrication.

And yet answer correctness moved **−0.0024**. Flat. Worse, the per-row diff showed *7 correctness regressions*.

So I read all 7 responses. Zero fabrications. Every one was **elaborate-but-true**: correct, grounded in its cited source, and penalized only because it added detail the terse hand-written reference didn't contain. One answer said "1,281 fatalities, of which 152 involved…" — exactly right, and marked down for the extra clause. `answer_correctness` wasn't measuring correctness here; it was measuring *verbosity against my reference phrasing*. (RAGAS also scores a clean, honest refusal as faithfulness 1.0 — correct behavior, flattering number.)

This is the first time the aggregate and the artifact disagreed, and the artifact won. It would not be the last.

## The two levers I killed before spending a single eval run

Here's the part I'm proudest of, and it's a part where I *didn't* build something.

Going into v4, I had two more-powerful retrieval ideas planned and pre-registered. First: **BM25 fusion** for the one stubborn cross-document row — comparing a chemical's NIOSH IDLH (immediately dangerous to life or health) value against its EPA toxic endpoint — that dense search kept missing. Second: full **hybrid retrieval** across the board.

The concepts, since you need them to follow the reasoning: dense retrieval (what I'd been using) matches on *meaning* — great for paraphrase, blind to exact identifiers. **BM25** is classic keyword search: it matches on *terms* and their frequency, so it catches things like "IDLH" or "UN3304" that dense embeddings smear away. **Hybrid** runs both and fuses the two rankings — typically with Reciprocal Rank Fusion, which scores each document by summing `1/(k + rank)` across the lists, rewarding things that rank high in *either*. The intuition is sound: cover meaning and terms at once.

The discipline: instead of building hybrid and spending eval runs to see if it helped, I wrote [**read-only rank probes**](../eval/run_notes_v4_densek10.md) — scripts that just report where the known answer chunk lands under each scheme. No generation, no scoring, no cost. And the probes killed both levers.

The finding was almost embarrassingly clean. A plain **increase in retrieval depth — k from 5 to 10 — strictly dominated hybrid.** Here's why, and it's a guarantee, not an observation: raising k only *adds* ranks 6 through 10. It cannot evict a chunk that dense search already ranked in the top 5. Zero evictions, by construction. Hybrid, because fusion re-ranks *everything*, can and does evict: in the probe, hybrid recovered one row (Flowserve) but **demoted two rows that were already passing** — an ammonia autoignition fact and a NIOSH finding — off the bottom of the list. Depth recovered *two* rows and evicted *zero*.

So the simpler change won, and I could prove it before writing it. I dropped both levers. v4 was one integer: `k = 5 → 10`.

The payoff, measured against a same-commit k=5 re-run so it isolates depth alone:

| metric | Δ v4 (k=10) |
|---|--:|
| faithfulness | **+0.142** (to 0.97) |
| context precision | −0.06 |

Two read-verified recoveries. The ammonia PEL row flipped from refusal to correct (0.019 → 0.759): with k=10 pulling the NIOSH Pocket Guide's ammonia entry into context at rank 6, the answer now gives OSHA's PEL (its legal exposure limit) of 50 ppm and NIOSH's REL (its recommended one) of 25 ppm. Flowserve finally recovered too (0.202 → 0.559) — and it beat its own pre-registration, which had predicted it would need a *further* generation fix. With both the checklist and the ordered procedure in context, the model just chose the sequence on its own. That removed a task from the backlog.

The one metric that fell — context precision, −0.06 — is the mechanical cost of grading twice as many chunks, not a loss of quality. I read every per-row correctness dip to be sure: each was the same verbose-but-true artifact from v3, correct core value plus grounded elaboration, faithfulness 1.0.

## When the measurement apparatus lied

By v4 the pattern was undeniable, so I'll name the lies directly. Each one is a place where trusting the number would have sent me the wrong way.

**"Recall" didn't measure recall.** RAGAS `context_recall` returned a perfect **1.0** on rows where the answer chunk was flatly absent from what I retrieved — acetone's flash point, the Flowserve steps. A metric named for whether you retrieved the answer, unable to tell you whether you retrieved the answer. The arbiter for retrieval became reading the retrieved contexts, or a rank probe — never the metric.

**"temp=0 is deterministic" — conditionally.** I ran a controlled probe: the same call, 10 times back-to-back, gave 10 identical outputs under one backend fingerprint. Determinism confirmed — *at a fixed fingerprint*. But OpenAI's `system_fingerprint` — its identifier for the backend configuration that served your request, which changes when they change the infrastructure underneath you — drifts between runs, and an identical-config re-run 35 minutes later produced different responses on **14 of 28 rows**. My two v4 replicates differed on **13 of 28** despite reporting the same primary fingerprint. The determinism is real and conditional on infrastructure you don't control — which is why I started persisting the fingerprint with every result, and why big deltas (recall +0.17, faithfulness +0.14) count as signal while a ±0.03 wiggle in correctness doesn't.

**A row gained +0.34 correctness by doing nothing differently.** The IDLH comparison row moved 0.024 → 0.362 — and still refuses the comparison. The k=10 answer correctly states the EPA endpoint (200 ppm) and then honestly declines the NIOSH half it *still* can't retrieve. The judge partial-credited an honest partial answer. Not a recovery, not noise — an artifact of the metric that only reading the response reveals.

The through-line, and the thing I'd want a reviewer to take away: **the metric triages; the artifact arbitrates.** The aggregate score tells you where to look. The actual response, or the actual rank probe, tells you what's true. Treat the aggregate as ground truth and you will eventually ship a number that moved instead of a system that improved.

## Shipping it

The pipeline is behind a FastAPI service with a typed response contract: `answer`, `citations: [{document, page}]`, `confidence_score`, and a `confidence_basis` string that says *why* in plain language. It's [live](https://equip-docs-rag-api.onrender.com). A real request and response:

```
POST /ask
{"question": "In an ammonia refrigeration system, why is a vapor-space rupture
              unlikely to be the worst-case release compared to a liquid release?"}
```
```json
{
  "answer": "In an ammonia refrigeration system, a vapor-space rupture is unlikely to be the
             worst-case release compared to a liquid release because the rate of release of
             ammonia gas is significantly less than that of liquid ammonia. … a vapor release
             results in a buoyant ammonia jet, which is less dense than air, and thus disperses
             more quickly … (source_doc_id=epa-rmp-ammonia-refrigeration page=17).",
  "citations": [{"document": "EPA RMP Guidance for Ammonia Refrigeration Facilities", "page": 23}, …],
  "confidence_score": 0.9,
  "confidence_basis": "high: answer generated from retrieved context"
}
```

Notice the answer's prose signs off with an ad-hoc "(… page 17)" — the model's own guess at provenance. The `citations` field ignores that prose entirely: it's built from the retrieved chunks' **metadata**, so it reports the real pages retrieval pulled (led by page 23), not the page the model typed. That matters because the model *does* sometimes type the wrong one — here it wrote "page 17" for a chunk that came from page 23, and in eval a value-correct ammonia answer wrote "page 46" for a fact whose chunk was page 44. Deriving `{document, page}` from metadata makes the citation's **provenance** right by construction — it names the chunk the text actually came from, independent of the prose. (What metadata can't guarantee is the exact *page of the fact* — see the limitation below.)

On latency: warm requests run at a P50 (median) of 3.7s and a P95 (the slow-tail 95th percentile) of 11.0s end-to-end (cold starts on the free tier are excluded, and slower). Where the time *goes* is a separate question, answered by a local timing split — run on my laptop with no Render host and no network hop, n=4, so its absolute seconds are **not** comparable to the deployed figures above; read it only for the ratio. And the ratio is stark: generation is the bulk of `/ask`, roughly **3× retrieval**. The bottleneck is the LLM call, not the vector search.

## What still doesn't work

The honesty is the credibility, so:

- **Two rows remain retrieval misses even at k=10** *(this was the original closing — I've left it standing; see the update right after)*. The NIOSH IDLH entry sits at rank 20 for its query; the acetone flash-point chunk at rank 107. Both are cross-source or dense-tabular lookups that are lexically and semantically unlike the natural-language question — neither more depth nor BM25 fusion surfaces them. The real fix is query decomposition (break the comparison into per-source sub-lookups) or table-aware extraction, and that's honestly a different, agentic phase of work — not a retrieval-mode swap I can hand-wave in.
- **Page-level citations are provenance-correct but not always fact-precise.** Metadata gets the document and the source chunk right — but a chunk is labeled by its *start* page, so when it spans a boundary the fact can sit a page or two from the labeled page (the IDLH answer lived in the chunk labeled p44 though the value is on p45; an EPA value whose chunk cites page 1 sits nearer page 4). Provenance is right by construction; exact fact-location isn't — an open concern, tracked in [known limitations](../eval/KNOWN_LIMITATIONS.md).
- **The confidence score is deliberately coarse — and that's a measured decision, not laziness.** It's two tiers: a refusal scores low, an answered question scores high. I *tried* to make it finer, using retrieval similarity to grade answer quality, and probed whether that signal actually discriminated. It doesn't: top-1 similarity sits in a narrow 0.53–0.76 band, and the weak-answer median (about 0.65) actually lands *above* the correct-answer median (about 0.61) — the signal isn't just weak, it's faintly inverted. A continuous confidence number built on that would be false precision, so I didn't ship one. The `confidence_basis` string carries the honest reason instead.

**Update (August 2026) — I falsified the fix in that first bullet.** A read-only rank probe showed the IDLH answer chunk *absent from the top 100* under every reformulation — the "rank 20" I reported above was itself the metric flattering the row — so no rephrasing, query decomposition included, could have surfaced it. The real cause was fat multi-record chunks diluting each fact below dense reach. Both rows recovered: IDLH via structure-aware re-chunking (now the promoted `semantic_v2` default on `/ask`) and acetone via a source-scoped router (on `/ask/agent`); the shipped system scores answer-correctness **0.5932** on `/ask` and **0.6305** on `/ask/agent` ([metrics ledger](../eval/METRICS_HISTORY.md), [probe receipt](../eval/decomp_probe_RESULT.md)). Which is this piece's own thesis turned on its author: a fix proposed in print, killed by a cheap probe, replaced by something simpler that shipped. The full diagnosis is its own write-up.

## The takeaway

The pipeline I shipped is simpler than the one I planned. No hybrid retrieval. No BM25 arm. No fusion. Dense retrieval over semantic chunks, and one integer changed from 5 to 10. Every one of those removals was earned by a measurement — a rank probe that showed depth dominated hybrid, a read of seven "regressions" that were nothing of the kind, a fingerprint probe that explained away run-to-run noise.

That's what evaluation-first actually buys you. Not ceremony, and not a dashboard to feel good about. It's the ability to tell a real improvement from a number that merely moved — and, just as important, the confidence to *stop building* when the evidence says the simple thing has already won.

---

*The code, the eval harness, and every run described here live in the [repository](https://github.com/FernandoSanabria/rag-pipeline); the run-by-run metrics are in [`eval/METRICS_HISTORY.md`](../eval/METRICS_HISTORY.md). Its companion piece — [what all this measurement actually cost](the-cost-of-rigor.md) — links back here.*
