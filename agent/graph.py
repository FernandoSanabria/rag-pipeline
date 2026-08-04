"""Minimal LangGraph graph — the v4 path expressed as a graph (2A skeleton).

    START -> retrieve_node -> generate_node -> END

No routing, no decomposition. This is the "empty pipeline baseline" of Phase 2: prove the
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
from functools import lru_cache
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from langsmith import traceable
from pydantic import BaseModel, Field

from agent.state import AgentState, fresh_state
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
    """Dense retrieval filtered to the router-chosen document (2C). Same {retrieved, trace_notes}
    contract + retrieval_error sentinel as retrieve_node."""
    question = state["question"]
    settings = get_settings()
    k = settings.retrieval_k
    doc = state["source_doc_id"]
    try:
        retrieved = dense_search(question, k=k, source_doc_id=doc)
        note = f"retrieve[source_scoped:{doc}]: dense_search(k={k}, ns={settings.retrieval_namespace}) -> {len(retrieved)} chunks"
        return {"retrieved": retrieved, "trace_notes": [note]}
    except Exception as exc:  # never abort the run / drop a row
        logger.warning("source_scoped_retrieve error for %r: %s", question, exc)
        note = f"retrieve[source_scoped:{doc}]: ERROR {type(exc).__name__}: {exc} -> 0 chunks"
        return {"retrieved": [], "retrieval_error": True, "trace_notes": [note]}


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


@lru_cache(maxsize=1)
def _compiled_graph():
    """Build + compile the graph once (stateless; state is per-invoke).
    START → router → (source_scoped) source_scoped_retrieve | (direct) retrieve → generate → END."""
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("source_scoped_retrieve", source_scoped_retrieve_node)
    builder.add_node("generate", generate_node)
    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", _route,
                                  {"source_scoped": "source_scoped_retrieve", "direct": "retrieve"})
    builder.add_edge("retrieve", "generate")
    builder.add_edge("source_scoped_retrieve", "generate")
    builder.add_edge("generate", END)
    return builder.compile()


@traceable(name="agent_pipeline")
def ask(question: str) -> dict:
    """Entry adapter — mirrors src.pipeline.ask's frozen {answer, contexts, chunks} contract.

    Invokes the graph with a FRESH state per call (invariant (a)), then rebuilds contexts from
    the final `retrieved` so `contexts == format_contexts(retrieved)` byte-for-byte. `chunks` is
    the retrieved metadata list (aligned with contexts) the API layer derives citations from.
    """
    state = _compiled_graph().invoke(fresh_state(question))
    retrieved = state["retrieved"]
    contexts = format_contexts(retrieved)
    return {"answer": state["answer"], "contexts": contexts, "chunks": retrieved}
