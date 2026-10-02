"""Hermetic tests for the G5 MCP server (mcp_server/): no secrets, no network.

`dense_search` is replaced at the ONE seam (`mcp_server.server.dense_search`, the same convention as
`agent.graph.dense_search`), and the manifest is the committed local file. Every tool call goes through a real
MCP client session over the SDK's in-memory transport, so these assert what an external client actually
receives — schemas, `isError`, `structuredContent` — not just what the Python functions return.
"""

import json
from pathlib import Path

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from agent import tools as agent_tools
from mcp_server import policy, server
from src.config import Settings

SEARCH, LOOKUP = "search_safety_docs", "lookup_document_metadata"
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "manifest.json"
MANIFEST = {d["doc_id"]: d for d in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["docs"]}


class FakeSearch:
    """Stands in for dense_search: records every call, returns canned chunks or raises."""

    def __init__(self):
        self.chunks: list[dict] = []
        self.exc: Exception | None = None
        self.calls: list[dict] = []

    def __call__(self, query, k, source_doc_id=None):
        self.calls.append({"query": query, "k": k, "source_doc_id": source_doc_id})
        if self.exc is not None:
            raise self.exc
        return [dict(c) for c in self.chunks]


@pytest.fixture
def fake(monkeypatch):
    fake_search = FakeSearch()
    monkeypatch.setattr(server, "dense_search", fake_search)
    return fake_search


def _session(action):
    """Run `action(session)` inside a connected in-memory MCP client session; return its result."""

    async def main():
        async with create_connected_server_and_client_session(server.mcp) as session:
            return await action(session)

    return anyio.run(main)


def call(name, args):
    return _session(lambda session: session.call_tool(name, args))


def error_detail(result, tool):
    """Assert the error contract (isError, one text block, no rows) and return the parsed JSON detail."""
    assert result.isError is True
    assert result.structuredContent is None
    (block,) = result.content
    prefix = f"Error executing tool {tool}: "
    assert block.text.startswith(prefix)
    return json.loads(block.text[len(prefix):])


def chunk(doc_id, page=1, text=None):
    # Pinecone returns metadata numbers as floats, so the fake does too.
    return {"text": f"chunk text from {doc_id}" if text is None else text, "source_doc_id": doc_id, "page": float(page)}


# --- 1. discovery --------------------------------------------------------------------------------------------


def test_discovery_lists_exactly_two_tools_with_the_contract_schemas():
    tools = {t.name: t for t in _session(lambda session: session.list_tools()).tools}
    assert set(tools) == {SEARCH, LOOKUP}

    # k: default == maximum == the shipped evaluated depth (the Settings field default, not the env).
    depth = Settings.model_fields["retrieval_k"].default
    search_in = tools[SEARCH].inputSchema
    k = search_in["properties"]["k"]
    assert k["default"] == k["maximum"] == depth == 10
    assert (k["type"], k["minimum"]) == ("integer", 1)
    query = search_in["properties"]["query"]  # api.schemas.Question: a flat string, not a nested model
    assert (query["type"], query["minLength"], query["maxLength"]) == ("string", 3, 1000)
    assert search_in["required"] == ["query"]
    assert search_in["properties"]["source_doc_id"]["default"] is None

    row = tools[SEARCH].outputSchema["$defs"]["SearchResult"]
    assert set(row["required"]) == {
        "kind", "text", "source_doc_id", "title", "publisher", "page", "tier", "license", "rank",
    }
    assert row["properties"]["kind"]["enum"] == ["document", "computed"]
    assert "score" not in row["properties"]

    assert tools[LOOKUP].inputSchema["required"] == ["source_doc_id"]
    assert set(tools[LOOKUP].outputSchema["required"]) == {
        "doc_id", "title", "publisher", "tier", "license", "source_url", "revision_date",
    }
    for tool in tools.values():
        hints = tool.annotations
        assert (hints.readOnlyHint, hints.idempotentHint, hints.openWorldHint) == (True, True, False)


# --- 2. provenance on every row ------------------------------------------------------------------------------


def test_every_manifest_doc_gets_full_text_and_manifest_provenance(fake):
    fake.chunks = [chunk(doc_id, page=i + 1) for i, doc_id in enumerate(MANIFEST)]
    result = call(SEARCH, {"query": "hazardous energy control"})
    assert result.isError is False
    rows = result.structuredContent["results"]
    assert len(rows) == len(MANIFEST) == 19
    for rank, (row, doc_id) in enumerate(zip(rows, MANIFEST), start=1):
        doc = MANIFEST[doc_id]
        assert (row["kind"], row["rank"], row["page"]) == ("document", rank, rank)
        assert row["text"] == f"chunk text from {doc_id}"  # full text under Option A, non-empty
        assert (row["source_doc_id"], row["title"], row["publisher"], row["tier"], row["license"]) == (
            doc_id, doc["title"], doc["publisher"], doc["tier"], doc["license"],
        )
        assert row["title"] != doc["filename"] and not row["title"].endswith(".pdf")
    assert {row["tier"] for row in rows} == {1, 2}


# --- 3–4. the licensing policy table -------------------------------------------------------------------------


def test_configured_policy_is_full_text_for_both_tiers():
    assert policy.TEXT_POLICY == {1: "full", 2: "full"}


@pytest.mark.parametrize("mode, expected", [("excerpt", "x" * policy.EXCERPT_CHARS + "…"), ("none", "")])
def test_policy_table_is_live(fake, monkeypatch, mode, expected):
    long_text = "x" * (policy.EXCERPT_CHARS + 100)
    fake.chunks = [chunk("osha-1910-147", text=long_text), chunk("sds-sigma-aldrich-acetone", text=long_text)]
    monkeypatch.setitem(policy.TEXT_POLICY, 2, mode)
    tier1, tier2 = call(SEARCH, {"query": "lockout tagout"}).structuredContent["results"]
    assert tier1["text"] == long_text  # tier 1 is still "full"
    assert tier2["text"] == expected  # the table drives the output
    acetone = MANIFEST["sds-sigma-aldrich-acetone"]
    assert (tier2["tier"], tier2["license"], tier2["title"]) == (2, acetone["license"], acetone["title"])


# --- 5. pass-through ------------------------------------------------------------------------------------------


def test_filter_and_k_pass_through_unchanged_and_omitted_k_is_the_evaluated_depth(fake):
    call(SEARCH, {"query": "acetone flash point", "k": 7, "source_doc_id": "sds-sigma-aldrich-acetone"})
    call(SEARCH, {"query": "acetone flash point"})
    assert fake.calls == [
        {"query": "acetone flash point", "k": 7, "source_doc_id": "sds-sigma-aldrich-acetone"},
        {"query": "acetone flash point", "k": 10, "source_doc_id": None},
    ]


# --- 6–9. the error contract ----------------------------------------------------------------------------------


def test_unknown_source_doc_id_is_a_structured_error_before_any_retrieval(fake):
    fake.chunks = [chunk("osha-1910-119")]
    detail = error_detail(call(LOOKUP, {"source_doc_id": "not-a-doc"}), LOOKUP)
    assert (detail["code"], detail["source_doc_id"]) == ("unknown_source_doc_id", "not-a-doc")
    detail = error_detail(call(SEARCH, {"query": "anything at all", "source_doc_id": "not-a-doc"}), SEARCH)
    assert detail["code"] == "unknown_source_doc_id"
    assert fake.calls == []  # rejected before dense_search: no embedding call


@pytest.mark.parametrize(
    "args",
    [{"query": "lockout tagout", "k": 11}, {"query": "lockout tagout", "k": 0}, {"query": "   "}],
)
def test_out_of_range_arguments_are_rejected_not_clamped(fake, args):
    result = call(SEARCH, args)
    assert result.isError is True and result.structuredContent is None
    assert result.content[0].text.startswith(f"Error executing tool {SEARCH}: ")
    assert fake.calls == []


def test_retrieval_failure_is_a_structured_error_with_zero_rows(fake):
    fake.exc = RuntimeError("pinecone 401: api key sk-secret-internal-detail")
    result = call(SEARCH, {"query": "lockout tagout"})
    detail = error_detail(result, SEARCH)
    assert detail["code"] == "retrieval_failed"
    assert "RuntimeError" in detail["message"]
    text = result.content[0].text
    assert "secret-internal-detail" not in text and "Traceback" not in text


@pytest.mark.parametrize(
    "bad",
    [chunk("ghost-doc"), {"text": "t", "source_doc_id": "osha-1910-119", "page": None}],
    ids=["unknown-doc", "missing-page"],
)
def test_unresolvable_provenance_fails_the_whole_call_closed(fake, bad):
    fake.chunks = [chunk("osha-1910-119"), bad]  # a good row first: it must NOT come back on its own
    detail = error_detail(call(SEARCH, {"query": "process safety management"}), SEARCH)
    assert detail["code"] == "provenance_unavailable"


# --- 10. lookup -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("doc_id", ["osha-1910-119", "sds-sigma-aldrich-acetone"])
def test_lookup_returns_exactly_the_agent_tool_shape(doc_id):
    result = call(LOOKUP, {"source_doc_id": doc_id})
    assert result.isError is False
    assert result.structuredContent == agent_tools.lookup_document_metadata(doc_id)
