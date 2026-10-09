# G10b — Checkpointing and the approval gate: design (GATE 1)

Recorded 2026-10-09. This is the record of the read-only probes and the design, accepted at GATE 1 **before any gate
code**. The pre-registration is [`g10b_PREDICTION.md`](g10b_PREDICTION.md).

**Status:** see the Outcome section of [`g10b_PREDICTION.md`](g10b_PREDICTION.md).

**What G10b is.** `/ask/agent` can:
- pause before generation, on a pre-registered trigger;
- hand the retrieved evidence to a reviewer;
- resume on approve, reject or amend.

The pause survives a process restart on any host whose filesystem persists, and four paths are tested. `/ask` is
untouched. Every existing path stays byte-identical when the gate doesn't fire.

**Budget:** at most 6 build-days and $3.00.

**Frozen:**
- `src/**`, `eval/dataset.jsonl`, `run_eval.py`'s metrics, the namespace and k;
- `.github/workflows/ci.yml`;
- the G9 smoke set and snapshot.

**Rows are 1-based.**

**Starting point.** `main` was `b278f18` (G9 merged). Probe spend was $0: local result files, the installed packages,
and three public documentation pages.

**Decided earlier, not re-opened** ([`replay_safety_design.md`](replay_safety_design.md) §3 and §5):
- one thread per question, with a fresh `uuid4`;
- a 24 h TTL, after which the thread resolves REJECTED;
- Postgres, or fail closed. Silent loss is forbidden;
- the resume contract `{op, removals, additions}`;
- the `RemoveChunk(key)` and `Reset()` sentinels on `retrieved`'s reducer.

## GATE 1 decisions (accepted 2026-10-09)
1. **The trigger:** `exposure_limit_named ∨ scoped_fallback` (R1). The predicted fire set is frozen rows {9, 10, 11},
   with 0 smoke, 0 capability and 0 hard-negative fires.
   - **The trigger is a policy on the question's wording, not a measurement of answer quality.**
   - **Its two named misses:**
     - hard negative 25, "immediately dangerous to life or health": the spelled-out IDLH;
     - frozen row 21, "exposure ceiling".
2. **Durability: A,** `SqliteSaver`, failing closed (R2).
   - **P3b** runs after the merge, on the G5-closure pattern. Its registered prediction: one live pause, one Render
     redeploy, and the resume returns `expired_or_lost`.
3. **Amend.** The API exposes **removals only**. Additions and `Reset()` are implemented and tested at the reducer
   level but not exposed.
   - **The reason:** on an unauthenticated public API, additions would let anyone inject text attributed to a real
     manifest document, which then gets cited.
4. **The gate node is on G9 smoke row 24's path, as a no-op.** Accepted. The smoke runs with no checkpointer, and the
   smoke set, the snapshot and `scripts/smoke_eval.py` are not touched.
5. **Placement:** after the tool loop, immediately before `generate`.
   - **A deviation from the spec's "after retrieval":** the reviewer then sees exactly what `generate` receives, tool
     chunks included.
   - That is what P2's approve path requires: rows 9–11 often fire tools.

**Refinements accepted at GATE 1:**
- **A. The topology test.** `test_shipped_graph_topology_is_pre_g12` is **replaced**, not repurposed, by
  `test_shipped_graph_topology_is_expected`. The new test hardcodes the G10b node and edge sets and asserts the
  fan-out nodes are absent. The PR body names the replacement.
- **B. The 202 response.**
  - `expires_at` is an absolute UTC ISO-8601 time.
  - `reason` is the disjunct that fired, `"exposure_limit_named"` or `"scoped_fallback"`.
- **C. The output guard after approval.** P2's approve path runs the output guard after generation. If the guard
  withholds an approved answer, that is the correct composition: review is not a bypass. It is pre-registered as
  expected behaviour, counted, and **not** a P2 deviation.

## R1 — The trigger, from signals that exist after the retrieval stage
**Where the evidence came from.**
- **The frozen 28 on the agent path:** 5 observations per row. That is the G1 closure's CAP=0 and CAP=3 arms, plus
  G12's current arm (P5 for the 24 non-comparison rows, P1 for rows 9–11 and 21).
- **G9 smoke row 24:** 22 artifact observations.
- **The capability set** (3 rows) and **the 9 guardrail hard negatives** (`eval/guardrail_set.jsonl` rows 24–32): the
  question text only. No local agent traces exist for them, except the capability set's G6 P3b contexts.

**Each signal's fire set:**

| signal | frozen 28 | 8 smoke rows | capability (3) | hard negatives (9) | note |
|---|---|---|---|---|---|
| `retrieval_error` | 0 | 0 | 0 recorded | no runs | nothing to review: `generate` already short-circuits to an empty answer |
| zero chunks | 0 (every observation had at least 8 contexts) | 0 | 0 recorded | no runs | generation over empty context already gives a refusal |
| source-scoped **fallback** fired | 0 (rows 22–25 stayed `source_scoped` in 20/20) | 0 (row 24: 22/22 `source_scoped`, 8 pages) | not recorded | no runs | a real provenance hazard: the named document failed, and the answer came from the whole corpus |
| tool failure record | not computable | — | — | — | the result files persist no `trace_notes`; no `FAILED` or `CAP_REACHED` anywhere |
| fan-out branch failure | 0, inert | 0 | 0 | 0 | `FANOUT_ENABLED = False` |
| router `routing_reason` (`source_scoped`) | 22, 23, 24, 25 | **24** | — | — | fires on a smoke row |
| category: **named exposure limit** | **9, 10, 11** | 0 | 0 | 0 | deterministic on the question text |
| category add-on: `ceilings?` | 21 | **21** | 0 | 0 | row 21 runs `/ask` in the smoke, but it is a smoke row |
| category add-on: `lethal`, `LC50`, `LD50` | 0 | 0 | 0 | **row 24** | "What concentration of chlorine is lethal?" |
| category add-on: `immediately dangerous` | 0 | 0 | 0 | **row 25** | the spelled-out IDLH question |

The named-exposure-limit pattern, case-insensitive, is:

```text
\b(exposure limits?|IDLH|PELs?|RELs?|TLVs?|STELs?)\b
```

**The policy:** `TRIGGER = exposure_limit_named(question) ∨ scoped_fallback(route downgraded to direct)`.
- **Predicted fire set:** frozen **{9, 10, 11}**, 3/3 trials each; smoke **0/8**; capability **0/3**; hard negatives
  **0/9**.
- **It meets both requirements:** at least 2 frozen rows, and 0 smoke rows.
- **The fallback disjunct is predicted to fire 0 times live.** It is proven hermetically.

**What it is.** The trigger pauses questions that ask for a *named* occupational exposure limit, plus the one
structural provenance failure. It says nothing about whether the eventual answer would be right.

**Tensions, stated:**
- **It is keyed on wording, not meaning.** It fires on "IDLH" (row 9) but not on hard negative 25, which spells IDLH
  out. It also misses row 21's "exposure ceiling". Widening it to catch either breaks one of the two zero-fire
  requirements.
- **The 0/8 smoke requirement is stricter than G9 needs.** Only row 24 traverses `/ask/agent` in the smoke.
- **On the live service,** every `/ask/agent` question naming an exposure limit returns 202 until someone reviews it.
  `/ask` is unchanged.

**Excluded, and why:**
- **`retrieval_error` and zero chunks:** there is nothing to review.
- **Tool failure:** its fire set can't be predicted from local files.
- **Router reason:** it fires on smoke row 24.

## R2 — Durability (decided: A)
**Facts checked on 2026-10-09.**
- **Render free web services** lose their filesystem, "local SQLite databases" included, on every redeploy, restart
  or spin-down. They spin down after 15 minutes without inbound traffic and cannot attach a disk
  (https://render.com/docs/free).
- The repo's `keepalive.yml` pings the service about every 10 minutes.
- The Dockerfile runs a single uvicorn worker.

| | **A. `SqliteSaver` on Render free (chosen)** | **B. `PostgresSaver` on Neon free** |
|---|---|---|
| **Provider** | a local file (`REVIEW_DB_PATH`) | Neon free (https://neon.com/faqs/free-plan-limits-and-quotas): 1 GB per project, 100 CU-hours a month, compute suspends after 5 min idle, no expiry. Not Render's free Postgres, which expires 30 days after creation |
| **New secret** | none | `DATABASE_URL` |
| **New dependency pins** | `langgraph-checkpoint-sqlite==3.0.3`, which requires `langgraph-checkpoint>=3` (ours is 3.0.1) | `langgraph-checkpoint-postgres==3.0.5`, plus psycopg and psycopg-pool |
| **What "the pause survives a process restart" means** | **true** on any host whose filesystem persists, proven hermetically and locally live. **False on Render free:** the resume fails closed with `expired_or_lost` | true everywhere, including across a Render redeploy |
| **New failure modes** | a pause is lost on every deploy to `main` and on any platform restart. Never silently: the resume refuses | every `/ask/agent` request writes one checkpoint over the network. A database outage would fail all `/ask/agent` requests, not only gated ones |

The 3.1.x lines of both savers require `langgraph-checkpoint>=4.1`, so the 3.0.x lines are the ones compatible with
`langgraph==1.0.1`.

**Why A.**
- §3 allows accepting the loss as long as it fails closed.
- A adds no secret, no provider, and no network write on every request.
- Its loss conditions are known and are tested live (P3b).
- B is a later configuration change: the factory returns a `PostgresSaver` when `DATABASE_URL` is set, plus the pins.

## R3 — The reducer change (exactly §5)
**Where `update_state` meets the reducer.** It routes through the channel's reducer in the installed langgraph 1.0.1:
- `update_state` (`pregel/main.py:2359`) calls `bulk_update_state`;
- the node writers run on the values (`pregel/main.py:1816`);
- `apply_writes` follows (`pregel/main.py:1852`);
- then `channels[chan].update(vals)` (`pregel/_algo.py:293`);
- finally `BinaryOperatorAggregate.update`, which applies `self.operator(self.value, value)`
  (`channels/binop.py:92-93`).

That agrees with EXP3 in [`replay_safety_design.md`](replay_safety_design.md).

**The reducer.** In `agent/state.py`, `retrieved: Annotated[list[dict], retrieved_reducer]`. Items are processed in
order:
- a plain chunk dict **appends** (identical to `operator.add`);
- `RemoveChunk(key)` **drops** the chunk whose `chunk_key` matches;
- `Reset()` **clears** the list.

**Details:**
- **The key.** `chunk_key(c)` is the sha256 hex of `chunk_content_key(c)`, the "equivalent representation" its
  docstring names. That is the form the API exposes. The tuple form stays for G12's join.
- **The sentinels are marked dicts**, `{"__review_op__": "remove", "key": …}` and `{"__review_op__": "reset"}`, built
  by `RemoveChunk()` and `Reset()` helpers.
  - Pending writes are serialized by the checkpointer, and plain dicts avoid custom-class deserialization.
  - The marker key never occurs in a chunk.
- **Byte-identity, tested hermetically:**
  1. on every plain-list sequence the existing writers produce, the reducer equals `operator.add`;
  2. a recording fake for `generate` sees exactly the same calls on every existing path, under the new reducer and
     under `operator.add`.

## R4 — The API shape (additive only)
**`POST /ask/agent`:**
- **200** `AgentAskResponse`, unchanged, when the gate doesn't fire.
- **202** when it fires:

  ```text
  {status: "pending_review", thread_id, reason, route, routing_reason,
   evidence: [{key, source_doc_id, title, page, text}], expires_at}
  ```

  - `reason` is the disjunct that fired; `expires_at` is absolute UTC ISO-8601.
  - Evidence text is included, as the G5 MCP tools already serve chunk text. Tool chunks carry `page: null`.

**`POST /ask/agent/resume`** with `{thread_id, op, removals?}`:

| case | what happens | response |
|---|---|---|
| `approve` | resume, then `generate`, then the shared assembly (**the output guard still applies**) | 200 `AgentAskResponse` |
| `reject` | resolved with no `generate` call | 200 refusal, `guard: {stage: "review", reason: "rejected"}` |
| `amend` | `update_state` with `RemoveChunk` sentinels, then resume | 200 `AgentAskResponse` |
| unknown or expired thread | fail closed | 200 refusal, `guard: {stage: "review", reason: "expired_or_lost"}` |
| malformed request (a non-UUID `thread_id`, or removals with approve or reject) | rejected | 422, as for any malformed request today |

**Amend's fallback:** if `update_state` as the interrupted node misbehaves in 1.0.1 (a hermetic test decides), the
amendment rides in the resume value and the review node returns the sentinels. That is still reducer-routed.

**`GET /ask/agent/review/{thread_id}`** returns the status only: `{thread_id, status, reason, expires_at,
resolved_at}`.

**Schema changes, all additive:**
- `GuardInfo.stage` gains `"review"`;
- `reason` gains `"rejected"`, `"expired_or_lost"` and `"review_unavailable"`;
- new `PendingReviewResponse`, `ReviewEvidence`, `ResumeRequest` and `ReviewStatusResponse`.

**No authentication: the trade-off, named.** Anyone who can reach the service can approve a paused safety answer, or
remove its evidence. The follow-on is a reviewer token, which is not built here.

## R5 — TTL and the state machine
**The TTL** is 24 h (`REVIEW_TTL_S`, default 86400). `expires_at` is stored in a new `review` channel (overwrite),
which makes 14 channels.

**Expiry is lazy:**
- checked on every resume and every status call;
- plus a sweep at startup, in the FastAPI `lifespan`, which deletes expired pending threads.

There's no scheduler on Render free, and none is needed.

```text
PENDING --approve--> APPROVED            (one generate; the resolution is recorded)
PENDING --reject---> REJECTED            (no generate)
PENDING --amend----> AMENDED --resume--> APPROVED (amended)   (one generate on the amended set)
PENDING --TTL------> EXPIRED (= REJECTED; the checkpoint is deleted; "review: expired")
APPROVED | REJECTED | AMENDED --any further resume--> the recorded resolution (never a second generate)
unknown thread, or after the sweep --> expired_or_lost
```

**Records and locking:**
- **The resolution record** is the thread's final checkpoint (the `review` channel plus the answer). It's kept until
  the TTL, then swept.
- **Non-firing threads** are deleted as soon as they finish.
- **Concurrent resumes** of one thread are serialized by a per-thread lock with a status check-and-set. That holds for
  a single uvicorn worker; several workers would need a database-level guard, which is named as a limitation.

**Fail closed:**
- if no checkpointer is configured and the trigger fires, the request is refused (`review_unavailable`) with no
  generation;
- if the checkpointer errors at the pause, the request is refused the same way.

Never an unreviewed answer.

## R6 — Observability
**New `trace_notes`:**
- `review: paused (<reason>) thread=<id>`;
- `review: resumed op=<op> removals=<n> additions=<n>`;
- `review: expired`.

**Logging:** the `thread_id` and the op are logged at INFO. **The question text is not**, which treats it as personal
information, as in G6.

## Graph design
```text
… → tool_decide ⇄ tool_exec → review_gate → (fired) review_wait → generate | END (rejected)
                                          → (not fired) generate
```

- **`review_gate`** decides. It writes the `review` channel and the paused note, or returns `{}`.
- **`review_wait`** calls LangGraph's dynamic `interrupt()` (`types.py:396`). The graph is compiled once, and the node
  decides.
- **The checkpointer** comes from a factory: SQLite when `REVIEW_DB_PATH` is set, otherwise none.
- **`durability="exit"`** persists only at exit or interrupt (`pregel/_loop.py:791-798`). A non-firing request
  therefore makes one write and one `delete_thread`.
- **Each `ask()`** mints `thread_id = uuid4()`, never a default (invariant (a)).

## R7 — Hermetic test plan
The checkpointer is `SqliteSaver` on a tmp file, or `InMemorySaver` where a restart isn't the point.
- **The gate:** it fires on a trigger row and not on a non-trigger row (trace asserted). The fallback disjunct fires on
  a forced 0-chunk filtered query.
- **The 202 shape:** the evidence keys match `chunk_key`.
- **Approve:** `generate` receives exactly the paused contexts.
- **Reject:** a refusal, and no `generate` call.
- **Amend with one `RemoveChunk`:** `generate` receives the set minus that chunk, nothing else changed.
- **`Reset` plus additions, at the reducer and graph level:** `generate` receives exactly the additions.
- **Expired:** a refusal, no `generate`, the checkpoint deleted, `review: expired` noted.
- **A second resume:** returns the recorded resolution; `generate` is called once in total.
- **Restart survival:** graph A interrupts on a tmp SQLite file; graph B, freshly compiled in a fresh process (a
  subprocess), resumes on the same file and answers.
- **No checkpointer configured:** the trigger path refuses instead of answering unreviewed.
- **Reducer byte-identity** for every existing path.
- **Invariant (a):** two `ask()` calls get distinct `uuid4` thread ids, and there is no default `thread_id`.
- **The startup sweep** deletes expired pending threads only.
- **API:** 422s on malformed resumes; the status endpoint's transitions; the existing `/ask/agent` shapes unchanged.
- **The topology:**
  - `test_shipped_graph_topology_is_pre_g12` is **replaced** by `test_shipped_graph_topology_is_expected`, which
    hardcodes the G10b node and edge sets and asserts the fan-out nodes are absent;
  - the channel-count test goes to 14.
