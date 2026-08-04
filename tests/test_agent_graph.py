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

import pytest
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


class _StubRouter:
    """Stub for graph._router_llm() — .invoke(prompt) returns a fixed RouteDecision (no LLM call)."""

    def __init__(self, decision):
        self.decision = decision

    def invoke(self, prompt):
        return self.decision


def _route_llm(scoped, doc_id):
    return lambda: _StubRouter(graph.RouteDecision(source_scoped=scoped, source_doc_id=doc_id))


@pytest.fixture(autouse=True)
def _router_direct_by_default(monkeypatch):
    """Every graph invocation hits router_node -> _router_llm(). Default it to DIRECT (no network),
    so the 2A tests stay hermetic; source-scoped tests override with monkeypatch on graph._router_llm."""
    monkeypatch.setattr(graph, "_router_llm", _route_llm(False, None))


def test_fresh_state_initializes_all_nine_channels():
    """State construction contract: fresh_state seeds ALL 9 channels with correct defaults, so no
    node ever reads an unset channel and the add-reducer accumulators start EMPTY (invariant (a))."""
    st = fresh_state("what is X?")
    assert set(st) == {
        "question", "sub_questions", "route", "source_doc_id", "retrieval_error",
        "retrieved", "answer", "citations", "trace_notes",
    }
    assert st["question"] == "what is X?"
    assert st["route"] == "direct"         # 2A / v4 direct path
    assert st["source_doc_id"] == ""        # 2C: no source-scope on the direct path
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


def test_ask_returns_contract_with_routing_fields(monkeypatch):
    _stub_ok(monkeypatch)
    out = graph.ask("what is the flash point of acetone?")

    # 2D additive contract: the {answer, contexts, chunks} run_eval reads, PLUS the routing-transparency
    # fields the /ask/agent endpoint surfaces. run_eval reads answer/contexts BY KEY, so extra keys are
    # invisible to it and to the v4 byte-repro — the addition is safe.
    assert set(out) == {"answer", "contexts", "chunks", "route", "source_doc_id", "routing_reason"}
    assert out["answer"] == "STUB ANSWER"
    assert out["chunks"] == CHUNKS
    # contexts must be the SAME pure representation the model saw — byte-identical via the real fn.
    assert out["contexts"] == format_contexts(CHUNKS)
    # router stubbed direct (autouse fixture): no source scope, no reason
    assert out["route"] == "direct"
    assert out["source_doc_id"] == ""
    assert out["routing_reason"] is None


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

    # (a) the three pipeline-parity fields on the exception path (unchanged behavior)...
    assert out["answer"] == "" and out["contexts"] == [] and out["chunks"] == []
    # ...plus the additive 2D routing fields (direct path, nothing scoped); set() rejects stray keys.
    assert out["route"] == "direct" and out["source_doc_id"] == "" and out["routing_reason"] is None
    assert set(out) == {"answer", "contexts", "chunks", "route", "source_doc_id", "routing_reason"}
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


# ---------------- 2C source-scoped router ----------------
def _stub_route(monkeypatch, scoped, doc_id):
    monkeypatch.setattr(graph, "_router_llm", _route_llm(scoped, doc_id))


def test_router_source_scopes_single_document_question(monkeypatch):
    _stub_route(monkeypatch, True, "sds-sigma-aldrich-acetone")
    out = graph.router_node(fresh_state("What is the flash point of acetone per the Sigma-Aldrich SDS?"))
    assert out["route"] == "source_scoped"
    assert out["source_doc_id"] == "sds-sigma-aldrich-acetone"


@pytest.mark.parametrize("question", [
    # the shipped 2B IDLH win — a TWO-source comparison; source-scoping would drop one side
    "For anhydrous ammonia, how does the NIOSH IDLH compare to the EPA RMP toxic endpoint "
    "used in offsite consequence analysis?",
    # a regulation-by-name general question (the router OVER-scoped this before the prompt was narrowed)
    "What triggers the Management of Change requirement under the PSM standard?",
    # a cross-source comparison
    "What is the exposure limit for chlorine under OSHA versus NIOSH?",
])
def test_router_non_source_anchored_stays_direct(monkeypatch, question):
    """PIN the DIRECT boundary at the router_node contract level: comparisons + regulation-by-name
    questions route direct (the 2B IDLH win + simple rows must NOT be source-scoped). NOTE: the LLM
    is stubbed, so this pins router_node's HANDLING, not prompt classification — real prompt-drift is
    caught by the mandatory pre-eval real-router scope check (which caught the MOC over-scope)."""
    _stub_route(monkeypatch, False, None)
    out = graph.router_node(fresh_state(question))
    assert out["route"] == "direct"
    assert out["source_doc_id"] == ""


def test_router_unknown_doc_id_falls_back_to_direct(monkeypatch):
    """Guard: a source_scoped decision naming an UNKNOWN doc_id must NOT scope — fall back to direct."""
    _stub_route(monkeypatch, True, "not-a-real-doc-id")
    out = graph.router_node(fresh_state("per the Bogus SDS, what is X?"))
    assert out["route"] == "direct"
    assert out["source_doc_id"] == ""


def test_dense_search_source_filter_shape_and_kwargs(monkeypatch):
    """Additive source_doc_id filter: SAME {text, source_doc_id, page} dict contract; the filter kwarg
    is present only when set (a wrong filter key would silently return empty and misdiagnose the lever)."""
    from src import retrieve

    class _FakeIndex:
        last = None

        def query(self, **kw):
            _FakeIndex.last = kw
            return {"matches": [{"metadata": {"text": "t", "source_doc_id": "doc-a", "page": 3}}]}

    monkeypatch.setattr(retrieve, "_index", lambda: _FakeIndex())
    monkeypatch.setattr(retrieve, "_embedder", lambda: type("E", (), {"embed_query": lambda self, q: [0.1]})())

    out = retrieve.dense_search("q", k=5)
    assert "filter" not in _FakeIndex.last
    assert out == [{"text": "t", "source_doc_id": "doc-a", "page": 3}]

    out2 = retrieve.dense_search("q", k=5, source_doc_id="sds-sigma-aldrich-acetone")
    assert _FakeIndex.last["filter"] == {"source_doc_id": {"$eq": "sds-sigma-aldrich-acetone"}}
    assert set(out2[0]) == {"text", "source_doc_id", "page"}


def test_source_scoped_path_wires_through_the_graph(monkeypatch):
    """End-to-end: router -> source_scoped_retrieve (filtered dense_search) -> generate; trace records it."""
    _stub_route(monkeypatch, True, "sds-sigma-aldrich-acetone")
    captured = {}

    def fake_dense(q, k, source_doc_id=None):
        captured["source_doc_id"] = source_doc_id
        return [{"text": "Flash point : -17 C", "source_doc_id": "sds-sigma-aldrich-acetone", "page": 7}]

    monkeypatch.setattr(graph, "dense_search", fake_dense)
    monkeypatch.setattr(graph, "generate", lambda q, c: "-17 C")

    state = graph._compiled_graph().invoke(fresh_state("flash point of acetone per the Sigma-Aldrich SDS?"))
    assert state["route"] == "source_scoped"
    assert captured["source_doc_id"] == "sds-sigma-aldrich-acetone"  # the filter was actually applied
    assert any(n.startswith("retrieve[source_scoped:") for n in state["trace_notes"])
    assert state["answer"] == "-17 C"


# ---------------- 2D source-scoped EXECUTION fallback ----------------
# The router try/except guards CLASSIFICATION; source_scoped_retrieve_node guards EXECUTION — a
# distinct failure that only occurs on the live wire (the 4 scoped eval rows all return chunks, so the
# fallback never fires in the byte-repro). A filtered query that THROWS or returns EMPTY falls back to
# the direct full-corpus retrieve, DOWNGRADES route to "direct", and clears source_doc_id so the
# response never claims a single doc while citing others. A full-corpus answer beats a 500 or a blank.


def test_source_scoped_execution_error_falls_back_to_direct(monkeypatch):
    """Filtered dense_search THROWS -> direct full-corpus fallback; route downgraded, not an error."""
    _stub_route(monkeypatch, True, "sds-sigma-aldrich-acetone")

    def fake_dense(q, k, source_doc_id=None):
        if source_doc_id is not None:  # the filtered query fails on the wire
            raise RuntimeError("pinecone filter timeout")
        return list(CHUNKS)            # the unfiltered fallback succeeds

    monkeypatch.setattr(graph, "dense_search", fake_dense)
    gen = MagicMock(return_value="FALLBACK ANSWER")
    monkeypatch.setattr(graph, "generate", gen)

    state = graph._compiled_graph().invoke(fresh_state("flash point of acetone per the Sigma-Aldrich SDS?"))
    assert state["route"] == "direct"         # downgraded from source_scoped
    assert state["source_doc_id"] == ""        # cleared -> response won't claim a single doc
    assert state["retrieval_error"] is False   # the fallback succeeded; this is NOT the error sentinel
    assert state["retrieved"] == CHUNKS
    assert state["answer"] == "FALLBACK ANSWER"
    gen.assert_called_once()                   # a full-corpus answer was produced, not skipped
    notes = state["trace_notes"]
    assert any("direct fallback" in n for n in notes)                 # the reason breadcrumb
    assert any(n.startswith("retrieve[direct-fallback]:") for n in notes)


def test_source_scoped_empty_result_falls_back_to_direct(monkeypatch):
    """Filtered query returns ZERO chunks (valid doc_id, nothing matched) -> direct fallback. Asserted
    through ask() so the RESPONSE contract shows route/source_doc_id/routing_reason all downgraded."""
    _stub_route(monkeypatch, True, "sds-sigma-aldrich-acetone")

    def fake_dense(q, k, source_doc_id=None):
        return [] if source_doc_id is not None else list(CHUNKS)

    monkeypatch.setattr(graph, "dense_search", fake_dense)
    monkeypatch.setattr(graph, "generate", MagicMock(return_value="FALLBACK ANSWER"))

    out = graph.ask("flash point of acetone per the Sigma-Aldrich SDS?")
    assert out["route"] == "direct"
    assert out["source_doc_id"] == ""
    assert out["routing_reason"] is None       # no single-doc claim survives the fallback
    assert out["chunks"] == CHUNKS
    assert out["answer"] == "FALLBACK ANSWER"


def test_source_scoped_fallback_also_failing_sets_sentinel(monkeypatch):
    """If even the direct fallback throws, mirror retrieve_node: retrieval_error sentinel, generate
    SKIPPED (no LLM cost), empty answer — the endpoint still returns 200/empty, never a 500."""
    _stub_route(monkeypatch, True, "sds-sigma-aldrich-acetone")

    def always_boom(q, k, source_doc_id=None):
        raise RuntimeError("pinecone fully down")

    monkeypatch.setattr(graph, "dense_search", always_boom)
    gen = MagicMock(return_value="SHOULD NEVER RUN")
    monkeypatch.setattr(graph, "generate", gen)

    state = graph._compiled_graph().invoke(fresh_state("flash point of acetone per the Sigma-Aldrich SDS?"))
    assert state["retrieval_error"] is True
    assert state["route"] == "direct"
    assert state["retrieved"] == []
    assert state["answer"] == ""
    gen.assert_not_called()
