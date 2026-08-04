"""FastAPI service wrapping the frozen RAG pipeline (`src.pipeline.ask`).

Two derived layers sit on top of `ask()` without touching retrieval/generation:
  - confidence : refusal-gated 2 tiers (`api.confidence`)
  - citations  : from retrieved-chunk metadata, deduped (`api.citations`)

Endpoints:
  GET  /health    -> {"status": "ok"}
  POST /ask       -> AskResponse {answer, citations, confidence_score, confidence_basis}
  POST /ask/agent -> AgentAskResponse (the above + route/source_doc_id/routing_reason)

`/ask` serves the frozen v4 pipeline (no router). `/ask/agent` serves the LangGraph agent, which adds
a gpt-4o-mini router call (~1 s, ~$0.0001) to EVERY request — INCLUDING the ~85% of questions that
then route direct — to recover the handful of single-document rows (e.g. the acetone flash point "per
the Sigma-Aldrich SDS"). So `/ask/agent` is the RICHER path (source-scoped routing + a routing_reason
transparency payload) at a fixed per-request cost, NOT a strict upgrade: on a non-source-anchored
question both endpoints serve the same answer and `/ask` pays nothing. It is not a drop-in replacement
for `/ask`.

A malformed request (blank/over-length question) is rejected by Pydantic with HTTP 422. A valid but
unanswerable question returns HTTP 200 with the refusal answer, LOW confidence, and empty citations —
the service never errors a legitimate question it simply cannot answer.
"""

from dotenv import load_dotenv
from fastapi import FastAPI

# Local-dev parity with eval/smoke scripts: populate os.environ from .env so the OpenAI/Pinecone
# clients find their keys. No-op in the container (no .env; real env vars are injected at runtime)
# and in tests (conftest sets dummy env first; load_dotenv does not override existing vars).
load_dotenv()

from agent.graph import ask as agent_ask  # noqa: E402
from api.citations import derive_citations  # noqa: E402
from api.confidence import score_confidence  # noqa: E402
from api.schemas import AgentAskResponse, AskRequest, AskResponse  # noqa: E402
from src.pipeline import ask  # noqa: E402

app = FastAPI(
    title="Industrial-equipment-safety RAG API",
    description=(
        "Ask questions about the industrial-equipment-safety corpus. Answers are grounded in "
        "retrieved context, cite their source documents (page from chunk metadata), and carry a "
        "refusal-gated confidence signal."
    ),
    version="1.0.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _assemble(result: dict) -> tuple[str, list, float, str]:
    """Shared answer/citation/confidence assembly for BOTH /ask and /ask/agent.

    Both endpoints derive their response the same way (confidence from the answer; citations from the
    chunk metadata). Keeping that in ONE place means a future citations.py/confidence.py change reaches
    both endpoints or neither — they can't silently drift. Any pipeline/agent returning the frozen
    {answer, chunks} shape satisfies it."""
    answer = result["answer"]
    score, basis = score_confidence(answer)
    citations = derive_citations(answer, result.get("chunks", []))
    return answer, citations, score, basis


@app.post("/ask", response_model=AskResponse)
def ask_question(req: AskRequest) -> AskResponse:
    answer, citations, score, basis = _assemble(ask(req.question))
    return AskResponse(
        answer=answer,
        citations=citations,
        confidence_score=score,
        confidence_basis=basis,
    )


@app.post("/ask/agent", response_model=AgentAskResponse)
def ask_question_agent(req: AskRequest) -> AgentAskResponse:
    """Agentic path: a gpt-4o-mini router source-scopes single-document questions (else routes
    direct), and the response reports the route taken. Cost: the router call is paid on EVERY request
    (incl. the ~85% that route direct) — see the module docstring; not a drop-in replacement for /ask.
    Router hiccups and filtered-retrieval failures/empties both fall back to the direct path inside the
    graph, so a classifier or metadata-filter problem degrades to a full-corpus answer, never a 500."""
    result = agent_ask(req.question)
    answer, citations, score, basis = _assemble(result)
    return AgentAskResponse(
        answer=answer,
        citations=citations,
        confidence_score=score,
        confidence_basis=basis,
        route=result.get("route", "direct"),
        source_doc_id=result.get("source_doc_id") or None,  # "" (direct) -> null in the response
        routing_reason=result.get("routing_reason"),
    )
