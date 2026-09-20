"""LangGraph agent graph — router + retrieval strategies + a bounded tool-calling loop (G1).

    START -> router -> (direct) retrieve | (source_scoped) source_scoped_retrieve
          -> tool_decide <-> tool_exec (bounded loop) -> generate_node -> END

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
from functools import lru_cache
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from langsmith import traceable
from pydantic import BaseModel, Field

from agent.state import AgentState, fresh_state
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
    cited source, not a source). The 'COMPUTED ... not a document quote' marker keeps the model from citing
    it in prose as a passage."""
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


@lru_cache(maxsize=1)
def _compiled_graph():
    """Build + compile the graph once (stateless; state is per-invoke).
    START → router → (source_scoped) source_scoped_retrieve | (direct) retrieve
          → tool_decide ⇄ tool_exec (G1 loop) → generate → END.
    The tool loop sits BETWEEN retrieval and generation and is conditional: a question needing no tool passes
    tool_decide straight to generate, so the direct path byte-reproduces v4 (generate_node and its inputs are
    untouched on the no-tool path). Two nodes (not one internal loop) so each iteration is a visible
    super-step for tracing + future checkpointing (G10b)."""
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("source_scoped_retrieve", source_scoped_retrieve_node)
    builder.add_node("tool_decide", tool_decide_node)
    builder.add_node("tool_exec", tool_exec_node)
    builder.add_node("generate", generate_node)
    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", _route,
                                  {"source_scoped": "source_scoped_retrieve", "direct": "retrieve"})
    builder.add_edge("retrieve", "tool_decide")
    builder.add_edge("source_scoped_retrieve", "tool_decide")
    builder.add_conditional_edges("tool_decide", _route_tools,
                                  {"tool_exec": "tool_exec", "generate": "generate"})
    builder.add_edge("tool_exec", "tool_decide")
    builder.add_edge("generate", END)
    return builder.compile()


def _routing_reason(route: str, source_doc_id: str) -> str | None:
    """Human-readable rationale for the route taken — the /ask/agent transparency payload (2D).
    None on the direct path (nothing to explain); names the document on the source-scoped path."""
    if route == "source_scoped" and source_doc_id:
        return f"Question attributed to a single named document: {_doc_titles().get(source_doc_id, source_doc_id)}"
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
    """
    state = _compiled_graph().invoke(fresh_state(question))
    retrieved = state["retrieved"]
    contexts = format_contexts(retrieved)
    route = state["route"]
    source_doc_id = state["source_doc_id"]
    return {
        "answer": state["answer"],
        "contexts": contexts,
        "chunks": retrieved,
        "route": route,
        "source_doc_id": source_doc_id,
        "routing_reason": _routing_reason(route, source_doc_id),
    }
