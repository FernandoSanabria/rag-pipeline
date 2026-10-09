"""FastAPI service wrapping the frozen RAG pipeline (`src.pipeline.ask`).

Three derived layers sit on top of `ask()` without touching retrieval/generation:
  - confidence : refusal-gated 2 tiers (`api.confidence`)
  - citations  : from retrieved-chunk metadata, deduped (`api.citations`)
  - guards     : the G6 output guard after the pipeline (`api.guards`); the input guard is built but off

Endpoints:
  GET  /health    -> {"status": "ok"}
  POST /ask       -> AskResponse {answer, citations, confidence_score, confidence_basis, guard}
  POST /ask/agent -> AgentAskResponse (the above + route/source_doc_id/routing_reason), or HTTP 202
                     PendingReviewResponse when the G10b approval gate pauses the question before generation
  POST /ask/agent/resume           -> approve | reject | amend (removals) a paused question (G10b)
  GET  /ask/agent/review/{thread}  -> the review's status only (G10b)
  POST /mcp       -> the G5 MCP server over streamable HTTP (stateless, JSON responses; mcp_server/CONTRACT.md)

Both /ask endpoints pass through ONE wiring point, `_answer`. The output guard (every figure in the answer must
trace to the retrieved contexts or the question) runs in `_assemble`; when it refuses, the response is HTTP 200
with a fixed sentence, empty citations, LOW confidence, and `guard: {stage, reason}`. The input guard (injection
rules, then one gpt-4o-mini classification; it fails closed) is wired into `_answer` but switched off by
`INPUT_GUARD_ENABLED`: its two pre-registered prompts were both falsified (eval/g6_PREDICTION.md). The question
always reaches the pipeline unchanged. The MCP tools are not guarded (eval/g6_design.md).

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

import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# Local-dev parity with eval/smoke scripts: populate os.environ from .env so the OpenAI/Pinecone
# clients find their keys. No-op in the container (no .env; real env vars are injected at runtime)
# and in tests (conftest sets dummy env first; load_dotenv does not override existing vars).
load_dotenv()

from api import guards  # noqa: E402
from api.citations import derive_citations  # noqa: E402
from api.confidence import score_confidence  # noqa: E402
from api.schemas import (  # noqa: E402
    AgentAskResponse,
    AskRequest,
    AskResponse,
    GuardInfo,
    PendingReviewResponse,
    ResumeRequest,
    ReviewEvidence,
    ReviewStatusResponse,
)
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

# G6: the input guard is built and measured but NOT shipped. Two pre-registered classifier prompts were both
# falsified on zero-tolerance items: each refused a question from the frozen 28, and each let a wrapped injection
# through (eval/g6_PREDICTION.md, eval/KNOWN_LIMITATIONS.md). Turning it on needs a new pre-registration.
INPUT_GUARD_ENABLED = False


logger = logging.getLogger(__name__)


def _sweep_expired_reviews() -> None:
    """G10b: delete expired review threads at startup (there is no scheduler on Render free). Runs only when a review
    store is configured, and never blocks startup: an agent-side failure here is logged, and /ask stays up."""
    if not os.environ.get("REVIEW_DB_PATH", "").strip():
        return
    try:
        from agent.graph import sweep_expired

        sweep_expired()
    except Exception as exc:  # noqa: BLE001 — startup must not depend on the agent layer's health
        logger.warning("review: startup sweep skipped: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _sweep_expired_reviews()
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
    """The ONE guarded path for both endpoints: input guard (when enabled) -> pipeline -> `_assemble` (with the
    output guard).

    Returns the response fields and the pipeline's result, which is None when the input guard refused and the
    pipeline never ran. An allowed question reaches the pipeline as the same str object, never rewritten."""
    if INPUT_GUARD_ENABLED:
        refusal = guards.check_input(question).refusal
        if refusal is not None:
            return _refused(refusal), None
    result = pipeline(question)
    return _assemble(result, question), result


@app.post("/ask", response_model=AskResponse)
def ask_question(req: AskRequest) -> AskResponse:
    fields, _ = _answer(req.question, ask)
    return AskResponse(**fields)


def _agent_response(fields: dict, result: dict) -> AgentAskResponse:
    return AgentAskResponse(
        **fields,
        route=result.get("route", "direct"),
        source_doc_id=result.get("source_doc_id") or None,  # "" (direct) -> null in the response
        routing_reason=result.get("routing_reason"),
    )


def _review_refusal(reason: str, result: dict | None = None) -> AgentAskResponse:
    result = result or {}
    return AgentAskResponse(**_refused(guards.REVIEW_REFUSALS[reason]), route=result.get("route") or "none",
                            source_doc_id=result.get("source_doc_id") or None,
                            routing_reason=result.get("routing_reason"))


def _pending(result: dict) -> JSONResponse:
    """HTTP 202: the gate paused the question. The evidence is exactly what `generate` will receive."""
    from agent.graph import _doc_titles
    from agent.state import chunk_key

    titles = _doc_titles()
    evidence = [ReviewEvidence(key=chunk_key(c), source_doc_id=c.get("source_doc_id"),
                               title=titles.get(c.get("source_doc_id")), page=c.get("page"), text=c.get("text", ""))
                for c in result.get("chunks", [])]
    body = PendingReviewResponse(status="pending_review", thread_id=result["thread_id"], reason=result["reason"],
                                 route=result.get("route", "direct"), routing_reason=result.get("routing_reason"),
                                 evidence=evidence, expires_at=result["expires_at"])
    return JSONResponse(status_code=202, content=body.model_dump())


@app.post("/ask/agent", response_model=AgentAskResponse,
          responses={202: {"model": PendingReviewResponse, "description": "Paused for review (G10b)."}})
def ask_question_agent(req: AskRequest):
    """Agentic path: a gpt-4o-mini router source-scopes single-document questions (else routes
    direct), and the response reports the route taken. Cost: the router call is paid on EVERY request
    (incl. the ~85% that route direct) — see the module docstring; not a drop-in replacement for /ask.
    Router hiccups and filtered-retrieval failures/empties both fall back to the direct path inside the
    graph, so a classifier or metadata-filter problem degrades to a full-corpus answer, never a 500.
    An output-guard refusal keeps the route that ran. An input-guard refusal (only when INPUT_GUARD_ENABLED) runs
    no route, so `route` is "none"."""
    from agent.graph import ask as agent_ask  # lazy (see module note): isolates /ask from agent import

    fields, result = _answer(req.question, agent_ask)
    if result is None:
        return AgentAskResponse(**fields, route="none")
    status = result.get("status")
    if status == "pending_review":
        return _pending(result)
    if status == "review_unavailable":  # the gate fired but could not pause: refused, never answered unreviewed
        return _review_refusal("review_unavailable", result)
    return _agent_response(fields, result)


@app.post("/ask/agent/resume", response_model=AgentAskResponse)
def resume_review(req: ResumeRequest) -> AgentAskResponse:
    """Resolve a paused question (G10b). approve -> generate, then the same assembly and output guard as /ask/agent;
    reject -> a refusal, nothing generated; amend -> the listed evidence keys are removed, then generate. An unknown,
    lost or expired thread is refused (HTTP 200), never answered. A thread already resolved returns its recorded
    resolution and never generates twice. There is no authentication: anyone who can reach the service can resolve a
    review (eval/KNOWN_LIMITATIONS.md, G10b)."""
    from agent.graph import resume

    result = resume(str(req.thread_id), req.op, list(req.removals))
    status = result["status"]
    if status == "invalid":
        raise HTTPException(status_code=422, detail=result["detail"])
    if status == "expired_or_lost":
        return _review_refusal("expired_or_lost")
    if status == "rejected":
        return _review_refusal("rejected", result)
    question = result.get("question")
    return _agent_response(_assemble(result, question or ""), result)


@app.get("/ask/agent/review/{thread_id}", response_model=ReviewStatusResponse)
def review_status(thread_id: UUID) -> ReviewStatusResponse:
    """The review's status only (pending, approved, amended, rejected, or expired_or_lost). Never the answer."""
    from agent.graph import review_status as status_of

    record = status_of(str(thread_id))
    return ReviewStatusResponse(**{k: v for k, v in record.items() if k in ReviewStatusResponse.model_fields})
