# G8 — Cost control, measured: design

Recorded 2026-10-12. The pre-registration is [`g8_PREDICTION.md`](g8_PREDICTION.md). Rows are 1-based. Every GATE 1
probe was read-only and cost $0.

## Scope
**The goal.**
- Every request's cost is accounted for and observable.
- A deterministic policy routes eligible questions to a cheaper generator through the G11 seam
  ([`agent/generation.py`](../agent/generation.py)).
- Both the cost delta and the quality delta are measured on the frozen 28 under pre-registration.
- Caching is measured where it already exists, and **not built** where there's no traffic to measure it on.

**Budget:** at most 3 build-days and $3.00. **HARD STOP: 2026-10-17,** inside G11's box. GATE 2 is posted by then, and
anything unmeasured is NOT TESTED.

**Base:** the branch is cut from `main` at `be8aaa2`, the G11 merge.

**Frozen:**
- `src/**`;
- the dataset, `run_eval.py`'s metrics, the namespace and k;
- `ci.yml`, and the G9 smoke set and snapshot.
- The judge stays `gpt-4o-mini`, and `/ask` stays exactly as it is.

**Out of scope** (stop and ask):
- any change under `src/`;
- building an answer or semantic cache;
- changing the judge;
- routing ON by default;
- more than one cheap arm.

**The design constraint** is the roadmap's own caveat for G8 (`GAP_EXECUTION_ORDER.md`): *"caching needs traffic,
routing needs query diversity. Neither exists at 19 docs."* There is no production traffic, so any hit rate measured on
reruns of the frozen 28 is a property of the eval, not of users. Every cache number below says so.

## GATE 1 decisions (ruled 2026-10-12)
1. **No response field.** Cost is observable through an INFO log line and a trace note only.
2. **Policy A:** `route == source_scoped ∧ no tool result ∧ ¬comparison ∧ ¬exposure-limit`, eligible on
   **{22, 23, 24, 25}** (R3).
3. **The cheap arm is Nova Micro only** (`us.amazon.nova-micro-v1:0`), with the G11 adapter's Converse call unchanged.
4. **P8's "cost delta ≥ 30%"** is the delta **across the 28 under the policy**.
   - Under A it is about 3.7%, so routing stays OFF on cost regardless of quality.
   - The run is a pure measurement of P4 and P6 for the cheap arm.
5. **An addition, R7:** the tool-decision pre-filter is probed read-only and recorded as the named follow-on. It is not
   built.

## R1 — The bill per request, before G8
**Every billable call on the agent path, in order.**
- **Token counts** are medians on G11's profile.
- **The router and tool-decision figures are estimates:** tiktoken counts of the prompt text, calibrated to G11's
  measured aggregate (59 decision calls, 268,430 input tokens), which implies about 150–400 tokens per call of
  schema overhead. G8's accounting pass replaces them with measured values.

| # | call | model | per request | tokens | $ at list | usage captured before G8 |
|---|---|---|---|---|---|---|
| 1 | query embedding (`dense_search`) | `text-embedding-3-small` | 1 (2 on a scoped fallback) | 17 | under $0.000001 | nowhere: `embed_query` returns a vector only |
| 2 | router | `gpt-4o-mini`, `json_schema` | 1 | ≈ 940 in / 20 out | $0.00015 | nowhere: structured output returns the parsed object |
| 3 | tool decision | `gpt-4o-mini` + 3 bound tools | 1, plus 1 per tool round (CAP 3) | ≈ 7,700 in / 60 out | $0.0012 | nowhere: the response's `usage_metadata` is discarded |
| 4 | tool execution | local | per tool call | — | $0 | — |
| 5 | review gate | local + SQLite | rows naming an exposure limit | — | $0 | — |
| 6 | generation | `gpt-4o-mini`, seed 42 (or `bedrock`) | 1 (0 on a retrieval error; on resume when paused) | 7,505 in / 90 out | $0.00116 | model and fingerprint only; Bedrock usage in `generation.calls()` |
| 7 | output guard | deterministic | 1 | — | $0 | — |
| — | input-guard classifier | `gpt-4o-mini` | 0 (off) | — | $0 | — |
| — | decomposer | `gpt-4o-mini` | 0 (off) | — | $0 | — |

**Per pass of the 28, at list price:**

| line item | cost | share |
|---|--:|--:|
| tool decision | $0.0357 | 49% |
| generation | $0.0333 | 45% |
| router | $0.0043 | 6% |
| embeddings | $0.00001 | under 0.1% |
| **total** | **$0.0734** | |

The median request costs $0.00267. `/ask` makes an embedding and a generation, about $0.0012.

**Before G8, nothing was accounted per request in production.**
- Render has no LangSmith environment.
- The app logged no usage.
- [`COST_LEDGER.md`](COST_LEDGER.md) holds eval-run aggregates only.
- A request paused by the review gate bills the router, the tool decision and the embedding on `/ask/agent`. Its
  generation is billed on `/ask/agent/resume`, which does not pass through `_answer`.

**Finding: the largest line item is the tool decision, not generation.**
- It sends the whole retrieved context to decide whether a tool is needed, and 26 of the 28 rows need none.
- So a cheaper generator can save at most about 45% of a request. R7 probes the pre-filter.

**The per-request record** (`agent/cost.py`, with nothing under `src/`):
- **`PRICES`:** a data table of model → input, cached-input and output per 1M tokens, with source and date. It covers
  `gpt-4o-mini`, `text-embedding-3-small`, Nova 2 Lite and Nova Micro, and a test parses it against COST_LEDGER's
  table.
- **The record:** `{embedding, router, tool_decide, generate, total}`. Each entry holds calls, input, cached and
  output tokens, plus `usd_list` and `usd_billed`, with cached-input tokens kept separate.
- **OpenAI calls:** a LangChain callback handler registered once through `register_configure_hook`, the mechanism
  `get_openai_callback` uses. It reads `usage_metadata`, including `input_token_details.cache_read`, from every chat
  call made inside the request.
  - **Attribution** comes from a `phase` ContextVar that `router_node`, `tool_decide_node` and `generate_node` set
    around their own call.
  - **`/ask`'s single chat call** is attributed to generation.
- **Bedrock:** `generate_with` adds its Converse usage to the active record. That is one accounting line; the Converse
  request is unchanged.
- **Embeddings:** each `dense_search` call site adds a tiktoken count of the query (`cl100k_base`), labelled derived.
- **Isolation:** the record is context-local, so concurrent requests don't mix.
- **Where it goes:**
  - one INFO log line, `cost {json}`, from `_answer` and from the resume endpoint, with no question or answer text. App
    INFO lines reach Render's log; G10b's startup-sweep line was observed there;
  - on the agent path, a `trace_notes` entry from `generate_node`, the last billable call.

## R2 — Caching: measured, not built
**OpenAI's automatic prompt cache, with zero code,** on G11's 84 `gpt-4o-mini` generation calls (agent path; run
`20261012T133104Z`):

| trial | cached input / input | calls with any cached tokens | per-call median |
|---|--:|--:|--:|
| 1 | 13,824 / 210,487 = **6.6%** | 2/28 (rows 9 and 21, warmed by the discarded plumbing check) | 0% |
| 2 | 201,344 / 210,487 = **95.7%** | 27/28 | 98.7% |
| 3 | 208,000 / 210,487 = **98.8%** | 28/28 | 98.7% |

- **The rest of that run:** the memoized decisions were 14.4% cached, and the judge 44.9%.
- **What these measure:** reruns of the same prompt a few minutes apart, not traffic.
- **The router is expected near 0%:** its prompt, about 940 tokens, sits under OpenAI's documented 1,024-token minimum
  for caching.

**An answer cache and a semantic cache were decided against by this probe, and are not built.** This is a
probe-decided non-build, like BM25 fusion.
- **The hit rate says nothing here.** On the frozen 28, an answer cache's hit rate is 100% from the second trial, by
  construction.
- **The roadmap's caveat** applies verbatim: *"caching needs traffic, routing needs query diversity. Neither exists at
  19 docs."*
- **Real traffic would measure it.** The first 24 hours of it would, and the per-request cost log built here is what
  would show it.
- **A semantic cache carries a safety risk:** it would serve one question's safety answer to a different question
  judged similar.

## R3 — The routing policy: signals known before generation
**Each signal's fire set on the 28.**
- **Sources:** route and tool come from G11's memoized run. Comparison and exposure-limit are deterministic regexes:
  `graph.COMPARISON_GATE` and `review.EXPOSURE_LIMIT_PATTERN`.
- **Agreement with earlier gates:** the scoped rows match the four scoped rows of every earlier gate. Tool firing
  matches the G1 closure's stochastic pair (its 0-based rows 8 and 20 are 1-based 9 and 21).

| signal | fires on |
|---|---|
| `route == source_scoped` | 22, 23, 24, 25 |
| a tool fired (stochastic, per G1) | 9, 21 |
| comparison gate | 9, 10, 11, 21 |
| exposure-limit pattern (G10b) | 9, 10, 11 |
| contexts > k | 9 (16 contexts), 21 (12): the same as "a tool fired" |
| question length | 54–138 characters, with no threshold separating fact rows; **not used** |

**Policy A (ruled):** `route == source_scoped ∧ no tool result ∧ ¬comparison ∧ ¬exposure-limit`.
- **Eligible on the 28:** exactly {22, 23, 24, 25}, the single-document lookups the router already scopes.
- **Rows 1–8** are single-fact questions too, but the router sends them direct. The broader policy B (the same
  conjunction without the route condition) would be eligible on 24 rows.
- **A scoped fallback** downgrades the route, so it is never eligible.

**The smoke effect.**
- Smoke rows 1, 4, 15, 20, 21, 25 and 26 run on `/ask`, which routing never touches. That follows from the endpoint;
  it is not an exclusion.
- **Row 24 runs on `/ask/agent` and is eligible.** With routing ON, its generation would move to Nova Micro. Its
  retrieved pages (the T1 tier) are unchanged by construction, but its answer, and so its T2 and T3 scores, would change.
- Routing ships OFF, so CI's snapshot is untouched. Row 24's routed effect is measured in the eval.

**Routing is a policy, not a quality signal.** Its eligible set on the frozen 28 is a property of the set, not of real
queries: the second half of the roadmap's caveat.

## R4 — The cheap arm
**Nova Micro (`us.amazon.nova-micro-v1:0`).**
- **Prices,** from the AWS Price List API (`AmazonBedrock`, us-east-1, publication 2026-10-10T18:22:42Z, the same one
  G11 used; usage types `USE1-NovaMicro-*`):

  | tokens | price per 1M |
  |---|--:|
  | input | **$0.035** |
  | cache read | $0.00875 |
  | output | **$0.14** |

- **Cost per answer:** about $0.00028 at G11's token profile (7,453 in, 133 out), 4.1× below `gpt-4o-mini`'s $0.00116.
  On rows 22–25 it is about $0.00020.
- **Latency:** an expected p50 of about 0.8 s, against Nova 2 Lite's measured 1.39 s. Recorded, with no falsifier.
- **Quota:** 200 RPM / 8M TPM on this account. The eval keeps G11's limit of 15 RPM or less.
- **The adapter:** G11's Converse call is unchanged, through `generate_with(…, model_id=…)`.

**The OpenAI candidate, not in scope: `gpt-5.4-nano`,** the newest nano-class id the project key lists (2026-03-17).
`gpt-4.1-nano` and `gpt-5-nano` are also listed.
- **Why not:** GPT-5-family models are reasoning models whose sampling parameters differ from the frozen generator's
  (temperature 0, seed 42), which would add a second variable. Its price was not verified.
- **The trade-off:** an OpenAI arm would need no AWS keys in production. A Nova Micro route can only run where AWS
  credentials exist. G11 keeps them local, and Render has none.

## R5 — What a routed request must still satisfy
- **The exact refusal sentence.** `is_refusal` is whole-answer equality. G11's P6 showed that Nova 2 Lite hedges (0/3),
  and [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) records that a hedged answer is served at high confidence. P6
  reuses row 25 under `/ask` contexts for Nova Micro.
- **The output guard.** `_assemble` is unchanged; routing changes only the generator.
- **Structured citations.** They derive from chunk metadata, so they are unchanged by construction.
- **The review gate.** An eligible row never triggers it. The policy excludes the exposure-limit pattern, and a scoped
  fallback makes a row ineligible.

## R6 — The hermetic test plan
1. **The record sums.** The total equals the sum of its parts. On canned usage (G11's recording-transport pattern, with
   `cached_tokens` set), USD equals tokens × `PRICES`, and `PRICES` equals COST_LEDGER's table, which the test parses.
2. **Cached tokens are separated,** and `usd_billed` uses the cached rate.
3. **Policy A fires exactly on {22–25}** over the 28 questions, given the recorded route and tool signals. The
   generate node's trace note is asserted.
4. **A routed answer still goes through `_assemble`.** An untraceable number is withheld by the output guard. A stubbed
   exact refusal passes `is_refusal`, and a stubbed hedge does not.
5. **With routing OFF, the default,** request bodies are byte-identical on all 28 across the frozen generator, the seam
   and `generate_node`.
6. **No question text** appears in the INFO cost line.
7. **A record per request:** the resume endpoint and a paused request each get their own record, and concurrent
   requests don't mix.

## R7 — The tool-decision pre-filter (recorded, not built)
**The predicate,** on the question text, would gate the tool-decision LLM call:
- `graph.COMPARISON_GATE`, or
- `review.EXPOSURE_LIMIT_PATTERN`, or
- a unit-conversion pattern: `ppm`, `mg/m3`, `mg/m³`, `mg/L`, `convert…`, `conversion`, or `express … in`.

| set | fires on | what it means |
|---|---|---|
| frozen 28 | **9, 10, 11, 21** (comparison or exposure) | includes 9 and 21, the rows where tools fired; it would skip the other 24 |
| capability set (3) | **3/3** (unit pattern) | every row built to exercise the tools is kept |
| guardrail hard negatives (`guardrail_set.jsonl` rows 24–32) | **0/9** | all nine are single lookups; none asks for a conversion or a comparison |
| smoke (8) | row 21 only | row 21 runs on `/ask`, which has no tool decision |

- **Row 24,** the only `/ask/agent` smoke row, would skip the tool decision. G11's decision on it was "no tool", so
  generation's input is unchanged and one LLM call is dropped.
- **The estimated saving, per pass at list price:** the skipped rows' tool decisions cost $0.0292 of $0.0734, or
  **39.7%**.
- **Rows 10 and 11 would still call the decision,** although G11's returned "no tool": the predicate is conservative
  there.
- **The named follow-on.** A pre-filter on `tool_decide` is the largest controllable line item. Its fire set on the
  frozen 28 is {9, 10, 11, 21} (capability 3/3, hard negatives 0/9), and it would save about 40% of a pass at list price.
  It is not built in G8: it is a second mechanism, it touches the G1 path, and it needs its own pre-registration,
  including a check that no tool-needing question is skipped.
