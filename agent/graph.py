"""LangGraph agent graph — router + retrieval strategies + a bounded tool-calling loop (G1) + a parallel fan-out
for comparison questions (G12).

    START -> router -> (direct) retrieve | (source_scoped) source_scoped_retrieve
                     | (comparison-worded) decompose -> Send x N -> branch_retrieve -> join   [or -> retrieve]
          -> tool_decide <-> tool_exec (bounded loop) -> review_gate -> [review_wait] -> generate_node -> END

G10b adds the approval gate immediately before generation (eval/g10b_design.md). `review_gate` applies the trigger
POLICY (agent/review.py: a question naming an exposure limit, or a source-scoped fallback) — a policy on the question's
wording, not a measurement of answer quality. When it does not fire, it returns nothing and `generate` receives exactly
what it received before G10b. When it fires, `review_wait` pauses the graph with LangGraph's dynamic `interrupt()`;
the thread's checkpoint (a SqliteSaver, one fresh uuid4 thread per ask()) holds the paused evidence until a reviewer
approves, rejects or amends it through /ask/agent/resume, or the TTL resolves it as rejected. With no checkpointer
configured, a firing question is refused, never answered unreviewed.

G12 is BUILT, MEASURED and SWITCHED OFF (FANOUT_ENABLED = False): the shipped graph is the pre-G12 one, and the bracketed
fan-out path above exists only in the enabled build (eval/g12_PREDICTION.md, Outcome). It is a DISPATCH-AND-AGGREGATE
node, not a hierarchy of agents: a deterministic wording gate sends comparison-worded
direct questions to one decomposer call; 2-3 sub-questions are dispatched concurrently with LangGraph's `Send`, each
branch runs ONE retrieval (source-scoped when the sub-question names a document), and `join_node` rank-interleaves
the surviving branches, dedups by `chunk_content_key`, and writes `retrieved` once. Anything the gate or decomposer
declines takes today's single-query path unchanged. Design and degradation contract: eval/g12_design.md.

G1 adds the tool loop between retrieval and generation. `generate_node` and `src.generate.generate` are
UNTOUCHED (the v4 byte-repro path). Because generate grounds ONLY on `retrieved`, a tool output reaches the
answer solely by `tool_exec` appending a synthetic labeled chunk (source_doc_id="tool:<name>", page=None) to
`retrieved` — which also keeps the answer traceable to a tool output. A no-tool question passes tool_decide
straight to generate with `retrieved` unchanged, so the direct path still byte-reproduces v4.

The 2A skeleton this grew from: prove the
LangGraph plumbing + tracing reproduce v4 BEFORE adding any intelligence. `agent/` is the
ORCHESTRATION layer — every node wraps an existing `src/` capability and reimplements nothing:

  retrieve_node -> src.retrieve.dense_search   (depth from settings, NOT hardcoded)
  generate_node -> src.retrieve.format_contexts + src.generate.generate

Byte-repro contract (why this reproduces v4 exactly):
  * retrieve depth is read from `get_settings().retrieval_k` — same source pipeline.ask reads,
    so RETRIEVAL_K / RETRIEVAL_NAMESPACE A/B overrides still work and the direct path matches v4.
    Hardcoding 10 would match v4's *number* today but silently break the override and diverge
    from pipeline.ask.
  * `contexts` is `format_contexts(retrieved)` — the SAME pure function generate_node feeds the
    model AND the entry adapter returns, so RAGAS grades byte-identical text (identical to
    pipeline.ask, which computes it once).
  * the entry `ask()` returns the frozen {answer, contexts, chunks} shape run_eval.py reads.

Per-node fail-safe, mirroring pipeline.ask EXACTLY (so a node exception can't crash the harness
and desync the RAGAS denominator):
  * retrieval exception -> retrieve_node sets retrieval_error=True, retrieved=[]; generate_node
    SHORT-CIRCUITS to answer="" WITHOUT calling generate (no LLM cost). ask() then returns
    answer="", contexts=[], chunks=[] — all three fields identical to pipeline.ask, which also
    skips generation on a retrieval throw (the except fires before generate() runs).
  * generate exception/empty -> answer="" with contexts/chunks populated — identical to
    pipeline.ask (retrieval already ran, only generation failed).
A *legitimate* empty retrieval (no matches, no exception) is NOT a failure: retrieval_error stays
False and generate runs over empty context (→ refusal), exactly as pipeline.ask does. The only
intended difference from pipeline.ask anywhere is the extra `trace_notes` breadcrumb (including a
"generate[skipped]" note on the short-circuit, so the path record never goes silent). The v4 eval
never hits an exception branch, so byte-repro holds on the normal path regardless.

Fresh state per call (invariant (a) in agent/state.py): `ask()` invokes the compiled graph with
`fresh_state(question)`, so the `add`-reducer channels never leak evidence across dataset rows.
"""

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from functools import lru_cache
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt
from langsmith import traceable
from pydantic import BaseModel, Field

from agent import review
from agent.state import AgentState, RemoveChunk, chunk_content_key, chunk_key, fresh_state
from agent.tools import TOOL_SCHEMAS, ToolError, run_tool
from src.config import get_settings
from src.generate import generate
from src.retrieve import dense_search, format_contexts

logger = logging.getLogger(__name__)

# ---- 2C source-scoped router --------------------------------------------------------------------
_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "manifest.json"


@lru_cache(maxsize=1)
def _docs():
    return json.loads(_MANIFEST_PATH.read_text())["docs"]


@lru_cache(maxsize=1)
def _known_doc_ids():
    return {d["doc_id"] for d in _docs()}


@lru_cache(maxsize=1)
def _doc_catalog():
    return "\n".join(f"  {d['doc_id']}: {d['title']}" for d in _docs())


@lru_cache(maxsize=1)
def _doc_titles():
    """doc_id -> human title (manifest). Used to render the endpoint's routing_reason; the
    id->title lookup the router prompt (_doc_catalog) and validation (_known_doc_ids) don't expose."""
    return {d["doc_id"]: d["title"] for d in _docs()}


class RouteDecision(BaseModel):
    """Router output — the single-document source-scoping decision (2C)."""

    source_scoped: bool = Field(description=(
        "true ONLY if the question is answerable FROM one SINGLE named document; "
        "false for general questions AND for any multi-source comparison"))
    source_doc_id: str | None = Field(default=None, description=(
        "the source_doc_id of that one document (exactly one of the known ids), else null"))


@lru_cache(maxsize=1)
def _router_llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model="gpt-4o-mini", temperature=0).with_structured_output(RouteDecision, method="json_schema")


def _router_prompt(question: str) -> str:
    return (
        "You route questions in an industrial-safety-document RAG system. Decide whether the "
        "question EXPLICITLY attributes its answer to ONE specific named source document.\n"
        "Rules:\n"
        "- source_scoped = true ONLY when the question says to answer FROM one specific named "
        "document — typically a vendor SDS / manual / datasheet, e.g. 'per the Sigma-Aldrich SDS', "
        "'per the Nutrien SDS', 'in the Fisher 657 manual'. Return that document's source_doc_id.\n"
        "- source_scoped = FALSE for: (i) general questions; (ii) any COMPARISON across two or more "
        "sources (e.g. 'how does the NIOSH IDLH compare to the EPA endpoint'); and (iii) questions "
        "that merely reference a REGULATION or STANDARD by name as the governing rule (e.g. 'under "
        "the PSM standard', 'under the lockout/tagout standard') — those are general questions, NOT "
        "single-document attributions. When unsure, choose false (the direct path is safe).\n"
        "- source_doc_id must be EXACTLY one of the known ids below, or null.\n"
        "Examples: 'flash point of acetone per the Sigma-Aldrich SDS' -> true, "
        "sds-sigma-aldrich-acetone. 'What triggers Management of Change under the PSM standard?' -> "
        "false. 'How does the NIOSH IDLH compare to the EPA endpoint?' -> false.\n\n"
        f"Known documents (source_doc_id: title):\n{_doc_catalog()}\n\nQuestion: {question}"
    )


def router_node(state: AgentState) -> dict:
    """Classify the question: source-scope a SINGLE-document question, else route direct. Comparisons
    (incl. the shipped IDLH row) stay DIRECT so a single-doc filter can't halve the answer."""
    question = state["question"]
    try:
        d = _router_llm().invoke(_router_prompt(question))
        if d.source_scoped and d.source_doc_id in _known_doc_ids():
            return {"route": "source_scoped", "source_doc_id": d.source_doc_id,
                    "trace_notes": [f"router: source_scoped -> {d.source_doc_id}"]}
    except Exception as exc:  # never abort — fall back to the v4 direct path
        logger.warning("router_node error for %r: %s", question, exc)
    return {"route": "direct", "source_doc_id": "", "trace_notes": ["router: direct"]}


def _route(state: AgentState) -> str:
    return "source_scoped" if state["route"] == "source_scoped" else "direct"


# ---- G12 parallel fan-out: dispatch-and-aggregate (eval/g12_design.md) --------------------------------------------
# Stage 1 of the gate. Deterministic, so a question without comparison wording never calls the decomposer and takes
# the pre-G12 path byte-for-byte. Registered in eval/g12_PREDICTION.md.
COMPARISON_GATE = re.compile(
    r"\b(compare[sd]?|comparison|versus|vs\.?|agree|disagree|differ|differs|difference|higher than|lower than"
    r"|stricter than)\b",
    re.IGNORECASE,
)
MAX_BRANCHES = 3

# G12 ships SWITCHED OFF. The fan-out is built and measured, but on the four comparison rows it was slower (+1.98 s
# p50) and two answers got worse (row 9's correctness, and an unregistered drop on row 21): eval/g12_PREDICTION.md,
# Outcome. With the flag off, `_build_graph` leaves the decompose/branch/join nodes OUT of the compiled graph, so the
# shipped graph has the pre-G12 topology exactly; turning it on needs a new pre-registration.
FANOUT_ENABLED = False


def _route_fanout(state: AgentState) -> str:
    """The router edge of the ENABLED build only: G12 stage 1, the deterministic wording gate, on the direct route."""
    route = _route(state)
    if route == "direct" and COMPARISON_GATE.search(state["question"]):
        return "decompose"
    return route


class SubQuestion(BaseModel):
    """One branch of a decomposed comparison (field descriptions frozen with eval/g12_PREDICTION.md)."""

    question: str = Field(description="a self-contained question asking for ONE source's value or requirement")
    source_doc_id: str | None = Field(default=None, description=(
        "the one known document this sub-question asks about, else null"))


class Decomposition(BaseModel):
    """Decomposer output — stage 2 of the gate (field descriptions frozen with eval/g12_PREDICTION.md)."""

    comparison: bool = Field(description=(
        "true only if the question compares a value or requirement across two or more sources"))
    sub_questions: list[SubQuestion] = Field(description=(
        "2 or 3 sub-questions when comparison is true, else an empty list"))


# Frozen with the G12 pre-registration. Never edit it after the first scored run; a change is a new
# pre-registration. tests/test_fanout.py asserts it is byte-identical to the registered `text decomposer-prompt`.
DECOMPOSER_PROMPT = "\n".join([
    "You split comparison questions for an industrial-safety document search. A comparison question asks how a value or requirement stated by one source compares with, differs from, or agrees with the same kind of value or requirement in another source (for example a NIOSH limit versus an OSHA limit, or an SDS versus a regulation).",
    "Rules:",
    "- If the question is a comparison, set comparison = true and write 2 or 3 sub-questions, ONE per compared source. Each sub-question must be self-contained (name the chemical or equipment), ask only for that one source's value or requirement, and must not ask for the comparison itself.",
    "- For each sub-question, set source_doc_id to exactly one of the known ids below when it asks about exactly one of these documents; otherwise null.",
    "- If the question is not a comparison, set comparison = false and sub_questions = []. A question with two parts that does not compare the same kind of value across sources is NOT a comparison.",
    "",
    "Known documents (source_doc_id: title):",
    "{catalog}",
    "",
    "Question: {question}",
])


@lru_cache(maxsize=1)
def _decomposer_llm():
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model="gpt-4o-mini", temperature=0).with_structured_output(Decomposition, method="json_schema")


def decompose_node(state: AgentState) -> dict:
    """Stage 2 of the gate: one decomposer call. Fans out only on comparison=true with 2-3 sub-questions; anything
    else (error, no output, not a comparison, out-of-range count) leaves `sub_questions` empty so `_dispatch` takes
    today's single-query `retrieve` — decomposition failing never refuses a question (R4)."""
    question = state["question"]
    started = time.monotonic()
    try:
        d = _decomposer_llm().invoke(DECOMPOSER_PROMPT.format(catalog=_doc_catalog(), question=question))
    except Exception as exc:  # never abort — fall back to the single-query path
        logger.warning("decompose_node error: %s", type(exc).__name__)
        return {"sub_questions": [], "trace_notes": [f"decompose: ERROR {type(exc).__name__} -> single-query"]}
    elapsed = time.monotonic() - started
    if d is None:
        return {"sub_questions": [], "trace_notes": ["decompose: no structured output -> single-query"]}
    n = len(d.sub_questions)
    if not d.comparison:
        return {"sub_questions": [], "trace_notes": [f"decompose: not a comparison -> single-query ({elapsed:.2f}s)"]}
    if not 2 <= n <= MAX_BRANCHES:
        return {"sub_questions": [],
                "trace_notes": [f"decompose: {n} sub-questions, outside 2..{MAX_BRANCHES} -> single-query ({elapsed:.2f}s)"]}
    plans, notes = [], []
    for sq in d.sub_questions:
        doc = sq.source_doc_id or ""
        if doc and doc not in _known_doc_ids():  # same check the router makes: an unknown id is not trusted
            notes.append(f"decompose: unknown source_doc_id {doc!r} -> unscoped")
            doc = ""
        plans.append({"question": sq.question, "source_doc_id": doc})
    docs = ", ".join(p["source_doc_id"] or "unscoped" for p in plans)
    notes.insert(0, f"decompose: comparison -> {n} sub-questions [{docs}] ({elapsed:.2f}s)")
    return {"sub_questions": plans, "route": "decomposed", "trace_notes": notes}


def _dispatch(state: AgentState):
    """One `Send` per sub-question plan, all in the same super-step (so LangGraph runs them concurrently), each
    carrying the dispatch time so branches can report overlapping intervals. No plans -> the single-query path."""
    plans = state["sub_questions"]
    if not plans:
        return "retrieve"
    t0 = time.monotonic()
    n = len(plans)
    return [Send("branch_retrieve", {"branch": i, "n": n, "question": p["question"],
                                     "source_doc_id": p["source_doc_id"], "t0": t0})
            for i, p in enumerate(plans, 1)]


def branch_retrieve_node(payload: dict) -> dict:
    """ONE retrieval for one sub-question — not an agent: no planning, no tools. Scoped when the plan names a
    document. Records its outcome (chunks or the exception class) and its interval in the `fanout` channel; the join
    decides what to do with a failure (R4)."""
    i, n, question, doc, t0 = (payload["branch"], payload["n"], payload["question"],
                               payload["source_doc_id"], payload["t0"])
    k = get_settings().retrieval_k
    start = time.monotonic() - t0
    chunks, error = [], None
    try:
        if doc and doc not in _known_doc_ids():  # only reachable by injection: decompose already validated ids
            raise ValueError(f"unknown source_doc_id {doc!r}")
        chunks = dense_search(question, k=k, source_doc_id=doc) if doc else dense_search(question, k=k)
    except Exception as exc:  # a failed branch is a record, never an abort
        logger.warning("branch_retrieve[%d/%d] error: %s", i, n, type(exc).__name__)
        error = type(exc).__name__
    end = time.monotonic() - t0
    scope = f"source_scoped:{doc}" if doc else "unscoped"
    outcome = f"ERROR {error}" if error else f"{len(chunks)} chunks"
    record = {"branch": i, "n": n, "sub_question": question, "source_doc_id": doc, "chunks": chunks,
              "error": error, "t_start": round(start, 3), "t_end": round(end, 3)}
    return {"fanout": [record],
            "trace_notes": [f"fanout[{i}/{n}] retrieve({scope}) t+{start:.3f}s..t+{end:.3f}s -> {outcome}"]}


def join_node(state: AgentState) -> dict:
    """Aggregate the branches: rank-interleave the survivors (round-robin by rank, in the decomposer's order), dedup
    by `chunk_content_key` (first occurrence wins), and write `retrieved` ONCE. One surviving branch is enough to
    generate; if every branch failed, take the existing retrieval-error path (empty answer, no generate call)."""
    records = {}
    for rec in state["fanout"]:
        records[rec["branch"]] = rec  # keyed by branch: a retried branch's record replaces, never doubles
    ordered = [records[b] for b in sorted(records)]
    ok = [r for r in ordered if r["error"] is None]
    failed = ", ".join(f"fanout[{r['branch']}/{r['n']}] {r['error']}" for r in ordered if r["error"] is not None)
    n = len(ordered)
    if not ok:
        return {"retrieved": [], "retrieval_error": True,
                "trace_notes": [f"join: 0/{n} branches ok; failed {failed} -> retrieval_error"]}
    seen, joined, total = set(), [], 0
    for rank in range(max(len(r["chunks"]) for r in ok)):
        for r in ok:
            if rank < len(r["chunks"]):
                total += 1
                key = chunk_content_key(r["chunks"][rank])
                if key not in seen:
                    seen.add(key)
                    joined.append(r["chunks"][rank])
    head = f"join: {len(ok)}/{n} branches ok" + (f"; failed {failed}" if failed else "")
    return {"retrieved": joined,
            "trace_notes": [f"{head} -> {total} chunks, {len(joined)} after dedup ({total - len(joined)} duplicates)"]}


def source_scoped_retrieve_node(state: AgentState) -> dict:
    """Dense retrieval filtered to the router-chosen document (2C), with an EXECUTION fallback (2D).

    The router try/except guards CLASSIFICATION; this guards EXECUTION — a distinct failure that only
    occurs on the live wire (never in the byte-repro eval, where the 4 scoped rows all return chunks).
    If the filtered query THROWS or returns ZERO chunks (valid doc_id, but nothing matched for any
    reason), fall back to the DIRECT full-corpus retrieve for this request: a full-corpus answer beats
    a 500 or a blank one. On fallback we DOWNGRADE the reported route to "direct" and clear
    source_doc_id, so the response stays coherent (route=direct => no single-doc claim => citations may
    span docs). This makes source_scoped_retrieve a SECOND, sequential writer of route/source_doc_id
    after router_node (safe: no parallel branch touches them; see agent/state.py)."""
    question = state["question"]
    settings = get_settings()
    ns = settings.retrieval_namespace
    k = settings.retrieval_k
    doc = state["source_doc_id"]
    try:
        retrieved = dense_search(question, k=k, source_doc_id=doc)
        if retrieved:
            note = f"retrieve[source_scoped:{doc}]: dense_search(k={k}, ns={ns}) -> {len(retrieved)} chunks"
            return {"retrieved": retrieved, "trace_notes": [note]}
        reason = f"retrieve[source_scoped:{doc}]: 0 chunks matched -> direct fallback"
    except Exception as exc:  # execution failure on the filtered query -> fall back, never abort
        logger.warning("source_scoped_retrieve error for %r: %s", question, exc)
        reason = f"retrieve[source_scoped:{doc}]: ERROR {type(exc).__name__}: {exc} -> direct fallback"
    try:
        retrieved = dense_search(question, k=k)  # unfiltered = the v4 direct path
        note = f"retrieve[direct-fallback]: dense_search(k={k}, ns={ns}) -> {len(retrieved)} chunks"
        return {"retrieved": retrieved, "route": "direct", "source_doc_id": "",
                "trace_notes": [reason, note]}
    except Exception as exc:  # even the fallback threw -> mirror retrieve_node's sentinel
        logger.warning("source_scoped direct-fallback error for %r: %s", question, exc)
        note = f"retrieve[direct-fallback]: ERROR {type(exc).__name__}: {exc} -> 0 chunks"
        return {"retrieved": [], "retrieval_error": True, "route": "direct", "source_doc_id": "",
                "trace_notes": [reason, note]}


def retrieve_node(state: AgentState) -> dict:
    """Dense retrieval at the settings-configured depth. Wraps src.retrieve.dense_search.

    Returns only the channels it changed. `retrieved` has an `add` reducer, so on the direct
    path this single write appends to the fresh_state [] (== the dense_search result, in order).
    """
    question = state["question"]
    settings = get_settings()
    k = settings.retrieval_k  # settings-driven depth — never hardcode 10 (breaks the A/B override)
    try:
        retrieved = dense_search(question, k=k)
        note = f"retrieve[{state['route']}]: dense_search(k={k}, ns={settings.retrieval_namespace}) -> {len(retrieved)} chunks"
        return {"retrieved": retrieved, "trace_notes": [note]}
    except Exception as exc:  # never abort the run / drop a row
        logger.warning("retrieve_node error for %r: %s", question, exc)
        # Set the sentinel so generate_node short-circuits without an LLM call (mirrors pipeline.ask,
        # which skips generation entirely when dense_search throws). retrieved stays [] -> ask()
        # returns contexts=[]/chunks=[], matching pipeline on all three fields.
        note = f"retrieve[{state['route']}]: ERROR {type(exc).__name__}: {exc} -> 0 chunks"
        return {"retrieved": [], "retrieval_error": True, "trace_notes": [note]}


def generate_node(state: AgentState) -> dict:
    """Grounded generation over the canonical context representation. Wraps src.generate.generate.

    Builds contexts with the SAME format_contexts the entry adapter returns, so the graded text
    equals the text the model saw (byte-identical to pipeline.ask).
    """
    question = state["question"]
    # Short-circuit on a caught retrieval exception: mirror pipeline.ask, which never calls
    # generate() when dense_search throws. Same return shape as the normal path (answer +
    # trace_notes both present) so nothing downstream reads an unset key, and a breadcrumb so the
    # path record shows the node fired and why. No format_contexts, no generate() call, no cost.
    if state["retrieval_error"]:
        return {"answer": "", "trace_notes": ["generate[skipped]: retrieval_error -> answer_len=0"]}
    contexts = format_contexts(state["retrieved"])
    try:
        answer = generate(question, contexts)
        if not answer:  # mirror pipeline.ask: empty generation -> scoreable empty answer
            logger.warning("generate_node empty answer for %r", question)
            answer = ""
        note = f"generate: {len(contexts)} contexts -> answer_len={len(answer)}"
    except Exception as exc:  # never abort the run / drop a row
        logger.warning("generate_node error for %r: %s", question, exc)
        answer = ""
        note = f"generate: ERROR {type(exc).__name__}: {exc} -> answer_len=0"
    return {"answer": answer, "trace_notes": [note]}


# ---- G1 tool-calling loop (tool_decide -> tool_exec, conditional; see module docstring) ----------
# Two nodes + a conditional edge (not one internal-loop node) so EACH iteration is a visible super-step in
# the trace — the observability story, and what a future checkpointer (G10b) will resume on.
CAP = int(os.environ.get("AGENT_TOOL_MAX_ITERATIONS", "3"))          # hard loop bound (§5)
TOOL_TIMEOUT_S = float(os.environ.get("AGENT_TOOL_TIMEOUT_S", "5"))  # per-tool wall-clock guard


@lru_cache(maxsize=1)
def _tool_llm():
    """The model with tools bound (bind_tools, free-form: zero/one/several calls). Mirrors _router_llm's
    factory shape so tests monkeypatch `graph._tool_llm` with a stub whose .invoke -> AIMessage."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model="gpt-4o-mini", temperature=0).bind_tools(TOOL_SCHEMAS)


@lru_cache(maxsize=1)
def _tool_pool():
    from concurrent.futures import ThreadPoolExecutor

    return ThreadPoolExecutor(max_workers=2, thread_name_prefix="tool")


def _tool_prompt(question: str, contexts: list[str], prior_results: list[dict]) -> str:
    ctx = "\n\n".join(contexts) if contexts else "(no retrieved context)"
    prior = json.dumps(prior_results, default=str) if prior_results else "(none yet)"
    return (
        "You are answering an industrial-safety question. You MAY call a tool, but ONLY if the retrieved "
        "context below does not already contain the value the question needs — prefer NOT calling a tool when "
        "the answer is already present. Tools: ConvertExposureLimit (ppm<->mg/m3, gases/vapors only), "
        "LookupDocumentMetadata (a document's provenance), CompareThresholds (compare two limits). If no tool "
        "is needed, answer with no tool calls.\n\n"
        f"Question: {question}\n\nRetrieved context:\n{ctx}\n\nPrior tool results: {prior}"
    )


def tool_decide_node(state: AgentState) -> dict:
    """Ask the model whether a tool is needed; emit the requested calls (or none). Cap-bounded (§5)."""
    if state["retrieval_error"]:  # retrieval already failed -> no tools (mirror generate's short-circuit)
        return {"tool_calls": [], "trace_notes": ["tool_decide[skipped]: retrieval_error -> generate"]}
    it = state["tool_iterations"]
    if it >= CAP:  # hard bound: stop looping, generate from what's available (countable signal, not swallowed)
        logger.warning("tool loop hit cap (%d) for %r -> generating from available context", CAP, state["question"])
        return {"tool_calls": [], "trace_notes": [f"tool_decide: CAP_REACHED ({CAP} iterations) -> generate"]}
    try:
        contexts = format_contexts(state["retrieved"])
        resp = _tool_llm().invoke(_tool_prompt(state["question"], contexts, state["tool_results"]))
        calls = list(getattr(resp, "tool_calls", []) or [])
    except Exception as exc:  # never abort — a decide failure just means no tools this run
        logger.warning("tool_decide error for %r: %s", state["question"], exc)
        return {"tool_calls": [], "trace_notes": [f"tool_decide: ERROR {type(exc).__name__}: {exc} -> generate"]}
    if not calls:
        return {"tool_calls": [], "trace_notes": [f"tool_decide[iter {it + 1}]: no tools needed -> generate"]}
    names = ", ".join(c.get("name", "?") for c in calls)
    return {"tool_calls": calls,
            "trace_notes": [f"tool_decide[iter {it + 1}]: requested {len(calls)} tool(s): {names}"]}


def _route_tools(state: AgentState) -> str:
    return "tool_exec" if state["tool_calls"] else "generate"


def _tool_chunk(name: str, args: dict, result: dict) -> dict:
    """A synthetic, SELF-DESCRIBING context chunk so the FROZEN generate_node grounds on the tool output.
    `page=None` so api/citations.py's provenance-skip excludes it (a tool output is a transformation of a
    cited source, not a source). NOTE: the 'COMPUTED ... not a document quote' marker does NOT prevent the
    model from citing the chunk in prose (observed 5/5 on the source-scoped acetone row,
    eval/g1_closure_probe.md); structured citations exclude it via page=None regardless."""
    text = (
        f"COMPUTED by {name} — not a document quote.\n"
        f"args={json.dumps(args, default=str)} -> {json.dumps(result, default=str)}\n"
        "Input value(s) sourced from the retrieved context above."
    )
    return {"text": text, "source_doc_id": f"tool:{name}", "page": None}


def tool_exec_node(state: AgentState) -> dict:
    """Run this iteration's tool calls via the manual Pydantic-validated dispatch, with a per-tool timeout.
    Success -> a structured `tool_results` entry AND a synthetic chunk into `retrieved`. Failure -> a
    trace_notes breadcrumb and nothing else (never a fabricated value). Increments the counter, clears calls."""
    results: list[dict] = []
    chunks: list[dict] = []
    notes: list[str] = []
    for call in state["tool_calls"]:
        name = call.get("name", "?")
        args = call.get("args", {}) or {}
        try:
            result = _tool_pool().submit(run_tool, name, args).result(timeout=TOOL_TIMEOUT_S)
            results.append({"tool": name, "args": args, "result": result})
            chunks.append(_tool_chunk(name, args, result))
            notes.append(f"tool_exec[{name}]: ok -> {json.dumps(result, default=str)[:100]}")
        except ToolError as exc:  # honest tool failure — refuse, don't fabricate
            notes.append(f"tool_exec[{name}]: FAILED ({exc}) -> no result, continuing without it")
        except Exception as exc:  # timeout / unexpected — same policy: never a guessed value
            logger.warning("tool_exec %s error: %s", name, exc)
            notes.append(f"tool_exec[{name}]: ERROR {type(exc).__name__}: {exc} -> no result, continuing without it")
    return {
        "tool_results": results,          # add-reducer: accumulates across iterations
        "retrieved": chunks,              # add-reducer: synthetic tool chunks for the frozen generate
        "tool_iterations": state["tool_iterations"] + 1,
        "tool_calls": [],                 # clear this iteration's requests
        "trace_notes": notes or ["tool_exec: no calls"],
    }


# ---- G10b approval gate (review_gate -> review_wait; eval/g10b_design.md) ------------------------------------------
def review_gate_node(state: AgentState, config: RunnableConfig) -> dict:
    """Apply the trigger policy just before generation. No fire -> {} (generate sees exactly what it saw before G10b).
    Fire with a checkpointer -> a pending review record (review_wait then pauses). Fire without one -> refused."""
    reason = review.trigger_reason(state["question"], state["trace_notes"])
    if reason is None:
        return {}
    conf = config.get("configurable", {}) if config else {}
    thread_id = conf.get("thread_id")
    if not conf.get("review_durable") or not thread_id:
        return {"review": {"status": review.UNAVAILABLE, "reason": reason},
                "trace_notes": [f"review: unavailable ({reason}) -> refused, no checkpointer"]}
    return {"review": review.pending_record(reason, thread_id),
            "trace_notes": [f"review: paused ({reason}) thread={thread_id}"]}


def review_wait_node(state: AgentState) -> dict:
    """Pause until a reviewer decides. On resume, interrupt() returns {"op": ...}; anything but approve rejects
    (fail closed). Amend never reaches this body: the resume endpoint applies it with update_state(as_node=...)."""
    record = state["review"]
    decision = interrupt({"thread_id": record.get("thread_id"), "reason": record.get("reason")})
    op = decision.get("op") if isinstance(decision, dict) else None
    status = review.APPROVED if op == "approve" else review.REJECTED
    return {"review": {**record, "status": status, "op": op or "reject", "resolved_at": review.iso(review.now_utc())},
            "trace_notes": [f"review: resumed op={op or 'reject'} removals=0 additions=0"]}


def _route_review_gate(state: AgentState) -> str:
    status = (state.get("review") or {}).get("status")
    return "review" if status == review.PENDING else "refuse" if status == review.UNAVAILABLE else "generate"


def _route_review_wait(state: AgentState) -> str:
    return "refuse" if state["review"].get("status") == review.REJECTED else "generate"


def _compiled_graph():
    """The graph the service runs: the build selected by FANOUT_ENABLED (off in the shipped app), compiled with the
    process's checkpointer (agent/review.py; None when REVIEW_DB_PATH is unset)."""
    return _build_graph(FANOUT_ENABLED, review.checkpointer())


@lru_cache(maxsize=8)
def _build_graph(fanout: bool, checkpointer=None):
    """Build + compile the graph once per variant (stateless; state is per-invoke). With fanout=False the G12 nodes
    and edges are not added at all, so the compiled topology is exactly the pre-G12 one.
    START → router → (source_scoped) source_scoped_retrieve | (direct) retrieve
                   | (comparison-worded, G12) decompose → Send × N → branch_retrieve → join [or → retrieve]
          → tool_decide ⇄ tool_exec (G1 loop) → generate → END.
    The tool loop sits BETWEEN retrieval and generation and is conditional: a question needing no tool passes
    tool_decide straight to generate, so the direct path byte-reproduces v4 (generate_node and its inputs are
    untouched on the no-tool path). Two nodes (not one internal loop) so each iteration is a visible
    super-step for tracing + future checkpointing (G10b)."""
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("source_scoped_retrieve", source_scoped_retrieve_node)
    if fanout:
        builder.add_node("decompose", decompose_node)
        builder.add_node("branch_retrieve", branch_retrieve_node)
        builder.add_node("join", join_node)
    builder.add_node("tool_decide", tool_decide_node)
    builder.add_node("tool_exec", tool_exec_node)
    builder.add_node("review_gate", review_gate_node)
    builder.add_node("review_wait", review_wait_node)
    builder.add_node("generate", generate_node)
    builder.add_edge(START, "router")
    if fanout:
        builder.add_conditional_edges("router", _route_fanout,
                                      {"source_scoped": "source_scoped_retrieve", "direct": "retrieve",
                                       "decompose": "decompose"})
        builder.add_conditional_edges("decompose", _dispatch, ["branch_retrieve", "retrieve"])
        builder.add_edge("branch_retrieve", "join")
        builder.add_edge("join", "tool_decide")
    else:
        builder.add_conditional_edges("router", _route,
                                      {"source_scoped": "source_scoped_retrieve", "direct": "retrieve"})
    builder.add_edge("retrieve", "tool_decide")
    builder.add_edge("source_scoped_retrieve", "tool_decide")
    builder.add_conditional_edges("tool_decide", _route_tools,
                                  {"tool_exec": "tool_exec", "generate": "review_gate"})
    builder.add_edge("tool_exec", "tool_decide")
    builder.add_conditional_edges("review_gate", _route_review_gate,
                                  {"review": "review_wait", "generate": "generate", "refuse": END})
    builder.add_conditional_edges("review_wait", _route_review_wait, {"generate": "generate", "refuse": END})
    builder.add_edge("generate", END)
    return builder.compile(checkpointer=checkpointer)


def _routing_reason(route: str, source_doc_id: str, sub_questions: list[dict] | None = None) -> str | None:
    """Human-readable rationale for the route taken — the /ask/agent transparency payload (2D).
    None on the direct path (nothing to explain); names the document on the source-scoped path; lists the
    sub-questions (and the document each was scoped to) on the G12 decomposed path."""
    if route == "source_scoped" and source_doc_id:
        return f"Question attributed to a single named document: {_doc_titles().get(source_doc_id, source_doc_id)}"
    if route == "decomposed" and sub_questions:
        parts = "; ".join(f"[{p['source_doc_id'] or 'all documents'}] {p['question']}" for p in sub_questions)
        return f"Comparison split into {len(sub_questions)} sub-questions: {parts}"
    return None


@traceable(name="agent_pipeline")
def ask(question: str) -> dict:
    """Entry adapter — extends src.pipeline.ask's {answer, contexts, chunks} contract additively.

    Invokes the graph with a FRESH state per call (invariant (a)), then rebuilds contexts from
    the final `retrieved` so `contexts == format_contexts(retrieved)` byte-for-byte. `chunks` is
    the retrieved metadata list (aligned with contexts) the API layer derives citations from.

    2D: also returns `route`/`source_doc_id`/`routing_reason` for the /ask/agent transparency
    payload. This is PURELY ADDITIVE — eval/run_eval.py reads only `answer`/`contexts` by key, so
    the extra keys are invisible to the RAGAS harness and the v4 byte-repro. `route`/`source_doc_id`
    are always present post-invoke (router_node + fresh_state guarantee them); note the source-scoped
    retrieve node may have DOWNGRADED route to "direct" on an execution fallback, so these reflect
    what actually ran, not just the classifier's intent.

    G10b: each call runs on a FRESH uuid4 thread (never a default; invariant (a) with a checkpointer). When the gate
    pauses, the return adds `status: "pending_review"`, `thread_id`, `reason` and `expires_at`, and `answer` is "";
    when the gate fires with no checkpointer, `status: "review_unavailable"`. Otherwise the shape is unchanged.
    """
    saver = review.checkpointer()
    graph = _compiled_graph()
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id, "review_durable": saver is not None}}
    try:
        state = graph.invoke(fresh_state(question), config, **({"durability": "exit"} if saver is not None else {}))
    except sqlite3.Error as exc:  # the checkpoint store failed: fail closed, never an unreviewed answer
        logger.warning("review: checkpointer error thread=%s: %s", thread_id, type(exc).__name__)
        return {"answer": "", "contexts": [], "chunks": [], "route": "direct", "source_doc_id": "",
                "routing_reason": None, "status": "review_unavailable", "reason": "checkpointer_error"}
    result = _result(state)
    record = state.get("review") or {}
    if record.get("status") == review.PENDING:
        logger.info("review: paused (%s) thread=%s", record["reason"], thread_id)
        return {**result, "status": "pending_review", "thread_id": thread_id, "reason": record["reason"],
                "expires_at": record["expires_at"]}
    if saver is not None:  # a finished, unpaused thread is never resumed: drop its checkpoint now
        try:
            saver.delete_thread(thread_id)
        except sqlite3.Error as exc:
            logger.warning("review: could not delete finished thread=%s: %s", thread_id, type(exc).__name__)
    if record.get("status") == review.UNAVAILABLE:
        return {**result, "status": "review_unavailable", "reason": record["reason"]}
    return result


def _result(state: dict) -> dict:
    retrieved = state["retrieved"]
    route = state["route"]
    source_doc_id = state["source_doc_id"]
    return {
        "answer": state["answer"],
        "contexts": format_contexts(retrieved),
        "chunks": retrieved,
        "route": route,
        "source_doc_id": source_doc_id,
        "routing_reason": _routing_reason(route, source_doc_id, state["sub_questions"]),
    }


def _thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id, "review_durable": True}}


def _expire(saver, thread_id: str) -> dict:
    saver.delete_thread(thread_id)
    logger.info("review: expired thread=%s", thread_id)
    return {"status": "expired_or_lost", "thread_id": thread_id, "expired": True}


def resume(thread_id: str, op: str, removals: list[str] | None = None) -> dict:
    """Resolve a paused thread: approve, reject or amend (removals only through the API; eval/g10b_design.md R4).

    Returns {"status": "answered" | "rejected" | "expired_or_lost" | "invalid", ...}. A thread already resolved returns
    its recorded resolution (`recorded: True`) and never generates twice. Unknown, lost or expired -> expired_or_lost."""
    saver = review.checkpointer()
    if saver is None:
        return {"status": "expired_or_lost", "thread_id": thread_id}
    graph = _compiled_graph()
    config = _thread_config(thread_id)
    with review.thread_lock(thread_id):
        try:
            snapshot = graph.get_state(config)
            values = snapshot.values or {}
            record = values.get("review") or {}
            if not record:
                return {"status": "expired_or_lost", "thread_id": thread_id}
            if review.expired(record):
                return _expire(saver, thread_id)
            status = record.get("status")
            if status in review.RESOLVED:
                return {**_result(values), "question": values["question"],
                        "status": "rejected" if status == review.REJECTED else "answered",
                        "thread_id": thread_id, "recorded": True, "op": record.get("op")}
            if status != review.PENDING:
                return {"status": "expired_or_lost", "thread_id": thread_id}
            if op == "amend":
                keys = {chunk_key(c) for c in values["retrieved"]}
                unknown = [k for k in (removals or []) if k not in keys]
                if not removals or unknown:
                    return {"status": "invalid", "thread_id": thread_id,
                            "detail": "amend needs removals, each the key of a paused evidence item"}
                graph.update_state(config, {
                    "retrieved": [RemoveChunk(k) for k in removals],
                    "review": {**record, "status": review.AMENDED, "op": "amend", "removals": list(removals),
                               "resolved_at": review.iso(review.now_utc())},
                    "trace_notes": [f"review: resumed op=amend removals={len(removals)} additions=0"],
                }, as_node="review_wait")
                state = graph.invoke(None, config, durability="exit")
            else:
                state = graph.invoke(Command(resume={"op": op}), config, durability="exit")
        except sqlite3.Error as exc:  # fail closed
            logger.warning("review: checkpointer error on resume thread=%s: %s", thread_id, type(exc).__name__)
            return {"status": "expired_or_lost", "thread_id": thread_id}
    logger.info("review: resumed op=%s thread=%s", op, thread_id)
    final = (state.get("review") or {}).get("status")
    return {**_result(state), "question": state["question"],
            "status": "rejected" if final == review.REJECTED else "answered",
            "thread_id": thread_id, "recorded": False, "op": op}


def review_status(thread_id: str) -> dict:
    """A thread's review record, with lazy expiry. Never resumes or generates."""
    saver = review.checkpointer()
    if saver is None:
        return {"thread_id": thread_id, "status": "expired_or_lost"}
    graph = _compiled_graph()
    with review.thread_lock(thread_id):
        try:
            record = (graph.get_state(_thread_config(thread_id)).values or {}).get("review") or {}
            if not record:
                return {"thread_id": thread_id, "status": "expired_or_lost"}
            if review.expired(record):
                return {**_expire(saver, thread_id), "status": "expired_or_lost"}
        except sqlite3.Error:
            return {"thread_id": thread_id, "status": "expired_or_lost"}
    return {"thread_id": thread_id, "status": record.get("status"), "reason": record.get("reason"),
            "expires_at": record.get("expires_at"), "resolved_at": record.get("resolved_at")}


def sweep_expired() -> int:
    """Startup sweep (no scheduler exists on Render free): delete every thread whose review has expired, and any thread
    with no review record (a finished run whose cleanup was interrupted). Returns the number deleted."""
    saver = review.checkpointer()
    if saver is None:
        return 0
    graph = _compiled_graph()
    deleted = 0
    for thread_id in {c.config["configurable"]["thread_id"] for c in saver.list(None)}:
        record = (graph.get_state(_thread_config(thread_id)).values or {}).get("review") or {}
        if not record or review.expired(record):
            saver.delete_thread(thread_id)
            deleted += 1
    logger.info("review: startup sweep deleted %d thread(s)", deleted)
    return deleted
