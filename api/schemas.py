"""Pydantic v2 request/response contract for the API.

Request validation is strict: `strip_whitespace=True` means a blank or whitespace-only question fails
`min_length` and returns HTTP 422 — distinct from a valid-but-unanswerable question, which returns 200
with a refusal answer, LOW confidence, and empty citations.
"""

from typing import Annotated

from pydantic import BaseModel, StringConstraints

# A blank / whitespace-only / over-length question is a malformed REQUEST -> 422 (not a refusal).
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class AskRequest(BaseModel):
    question: Question


class Citation(BaseModel):
    document: str  # manifest title (per CLAUDE.md), never a raw filename
    page: int      # 1-based page from chunk metadata, never the model's cited page


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    confidence_score: float
    confidence_basis: str
