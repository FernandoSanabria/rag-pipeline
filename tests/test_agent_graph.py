"""Hermetic smoke tests for the 2A minimal agent graph (START -> retrieve -> generate -> END).

No network, no LLM: the src/ capabilities are stubbed by monkeypatching the module-bound names
`agent.graph.dense_search` and `agent.graph.generate`. `format_contexts` is left REAL so the
"grades byte-identical text" assertion is meaningful. Dummy OPENAI/PINECONE keys are set by
tests/conftest.py before import, and src/ clients are lazy, so get_settings() resolves offline.

Coverage: the {answer, contexts, chunks} contract; the direct-path route + trace_notes record;
the `add` reducer actually accumulating (two-writer graph); and — the point of this change — the
retrieval-exception path mirroring pipeline.ask on ALL THREE fields with ZERO generate() calls,
while a *legitimate* empty retrieval still generates.
"""

from unittest.mock import MagicMock

from langgraph.graph import END, START, StateGraph

from agent import graph
from agent.state import AgentState, fresh_state
from src.retrieve import format_contexts

CHUNKS = [
    {"text": "chunk one", "source_doc_id": "doc-a", "page": 1},
    {"text": "chunk two", "source_doc_id": "doc-b", "page": 2},
]


def _stub_ok(monkeypatch, answer="STUB ANSWER", chunks=CHUNKS):
    """Stub retrieval + generation for the happy path. Returns the generate MagicMock."""
    monkeypatch.setattr(graph, "dense_search", lambda q, k: list(chunks))
    gen = MagicMock(return_value=answer)
    monkeypatch.setattr(graph, "generate", gen)
    return gen


def test_fresh_state_initializes_all_eight_channels():
    """State construction contract: fresh_state seeds ALL 8 channels with correct defaults, so no
    node ever reads an unset channel and the add-reducer accumulators start EMPTY (invariant (a),
    which 2B's parallel Send fan-in relies on)."""
    st = fresh_state("what is X?")
    assert set(st) == {
        "question", "sub_questions", "route", "retrieval_error",
        "retrieved", "answer", "citations", "trace_notes",
    }
    assert st["question"] == "what is X?"
    assert st["route"] == "direct"         # 2A / v4 direct path
    assert st["retrieval_error"] is False   # sentinel default
    assert st["sub_questions"] == []
    assert st["retrieved"] == []            # add-reducer accumulator starts empty
    assert st["trace_notes"] == []          # add-reducer accumulator starts empty
    assert st["citations"] == []
    assert st["answer"] == ""

    # Aliasing / independence — a shared-mutable-default would pass every ==[] check above yet break
    # invariant (a) (evidence leaking across invocations). Two fresh states must be independent, and a
    # single state's two accumulators must be distinct objects.
    a = fresh_state("qa")
    b = fresh_state("qb")
    a["retrieved"].append({"x": 1})
    a["trace_notes"].append("note")
    assert b["retrieved"] == [] and b["trace_notes"] == []
    assert a["retrieved"] is not a["trace_notes"]


def test_ask_returns_frozen_contract(monkeypatch):
    _stub_ok(monkeypatch)
    out = graph.ask("what is the flash point of acetone?")

    assert set(out) == {"answer", "contexts", "chunks"}  # exactly the frozen shape run_eval reads
    assert out["answer"] == "STUB ANSWER"
    assert out["chunks"] == CHUNKS
    # contexts must be the SAME pure representation the model saw — byte-identical via the real fn.
    assert out["contexts"] == format_contexts(CHUNKS)


def test_direct_path_route_and_trace_notes(monkeypatch):
    _stub_ok(monkeypatch)
    state = graph._compiled_graph().invoke(fresh_state("q"))

    assert state["route"] == "direct"
    assert state["retrieval_error"] is False

    # trace_notes is the in-state PATH RECORD — the 2A observability gate independent of LangSmith,
    # and exactly what 2B's regression guard reads to prove "simple rows took the direct path
    # unchanged". Assert the path by RELATIONSHIP (retrieve precedes generate), not fixed indices,
    # so it survives 2B inserting decompose/synthesize/router breadcrumbs between them. next() also
    # asserts each breadcrumb is present (StopIteration → test failure if a node went silent).
    notes = state["trace_notes"]
    retrieve_idx = next(i for i, n in enumerate(notes) if n.startswith("retrieve[direct]:"))
    generate_idx = next(i for i, n in enumerate(notes) if n.startswith("generate:"))
    assert retrieve_idx < generate_idx


def test_add_reducer_accumulates_across_two_writers():
    """Two nodes both write `retrieved`; the `add` reducer must CONCATENATE, not overwrite."""

    def writer_a(state):
        return {"retrieved": [{"text": "a", "source_doc_id": "d", "page": 1}]}

    def writer_b(state):
        return {"retrieved": [{"text": "b", "source_doc_id": "d", "page": 2}]}

    builder = StateGraph(AgentState)
    builder.add_node("a", writer_a)
    builder.add_node("b", writer_b)
    builder.add_edge(START, "a")
    builder.add_edge("a", "b")
    builder.add_edge("b", END)
    compiled = builder.compile()

    out = compiled.invoke(fresh_state("q"))
    # Overwrite would leave only ["b"]; the reducer keeps both, in write order.
    assert [c["text"] for c in out["retrieved"]] == ["a", "b"]


def test_retrieval_exception_short_circuits_with_no_generate_call(monkeypatch):
    """The fix: a dense_search exception must mirror pipeline.ask on all three fields AND skip generate."""

    def boom(q, k):
        raise RuntimeError("pinecone timeout")

    monkeypatch.setattr(graph, "dense_search", boom)
    gen = MagicMock(return_value="SHOULD NEVER BE PRODUCED")
    monkeypatch.setattr(graph, "generate", gen)

    out = graph.ask("q")

    # (a) all three fields — exact dict equality also rejects any stray/extra key.
    assert out == {"answer": "", "contexts": [], "chunks": []}
    # (b) no LLM call — answer=="" alone would pass even if generate ran and returned "".
    gen.assert_not_called()


def test_generate_exception_keeps_contexts_and_chunks(monkeypatch):
    """Generate-failure branch already matches pipeline.ask: answer="" but contexts/chunks populated."""
    monkeypatch.setattr(graph, "dense_search", lambda q, k: list(CHUNKS))

    def boom(q, c):
        raise RuntimeError("llm down")

    monkeypatch.setattr(graph, "generate", boom)

    out = graph.ask("q")
    assert out["answer"] == ""
    assert out["chunks"] == CHUNKS
    assert out["contexts"] == format_contexts(CHUNKS)


def test_legit_empty_retrieval_still_calls_generate(monkeypatch):
    """A no-match retrieval (empty, NO exception) is NOT an error: generate still runs (→ refusal),
    exactly as pipeline.ask does. This is why the sentinel is a flag, not `if not retrieved`."""
    monkeypatch.setattr(graph, "dense_search", lambda q, k: [])
    refusal = "The provided context does not contain the answer."
    gen = MagicMock(return_value=refusal)
    monkeypatch.setattr(graph, "generate", gen)

    out = graph.ask("q")
    gen.assert_called_once()
    assert out["answer"] == refusal
    assert out["contexts"] == []
    assert out["chunks"] == []
