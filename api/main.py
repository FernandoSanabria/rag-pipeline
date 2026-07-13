"""FastAPI service wrapping the frozen RAG pipeline (`src.pipeline.ask`).

Two derived layers sit on top of `ask()` without touching retrieval/generation:
  - confidence : refusal-gated 2 tiers (`api.confidence`)
  - citations  : from retrieved-chunk metadata, deduped (`api.citations`)

Endpoints:
  GET  /health -> {"status": "ok"}
  POST /ask    -> AskResponse {answer, citations, confidence_score, confidence_basis}

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

from api.citations import derive_citations  # noqa: E402
from api.confidence import score_confidence  # noqa: E402
from api.schemas import AskRequest, AskResponse  # noqa: E402
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


@app.post("/ask", response_model=AskResponse)
def ask_question(req: AskRequest) -> AskResponse:
    result = ask(req.question)
    answer = result["answer"]
    score, basis = score_confidence(answer)
    citations = derive_citations(answer, result.get("chunks", []))
    return AskResponse(
        answer=answer,
        citations=citations,
        confidence_score=score,
        confidence_basis=basis,
    )
