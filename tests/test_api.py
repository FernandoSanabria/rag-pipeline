"""Integration test for /ask and /health — ask() is STUBBED (no real LLM, no network).

Tests the WIRING only: that the endpoint assembles the response contract, maps confidence + citations
correctly, and returns the right status codes. Answer QUALITY is the eval harness's job, not this suite's.
"""

from fastapi.testclient import TestClient

from agent import graph as agent_graph
from api import main

client = TestClient(main.app)


def _stub(result):
    return lambda question: result


def _stub_agent(monkeypatch, result):
    """Stub the agent entry for /ask/agent. The handler imports `agent.graph.ask` LAZILY (module note
    in api/main.py), so there is no `main.agent_ask` attribute to patch — patch it at the source, which
    the lazy `from agent.graph import ask` picks up at call time."""
    monkeypatch.setattr(agent_graph, "ask", _stub(result))


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ask_answered_maps_high_and_citations(monkeypatch):
    monkeypatch.setattr(main, "ask", _stub({
        "answer": "The OSHA PEL for anhydrous ammonia is 50 ppm.",
        "contexts": ["[source_doc_id=osha-1910-119 page=7]\n..."],
        "chunks": [{"source_doc_id": "osha-1910-119", "page": 7, "text": "..."}],
    }))
    r = client.post("/ask", json={"question": "What is the OSHA PEL for anhydrous ammonia?"})
    assert r.status_code == 200
    body = r.json()
    assert body["confidence_score"] == 0.9
    assert body["confidence_basis"].startswith("high")
    assert len(body["citations"]) == 1
    assert body["citations"][0]["page"] == 7
    assert isinstance(body["citations"][0]["document"], str) and body["citations"][0]["document"]


def test_ask_refusal_maps_low_and_empty_citations(monkeypatch):
    monkeypatch.setattr(main, "ask", _stub({
        "answer": "The provided context does not contain the answer.",
        "contexts": ["[source_doc_id=sds-sigma-aldrich-acetone page=1]\n..."],
        "chunks": [{"source_doc_id": "sds-sigma-aldrich-acetone", "page": 1, "text": "..."}],
    }))
    r = client.post("/ask", json={"question": "What is the flash point of acetone?"})
    assert r.status_code == 200
    body = r.json()
    assert body["confidence_score"] == 0.25
    assert body["confidence_basis"].startswith("low")
    assert body["citations"] == []


def test_ask_blank_question_returns_422():
    assert client.post("/ask", json={"question": "  "}).status_code == 422


def test_ask_missing_question_returns_422():
    assert client.post("/ask", json={}).status_code == 422


# ---- /ask/agent — routing transparency (agent_ask stubbed; no LLM, no network) ------------------
# agent_ask returns the same {answer, contexts, chunks} as /ask PLUS {route, source_doc_id,
# routing_reason}. These assert the endpoint reuses the shared confidence/citation assembly AND
# surfaces the route fields. Answer quality + real routing are the eval harness's job, not this suite's.


def test_ask_agent_direct_surfaces_route_and_reuses_assembly(monkeypatch):
    # A comparison (e.g. the shipped IDLH row) routes DIRECT: route=direct, no source_doc_id/reason.
    _stub_agent(monkeypatch, {
        "answer": "The NIOSH IDLH is 300 ppm; the EPA RMP endpoint is 200 ppm.",
        "contexts": ["[source_doc_id=niosh-pocket-guide page=45]\n..."],
        "chunks": [{"source_doc_id": "niosh-pocket-guide", "page": 45, "text": "..."}],
        "route": "direct", "source_doc_id": "", "routing_reason": None,
    })
    r = client.post("/ask/agent", json={"question": "How does the NIOSH IDLH compare to the EPA endpoint?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "direct"
    assert body["source_doc_id"] is None  # "" downgraded to null in the response
    assert body["routing_reason"] is None
    # shared assembly still applies: high confidence + a citation
    assert body["confidence_score"] == 0.9
    assert body["citations"][0]["page"] == 45


def test_ask_agent_source_scoped_surfaces_doc_and_reason(monkeypatch):
    # A single-document question ("per the Sigma-Aldrich SDS") routes SOURCE_SCOPED.
    _stub_agent(monkeypatch, {
        "answer": "The flash point of acetone is -17.0 C (closed cup).",
        "contexts": ["[source_doc_id=sds-sigma-aldrich-acetone page=7]\n..."],
        "chunks": [{"source_doc_id": "sds-sigma-aldrich-acetone", "page": 7, "text": "..."}],
        "route": "source_scoped", "source_doc_id": "sds-sigma-aldrich-acetone",
        "routing_reason": "Question attributed to a single named document: Acetone SDS",
    })
    r = client.post("/ask/agent", json={"question": "Flash point of acetone per the Sigma-Aldrich SDS?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "source_scoped"
    assert body["source_doc_id"] == "sds-sigma-aldrich-acetone"
    assert isinstance(body["routing_reason"], str) and body["routing_reason"]
    assert body["confidence_score"] == 0.9
    assert body["citations"][0]["document"]  # manifest title, not a raw id


def test_ask_agent_refusal_maps_low_and_empty_citations(monkeypatch):
    # The shared assembly must behave identically to /ask on a refusal, whatever the route.
    _stub_agent(monkeypatch, {
        "answer": "The provided context does not contain the answer.",
        "contexts": [], "chunks": [{"source_doc_id": "sds-sigma-aldrich-acetone", "page": 1, "text": "..."}],
        "route": "direct", "source_doc_id": "", "routing_reason": None,
    })
    r = client.post("/ask/agent", json={"question": "What is the meaning of life?"})
    assert r.status_code == 200
    body = r.json()
    assert body["confidence_score"] == 0.25
    assert body["citations"] == []


def test_ask_agent_blank_question_returns_422():
    assert client.post("/ask/agent", json={"question": "  "}).status_code == 422
