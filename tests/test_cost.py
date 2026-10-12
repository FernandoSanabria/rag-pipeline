"""Hermetic tests for G8's per-request cost accounting (agent/cost.py; eval/g8_design.md R1 and R6).

No network. OpenAI calls go through a fake transport (httpx.Client.send patched) that answers each call type (router,
tool decision, generation) with canned usage, so the real ChatOpenAI objects and the real callback hook are exercised.
Bedrock is a stub client, and retrieval is stubbed with synthetic chunks.
"""

import json
import logging
import re
import threading
import uuid
from pathlib import Path

import httpx
import pytest

import src.pipeline as pipeline
from agent import cost, generation, graph
from agent.state import fresh_state
from api import main as api_main
from api.schemas import ResumeRequest

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "What purge gas pressure does the sensor manual allow?"
ANSWER = "Keep the purge gas pressure below 7.25 psi."
CHUNKS = [{"text": f"synthetic chunk {p}: keep the purge gas pressure below 7.25 psi", "source_doc_id": "doc-a",
           "page": p} for p in range(1, 4)]
USAGE = {"router": (900, 0, 20), "tool": (7000, 4096, 50), "generate": (6000, 5120, 80)}  # prompt, cached, completion


def _kind(body: dict) -> str:
    if "response_format" in body:
        return "router"
    return "tool" if "tools" in body else "generate"


@pytest.fixture
def fake_openai(monkeypatch):
    """Every OpenAI chat call returns canned usage for its kind; the list records the call order."""
    calls = []

    def send(self, request, *args, **kwargs):
        kind = _kind(json.loads(request.content))
        calls.append(kind)
        prompt, cached, completion = USAGE[kind]
        content = {"router": '{"source_scoped": false, "source_doc_id": null}', "tool": "No tool is needed.",
                   "generate": ANSWER}[kind]
        return httpx.Response(200, request=request, json={
            "id": f"chatcmpl-{kind}", "object": "chat.completion", "created": 0, "model": "gpt-4o-mini-2024-07-18",
            "system_fingerprint": "fp_test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
                      "prompt_tokens_details": {"cached_tokens": cached}}})

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.delenv("GENERATION_PROVIDER", raising=False)
    monkeypatch.setattr(graph, "dense_search", lambda q, k, source_doc_id=None: [dict(c) for c in CHUNKS])
    monkeypatch.setattr(pipeline, "dense_search", lambda q, k: [dict(c) for c in CHUNKS])
    return calls


def _cost_line(caplog) -> dict:
    [line] = [r.getMessage() for r in caplog.records if r.name == "agent.cost"]
    assert line.startswith("cost ")
    return json.loads(line.removeprefix("cost "))


def _list(prompt, completion):
    return (prompt * 0.15 + completion * 0.60) / 1e6


def _billed(prompt, cached, completion):
    return ((prompt - cached) * 0.15 + cached * 0.075 + completion * 0.60) / 1e6


# --- the agent request's record ---------------------------------------------------------------------------------------


def test_an_agent_request_is_accounted_by_component_and_logged_without_text(fake_openai, caplog):
    caplog.set_level(logging.INFO, logger="agent.cost")
    fields, result = api_main._answer(QUESTION, graph.ask)
    assert fake_openai == ["router", "tool", "generate"] and result["answer"] == ANSWER
    record = _cost_line(caplog)
    parts = record["components"]
    assert record["endpoint"] == "/ask/agent" and record["routing"] == "off" and record["unpriced_calls"] == 0
    for component, kind in (("router", "router"), ("tool_decide", "tool"), ("generate", "generate")):
        prompt, cached, completion = USAGE[kind]
        part = parts[component]
        assert (part["calls"], part["input"], part["cached"], part["output"]) == (1, prompt, cached, completion)
        assert part["models"] == ["gpt-4o-mini-2024-07-18"]
        assert part["usd_list"] == pytest.approx(_list(prompt, completion), abs=1e-8)
        assert part["usd_billed"] == pytest.approx(_billed(prompt, cached, completion), abs=1e-8)
    assert parts["embedding"]["calls"] == 1 and parts["embedding"]["derived"] is True
    assert parts["embedding"]["models"] == ["text-embedding-3-small"]
    for key in ("calls", "input", "cached", "output"):  # the total is the sum of the parts
        assert record["total"][key] == sum(part[key] for part in parts.values())
    for key in ("usd_list", "usd_billed"):
        assert record["total"][key] == pytest.approx(sum(part[key] for part in parts.values()), abs=1e-7)
    line = json.dumps(record)
    assert QUESTION not in line and ANSWER not in line  # never question or answer text


def test_the_generate_node_appends_the_record_to_trace_notes(fake_openai):
    with cost.request("/ask/agent"):
        state = graph._compiled_graph().invoke(fresh_state(QUESTION),
                                               {"configurable": {"thread_id": "t", "review_durable": False}})
    notes = state["trace_notes"]
    assert "routing: off" in notes
    [note] = [n for n in notes if n.startswith("cost: $")]
    assert "router 1" in note and "tool_decide 1" in note and "generate 1" in note and "embedding 1" in note


def test_the_ask_endpoint_records_one_generation_and_one_embedding(fake_openai, caplog):
    caplog.set_level(logging.INFO, logger="agent.cost")
    api_main._answer(QUESTION, api_main.ask)
    record = _cost_line(caplog)
    parts = record["components"]
    assert record["endpoint"] == "/ask" and fake_openai == ["generate"]
    assert parts["generate"]["calls"] == 1 and parts["embedding"]["calls"] == 1
    assert parts["router"]["calls"] == parts["tool_decide"]["calls"] == 0


def test_the_resume_endpoint_logs_its_own_record(monkeypatch, caplog):
    """A paused question's generation is billed on resume, which does not go through _answer."""
    def fake_resume(thread_id, op, removals):
        with cost.phase("generate"):
            cost.add_call(None, "gpt-4o-mini-2024-07-18", 5000, 0, 60)
        return {"status": "answered", "answer": "", "contexts": [], "chunks": [], "route": "direct",
                "source_doc_id": "", "routing_reason": None, "question": "q"}

    monkeypatch.setattr(graph, "resume", fake_resume)
    caplog.set_level(logging.INFO, logger="agent.cost")
    api_main.resume_review(ResumeRequest(thread_id=uuid.uuid4(), op="approve"))
    record = _cost_line(caplog)
    assert record["endpoint"] == "/ask/agent/resume" and record["status"] == "answered"
    assert record["components"]["generate"]["calls"] == 1
    assert record["total"]["usd_list"] == pytest.approx(_list(5000, 60), abs=1e-8)


# --- prices, Bedrock and isolation --------------------------------------------------------------------------------------


def test_prices_equal_the_cost_ledgers_table():
    labels = {"`gpt-4o-mini`": "gpt-4o-mini", "`text-embedding-3-small`": "text-embedding-3-small",
              "Amazon Nova 2 Lite (Bedrock, the `us.` profile)": "us.amazon.nova-2-lite-v1:0",
              "Amazon Nova Micro (Bedrock, the `us.` profile)": "us.amazon.nova-micro-v1:0"}
    money = lambda cell: None if cell.strip() == "—" else float(re.match(r"\s*\$([\d.]+) / 1M", cell).group(1))
    found = {}
    for line in (ROOT / "eval" / "COST_LEDGER.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in labels:
            found[labels[cells[0]]] = (money(cells[1]), money(cells[2]), money(cells[3]))
    assert set(found) == set(labels.values())
    for model, (input_price, cached_price, output_price) in found.items():
        row = cost.PRICES[model]
        assert (row["input"], row["cached"], row["output"] or None) == (input_price, cached_price, output_price or None)


def test_a_dated_openai_snapshot_is_priced_and_an_unknown_model_is_counted_unpriced():
    assert cost.price_for("gpt-4o-mini-2024-07-18") is cost.PRICES["gpt-4o-mini"]
    assert cost.usd("text-embedding-3-small", 1000, 0, 0) == pytest.approx((0.00002, 0.00002))
    record = cost.RequestCost("/ask")
    record.add("generate", "some-other-model", 100, 0, 10)
    assert record.unpriced == 1 and record.total()["usd_list"] == 0.0


class _StubBedrock:
    def __init__(self, text="Nova answer."):
        self.text, self.kwargs = text, []

    def converse(self, **kwargs):
        self.kwargs.append(kwargs)
        return {"output": {"message": {"role": "assistant", "content": [{"text": self.text}]}},
                "stopReason": "end_turn", "usage": {"inputTokens": 4000, "outputTokens": 50, "totalTokens": 4050},
                "metrics": {"latencyMs": 300}, "ResponseMetadata": {"RequestId": "req", "HTTPStatusCode": 200}}


def test_a_bedrock_generation_is_priced_at_its_own_model():
    with cost.request("/ask/agent") as record, cost.phase("generate"):
        generation.generate_with(_StubBedrock(), "q", ["ctx"], model_id="us.amazon.nova-micro-v1:0")
    part = record.as_dict()["components"]["generate"]
    assert part["models"] == ["us.amazon.nova-micro-v1:0"] and (part["input"], part["output"]) == (4000, 50)
    assert part["usd_list"] == pytest.approx((4000 * 0.035 + 50 * 0.14) / 1e6)
    generation.reset_calls()


def test_outside_a_request_nothing_is_recorded_and_nothing_fails():
    assert cost.current() is None and cost.trace_note() == "cost: no request record"
    cost.add_call("generate", "gpt-4o-mini", 1, 0, 1)
    cost.add_embedding("q")
    assert cost.current() is None


def test_a_nested_request_reuses_the_active_record():
    with cost.request("/ask/agent") as outer, cost.request("inner") as inner:
        assert inner is outer


def test_concurrent_requests_keep_separate_records():
    barrier, results = threading.Barrier(2), {}

    def work(name, n):
        with cost.request(name) as record:
            barrier.wait()
            for _ in range(n):
                cost.add_call("generate", "gpt-4o-mini", 100, 0, 10)
            barrier.wait()
            results[name] = record.as_dict()["components"]["generate"]["calls"]

    threads = [threading.Thread(target=work, args=("a", 3)), threading.Thread(target=work, args=("b", 5))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert results == {"a": 3, "b": 5}
