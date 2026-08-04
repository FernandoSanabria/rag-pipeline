# The chunk that wasn't there: falsifying my own fix before I built it

My RAG pipeline was fully evaluated, live behind an API, and still got two questions wrong. I knew which two, I had a plan for fixing one of them, and I was confident in the plan. This is the story of proving that plan wrong — before I wrote a line of code to build it — for the cost of a few retrieval calls, and of what the same cheap probe turned up instead: the two failures I'd been treating as unrelated were one defect wearing two disguises.

The useful part isn't that I found the bug. It's the order of operations that found it: write the prediction down first, kill it cheaply, and — when my own instrument gave me a confident wrong answer — believe the raw artifact over the instrument. That last one nearly got me.

*(This is the diagnosis half of a two-part story, and a companion to the earlier build write-up, [Evaluation-first RAG: what happened when my own metrics lied to me](evaluation-first-rag.md). Article B is the fix.)*

## Two failures I'd already measured

The pipeline answers questions over a corpus of industrial-safety documents — OSHA and EPA regulations, NIOSH guides, chemical safety data sheets. By the end of the build phase it was good, but the honest write-up ended with what still didn't work: **"Two rows remain retrieval misses even at k=10. The NIOSH IDLH entry sits at rank 20 for its query; the acetone flash-point chunk at rank 107."** Both are the kind of lookup where the answer is a single value buried in a dense table, phrased in the document nothing like it's phrased in the question.

I had a plan for the first one. The IDLH question is a cross-source comparison — *"for anhydrous ammonia, how does the NIOSH IDLH compare to the EPA RMP toxic endpoint?"* — and the textbook fix for a comparison that retrieval keeps missing is **query decomposition**: instead of searching for the whole two-part question at once, break it into per-source sub-questions ("what is the NIOSH IDLH for ammonia?" and "what is the EPA endpoint?"), retrieve for each, and combine. It's a real technique, and it sounded right.

Here's the discipline that made the difference, and the first one worth stealing: **I wrote the prediction down before I ran anything.** Not in my head — in a file, committed to git. `scripts/decomp_probe_PREDICTION.md`, at commit `cc01954`, timestamped *before* the probe that would test it existed. This matters more than it looks. A prediction you record after seeing the result isn't a prediction; it's a rationalization, and you will always be able to construct one that makes you look prescient. The only version that can actually catch you is the one that's on the record before the data comes in.

And what I put on the record was hedged, deliberately: I predicted decomposition would only *partially* work. The EPA half of the comparison would surface; the NIOSH half — the value that had been stubborn all along — would stay buried even when isolated as its own sub-question, because I suspected it was a term-level miss that rephrasing wouldn't fix. Partial recovery, which the correctness metric would partial-credit (~0.02 → ~0.36) without actually solving the row. The point of writing that down wasn't to be right. It was to give the probe something of mine to kill.

## The probe that cost nothing

Before building any of it — no router to decide when to decompose, no graph to orchestrate the sub-queries, no re-run of the evaluation suite — I asked the cheapest possible version of the question.

Decomposition's entire job is to turn one query into better sub-queries. So the thing to test isn't the machinery; it's the premise. I hand-wrote the two sub-questions and ran them straight through the retrieval I already had — `dense_search`, at depth 10 and depth 100, twice back-to-back to catch any ordering wobble. No router, no generation, no scoring, no LLM in the loop at all. A handful of vector lookups.

This is the second discipline: **falsify cheaply before you build.** If decomposition can help, its sub-questions must — at an absolute minimum — retrieve the answer chunks. If they don't, no amount of orchestration built on top will conjure the answer into existence. I could spend a day building a router and a decompose node and an eval run to find that out, or I could spend a few retrieval calls. The probe is read-only and it is nearly free, and being wrong at that price is the whole idea.

## The prediction inverted

The EPA sub-question improved — its answer chunk moved from **rank 27 to rank 13** — but never cracked the top 10.

The NIOSH sub-question, the one I'd predicted would stay "deep," did something worse than deep. The ammonia IDLH entry was **absent from the top 100** — for the isolated sub-question *and* for the combined query. Not rank 40, not rank 90. Not there.

So my prediction didn't just miss; it inverted. I'd written *EPA surfaces, NIOSH stays deep*; the artifact said *EPA never reaches the top 10, NIOSH is absent from the top 100 entirely*. Every one of the pre-registered decision branches I'd defined assumed the answer chunk was at least *retrievable at depth*. None of them fit. The premise was gone.

And this is the reframe that turned the whole investigation, and it's free once you see it: **decomposition rewrites the query, but you cannot retrieve a chunk that isn't in the candidate set — no matter how you phrase the question.** If the answer isn't in the top 100 for *any* phrasing, then rephrasing was never going to be the lever. Not decomposition, not keyword search, not hybrid fusion. The problem lived somewhere upstream of retrieval mode entirely. I'd been about to build the wrong machine, and the probe cost me almost nothing to find that out.

## When the probe lied to me

Except the probe almost told me to build it anyway. This is the part I'd most want you to take with you, because it's the failure mode nobody warns you about.

The probe didn't just report ranks — it made a call. I'd pre-registered a matcher to answer "did this sub-question retrieve the answer chunk?", and its mechanical verdict came back **`FULL_LEVER`**: SQ1 had hit the NIOSH target at **rank 7**. Read literally, that verdict said *decomposition works — go build it.* If I'd trusted it, the story ends here, with me implementing a router to solve a problem that isn't a routing problem.

I read the rank-7 chunk. It was `niosh-pocket-guide` page 44 — an unrelated **"Combustible Solid"** entry. Not ammonia. Not remotely the answer. The verdict was confidently, mechanically wrong.

Why did the matcher misfire? Because I'd built it to be *tolerant*, on purpose. I told it to count a hit if the chunk was within a page of the ground truth (p45, give or take one) **and** contained the strings "IDLH" or "300". That tolerance was defensive: chunks are labeled by their starting page, and the ammonia answer actually lives in the chunk labeled p44, not the ground-truth p45 — so a strict page match would have missed the real answer over an off-by-one. Reasonable. But in the NIOSH Pocket Guide, **every** chemical entry lists an "IDLH:" value, and "300" is an unremarkable number that recurs across the book. The strings I'd matched on are ubiquitous. So a page-adjacent chunk for the *wrong chemical* satisfied every condition I'd written. The matcher I'd tuned to avoid **false negatives** had quietly handed me a **false positive** instead — and false positives are the worse trade here, because a missed hit makes you look again while a phantom hit makes you stop.

What actually settled it was reading the chunk — specifically, scanning it for the chemical's *name* (does this text say "ammonia"?) alongside the strict exact-page rank. Both showed the same thing the tolerant matcher had papered over: absence. (This is also, I suspect, why the earlier build write-up had logged this row at "rank 20" rather than absent — a page-level read finds a plausible-looking neighbor near the right page; reading for the actual chemical finds nothing in the top 100. The instrument had been flattering this row all along.)

Here is the rule that moment named, and I'll be carrying it for the rest of this project: **the metric triages; the artifact arbitrates.** A fuzzy match, a similarity score, an aggregate — these tell you *where to look*. They cannot tell you *what's true*, and any matcher tuned to suppress one kind of error will manufacture the other. The only thing that adjudicates is the raw artifact itself: the retrieved chunk, read with your own eyes. Every real turn in this project has come from the moment the number and the text disagreed and I believed the text.

## Absent is a symptom, so I audited ingestion

"Absent from the top 100" is a strange result, and stranger than a bad rank. A chunk at rank 200 is a ranking problem — the answer's in there, just sorted too low. A chunk that never appears, under any phrasing, suggests something more basic is wrong with the chunk itself. So — still read-only, still no spend — I stopped searching by similarity and started querying the index by metadata: pull the raw chunks for this document and just look at them.

The ammonia entry was there. Ingested, intact, coherent: *"Ammonia … IDLH: 300 ppm … Anhydrous ammonia … NIOSH REL 25 / OSHA PEL 50."* The data was fine. The **container** was the problem — that entry was buried inside a single chunk that merged **about five different chemical entries**.

And that's fatal for dense retrieval, in a way worth spelling out. An embedding is one vector meant to represent the meaning of its whole chunk. Feed it a passage about five different chemicals and it encodes the *average* of five — a smear that points nowhere in particular. When a query for "IDLH for ammonia" gets embedded, it points cleanly at ammonia; the blurred five-chemical vector just isn't close to it. The fact was retrievable in principle and unreachable in practice, not because it was missing or mis-ingested, but because it had been forced to share one vector with four unrelated chemicals. Call it **embedding dilution**: pack multiple records into a chunk and each individual fact sinks below the surface the query can reach.

## One defect, not two

Then I ran the exact same metadata audit on the other failure — acetone's flash point, the rank-107 miss I'd mentally filed under a completely different heading ("that one needs table-aware extraction").

Identical shape. The value was present and correct (**"-17,0 °C", closed cup**) and buried the same way: this time inside a single **5,487-character** SDS chunk spanning Sections 9 through 11 — dozens of physical properties, plus reactivity, plus toxicology, all compressed into one vector. The flash point was diluted below reach for precisely the reason the ammonia IDLH was.

That's the payoff of the diagnosis, and it's why the cheap probe was worth more than the router I didn't build. I'd been carrying two problems: a cross-source comparison that supposedly needed decomposition, and a tabular lookup that supposedly needed table-aware extraction. Two failures, two planned fixes, two headings in the backlog. They were **one defect.** The chunker had merged multiple records into single oversized chunks wherever the source was tabular, and every fact inside was diluted below dense retrieval's reach. The comparison wasn't failing because it was a comparison. The table lookup wasn't failing because it was a table. Both were failing because the answer chunk, though present and coherent, shared its vector with a crowd.

Two hard targets, one root cause: fat, multi-record chunks over the corpus's tabular sources.

A defect with a shape tells you the shape of the lever — which is where the diagnosis ends and the fix begins. The next piece is about building that lever, and about how the fix, in its turn, also tried to lie to me about whether it had actually worked.
