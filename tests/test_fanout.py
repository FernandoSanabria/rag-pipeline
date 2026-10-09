"""Hermetic tests for the G12 parallel fan-out — a dispatch-and-aggregate node, not a hierarchy of agents.

Comparison-worded direct questions go to one decomposer call; 2-3 sub-questions are dispatched with `Send`, each
branch runs one retrieval, and the join rank-interleaves and dedups before tool_decide. The router, decomposer, tool
loop, retrieval and generation are all stubbed: no network. The numbered sections follow R6 in eval/g12_design.md.
Rows are 1-based indexes into eval/dataset.jsonl.
"""

import json
import re
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent import graph
from agent.state import fresh_state
from src.retrieve import format_contexts

ROOT = Path(__file__).resolve().parents[1]
ROWS = [json.loads(line)["question"]
        for line in (ROOT / "eval" / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
K = graph.get_settings().retrieval_k
COMPARISON = "What is the exposure limit for chlorine under OSHA versus NIOSH?"
OSHA = {"text": "Chlorine 7782-50-5 (C)1 (C)3", "source_doc_id": "osha-1910-1000", "page": 16}
NIOSH = {"text": "Chlorine ... NIOSH REL: C 0.5 ppm", "source_doc_id": "niosh-pocket-guide", "page": 89}
TWO_PLANS = (("What is the exposure limit for chlorine under OSHA?", "osha-1910-1000"),
             ("What is the exposure limit for chlorine under NIOSH?", "niosh-pocket-guide"))


def row(n: int) -> str:
    return ROWS[n - 1]


def chunk(doc, page, text):
    return {"text": text, "source_doc_id": doc, "page": page}


def decomposition(*plans, comparison=True):
    return graph.Decomposition(comparison=comparison,
                               sub_questions=[graph.SubQuestion(question=q, source_doc_id=d) for q, d in plans])


class StubLLM:
    """Records every prompt; returns a fixed value or raises."""

    def __init__(self, value=None, exc=None):
        self.value, self.exc, self.calls = value, exc, []

    def invoke(self, prompt):
        self.calls.append(prompt)
        if self.exc is not None:
            raise self.exc
        return self.value


def install_decomposer(monkeypatch, value=None, exc=None) -> StubLLM:
    stub = StubLLM(value, exc)
    monkeypatch.setattr(graph, "_decomposer_llm", lambda: stub)
    return stub


class FakeSearch:
    """A recording dense_search: results by source_doc_id, optional failures and an optional sleep."""

    def __init__(self, by_doc=None, default=None, fail=(), sleep=0.0):
        self.by_doc, self.default, self.fail, self.sleep = by_doc or {}, default or [], set(fail), sleep
        self.calls, self._lock = [], threading.Lock()

    def __call__(self, query, k, source_doc_id=None):
        with self._lock:
            self.calls.append((query, k, source_doc_id))
        if self.sleep:
            time.sleep(self.sleep)
        if source_doc_id in self.fail or query in self.fail:
            raise ConnectionError("vector store unreachable")
        return list(self.by_doc.get(source_doc_id, self.default))


def install(monkeypatch, search, answer="STUB ANSWER") -> MagicMock:
    monkeypatch.setattr(graph, "dense_search", search)
    gen = MagicMock(return_value=answer)
    monkeypatch.setattr(graph, "generate", gen)
    return gen


class _Router:
    def __init__(self, decision):
        self.decision = decision

    def invoke(self, prompt):
        return self.decision


def route(monkeypatch, scoped=False, doc=None):
    monkeypatch.setattr(graph, "_router_llm", lambda: _Router(graph.RouteDecision(source_scoped=scoped, source_doc_id=doc)))


class _ToolLLM:
    def __init__(self, sequence=None):
        self.sequence, self.calls = list(sequence or []), 0

    def invoke(self, prompt):
        self.calls += 1
        calls = self.sequence.pop(0) if self.sequence else []
        return type("AIMsg", (), {"tool_calls": list(calls), "content": ""})()


@pytest.fixture(autouse=True)
def _direct_router_and_no_tools(monkeypatch):
    # The shipped graph has the fan-out switched OFF (agent.graph.FANOUT_ENABLED); these tests exercise the enabled build.
    monkeypatch.setattr(graph, "FANOUT_ENABLED", True)
    route(monkeypatch)
    tools = _ToolLLM()
    monkeypatch.setattr(graph, "_tool_llm", lambda: tools)
    return tools


def run(question):
    return graph._compiled_graph().invoke(fresh_state(question))


def fanout_notes(state):
    return [t for t in state["trace_notes"] if t.startswith(("decompose", "fanout[", "join"))]


# --- 1. The gate --------------------------------------------------------------------------------------------------


def test_wording_gate_matches_exactly_the_four_comparison_rows():
    assert [i for i, q in enumerate(ROWS, 1) if graph.COMPARISON_GATE.search(q)] == [9, 10, 11, 21]


@pytest.mark.parametrize("n", [9, 10, 11, 21])
def test_comparison_rows_fan_out(monkeypatch, n):
    decomposer = install_decomposer(monkeypatch, decomposition(*TWO_PLANS))
    install(monkeypatch, FakeSearch(by_doc={"osha-1910-1000": [OSHA], "niosh-pocket-guide": [NIOSH]}))
    state = run(row(n))
    assert len(decomposer.calls) == 1
    assert state["route"] == "decomposed"
    assert any(t.startswith("decompose: comparison -> 2 sub-questions") for t in state["trace_notes"])
    assert sum(t.startswith("fanout[") for t in state["trace_notes"]) == 2


@pytest.mark.parametrize("n, scoped_doc", [(1, None), (4, None), (16, None), (25, "sds-sigma-aldrich-acetone")])
def test_other_rows_never_call_the_decomposer(monkeypatch, n, scoped_doc):
    decomposer = install_decomposer(monkeypatch, decomposition(*TWO_PLANS))  # would fan out if it were asked
    if scoped_doc:
        route(monkeypatch, True, scoped_doc)
    chunks = [chunk(scoped_doc or "doc-a", 1, "a")]
    search = FakeSearch(by_doc={scoped_doc: chunks}, default=chunks)
    gen = install(monkeypatch, search)
    state = run(row(n))
    assert decomposer.calls == []
    assert fanout_notes(state) == []
    assert state["fanout"] == []
    assert state["route"] == ("source_scoped" if scoped_doc else "direct")
    assert search.calls == [(row(n), K, scoped_doc)]  # one retrieval, exactly as before G12
    gen.assert_called_once_with(row(n), format_contexts(chunks))


# --- 2. One branch per sub-question; the cap ----------------------------------------------------------------------


@pytest.mark.parametrize("count", [2, 3])
def test_one_branch_per_sub_question(monkeypatch, count):
    plans = [(f"Sub-question {i}?", None) for i in range(1, count + 1)]
    install_decomposer(monkeypatch, decomposition(*plans))
    search = FakeSearch(default=[chunk("doc-a", 1, "a")])
    install(monkeypatch, search)
    state = run(COMPARISON)
    assert sorted(search.calls) == sorted((q, K, None) for q, _ in plans)
    assert sorted(r["branch"] for r in state["fanout"]) == list(range(1, count + 1))


# --- 3. The join --------------------------------------------------------------------------------------------------


def test_join_rank_interleaves_then_dedups_by_content(monkeypatch):
    shared = chunk("niosh-pocket-guide", 89, "shared")
    by_doc = {"osha-1910-1000": [chunk("osha-1910-1000", 1, "a1"), shared, chunk("osha-1910-1000", 3, "a3")],
              "niosh-pocket-guide": [chunk("niosh-pocket-guide", 2, "b1"), shared,
                                     chunk("niosh-pocket-guide", 90, "shared")]}  # same text, other page: kept
    install_decomposer(monkeypatch, decomposition(*TWO_PLANS))
    gen = install(monkeypatch, FakeSearch(by_doc=by_doc))
    state = run(COMPARISON)
    assert [(c["text"], c["page"]) for c in state["retrieved"]] == [
        ("a1", 1), ("b1", 2), ("shared", 89), ("a3", 3), ("shared", 90)]
    assert "join: 2/2 branches ok -> 6 chunks, 5 after dedup (1 duplicates)" in state["trace_notes"]
    gen.assert_called_once_with(COMPARISON, format_contexts(state["retrieved"]))


# --- 4 and 5. Branch failures (retrieval time) ----------------------------------------------------------------------


def test_one_branch_raising_degrades_to_the_survivors(monkeypatch):
    install_decomposer(monkeypatch, decomposition(*TWO_PLANS))
    search = FakeSearch(by_doc={"osha-1910-1000": [OSHA], "niosh-pocket-guide": [NIOSH]}, fail={"niosh-pocket-guide"})
    gen = install(monkeypatch, search)
    state = run(COMPARISON)
    assert state["retrieval_error"] is False
    assert state["retrieved"] == [OSHA]
    gen.assert_called_once_with(COMPARISON, format_contexts([OSHA]))
    assert any(re.fullmatch(r"fanout\[2/2\] retrieve\(source_scoped:niosh-pocket-guide\) t\+\S+ -> ERROR ConnectionError", t)
               for t in state["trace_notes"])
    assert "join: 1/2 branches ok; failed fanout[2/2] ConnectionError -> 1 chunks, 1 after dedup (0 duplicates)" in (
        state["trace_notes"])


def test_all_branches_raising_takes_the_retrieval_error_path(monkeypatch, _direct_router_and_no_tools):
    install_decomposer(monkeypatch, decomposition(*TWO_PLANS))
    gen = install(monkeypatch, FakeSearch(fail={"osha-1910-1000", "niosh-pocket-guide"}))
    out = graph.ask(COMPARISON)
    gen.assert_not_called()
    assert _direct_router_and_no_tools.calls == 0  # tool_decide short-circuits on retrieval_error
    assert (out["answer"], out["contexts"], out["chunks"]) == ("", [], [])
    assert out["route"] == "decomposed"


# --- 6. Decomposition failing or declining never refuses ---------------------------------------------------------


@pytest.mark.parametrize("value, exc, note", [
    (None, RuntimeError("boom"), "decompose: ERROR RuntimeError -> single-query"),
    (None, None, "decompose: no structured output -> single-query"),
    (decomposition(comparison=False), None, "decompose: not a comparison -> single-query"),
    (decomposition(("Only one?", None)), None, "decompose: 1 sub-questions, outside 2..3 -> single-query"),
    (decomposition(*[(f"q{i}?", None) for i in range(4)]), None, "decompose: 4 sub-questions, outside 2..3 -> single-query"),
])
def test_decomposition_failure_falls_back_to_the_single_query_path(monkeypatch, value, exc, note):
    install_decomposer(monkeypatch, value, exc)
    chunks = [chunk("doc-a", 1, "a")]
    search = FakeSearch(default=chunks)
    gen = install(monkeypatch, search)
    state = run(COMPARISON)
    assert any(t.startswith(note) for t in state["trace_notes"])
    assert search.calls == [(COMPARISON, K, None)]
    assert state["route"] == "direct"
    assert state["fanout"] == []
    gen.assert_called_once_with(COMPARISON, format_contexts(chunks))


# --- 7. Concurrency (R5) --------------------------------------------------------------------------------------------


def test_fanout_branches_run_concurrently(monkeypatch):
    plans = [(f"Sub-question {i}?", None) for i in range(1, 4)]
    install_decomposer(monkeypatch, decomposition(*plans))
    install(monkeypatch, FakeSearch(default=[chunk("doc-a", 1, "a")], sleep=0.2))
    started = time.perf_counter()
    state = run(COMPARISON)
    wall = time.perf_counter() - started
    assert wall < 0.45, f"3 x 200 ms branches took {wall:.3f}s: not concurrent"
    intervals = [(r["t_start"], r["t_end"]) for r in state["fanout"]]
    assert len(intervals) == 3
    assert max(s for s, _ in intervals) < min(e for _, e in intervals)  # every branch overlaps every other
    assert all(re.fullmatch(r"fanout\[\d/3\] retrieve\(unscoped\) t\+\d+\.\d{3}s\.\.t\+\d+\.\d{3}s -> 1 chunks", t)
               for t in state["trace_notes"] if t.startswith("fanout["))


# --- 8. Byte-identity on every non-fan-out path ---------------------------------------------------------------------


def test_source_scoped_empty_fallback_path_is_unchanged(monkeypatch):
    route(monkeypatch, True, "sds-sigma-aldrich-acetone")
    decomposer = install_decomposer(monkeypatch, decomposition(*TWO_PLANS))
    chunks = [chunk("doc-a", 1, "a")]
    search = FakeSearch(by_doc={"sds-sigma-aldrich-acetone": []}, default=chunks)
    gen = install(monkeypatch, search)
    question = "Flash point of acetone per the Sigma-Aldrich SDS versus the NIOSH guide?"  # gate wording, scoped route
    state = run(question)
    assert decomposer.calls == []  # the gate applies to the direct route only
    assert search.calls == [(question, K, "sds-sigma-aldrich-acetone"), (question, K, None)]
    assert state["route"] == "direct" and fanout_notes(state) == []
    gen.assert_called_once_with(question, format_contexts(chunks))


def test_tool_loop_path_is_unchanged(monkeypatch):
    call = {"name": "ConvertExposureLimit",
            "args": {"value": 50, "from_unit": "ppm", "to_unit": "mg/m3", "substance": "ammonia"}, "id": "c1"}
    tools = _ToolLLM(sequence=[[call]])
    monkeypatch.setattr(graph, "_tool_llm", lambda: tools)
    chunks = [chunk("doc-a", 1, "a")]
    search = FakeSearch(default=chunks)
    gen = install(monkeypatch, search)
    state = run("convert 50 ppm ammonia to mg/m3")
    assert search.calls == [("convert 50 ppm ammonia to mg/m3", K, None)]
    assert fanout_notes(state) == [] and state["fanout"] == []
    assert state["retrieved"][0] == chunks[0] and state["retrieved"][1]["source_doc_id"] == "tool:ConvertExposureLimit"
    gen.assert_called_once_with("convert 50 ppm ammonia to mg/m3", format_contexts(state["retrieved"]))


# --- 9. Invariant (a): fresh state per invocation -------------------------------------------------------------------


def test_consecutive_asks_do_not_leak_fanout_state(monkeypatch):
    install_decomposer(monkeypatch, decomposition(*TWO_PLANS))
    install(monkeypatch, FakeSearch(by_doc={"osha-1910-1000": [OSHA], "niosh-pocket-guide": [NIOSH]},
                                    default=[chunk("doc-a", 1, "a")]))
    first = run(COMPARISON)
    second = run("What is the RMP threshold quantity for anhydrous ammonia?")
    assert len(first["fanout"]) == 2
    assert second["fanout"] == [] and second["sub_questions"] == []
    assert second["retrieved"] == [chunk("doc-a", 1, "a")]
    assert fanout_notes(second) == []


# --- 10. tool_decide still runs after the join ----------------------------------------------------------------------


def test_tool_decide_runs_after_the_join(monkeypatch):
    call = {"name": "ConvertExposureLimit",
            "args": {"value": 1, "from_unit": "ppm", "to_unit": "mg/m3", "substance": "chlorine"}, "id": "c1"}
    tools = _ToolLLM(sequence=[[call]])
    monkeypatch.setattr(graph, "_tool_llm", lambda: tools)
    install_decomposer(monkeypatch, decomposition(*TWO_PLANS))
    gen = install(monkeypatch, FakeSearch(by_doc={"osha-1910-1000": [OSHA], "niosh-pocket-guide": [NIOSH]}))
    state = run(COMPARISON)
    notes = state["trace_notes"]
    join_at = next(i for i, t in enumerate(notes) if t.startswith("join:"))
    decide_at = next(i for i, t in enumerate(notes) if t.startswith("tool_decide"))
    assert join_at < decide_at
    assert state["retrieved"][:2] == [OSHA, NIOSH]
    assert state["retrieved"][2]["source_doc_id"] == "tool:ConvertExposureLimit"
    gen.assert_called_once_with(COMPARISON, format_contexts(state["retrieved"]))


# --- 11. Validation of source_doc_id --------------------------------------------------------------------------------


def test_unknown_decomposer_source_id_runs_unscoped(monkeypatch):
    install_decomposer(monkeypatch, decomposition(("OSHA chlorine limit?", "osha-1910-1000"),
                                                  ("NIOSH chlorine limit?", "not-a-real-doc")))
    search = FakeSearch(by_doc={"osha-1910-1000": [OSHA]}, default=[NIOSH])
    install(monkeypatch, search)
    state = run(COMPARISON)
    assert ("NIOSH chlorine limit?", K, None) in search.calls
    assert "decompose: unknown source_doc_id 'not-a-real-doc' -> unscoped" in state["trace_notes"]


def test_an_injected_unknown_id_fails_its_branch_before_retrieval(monkeypatch):
    search = FakeSearch(default=[NIOSH])
    monkeypatch.setattr(graph, "dense_search", search)
    out = graph.branch_retrieve_node({"branch": 3, "n": 3, "question": "Injected?", "source_doc_id": "no-such-doc",
                                      "t0": time.monotonic()})
    record = out["fanout"][0]
    assert record["error"] == "ValueError" and record["chunks"] == []
    assert search.calls == []  # raised at validation, before any retrieval call
    assert out["trace_notes"][0].endswith("-> ERROR ValueError")


# --- Routing transparency ---------------------------------------------------------------------------------------------


def test_ask_reports_the_decomposed_route_and_its_sub_questions(monkeypatch):
    install_decomposer(monkeypatch, decomposition(*TWO_PLANS))
    install(monkeypatch, FakeSearch(by_doc={"osha-1910-1000": [OSHA], "niosh-pocket-guide": [NIOSH]}))
    out = graph.ask(COMPARISON)
    assert out["route"] == "decomposed"
    assert out["source_doc_id"] == ""
    assert out["routing_reason"] == (
        "Comparison split into 2 sub-questions: [osha-1910-1000] What is the exposure limit for chlorine under OSHA?; "
        "[niosh-pocket-guide] What is the exposure limit for chlorine under NIOSH?")
    assert out["chunks"] == [OSHA, NIOSH]


# --- The enabled build is the measured build ---------------------------------------------------------------------------

# The topology of agent/graph.py at the commit the live G12 run executed (eval/g12_PREDICTION.md, Outcome), recorded
# from scripts/render_graph.py on that commit. If the enabled build drifts from it, the measurements no longer describe it.
MEASURED_NODES = {"__start__", "__end__", "router", "retrieve", "source_scoped_retrieve", "decompose", "branch_retrieve",
                  "join", "tool_decide", "tool_exec", "generate"}
MEASURED_EDGES = {("__start__", "router"), ("branch_retrieve", "join"), ("decompose", "branch_retrieve"),
                  ("decompose", "retrieve"), ("generate", "__end__"), ("join", "tool_decide"), ("retrieve", "tool_decide"),
                  ("router", "decompose"), ("router", "retrieve"), ("router", "source_scoped_retrieve"),
                  ("source_scoped_retrieve", "tool_decide"), ("tool_decide", "generate"), ("tool_decide", "tool_exec"),
                  ("tool_exec", "tool_decide")}


# G10b inserted the approval gate between the tool loop and generation on every build. This REPLACES
# test_enabled_graph_topology_matches_the_measured_build: the enabled build is now the measured G12 build plus exactly the
# review gate's nodes and edges (the G12 measurements describe its non-firing path, where review_gate is a no-op).
REVIEW_NODES = {"review_gate", "review_wait"}
REVIEW_EDGES = {("tool_decide", "review_gate"), ("review_gate", "generate"), ("review_gate", "review_wait"),
                ("review_gate", "__end__"), ("review_wait", "generate"), ("review_wait", "__end__")}


def test_enabled_graph_topology_is_the_measured_build_plus_the_review_gate():
    g = graph._build_graph(fanout=True).get_graph()
    assert set(g.nodes) == MEASURED_NODES | REVIEW_NODES
    assert {(e.source, e.target) for e in g.edges} == (MEASURED_EDGES - {("tool_decide", "generate")}) | REVIEW_EDGES


# --- 12. The frozen decomposer prompt, schema and gate ----------------------------------------------------------------


def _registered(info: bytes) -> bytes:
    text = (ROOT / "eval" / "g12_PREDICTION.md").read_bytes()
    blocks = re.findall(rb"^```" + info + rb"\n(.*?)\n```$", text, flags=re.S | re.M)
    assert len(blocks) == 1
    return blocks[0]


def test_decomposer_prompt_is_byte_identical_to_the_preregistered_one():
    assert graph.DECOMPOSER_PROMPT.encode("utf-8") == _registered(b"text decomposer-prompt")


def test_decomposer_schema_descriptions_match_the_preregistration():
    lines = [f"{model.__name__}.{name}: {field.description}"
             for model in (graph.SubQuestion, graph.Decomposition) for name, field in model.model_fields.items()]
    assert "\n".join(lines).encode("utf-8") == _registered(b"text decomposer-schema")


def test_wording_gate_is_the_preregistered_pattern():
    registered = re.findall(rb"^```text\n(\\b\(compare.*?)\n```$", (ROOT / "eval" / "g12_PREDICTION.md").read_bytes(),
                            flags=re.S | re.M)
    assert registered == [graph.COMPARISON_GATE.pattern.encode("utf-8")]
    assert graph.COMPARISON_GATE.flags & re.IGNORECASE
