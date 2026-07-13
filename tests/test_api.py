"""Integration test for /ask and /health — ask() is STUBBED (no real LLM, no network).

Tests the WIRING only: that the endpoint assembles the response contract, maps confidence + citations
correctly, and returns the right status codes. Answer QUALITY is the eval harness's job, not this suite's.
"""

from fastapi.testclient import TestClient

from api import main

client = TestClient(main.app)


def _stub(result):
    return lambda question: result


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
