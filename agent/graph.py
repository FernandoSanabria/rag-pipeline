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

import logging
from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from agent.state import AgentState, fresh_state
from src.config import get_settings
from src.generate import generate
from src.retrieve import dense_search, format_contexts

logger = logging.getLogger(__name__)


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
    """Build + compile the minimal graph once (compiled graph is stateless; state is per-invoke)."""
    builder = StateGraph(AgentState)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("generate", generate_node)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "generate")
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
