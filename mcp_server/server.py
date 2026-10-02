"""G5 MCP server: two read-only tools over the industrial-equipment-safety corpus. Contract: CONTRACT.md.

`search_safety_docs` calls the frozen `src.retrieve.dense_search` — the ONE mock seam: tests patch
`mcp_server.server.dense_search`, the same convention as `agent.graph.dense_search` — and attaches manifest
provenance to every row through `agent.tools.lookup_document_metadata`, never a filename.
`lookup_document_metadata` re-exports that G1 agent tool unchanged. Nothing here reimplements retrieval.

Every failure is a structured tool error: the tool raises `ToolError` with a JSON detail
`{"code", "message", ...}`, the SDK returns it as `isError: true`, and no row is ever returned with it. The tool
bodies catch everything, so no raw exception text or traceback reaches the client; the full exception goes to
the server log on stderr (stdout is the stdio protocol channel). See CONTRACT.md, "Error contract".
"""

import json
import logging
from typing import Annotated, Literal

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from agent import tools as agent_tools
from api.schemas import Question
from mcp_server import policy
from src.config import get_settings
from src.retrieve import dense_search

log = logging.getLogger(__name__)

SERVER_NAME = "equip-docs-rag"

# k defaults to and is capped at the evaluated retrieval depth. One source for both bounds: the cap moves with
# the configured depth (RETRIEVAL_K), and a request above it is rejected by schema validation, never clamped.
RETRIEVAL_DEPTH = get_settings().retrieval_k

INSTRUCTIONS = (
    "Read-only search over an industrial-equipment-safety corpus: OSHA, EPA and NIOSH regulations and guidance "
    "(tier 1, public-domain) and vendor safety data sheets and equipment manuals (tier 2, vendor-copyrighted). "
    "search_safety_docs returns verbatim document text; every result carries kind='document' plus its source "
    "title, page, tier and license, so cite title and page. lookup_document_metadata returns one document's "
    "provenance."
)

mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)


class SearchResult(BaseModel):
    """One retrieved chunk with its provenance."""

    kind: Literal["document", "computed"] = Field(
        description="'document' = verbatim text of the cited page. 'computed' is reserved for tool-derived "
        "values and is never emitted by this server."
    )
    text: str = Field(description="chunk text as the licensing policy renders it for this tier")
    source_doc_id: str
    title: str = Field(description="manifest title, never a filename")
    publisher: str
    page: int = Field(ge=1, description="1-based page, from chunk metadata")
    tier: int = Field(description="manifest licensing tier: 1 = public-domain, 2 = vendor-copyrighted")
    license: str = Field(description="manifest license string, reported verbatim, not certified")
    rank: int = Field(ge=1, description="1-based position in similarity order; no raw score is exposed")


class SearchResults(BaseModel):
    results: list[SearchResult]


class DocumentMetadata(BaseModel):
    """Provenance for one corpus document, exactly as the G1 agent tool returns it."""

    doc_id: str
    title: str
    publisher: str
    tier: int
    license: str
    source_url: str | None
    revision_date: None = Field(
        description="always null: the manifest records no revision date, and the tool never guesses one"
    )


def _tool_error(code: str, message: str, **context: object) -> ToolError:
    """The error contract: a JSON detail that the SDK returns as `isError: true`."""
    return ToolError(json.dumps({"code": code, "message": message, **context}))


def _metadata(source_doc_id: str) -> dict:
    """Manifest provenance via the G1 agent tool; an unknown id is an `unknown_source_doc_id` error."""
    try:
        return agent_tools.lookup_document_metadata(source_doc_id)
    except agent_tools.ToolError:
        raise _tool_error(
            "unknown_source_doc_id",
            f"no document {source_doc_id!r} in the manifest",
            source_doc_id=source_doc_id,
        ) from None


def _page(value: object) -> int:
    """Chunk metadata stores 1-based pages; Pinecone returns numbers as floats (44.0). Never truncate 44.7."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise ValueError(f"page {value!r} is not a whole number")
    return int(value)


def _result(rank: int, chunk: dict, docs: dict[str, dict]) -> SearchResult:
    """One retrieved chunk -> one result with manifest provenance. Anything unresolvable fails the call closed."""
    doc_id = chunk.get("source_doc_id")
    try:
        meta = docs.get(doc_id) or docs.setdefault(doc_id, agent_tools.lookup_document_metadata(doc_id))
        return SearchResult(
            kind="document",
            text=policy.render_text(meta["tier"], chunk.get("text") or ""),
            source_doc_id=doc_id,
            title=meta["title"],
            publisher=meta["publisher"],
            page=_page(chunk.get("page")),
            tier=meta["tier"],
            license=meta["license"],
            rank=rank,
        )
    # unknown doc (ToolError), tier missing from the policy (KeyError), bad page or failed model validation
    # (ValueError, which pydantic's ValidationError subclasses), wrong field types (TypeError)
    except (agent_tools.ToolError, KeyError, TypeError, ValueError) as exc:
        raise _tool_error(
            "provenance_unavailable",
            f"retrieved chunk at rank {rank} has no resolvable provenance ({type(exc).__name__}); "
            "no results are returned",
            source_doc_id=doc_id,
        ) from None


@mcp.tool(annotations=READ_ONLY)
async def search_safety_docs(
    query: Annotated[Question, Field(description="natural-language search query, 3 to 1000 characters")],
    k: Annotated[
        int,
        Field(
            ge=1,
            le=RETRIEVAL_DEPTH,
            description="number of results; defaults to and is capped at the evaluated retrieval depth",
        ),
    ] = RETRIEVAL_DEPTH,
    source_doc_id: Annotated[
        str | None,
        Field(description="restrict the search to one corpus document (see lookup_document_metadata)"),
    ] = None,
) -> SearchResults:
    """Search the industrial-equipment-safety corpus and return the most similar document chunks.

    Every result is verbatim document text (kind='document') with its provenance: manifest title, publisher,
    1-based page, licensing tier and license. Results come in similarity order (rank 1 first); no raw
    similarity score is exposed.
    """
    try:
        if source_doc_id is not None:
            _metadata(source_doc_id)  # an unknown id fails here, before retrieval: no embedding call
        try:
            chunks = await anyio.to_thread.run_sync(
                lambda: dense_search(query, k=k, source_doc_id=source_doc_id)
            )
        except Exception as exc:
            log.exception("search_safety_docs: dense_search raised")
            raise _tool_error(
                "retrieval_failed", f"retrieval raised {type(exc).__name__}; no results are returned"
            ) from None
        docs: dict[str, dict] = {}
        return SearchResults(results=[_result(rank, c, docs) for rank, c in enumerate(chunks, start=1)])
    except ToolError:
        raise
    except Exception as exc:
        log.exception("search_safety_docs: unexpected error")
        raise _tool_error("internal_error", f"unexpected {type(exc).__name__}; no results are returned") from None


@mcp.tool(annotations=READ_ONLY)
async def lookup_document_metadata(
    source_doc_id: Annotated[str, Field(description="corpus document id, e.g. 'sds-sigma-aldrich-acetone'")],
) -> DocumentMetadata:
    """Return one corpus document's provenance from the manifest: title, publisher, licensing tier, license and
    source URL. revision_date is always null: it is not recorded, and the tool never guesses one.
    """
    try:
        return DocumentMetadata(**_metadata(source_doc_id))
    except ToolError:
        raise
    except Exception as exc:
        log.exception("lookup_document_metadata: unexpected error")
        raise _tool_error("internal_error", f"unexpected {type(exc).__name__}") from None
