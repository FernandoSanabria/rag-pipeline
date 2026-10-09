"""Hermetic tests for the G10b approval gate (agent/review.py, agent/graph.py, api/main.py; eval/g10b_design.md R7).

No network, no LLM: the router, the tool decision, retrieval and generation are stubbed at agent.graph's module-bound
names; the checkpointer is a real SqliteSaver on a tmp file. The trigger policy is switched back on here (conftest.py
keeps it off for every other test). Rows are 1-based.
"""

import json
import logging
import operator
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent import graph, review
from agent.state import RemoveChunk, Reset, chunk_key, fresh_state, retrieved_reducer
from src.retrieve import format_contexts

ROOT = Path(__file__).resolve().parents[1]
REAL_TRIGGER = review.trigger_reason  # captured at import, before conftest's autouse fixture turns the policy off

TRIGGER_Q = "What is the exposure limit for chlorine under OSHA versus NIOSH?"   # frozen row 10
PLAIN_Q = "What is the RMP threshold quantity for anhydrous ammonia?"              # frozen row 1
CHUNKS = [
    {"text": "OSHA chlorine C 1 ppm (3 mg/m3)", "source_doc_id": "osha-1910-1000", "page": 5},
    {"text": "NIOSH chlorine REL C 0.5 ppm", "source_doc_id": "niosh-pocket-guide", "page": 66},
    {"text": "an unrelated low-ranked chunk", "source_doc_id": "osha-1910-1000", "page": 41},
]


class _Static:
    def __init__(self, value):
        self.value = value

    def invoke(self, prompt):
        return self.value


@pytest.fixture
def stubs(monkeypatch):
    """Router -> direct, no tool calls, fixed retrieval, a recording generate."""
    monkeypatch.setattr(graph, "_router_llm", lambda: _Static(graph.RouteDecision(source_scoped=False,
                                                                                   source_doc_id=None)))
    monkeypatch.setattr(graph, "_tool_llm", lambda: _Static(type("R", (), {"tool_calls": []})()))
    monkeypatch.setattr(graph, "dense_search", lambda q, k, source_doc_id=None: [dict(c) for c in CHUNKS])
    gen = MagicMock(return_value="The ceiling is 1 ppm (3 mg/m3) per OSHA and 0.5 ppm per NIOSH.")
    monkeypatch.setattr(graph, "generate", gen)
    return gen


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setattr(review, "trigger_reason", REAL_TRIGGER)


@pytest.fixture
def store(tmp_path, monkeypatch, gate_on):
    monkeypatch.setenv("REVIEW_DB_PATH", str(tmp_path / "review.sqlite"))
    return review.checkpointer()


def _threads(saver) -> set[str]:
    return {c.config["configurable"]["thread_id"] for c in saver.list(None)}


# --- R1: the trigger policy, on the registered sets (P1's deterministic part) ----------------------------------------


def _questions(path, keep=lambda r: True):
    return [json.loads(line)["question"] for line in (ROOT / path).read_text().splitlines()
            if line.strip() and keep(json.loads(line))]


def test_the_policy_fires_on_exactly_the_registered_frozen_rows_and_nothing_else():
    frozen = _questions("eval/dataset.jsonl")
    fired = [n for n, q in enumerate(frozen, 1) if REAL_TRIGGER(q, [])]
    assert fired == [9, 10, 11]
    smoke = [r["row"] for r in json.loads((ROOT / "eval/smoke_set.json").read_text())["rows"]]
    assert not set(fired) & set(smoke)                                                        # 0/8 smoke rows
    assert not any(REAL_TRIGGER(q, []) for q in _questions("eval/capability_set.jsonl"))       # 0/3
    hard = _questions("eval/guardrail_set.jsonl", keep=lambda r: r["category"] == "hard_negative")
    assert len(hard) == 9 and not any(REAL_TRIGGER(q, []) for q in hard)                     # 0/9
    assert {REAL_TRIGGER(frozen[n - 1], []) for n in fired} == {"exposure_limit_named"}


def test_the_scoped_fallback_disjunct_reads_the_path_record():
    assert REAL_TRIGGER("boiling point per the Nutrien SDS?", ["retrieve[direct-fallback]: dense_search(...)"]) \
        == "scoped_fallback"
    assert REAL_TRIGGER("boiling point per the Nutrien SDS?", ["retrieve[source_scoped:x]: ... -> 8 chunks"]) is None


def test_scoped_fallback_fires_the_gate_end_to_end(stubs, store, monkeypatch):
    monkeypatch.setattr(graph, "_router_llm", lambda: _Static(graph.RouteDecision(
        source_scoped=True, source_doc_id="sds-nutrien-anhydrous-ammonia")))
    monkeypatch.setattr(graph, "dense_search",
                        lambda q, k, source_doc_id=None: [] if source_doc_id else [dict(c) for c in CHUNKS])
    out = graph.ask("What is the boiling point of anhydrous ammonia per the Nutrien SDS?")
    assert out["status"] == "pending_review" and out["reason"] == "scoped_fallback" and out["route"] == "direct"
    stubs.assert_not_called()


# --- the gate on the graph -------------------------------------------------------------------------------------------


def test_the_gate_pauses_a_trigger_row_and_passes_a_plain_row(stubs, store):
    paused = graph.ask(TRIGGER_Q)
    assert paused["status"] == "pending_review" and paused["answer"] == ""
    assert paused["contexts"] == format_contexts(CHUNKS)
    stubs.assert_not_called()
    notes = graph._compiled_graph().get_state(graph._thread_config(paused["thread_id"])).values["trace_notes"]
    assert f"review: paused (exposure_limit_named) thread={paused['thread_id']}" in notes
    assert paused["thread_id"] in _threads(store)

    plain = graph.ask(PLAIN_Q)
    assert "status" not in plain and plain["answer"].startswith("The ceiling")
    stubs.assert_called_once_with(PLAIN_Q, format_contexts(CHUNKS))
    assert _threads(store) == {paused["thread_id"]}  # the finished plain thread was deleted at once


def test_approve_generates_on_exactly_the_paused_contexts(stubs, store):
    paused = graph.ask(TRIGGER_Q)
    out = graph.resume(paused["thread_id"], "approve")
    assert out["status"] == "answered" and out["recorded"] is False
    stubs.assert_called_once_with(TRIGGER_Q, paused["contexts"])
    notes = graph._compiled_graph().get_state(graph._thread_config(paused["thread_id"])).values["trace_notes"]
    assert "review: resumed op=approve removals=0 additions=0" in notes


def test_reject_refuses_with_no_generate_call(stubs, store):
    paused = graph.ask(TRIGGER_Q)
    out = graph.resume(paused["thread_id"], "reject")
    assert out["status"] == "rejected" and out["answer"] == ""
    stubs.assert_not_called()


def test_amend_removes_exactly_one_chunk_and_nothing_else(stubs, store):
    paused = graph.ask(TRIGGER_Q)
    lowest = paused["chunks"][-1]  # the lowest-ranked document chunk (P2's amend)
    out = graph.resume(paused["thread_id"], "amend", [chunk_key(lowest)])
    assert out["status"] == "answered"
    stubs.assert_called_once_with(TRIGGER_Q, format_contexts(CHUNKS[:-1]))
    record = graph._compiled_graph().get_state(graph._thread_config(paused["thread_id"])).values["review"]
    assert record["status"] == "amended" and record["removals"] == [chunk_key(lowest)]


def test_amend_with_an_unknown_key_is_invalid_and_changes_nothing(stubs, store):
    paused = graph.ask(TRIGGER_Q)
    out = graph.resume(paused["thread_id"], "amend", ["0" * 64])
    assert out["status"] == "invalid"
    assert graph.review_status(paused["thread_id"])["status"] == "pending"
    stubs.assert_not_called()


def test_reset_and_additions_replace_the_set_at_the_reducer_and_graph_level(stubs, store):
    """Not exposed by the API (no authentication; eval/g10b_design.md), but the reducer and the graph support it."""
    paused = graph.ask(TRIGGER_Q)
    addition = {"text": "a reviewer-supplied passage", "source_doc_id": "osha-1910-1000", "page": 7}
    g, config = graph._compiled_graph(), graph._thread_config(paused["thread_id"])
    g.update_state(config, {"retrieved": [Reset(), addition], "review": {"status": "amended"}}, as_node="review_wait")
    g.invoke(None, config, durability="exit")
    stubs.assert_called_once_with(TRIGGER_Q, format_contexts([addition]))


def test_expired_refuses_deletes_and_logs(stubs, store, monkeypatch, caplog):
    paused = graph.ask(TRIGGER_Q)
    later = review.now_utc() + timedelta(seconds=review.ttl_seconds() + 1)
    monkeypatch.setattr(review, "now_utc", lambda: later)
    with caplog.at_level(logging.INFO, logger="agent.graph"):
        out = graph.resume(paused["thread_id"], "approve")
    assert out["status"] == "expired_or_lost"
    stubs.assert_not_called()
    assert paused["thread_id"] not in _threads(store)
    assert f"review: expired thread={paused['thread_id']}" in caplog.text
    assert TRIGGER_Q not in caplog.text  # the question text is not logged at INFO


def test_a_short_ttl_is_honoured(stubs, store, monkeypatch):
    monkeypatch.setenv("REVIEW_TTL_S", "5")
    paused = graph.ask(TRIGGER_Q)
    record = graph.review_status(paused["thread_id"])
    span = review.datetime.strptime(record["expires_at"], "%Y-%m-%dT%H:%M:%SZ") - \
        review.datetime.strptime(paused["expires_at"], "%Y-%m-%dT%H:%M:%SZ")
    assert record["status"] == "pending" and span.total_seconds() == 0
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", paused["expires_at"])  # absolute UTC ISO-8601


def test_a_second_resume_returns_the_recorded_resolution_and_never_generates_twice(stubs, store):
    paused = graph.ask(TRIGGER_Q)
    first = graph.resume(paused["thread_id"], "approve")
    second = graph.resume(paused["thread_id"], "reject")  # a different op cannot change a resolved review
    assert second["status"] == "answered" and second["recorded"] is True and second["answer"] == first["answer"]
    stubs.assert_called_once()
    rejected = graph.ask(TRIGGER_Q)
    graph.resume(rejected["thread_id"], "reject")
    again = graph.resume(rejected["thread_id"], "approve")
    assert again["status"] == "rejected" and again["recorded"] is True
    assert stubs.call_count == 1


def test_an_unknown_thread_is_expired_or_lost(stubs, store):
    assert graph.resume("8f14e45f-ceea-4e5a-9f1b-2f2d2f1a0c11", "approve")["status"] == "expired_or_lost"
    assert graph.review_status("8f14e45f-ceea-4e5a-9f1b-2f2d2f1a0c11")["status"] == "expired_or_lost"
    stubs.assert_not_called()


def test_no_checkpointer_refuses_a_trigger_row_instead_of_answering(stubs, gate_on):
    out = graph.ask(TRIGGER_Q)
    assert out["status"] == "review_unavailable" and out["answer"] == ""
    stubs.assert_not_called()
    assert graph.resume("8f14e45f-ceea-4e5a-9f1b-2f2d2f1a0c11", "approve")["status"] == "expired_or_lost"


def test_the_pause_survives_a_process_restart(stubs, store, tmp_path):
    """Graph A pauses on a SQLite file; a FRESH PROCESS compiles graph B on the same file and resumes it."""
    paused = graph.ask(TRIGGER_Q)
    script = f"""
import os, warnings
warnings.filterwarnings("ignore")
os.environ.update(OPENAI_API_KEY="test", PINECONE_API_KEY="test", REVIEW_DB_PATH={str(tmp_path / "review.sqlite")!r})
from agent import graph
seen = []
graph.generate = lambda q, c: seen.append(c) or "resumed in a new process"
out = graph.resume({paused["thread_id"]!r}, "approve")
print(out["status"], "|", out["answer"], "|", seen == [{paused["contexts"]!r}])
"""
    proc = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "answered | resumed in a new process | True"
    stubs.assert_not_called()  # this process never generated


def test_each_ask_runs_on_a_fresh_uuid4_thread_and_there_is_no_default(stubs, store, monkeypatch):
    seen = []
    real = graph._compiled_graph()

    class Spy:
        def invoke(self, state, config=None, **kwargs):
            seen.append(config["configurable"]["thread_id"])
            return real.invoke(state, config, **kwargs)

    monkeypatch.setattr(graph, "_compiled_graph", lambda: Spy())
    graph.ask(PLAIN_Q)
    graph.ask(PLAIN_Q)
    assert len(set(seen)) == 2
    assert all(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", t) for t in seen)
    for path in ("agent/graph.py", "agent/review.py", "api/main.py"):
        source = (ROOT / path).read_text()
        assert not re.search(r"""["']thread_id["']\s*:\s*["']""", source), path  # never a literal thread id


def test_the_startup_sweep_deletes_expired_and_orphaned_threads_only(stubs, store, monkeypatch):
    fresh = graph.ask(TRIGGER_Q)["thread_id"]
    old = graph.ask(TRIGGER_Q)["thread_id"]
    orphan = "0b4e7a0e-5e2b-4c1a-9a7e-3f1d2c3b4a59"
    graph._compiled_graph().invoke(fresh_state(PLAIN_Q), graph._thread_config(orphan) | {}, durability="exit")
    g, cfg = graph._compiled_graph(), graph._thread_config(old)
    record = g.get_state(cfg).values["review"]
    g.update_state(cfg, {"review": {**record, "expires_at": "2000-01-01T00:00:00Z"}}, as_node="review_gate")
    assert graph.sweep_expired() == 2
    assert _threads(store) == {fresh}


# --- R3: the reducer is operator.add on every existing path ----------------------------------------------------------


@pytest.mark.parametrize("sequence", [
    [[]], [[{"a": 1}]], [[{"a": 1}, {"b": 2}], [{"c": 3}]], [CHUNKS, [{"text": "COMPUTED", "source_doc_id": "tool:x",
                                                                         "page": None}]],
])
def test_the_reducer_equals_operator_add_on_plain_lists(sequence):
    new, old = [], []
    for update in sequence:
        new, old = retrieved_reducer(new, update), operator.add(old, update)
    assert new == old and [id(x) for x in new] == [id(x) for x in old]


def test_the_sentinels_apply_in_order():
    a, b, c = ({"text": t, "source_doc_id": "d", "page": 1} for t in "abc")
    assert retrieved_reducer([a, b, c], [RemoveChunk(chunk_key(b))]) == [a, c]
    assert retrieved_reducer([a, b], [Reset(), c]) == [c]
    assert retrieved_reducer([a], [RemoveChunk("0" * 64)]) == [a]
    assert chunk_key(a) == chunk_key(dict(a)) and chunk_key(a) != chunk_key(b)


def _old_reducer_graph(fanout=False):
    """The same builder, compiled again, with its `retrieved` channel's operator set back to operator.add (pre-G10b).
    Each run copies the channel template with its operator (langgraph channels/binop.py from_checkpoint)."""
    built = graph._build_graph.__wrapped__(fanout)
    assert built.channels["retrieved"].operator is retrieved_reducer
    built.channels["retrieved"].operator = operator.add
    return built


@pytest.mark.parametrize("path", ["direct", "scoped", "fallback", "tool_loop", "fanout"])
def test_generate_sees_identical_calls_under_the_new_and_the_old_reducer(path, monkeypatch, stubs):
    question = "What is the exposure limit for chlorine under OSHA versus NIOSH?"
    if path in ("scoped", "fallback"):
        monkeypatch.setattr(graph, "_router_llm", lambda: _Static(graph.RouteDecision(
            source_scoped=True, source_doc_id="sds-nutrien-anhydrous-ammonia")))
    if path == "fallback":
        monkeypatch.setattr(graph, "dense_search",
                            lambda q, k, source_doc_id=None: [] if source_doc_id else [dict(c) for c in CHUNKS])
    if path == "tool_loop":
        call = {"name": "ConvertExposureLimit",  # the G1 tests' valid call: it appends a tool chunk to `retrieved`
                "args": {"value": 50, "from_unit": "ppm", "to_unit": "mg/m3", "substance": "ammonia"}, "id": "c1"}
        calls = iter([[call], []] * 4)
        monkeypatch.setattr(graph, "_tool_llm", lambda: type("T", (), {
            "invoke": lambda self, p: type("R", (), {"tool_calls": next(calls)})()})())
    if path == "fanout":
        decomposition = graph.Decomposition(comparison=True, sub_questions=[
            graph.SubQuestion(question="OSHA chlorine limit?", source_doc_id="osha-1910-1000"),
            graph.SubQuestion(question="NIOSH chlorine limit?", source_doc_id="niosh-pocket-guide")])
        monkeypatch.setattr(graph, "_decomposer_llm", lambda: _Static(decomposition))
    new = graph._build_graph.__wrapped__(path == "fanout")
    old = _old_reducer_graph(fanout=(path == "fanout"))
    new.invoke(fresh_state(question))
    if path == "tool_loop":
        calls = iter([[call], []] * 4)
    old.invoke(fresh_state(question))
    assert stubs.call_count == 2
    assert stubs.call_args_list[0] == stubs.call_args_list[1]
    contexts = stubs.call_args_list[0].args[1]
    if path == "tool_loop":  # the second writer really wrote: a tool chunk follows the documents
        assert any("COMPUTED by ConvertExposureLimit" in c for c in contexts)
    if path == "fanout":
        assert len(contexts) == len({c for c in contexts})  # the join's dedup ran on the new reducer too


# --- the API (R4) ------------------------------------------------------------------------------------------------------


@pytest.fixture
def client():
    from api import main

    return TestClient(main.app)


def test_post_ask_agent_returns_202_with_the_evidence(stubs, store, client):
    r = client.post("/ask/agent", json={"question": TRIGGER_Q})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "pending_review" and body["reason"] == "exposure_limit_named"
    assert [e["key"] for e in body["evidence"]] == [chunk_key(c) for c in CHUNKS]
    assert body["evidence"][0]["title"] and body["evidence"][0]["text"] == CHUNKS[0]["text"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["expires_at"])
    assert client.get(f"/ask/agent/review/{body['thread_id']}").json()["status"] == "pending"
    stubs.assert_not_called()


def test_resume_approve_reject_amend_through_the_api(stubs, store, client):
    t1 = client.post("/ask/agent", json={"question": TRIGGER_Q}).json()
    ok = client.post("/ask/agent/resume", json={"thread_id": t1["thread_id"], "op": "approve"})
    assert ok.status_code == 200 and ok.json()["guard"] is None and ok.json()["confidence_score"] == 0.9
    assert client.get(f"/ask/agent/review/{t1['thread_id']}").json()["status"] == "approved"

    t2 = client.post("/ask/agent", json={"question": TRIGGER_Q}).json()
    no = client.post("/ask/agent/resume", json={"thread_id": t2["thread_id"], "op": "reject"}).json()
    assert no["guard"] == {"stage": "review", "reason": "rejected"} and no["citations"] == []

    t3 = client.post("/ask/agent", json={"question": TRIGGER_Q}).json()
    key = t3["evidence"][-1]["key"]
    am = client.post("/ask/agent/resume", json={"thread_id": t3["thread_id"], "op": "amend", "removals": [key]})
    assert am.status_code == 200 and am.json()["guard"] is None
    assert stubs.call_args_list[-1].args == (TRIGGER_Q, format_contexts(CHUNKS[:-1]))
    assert stubs.call_count == 2  # approve + amend; reject generated nothing


def test_the_output_guard_still_applies_after_approval(stubs, store, client):
    """Refinement C: review is not a bypass. An approved answer with a figure no context states is withheld."""
    stubs.return_value = "The ceiling is 7.77 ppm."
    t = client.post("/ask/agent", json={"question": TRIGGER_Q}).json()
    body = client.post("/ask/agent/resume", json={"thread_id": t["thread_id"], "op": "approve"}).json()
    assert body["guard"] == {"stage": "output", "reason": "untraceable_numbers"}


@pytest.mark.parametrize("payload", [
    {"thread_id": "not-a-uuid", "op": "approve"},
    {"thread_id": "8f14e45f-ceea-4e5a-9f1b-2f2d2f1a0c11", "op": "approve", "removals": ["0" * 64]},
    {"thread_id": "8f14e45f-ceea-4e5a-9f1b-2f2d2f1a0c11", "op": "amend"},
    {"thread_id": "8f14e45f-ceea-4e5a-9f1b-2f2d2f1a0c11", "op": "amend", "removals": ["xyz"]},
    {"thread_id": "8f14e45f-ceea-4e5a-9f1b-2f2d2f1a0c11", "op": "approve", "additions": [{"text": "x"}]},
    {"thread_id": "8f14e45f-ceea-4e5a-9f1b-2f2d2f1a0c11", "op": "delete"},
])
def test_malformed_resumes_are_422(store, client, payload):
    if "additions" in payload:  # additions are not part of the API: the field is ignored, never applied
        r = client.post("/ask/agent/resume", json=payload)
        assert r.status_code == 200 and r.json()["guard"]["reason"] == "expired_or_lost"
        return
    assert client.post("/ask/agent/resume", json=payload).status_code == 422


def test_unknown_thread_resume_is_a_200_refusal_and_an_unknown_amend_key_is_422(stubs, store, client):
    r = client.post("/ask/agent/resume", json={"thread_id": "8f14e45f-ceea-4e5a-9f1b-2f2d2f1a0c11", "op": "approve"})
    assert r.status_code == 200 and r.json()["guard"] == {"stage": "review", "reason": "expired_or_lost"}
    t = client.post("/ask/agent", json={"question": TRIGGER_Q}).json()
    bad = client.post("/ask/agent/resume", json={"thread_id": t["thread_id"], "op": "amend", "removals": ["a" * 64]})
    assert bad.status_code == 422
    assert client.get("/ask/agent/review/not-a-uuid").status_code == 422


def test_review_unavailable_is_a_200_refusal_through_the_api(stubs, gate_on, client):
    body = client.post("/ask/agent", json={"question": TRIGGER_Q}).json()
    assert body["guard"] == {"stage": "review", "reason": "review_unavailable"} and body["answer"]
    stubs.assert_not_called()


def test_a_plain_question_is_unchanged_through_the_api(stubs, store, client):
    r = client.post("/ask/agent", json={"question": PLAIN_Q})
    assert r.status_code == 200 and set(r.json()) == {"answer", "citations", "confidence_score", "confidence_basis",
                                                      "guard", "route", "source_doc_id", "routing_reason"}
