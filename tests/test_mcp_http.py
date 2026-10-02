"""Hermetic tests for the G5 MCP server's HTTP transport at /mcp on the FastAPI app: no secrets, no network.

One module-scoped TestClient runs the app's lifespan once, because the SDK's session manager can run only once
per process. `initialize` needs no retrieval, so nothing here calls OpenAI or Pinecone.
"""

import pytest
from fastapi.testclient import TestClient

from api import main

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}},
}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture(scope="module")
def client():
    # Host "localhost:8000" matches the allow-list's "localhost:*"; a bare "testserver" would be rejected.
    with TestClient(main.app, base_url="http://localhost:8000") as test_client:
        yield test_client


def test_initialize_over_http_returns_the_server_info(client):
    response = client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["serverInfo"]["name"] == "equip-docs-rag"
    assert result["protocolVersion"] == "2025-11-25"


@pytest.mark.parametrize("host, status", [("equip-docs-rag-api.onrender.com", 200), ("evil.example", 421)])
def test_host_allow_list_admits_the_deployed_host_and_rejects_others(client, host, status):
    response = client.post("/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "host": host})
    assert response.status_code == status


def test_missing_accept_header_is_a_406(client):
    # The signature the wire-smoke's negative proof relies on: no Accept header -> 406, which it classifies hard.
    response = client.post("/mcp", json=INITIALIZE, headers={"content-type": "application/json"})
    assert response.status_code == 406


def test_unknown_routes_keep_fastapi_json_404(client):
    # /mcp is added as a Route, not a Mount, so FastAPI still answers unknown paths itself.
    response = client.get("/definitely-not-a-route")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
