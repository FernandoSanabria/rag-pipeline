# 94% of my LLM calls were the system grading itself

I built a small RAG system — *retrieval-augmented generation*: it answers a question by first pulling the most relevant passages from a document corpus, then having a language model write a grounded answer from them — over a 19-document industrial-safety corpus, put it live behind an API, and ran roughly fourteen evaluation passes against it, most of them full 28-question, five-metric runs. Then I pulled the traces to see where the calls went, and the split stopped me: **of every request the project made to the language model, 94% were the evaluation harness grading answers. Only 6% were the product actually answering a question.** The system spent almost all of its API activity measuring itself.

The whole thing cost **$1.53** — about as much as a coffee. That's the headline everyone likes, and it's true (it's the build-phase total, as of mid-July 2026; I'll come back to how it grew). But the number worth your time is the one hiding inside it, and almost no RAG tutorial mentions it: **most of that activity wasn't the product. It was measuring the product.** I have the traces to prove it, and the split turned out to be more lopsided than I'd have guessed.

Here's the thing that makes a number like that possible, because it isn't obvious walking in: **to score an answer, one LLM grades another.** You can't check whether an answer is faithful to its sources by diffing strings against a reference — you need something that understands meaning, so you ask another model. That judge reads the answer, pulls out each factual claim, and checks each claim against the retrieved passages — and every one of those checks is its own API call. The library that orchestrates this, **RAGAS**, does it for five separate metrics, on every question, across every run. The measurement fans out until it dwarfs the thing being measured.

*(This is the companion to the build story, [Evaluation-first RAG: what happened when my own metrics lied to me](evaluation-first-rag.md) — that piece is about what evaluation-first found; this one is about what it cost, including the cost of measuring honestly.)*

## The bill, itemized

Here is the entire infrastructure cost of a production-shaped RAG pipeline, live on the internet:

| Line item | What it is | Cost |
|---|---|--:|
| OpenAI — chat completions | `gpt-4o-mini`: answer generation **+** RAGAS judging | ~$1.25 |
| OpenAI — embeddings | `text-embedding-3-small`: 11.05M tokens, ingest + query | ~$0.28 |
| Pinecone | vector database — free tier | $0.00 |
| Render | container hosting — free tier | $0.00 |
| LangSmith | full request tracing — free tier | $0.00 |
| **Total** | | **$1.53** |

*(These are the build-phase totals, as of ~July 13, 2026 — the state when this piece was written. The project didn't stop there: by early August, after Phase-2 evaluation work, the cumulative OpenAI bill was ~$3.00 — but almost all of that growth is on the chat/judging side. The embedding line barely moved, because you embed the corpus once. That shape — judging climbs, ingestion is a fixed cost you've already paid — is this whole article in miniature; the [cost ledger](../eval/COST_LEDGER.md) carries both columns side by side.)*

Three of the five line items are zero, and that isn't a corner cut — it's the right call for the scale. The corpus is 19 documents, not 19 million; the traffic is a portfolio demo, not a business. At that size the free tiers aren't a hobbled trial. They're simply enough.

Pinecone makes the point sharply: free-tier vector search was never the bottleneck and never cost a cent. The model dominates both the bill and the clock — generation runs about **3× the latency of retrieval** (measured locally in the build piece), and warm, the deployed endpoint answers at a **p50 (median) of ~3.7 s and a p95 (the slow tail) of ~11 s**, almost all of it the LLM, not the search. Vector search rounds to noise. (Render's free tier has one real catch, documented in the repo's README: it spins the container down after ~15 minutes idle, so the first request after a nap eats a 30–60 s cold start. Warm, it's fine — a fair trade for $0/month on a demo.)

Only OpenAI cost real money, and mostly its chat-completion half. All eleven million embedding tokens — *embeddings* being the numeric vectors that let the system match a question to passages by meaning rather than by keyword — came to about a quarter, because `text-embedding-3-small` is $0.02 per million tokens (plus a rounding-error ~0.6M tokens on the legacy `ada-002` model RAGAS falls back to internally).

## The cost of rigor

That chat-completion spend paid for two completely different jobs:

1. **Generation** — the product doing its actual work: read the retrieved passages, write a grounded answer.
2. **Judging** — RAGAS scoring those answers: five metrics per question, each firing one or more LLM calls of its own, across every evaluation run.

I assumed judging was the bigger share. I *guessed* about two-thirds. Then — in the spirit of the whole project — I stopped guessing and pulled the real numbers from LangSmith, which tags every traced call by function. The measured split:

| | `gpt-4o-mini` calls | share |
|---|--:|--:|
| Answer generation | 315 | **6%** |
| RAGAS judging | 4,889 | **94%** |

Of the **5,204** `gpt-4o-mini` calls LangSmith traced across the build phase — **315 generation + 4,889 judging** — **94% were the evaluation harness judging answers**, only 6% the pipeline generating them. By tokens — and tokens are what you actually pay for — the split is less extreme but still decisive: **~82%** to judging, 18% to generation. (So the 94% is a count of *calls*; the share of the *bill* is nearer ~82%. Both say the same thing: measurement dwarfed serving.)

One of those numbers needed reconciling against itself, which is exactly the kind of thing this project exists to notice. Three sources that should agree on how many answers got generated don't quite: the eval harness's own on-disk counter logs **280**, LangSmith traced **315**, and the runs cover about **310** question-evaluations. None is wrong — they count slightly different populations. The on-disk counter only exists from the v4 pipeline onward, so it can't see the earlier runs' generations; LangSmith additionally captures a few read-only probe generations and rate-limited retries that never became a scored row. I didn't attribute the ~30-call gap call-for-call, and at this scale I didn't need to — but I'd rather show the seam than sand a clean number over three sources that disagree.

Why so lopsided? Because RAGAS fans out. Scoring one answer for **faithfulness** — whether every claim in the answer is actually supported by the retrieved text, i.e. whether the model made anything up — means extracting each claim and checking each one in a separate call. Scoring **context precision** — of the chunks retrieved, how many were actually relevant — at a retrieval depth of ten (*k=10*, the number of chunks each query pulls back) means judging all ten, one call apiece; it was the single most expensive metric in the whole project, precisely because k=10 means ten judgments where k=5 would mean five. My "two-thirds" guess assumed about five judge calls per question. The real fan-out is closer to **sixteen** — 4,889 judge calls across ~310 question-evaluations.

To make that concrete, here is one real question scored once. Take *"What is the RMP threshold quantity for anhydrous ammonia?"* — a single factual lookup, checked against the reference *"10,000 pounds."* Scoring that one answer across the five metrics fired **seventeen** `gpt-4o-mini` calls, a touch above the average:

| RAGAS metric | judge calls | what they do |
|---|--:|---|
| context precision | **10** | one verdict per retrieved chunk — at k=10, that's ten calls |
| answer correctness | 3 | break the answer *and* the reference into claims, then classify each true / false / missing |
| faithfulness | 2 | break the answer into claims, then check each against the retrieved text |
| answer relevancy | 1 | reverse-generate a question from the answer and compare it to the real one |
| context recall | 1 | one pass checking the reference answer against the retrieved context |
| **total** | **17** | one answer, one pass |

Ten of those seventeen were context precision alone — one judge call for every retrieved chunk — which is exactly why it was the most expensive metric in the project, and why retrieval depth *k* is a cost lever, not only a quality one. (Answer correctness also runs an embedding-similarity step; that costs a sliver of embeddings but no chat call.) Multiply seventeen-ish by 28 questions by fourteen runs, and 4,889 judge calls stops being a surprise.

Estimating the cost of measurement, I under-measured it — which is precisely the failure the project exists to catch.

There's a second layer, too. Even most of those 315 *generation* calls weren't serving real users — they were generating the answers the harness then turned around and judged. Almost nothing in this project's API usage was the product answering a question for a person. It was the system measuring itself.

Now the honest framing, because this is a *feature*. Those fourteen runs are what let me tell a real improvement from a number that merely wobbled, kill two planned fixes before building them, and catch the metrics that lied. All of that cost about **one dollar**. One dollar to *know*, with receipts, that the system works and where it doesn't. It is the best-spent money in the project, and it is a rounding error. If I had to put a price tag on "evaluation-first," that's it: at this scale, rigor is essentially free.

And here is that dollar as the number you'd actually reuse. The entire chat bill was essentially the evaluation — even most generation calls were feeding the judge — so at $1.25 across ~14 passes, one full 28-question, five-metric pass runs about **nine cents** ($1.25 ÷ 14 ≈ $0.09). That's the unit to steal: multiply nine cents by *your* eval-set size, not mine. The corpus embedding doesn't recur — you pay it once at ingestion — and none of it scales with user traffic, which is the whole point of the next section.

## What a senior engineer does when the eval bill starts to matter

At nineteen documents I never needed to optimize the measurement — I ran fourteen full passes precisely because I could. But the one cost lever I *did* pull is the one worth leading with, because it's the cheapest of all: **don't run the eval until a change has survived a read-only probe.** I walked into the fix phase with two planned improvements I was confident in — query decomposition for a cross-source lookup the pipeline kept missing, and table-aware extraction for a tabular one. The textbook move is to build both and re-run the evaluation to see if they helped. Instead I spent a few retrieval calls first: I hand-wrote the sub-queries and pulled the raw chunks straight from the index — no router, no generation, no judging, no LLM in the loop at all — just to check whether the answer chunk even moved into view. It didn't, and the probe falsified *both* fixes before I built either: the two failures turned out to be one defect — oversized, multi-record chunks that diluted each fact below retrieval's reach — and neither planned fix addressed it. Killing two builds for the cost of a handful of retrieval calls, before a single judge call was spent, is the highest-leverage cost move in the whole project. (That probe — and the way it nearly lied to me — is its own [story](falsification-and-diagnosis.md).)

The rest of the levers I'd reach for if this served real traffic and the eval bill actually stung — none of which I needed here, and I'll say so plainly:

- **A tiered eval set.** A small, fast smoke set on every change; the full 28-question set only for promotion decisions. Most changes don't need the whole thing.
- **A cheaper judge, validated.** Score with a smaller model — but only after checking it agrees with the expensive judge on a sample, or you've traded cost for a judge you can't trust.
- **Score only what changed.** If a change can only touch a handful of rows, judge those rows, not all 28.
- **Cache the judge.** Judge results keyed on (answer + context) are reusable across any run where neither changed.
- **Drop the metric that isn't arbitrating.** Context precision is the most expensive metric; if it isn't the one deciding a given promotion, don't pay to run it that pass.

"I didn't need these at nineteen documents" is a stronger, more honest position than pretending I'd solved a problem I never had. The point is knowing where the lever is before you need it.

## What changes at scale

None of this is the cost model of a real production system, and I won't pretend otherwise. If this served live traffic, the economics invert — here's the rough shape, clearly labeled as **extrapolation from the measured per-request numbers**, not something I ran:

- **Generation dominates.** Judging is *periodic* — you evaluate against a fixed question set when you change something, not on every request. In production the marginal cost of a request is one generation call. From the traces, an answer averages ~4,600 tokens (mostly the retrieved context), so at `gpt-4o-mini` prices that's on the order of **$0.75–1.00 per 1,000 questions**. *(Extrapolated.)*
- **Embeddings are a one-time cost.** You embed the corpus once at ingestion; after that it's only the tiny per-query embedding. For a stable 19-document corpus, a fixed cost you've already paid.
- **Retrieval stays cheap and fast.** The LLM is both the cost center and the bottleneck; vector search rounds to zero on both.

The rule underneath all three: **evaluation cost scales with the size of your eval set × metrics × fan-out, and is independent of traffic; generation cost scales with traffic.** That's why the two move in opposite directions as you grow, and why "rigor is essentially free" and "at scale the economics invert" are both true at once. You don't re-run the judge for every user, so the evaluation bill stays flat no matter how much traffic arrives; the serving bill is the one that climbs.

## The takeaway

Two things I didn't expect walking in, both now backed by numbers rather than vibes.

**Rigorous evaluation is astonishingly cheap.** The measurement that gave this project its entire backbone — fourteen runs, five metrics, 4,889 judge calls — cost about a dollar. If you've been skipping evaluation because it sounds expensive, it isn't. It's the price of a coffee, and it's the whole difference between shipping a system and shipping a vibe.

**The free tier is genuinely enough to ship something real.** A live service, full request tracing, four-decimal metrics — $1.53, three of five bills at zero. The barrier to building a RAG system you can actually *trust* was never the money. It's the discipline: write the eval first, change one variable at a time, and read the output when the metric and the truth disagree. That part's free too. It's just not easy.

## How I know these numbers

Every figure above is either measured or derived, and I try to keep the two labeled. The receipts:

- **The as-of date.** These are the numbers as of the build write-up (~July 13, 2026). The project kept running; by early August the cumulative OpenAI bill was ~$3.00, with essentially all the growth in chat/judging (more eval runs) and the embedding line flat (the corpus is embedded once). Same story, more of it — both columns are in the ledger below.
- **The split ($1.25 chat / $0.28 embeddings).** OpenAI's dashboard gave me the **$1.53 total** and the token buckets — not a per-line dollar split. The ~$1.25 and ~$0.28 are mine: computed from the token counts × OpenAI's published list prices, and they add back to $1.53 by construction. Read them as a reconciled derivation, not a figure OpenAI handed me.
- **The call counts.** Measured from LangSmith, which traces every completion call and tags it by function. One reconciliation for the careful reader: LangSmith traces 5,204 completion calls where OpenAI's dashboard bills ~3,766 requests — traced spans vs. billed units, the difference being retries and nested calls; the token totals agree to ~1%, and the generation-vs-judging split is computed entirely from the internally-consistent trace data.
- **The full ledger.** Every figure here — with a measured-vs-derived flag, the build-phase snapshot and the current cumulative side by side, and the call-count reconciliation — lives in [`eval/COST_LEDGER.md`](../eval/COST_LEDGER.md), alongside the evaluation passes recorded in [`eval/METRICS_HISTORY.md`](../eval/METRICS_HISTORY.md).
- **The code and the live service.** The pipeline, the eval harness, and the Dockerfile are in the [repository](https://github.com/FernandoSanabria/rag-pipeline); a deployed instance is [live](https://equip-docs-rag-api.onrender.com) (`POST /ask`, `GET /health`).
