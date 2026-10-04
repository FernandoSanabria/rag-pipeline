"""FastAPI service wrapping the frozen RAG pipeline (`src.pipeline.ask`).

Three derived layers sit on top of `ask()` without touching retrieval/generation:
  - confidence : refusal-gated 2 tiers (`api.confidence`)
  - citations  : from retrieved-chunk metadata, deduped (`api.citations`)
  - guards     : an input guard before the pipeline and an output guard after it (`api.guards`, G6)

Endpoints:
  GET  /health    -> {"status": "ok"}
  POST /ask       -> AskResponse {answer, citations, confidence_score, confidence_basis, guard}
  POST /ask/agent -> AgentAskResponse (the above + route/source_doc_id/routing_reason)
  POST /mcp       -> the G5 MCP server over streamable HTTP (stateless, JSON responses; mcp_server/CONTRACT.md)

Both /ask endpoints pass through ONE wiring point, `_answer`. The input guard (injection rules, then one
gpt-4o-mini classification, ~0.7 s, on every request; it fails closed) runs before the pipeline. The output guard
(every figure in the answer must trace to the retrieved contexts or the question) runs in `_assemble`. A guard
refusal is HTTP 200 with the category's fixed sentence, empty citations, LOW confidence, and `guard: {stage,
reason}`. An allowed question reaches the pipeline unchanged. The MCP tools are not guarded (eval/g6_design.md).

`/ask` serves the frozen v4 pipeline (no router). `/ask/agent` serves the LangGraph agent, which adds
a gpt-4o-mini router call (~1 s, ~$0.0001) to EVERY request — INCLUDING the ~85% of questions that
then route direct — to recover the handful of single-document rows (e.g. the acetone flash point "per
the Sigma-Aldrich SDS"). So `/ask/agent` is the RICHER path (source-scoped routing + a routing_reason
transparency payload) at a fixed per-request cost, NOT a strict upgrade: on a non-source-anchored
question both endpoints serve the same answer and `/ask` pays no router call. It is not a drop-in replacement
for `/ask`.

A malformed request (blank/over-length question) is rejected by Pydantic with HTTP 422. A valid but
unanswerable question returns HTTP 200 with the refusal answer, LOW confidence, and empty citations —
the service never errors a legitimate question it simply cannot answer.
"""

from collections.abc import Callable
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

# Local-dev parity with eval/smoke scripts: populate os.environ from .env so the OpenAI/Pinecone
# clients find their keys. No-op in the container (no .env; real env vars are injected at runtime)
# and in tests (conftest sets dummy env first; load_dotenv does not override existing vars).
load_dotenv()

from api import guards  # noqa: E402
from api.citations import derive_citations  # noqa: E402
from api.confidence import score_confidence  # noqa: E402
from api.schemas import AgentAskResponse, AskRequest, AskResponse, GuardInfo  # noqa: E402
from mcp_server.server import mcp  # noqa: E402
from src.pipeline import ask  # noqa: E402

# NOTE: `agent.graph` is imported LAZILY inside ask_question_agent (not at module load) so the shipped
# /ask path — and the whole app's startup — can never be coupled to the agent layer's import health.
# An agent-side import problem then degrades to a 500 on /ask/agent ALONE, never a boot crash that
# takes /ask down with it (which is exactly what a missing agent/ package once did to the deploy).
#
# The MCP server, by contrast, is imported EAGERLY: /mcp is a route, and a route has to exist at startup. If the
# image lacks mcp_server/, the new image fails on boot, the platform keeps serving the previous image (so /ask
# stays up), and /mcp 404s, which the post-deploy wire-smoke treats as a hard failure.
_mcp_http = mcp.streamable_http_app()  # builds the SDK's session manager and its /mcp route


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # /mcp requests need the SDK session manager's task group. A sub-app's own lifespan never runs once its
    # routes are added to this app, so it runs here; it can run only once per process.
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="Industrial-equipment-safety RAG API",
    description=(
        "Ask questions about the industrial-equipment-safety corpus. Answers are grounded in "
        "retrieved context, cite their source documents (page from chunk metadata), and carry a "
        "refusal-gated confidence signal."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Exactly /mcp, as a Route on this app. A Mount would either redirect /mcp -> /mcp/ (Mount("/mcp")) or take over
# FastAPI's JSON 404s (Mount("/")).
app.router.routes.extend(_mcp_http.routes)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _refused(refusal: guards.Refusal) -> dict:
    """A guard refusal: the category's fixed sentence, no citations, the LOW refusal tier, and the guard named."""
    return {
        "answer": refusal.answer,
        "citations": [],
        "confidence_score": guards.REFUSAL_SCORE,
        "confidence_basis": refusal.basis,
        "guard": GuardInfo(stage=refusal.stage, reason=refusal.reason),
    }


def _assemble(result: dict, question: str) -> dict:
    """Shared answer/citation/confidence assembly for BOTH /ask and /ask/agent, behind the output guard.

    Both endpoints derive their response the same way (confidence from the answer; citations from the
    chunk metadata). Keeping that in ONE place means a future citations.py/confidence.py change reaches
    both endpoints or neither — they can't silently drift. Any pipeline/agent returning the frozen
    {answer, contexts, chunks} shape satisfies it. When the output guard passes, the fields are exactly the
    pre-G6 assembly plus `guard: None`; when it refuses, the answer is withheld whole, never trimmed."""
    answer = result["answer"]
    refusal = guards.check_output(answer, result.get("contexts", []), question)
    if refusal is not None:
        return _refused(refusal)
    score, basis = score_confidence(answer)
    citations = derive_citations(answer, result.get("chunks", []))
    return {
        "answer": answer,
        "citations": citations,
        "confidence_score": score,
        "confidence_basis": basis,
        "guard": None,
    }


def _answer(question: str, pipeline: Callable[[str], dict]) -> tuple[dict, dict | None]:
    """The ONE guarded path for both endpoints: input guard -> pipeline -> `_assemble` (with the output guard).

    Returns the response fields and the pipeline's result, which is None when the input guard refused and the
    pipeline never ran. An allowed question reaches the pipeline as the same str object, never rewritten."""
    refusal = guards.check_input(question).refusal
    if refusal is not None:
        return _refused(refusal), None
    result = pipeline(question)
    return _assemble(result, question), result


@app.post("/ask", response_model=AskResponse)
def ask_question(req: AskRequest) -> AskResponse:
    fields, _ = _answer(req.question, ask)
    return AskResponse(**fields)


@app.post("/ask/agent", response_model=AgentAskResponse)
def ask_question_agent(req: AskRequest) -> AgentAskResponse:
    """Agentic path: a gpt-4o-mini router source-scopes single-document questions (else routes
    direct), and the response reports the route taken. Cost: the router call is paid on EVERY request
    (incl. the ~85% that route direct) — see the module docstring; not a drop-in replacement for /ask.
    Router hiccups and filtered-retrieval failures/empties both fall back to the direct path inside the
    graph, so a classifier or metadata-filter problem degrades to a full-corpus answer, never a 500.
    An input-guard refusal runs no route, so `route` is "none"; an output-guard refusal keeps the route that ran."""
    from agent.graph import ask as agent_ask  # lazy (see module note): isolates /ask from agent import

    fields, result = _answer(req.question, agent_ask)
    if result is None:
        return AgentAskResponse(**fields, route="none")
    return AgentAskResponse(
        **fields,
        route=result.get("route", "direct"),
        source_doc_id=result.get("source_doc_id") or None,  # "" (direct) -> null in the response
        routing_reason=result.get("routing_reason"),
    )
