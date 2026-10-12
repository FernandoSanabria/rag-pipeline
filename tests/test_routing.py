"""Hermetic tests for G8's routing policy (agent/routing.py; eval/g8_design.md R3, R5 and R6; P3 and P7 by design).

No network. Signals per row are G11's recorded ones: rows 22–25 source-scoped, tools fired on rows 9 and 21.
OpenAI requests are captured by a recording fake transport; Bedrock is a stub client.
"""

import json
from pathlib import Path

import httpx
import pytest

import src.generate as frozen
from agent import generation, graph, routing
from agent.state import fresh_state
from api import main as api_main
from api.confidence import is_refusal
from src.retrieve import format_contexts

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [json.loads(line)["question"] for line in (ROOT / "eval" / "dataset.jsonl").read_text().splitlines()
             if line.strip()]
SCOPED, TOOLS = {22, 23, 24, 25}, {9, 21}
REFUSAL = "The provided context does not contain the answer."
CHUNKS = [{"text": f"synthetic chunk {p}: the flash point is -17 C", "source_doc_id": "sds-sigma-aldrich-acetone",
           "page": p} for p in range(1, 4)]


@pytest.fixture(autouse=True)
def _reset():
    yield
    frozen.reset_generation_backends()
    generation.reset_calls()


def _state(n: int) -> dict:
    state = fresh_state(QUESTIONS[n - 1])
    state["route"] = "source_scoped" if n in SCOPED else "direct"
    state["source_doc_id"] = "sds-sigma-aldrich-acetone" if n in SCOPED else ""
    state["tool_results"] = [{"tool": "CompareThresholds", "args": {}, "result": {}}] if n in TOOLS else []
    state["retrieved"] = [dict(c) for c in CHUNKS]
    return state


class _StubBedrock:
    def __init__(self, text="Nova Micro answer."):
        self.text, self.kwargs = text, []

    def converse(self, **kwargs):
        self.kwargs.append(kwargs)
        return {"output": {"message": {"role": "assistant", "content": [{"text": self.text}]}},
                "stopReason": "end_turn", "usage": {"inputTokens": 300, "outputTokens": 9, "totalTokens": 309},
                "metrics": {"latencyMs": 200}, "ResponseMetadata": {"RequestId": "req", "HTTPStatusCode": 200}}


# --- the policy ---------------------------------------------------------------------------------------------------------


def test_routing_ships_off_with_nova_micro_as_the_cheap_model():
    assert routing.ROUTING_ENABLED is False and routing.enabled() is False
    assert routing.CHEAP_MODEL == "us.amazon.nova-micro-v1:0"


def test_policy_a_is_eligible_on_exactly_rows_22_to_25():
    eligible = {n for n in range(1, 29) if routing.eligible(_state(n))[0]}
    assert len(QUESTIONS) == 28 and eligible == {22, 23, 24, 25}


def test_each_signal_excludes_on_its_own():
    state = _state(25)
    assert routing.eligible(state) == (True, "source_scoped, no tool, no comparison, no exposure limit")
    assert routing.eligible({**state, "route": "direct"})[1] == "route=direct"
    assert routing.eligible({**state, "tool_results": [{"tool": "x"}]})[1] == "a tool ran"
    assert routing.eligible({**state, "question": "How does the SDS flash point compare to NIOSH's?"})[1] == "comparison"
    assert routing.eligible({**state, "question": "What is the PEL per the Airgas SDS?"})[1] == "exposure_limit_named"


# --- the generate node: trace and generator ------------------------------------------------------------------------------


def test_the_generate_node_names_its_routing_decision(monkeypatch):
    stub = _StubBedrock()
    monkeypatch.setattr(generation, "bedrock_client", lambda: stub)
    monkeypatch.setattr(graph, "generate", lambda q, c: "openai answer")
    out = graph.generate_node(_state(25))
    assert out["answer"] == "openai answer" and "routing: off" in out["trace_notes"]
    monkeypatch.setattr(routing, "ROUTING_ENABLED", True)
    out = graph.generate_node(_state(25))
    assert out["answer"] == "Nova Micro answer." and f"routing: eligible -> {routing.CHEAP_MODEL}" in out["trace_notes"]
    [kwargs] = stub.kwargs
    assert kwargs["modelId"] == routing.CHEAP_MODEL and kwargs["inferenceConfig"] == {"temperature": 0}
    contexts = format_contexts(CHUNKS)
    assert kwargs["messages"][0]["content"][0]["text"] == generation.human_message(QUESTIONS[24], contexts)
    out = graph.generate_node(_state(9))
    assert out["answer"] == "openai answer" and "routing: not eligible (route=direct)" in out["trace_notes"]
    assert len(stub.kwargs) == 1  # the ineligible row never reached Bedrock


# --- P7 by design: the OpenAI request body ---------------------------------------------------------------------------------


@pytest.fixture
def captured_bodies(monkeypatch):
    bodies = []

    def send(self, request, *args, **kwargs):
        bodies.append(request.content)
        return httpx.Response(200, request=request, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "gpt-4o-mini-2024-07-18",
            "system_fingerprint": "fp_test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.delenv("GENERATION_PROVIDER", raising=False)
    return bodies


@pytest.mark.parametrize("enabled", [False, True])
def test_the_openai_body_is_byte_identical_unless_a_row_is_routed(captured_bodies, monkeypatch, enabled):
    """Routing OFF: all 28 rows send today's body (P7a). Routing ON: the 24 non-eligible rows still do (P7b's design),
    and the 4 eligible rows send nothing to OpenAI."""
    monkeypatch.setattr(routing, "ROUTING_ENABLED", enabled)
    stub = _StubBedrock()
    monkeypatch.setattr(generation, "bedrock_client", lambda: stub)
    for n in range(1, 29):
        state = _state(n)
        start = len(captured_bodies)
        frozen.generate(QUESTIONS[n - 1], format_contexts(state["retrieved"]))
        graph.generate_node(state)
        sent = captured_bodies[start:]
        if enabled and n in SCOPED:
            assert len(sent) == 1, f"row {n}: an eligible row must not call OpenAI"
        else:
            today, node = sent
            assert today == node, f"row {n}: request bodies differ"
    assert len(stub.kwargs) == (4 if enabled else 0)


# --- R5: a routed request still meets the output guard and the refusal gate ------------------------------------------------


def _scoped_graph(monkeypatch, text):
    monkeypatch.setattr(routing, "ROUTING_ENABLED", True)
    monkeypatch.setattr(generation, "bedrock_client", lambda: _StubBedrock(text))

    class Router:
        def invoke(self, prompt):
            return graph.RouteDecision(source_scoped=True, source_doc_id="sds-sigma-aldrich-acetone")

    class Tools:
        def invoke(self, prompt):
            return type("Msg", (), {"tool_calls": []})()

    monkeypatch.setattr(graph, "_router_llm", lambda: Router())
    monkeypatch.setattr(graph, "_tool_llm", lambda: Tools())
    monkeypatch.setattr(graph, "dense_search", lambda q, k, source_doc_id=None: [dict(c) for c in CHUNKS])


def test_a_routed_answer_with_an_untraceable_figure_is_withheld(monkeypatch):
    _scoped_graph(monkeypatch, "The flash point is 999 C.")
    fields, result = api_main._answer(QUESTIONS[24], graph.ask)
    assert result["answer"] == "The flash point is 999 C."
    assert fields["guard"] is not None and fields["guard"].stage == "output"


def test_a_routed_exact_refusal_is_a_refusal_and_a_hedge_is_not(monkeypatch):
    _scoped_graph(monkeypatch, REFUSAL)
    fields, result = api_main._answer(QUESTIONS[24], graph.ask)
    assert is_refusal(result["answer"]) and fields["confidence_score"] == 0.25 and fields["citations"] == []
    _scoped_graph(monkeypatch, "The SDS excerpt lacks it; NIOSH lists -17 C.\n\n" + REFUSAL)
    fields, result = api_main._answer(QUESTIONS[24], graph.ask)
    assert not is_refusal(result["answer"]) and fields["confidence_score"] == 0.9  # the recorded limitation
