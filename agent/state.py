"""AgentState — the LangGraph state object for the Phase 2 agentic pipeline.

This module defines STATE and REDUCERS only. No nodes, no graph. The state schema is the
single most important design decision in a LangGraph agent (later nodes see exactly the
channels defined here, merged exactly the way the reducers say), so it is committed and
reviewed on its own before any node is written.

Reducer decisions at a glance
------------------------------
A LangGraph channel with NO reducer is *last-write-wins* (overwrite). A channel annotated
`Annotated[T, add]` is *accumulate* (the reducer merges the prior value with each node's
return instead of replacing it). Two fields accumulate; the rest overwrite:

  field          reducer        why
  question       overwrite      set once at entry, read-only thereafter
  sub_questions  overwrite      produced whole by ONE node (decompose); no fan-in
  route          overwrite      routing decision; single writer (router node, 2B)
  retrieved      add (ACCUM)    parallel sub-question retrieval must CONCATENATE branches
  answer         overwrite      produced by ONE node (generate); no fan-in
  citations      overwrite      derived once from the deduped context; no fan-in
  trace_notes    add (ACCUM)    every node (incl. parallel branches) appends a breadcrumb

Why `retrieved` MUST accumulate (the #1 silent LangGraph bug)
-------------------------------------------------------------
In 2B the graph fans out: one `dense_search` per sub-question, running in parallel, each
returning its own chunk list into the SAME `retrieved` channel. With the default overwrite
reducer, concurrent writes to one channel are a hard error in LangGraph — or, worse on a
sequential path, silently last-write-wins: the synthesize/generate node then sees only the
FINAL sub-query's chunks and every earlier sub-query's evidence vanishes. That failure is
invisible (the graph runs, returns an answer, and the answer is just quietly under-grounded),
which is exactly why it is the classic LangGraph footgun. `Annotated[list[dict], add]` makes
the branches concatenate, so all sub-queries' chunks survive to synthesis.
Tradeoff of `add` here: it concatenates blindly, so N overlapping sub-queries × k produce
duplicates (facets of one question retrieve many of the same chunks). Accumulation is correct;
DEDUP is the 2B synthesize_node's job — see `chunk_content_key` below for the recorded basis.

Why `trace_notes` accumulates: the eval harness needs to assert on the PATH TAKEN (decomposed
vs direct), not just the final answer. Every node appends one breadcrumb; parallel branches
each append theirs; `add` preserves all of them. Overwrite would keep only the last node's note
and destroy the path record. Tradeoff: the list grows unbounded within a run — acceptable, a
single request is short-lived; it is never persisted across requests (see invariant (a)).

Why the other five overwrite: each is written by exactly ONE node on any given path, so there
is no fan-in to merge. Overwrite is the simplest correct choice; its only hazard is that two
*concurrent* branches writing the same channel would conflict — none of these five are ever
written from a parallel branch (only `retrieved` and `trace_notes` are), so overwrite is safe.
Using `add` on them instead would be actively wrong: re-running a node (e.g. a retry) would
append a second `answer`/`sub_questions`/`route` rather than replace it.

CAUTION — the `add` reducer turns a node-level RETRY into an APPEND, not a replace.
A retried `retrieve_node` under `add` would DOUBLE `retrieved` (and re-append its
`trace_notes`), doubling the context and breaking the v4 byte-repro gate. This is safe TODAY
only because no retry policy is configured and dedup does not run on the direct path — so any
future addition of node retries (LangGraph `RetryPolicy`) must account for it (idempotent
returns, or dedup that also runs on the direct path). Do not add retries blind.

--------------------------------------------------------------------------------
Two repo-grounded invariants encoded here
--------------------------------------------------------------------------------

(a) FRESH STATE PER INVOCATION — non-negotiable with an `add` reducer.
Because `retrieved` (and `trace_notes`) accumulate, a state object REUSED across calls keeps
growing: row 2 would be graded against row 1's chunks + its own, row 3 against rows 1+2+3, and
so on — silently poisoning every row after the first with foreign evidence. The eval harness
runs all 28 dataset rows in one process (eval/run_eval.py loops `ask(question)` per row), so
this is a live hazard, not a hypothetical. The contract: the entry adapter builds a BRAND-NEW
AgentState for every question. `fresh_state()` is the one blessed constructor — invoke the
graph with its output and nothing leaks between rows.

(b) CHUNK DEDUP BASIS — recorded now, applied in 2B.
Retrieved chunks are `{text, source_doc_id, page}` with NO stable id (verified in
src/retrieve.dense_search). So 2B's dedup cannot key on an id; it keys on a CONTENT HASH over
the triple (source_doc_id, page, text). Critically, `text` MUST be in the key: the semantic
namespace sub-splits a single page into multiple chunks, so two DISTINCT chunks routinely share
the same (source_doc_id, page) — deduping on (source_doc_id, page) alone would wrongly collapse
them and drop real evidence. This CHUNK dedup is deliberately FINER than api/citations.py, which
dedups CITATIONS by (document, page) on purpose (a citation is page-level; a context chunk is
not). Keep them as two separate functions: when 2B moves citation assembly into the graph, REUSE
api/citations.py's page-level (document, page) dedup for citations — do NOT merge it with
`chunk_content_key`. Two dedups, two granularities, by design.
`page`/`source_doc_id` may be None and `text` may be "" — all are stable, hashable values.
The basis is recorded here as `chunk_content_key`; the actual dedup lives in synthesize_node.
"""

from operator import add
from typing import Annotated, TypedDict


class AgentState(TypedDict):
    """State channels for the Phase 2 LangGraph pipeline. See module docstring for reducer rationale."""

    question: str                              # original user question — set at entry, read-only after
    sub_questions: list[str]                   # decomposition output; [] when not decomposed (v4 path)
    route: str                                 # "direct" | "decomposed" — routing decision (single writer: router, 2B)
    retrieved: Annotated[list[dict], add]      # ACCUMULATE across parallel sub-query retrievals
    answer: str                                # final grounded answer (single writer: generate)
    citations: list[dict]                      # stays [] in 2A — API layer owns citations (see below)
    trace_notes: Annotated[list[str], add]     # ACCUMULATE per-node breadcrumbs — path record for eval


# `citations` is intentionally NOT populated by any node in the 2A skeleton. Citation ownership
# stays in the API layer: src.pipeline.ask returns {answer, contexts, chunks} and
# api/citations.py derives citations from `chunks` at the endpoint. If a 2A node filled this
# channel, the skeleton would stop byte-reproducing v4. The channel exists (for when 2B moves
# assembly into the graph, reusing api/citations.py's (document, page) dedup) but stays empty on
# the direct path.

# `route` gives the 2B conditional edge a machine-readable decision to branch on. Routing on
# len(sub_questions) would conflate the decision with its byproduct and force the regression
# guard to parse trace_notes substrings; an explicit channel makes "simple rows took the direct
# path unchanged" a clean assertion. (Step-10 note, do not act now: verify whether LangGraph
# 1.x's Command(goto=…, update=…) lets the router fold decision+routing+state-update into one
# node return vs. a separate conditional-edge function — same doc-verification discipline as
# Send. Either way `route` stays in state for the eval assertion.)


def fresh_state(question: str) -> AgentState:
    """Build a brand-new AgentState for a single invocation (invariant (a)).

    Initializes ALL SEVEN channels explicitly, so no node ever reads an unset channel and
    KeyErrors. The accumulators (`retrieved`, `trace_notes`) start EMPTY so no evidence leaks
    in from a prior call, and `route` defaults to "direct" (the 2A / v4 path). The entry adapter
    that wraps the graph (to mirror src.pipeline.ask's per-question contract) MUST call this per
    question; never reuse a returned dict across dataset rows.
    """
    return AgentState(
        question=question,
        sub_questions=[],
        route="direct",
        retrieved=[],
        answer="",
        citations=[],
        trace_notes=[],
    )


def chunk_content_key(chunk: dict) -> tuple[str | None, int | None, str]:
    """Recorded dedup basis for retrieved chunks (invariant (b)) — used by 2B synthesize_node.

    Identity is the content triple (source_doc_id, page, text). `text` is included on purpose:
    (source_doc_id, page) is NOT unique because the semantic namespace sub-splits one page into
    multiple chunks. Returned as a hashable tuple so it can be a set/dict key directly; a hex
    digest is an equivalent representation if a compact key is preferred at the call site.
    """
    return (chunk.get("source_doc_id"), chunk.get("page"), chunk.get("text", ""))
