"""Pydantic v2 request/response contract for the API.

Request validation is strict: `strip_whitespace=True` means a blank or whitespace-only question fails
`min_length` and returns HTTP 422 — distinct from a valid-but-unanswerable question, which returns 200
with a refusal answer, LOW confidence, and empty citations.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, StringConstraints, model_validator

# A blank / whitespace-only / over-length question is a malformed REQUEST -> 422 (not a refusal).
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


class AskRequest(BaseModel):
    question: Question


class Citation(BaseModel):
    document: str  # manifest title (per CLAUDE.md), never a raw filename
    page: int      # 1-based page from chunk metadata, never the model's cited page


class GuardInfo(BaseModel):
    """Which guard refused the request, and why (`api.guards`). The reason picks the fixed refusal sentence.
    G10b adds stage "review": the approval gate rejected the answer, the review expired or was lost, or a question the
    gate fires on could not be paused (no checkpointer) and was refused rather than answered unreviewed."""

    stage: Literal["input", "output", "review"]
    reason: Literal[
        "out_of_scope", "injection", "harmful_request", "pii_request", "guard_error", "untraceable_numbers",
        "rejected", "expired_or_lost", "review_unavailable",
    ]


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    confidence_score: float
    confidence_basis: str
    guard: GuardInfo | None = None  # null whenever nothing was blocked


class AgentAskResponse(AskResponse):
    """/ask/agent response — AskResponse plus routing transparency (the endpoint's reason to exist).

    `route` is what actually ran ("direct" | "source_scoped"); on the source-scoped path `source_doc_id`
    and `routing_reason` name the single document the answer was scoped to. Both are null on the direct
    path (incl. an execution fallback that downgraded a source-scoped classification to direct). On "decomposed"
    (G12, only when the fan-out is enabled; it ships off) `source_doc_id` is null and `routing_reason`
    lists the sub-questions. If the input guard refuses (it is off in the shipped app; api/main.py), nothing ran:
    `route` is "none" and both are null."""

    route: str
    source_doc_id: str | None = None
    routing_reason: str | None = None


# ---- G10b approval gate (eval/g10b_design.md R4) --------------------------------------------------------------------
class ReviewEvidence(BaseModel):
    """One paused context chunk. `key` is what a reviewer sends back in `removals`; `page` is null for a tool chunk."""

    key: str
    source_doc_id: str | None
    title: str | None
    page: int | None
    text: str


class PendingReviewResponse(BaseModel):
    """HTTP 202 from /ask/agent when the approval gate paused the question before generation. `reason` is the trigger
    disjunct that fired ("exposure_limit_named" | "scoped_fallback"); `expires_at` is absolute UTC ISO-8601."""

    status: Literal["pending_review"]
    thread_id: str
    reason: str
    route: str
    routing_reason: str | None = None
    evidence: list[ReviewEvidence]
    expires_at: str


ChunkKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ResumeRequest(BaseModel):
    """POST /ask/agent/resume. `removals` (evidence keys) only with op="amend"; additions are not exposed by the API
    while it has no authentication (eval/KNOWN_LIMITATIONS.md, G10b)."""

    thread_id: UUID
    op: Literal["approve", "reject", "amend"]
    removals: list[ChunkKey] = []

    @model_validator(mode="after")
    def _removals_only_with_amend(self):
        if self.op == "amend" and not self.removals:
            raise ValueError("op=amend needs at least one removal")
        if self.op != "amend" and self.removals:
            raise ValueError("removals are only allowed with op=amend")
        return self


class ReviewStatusResponse(BaseModel):
    """GET /ask/agent/review/{thread_id}: the status only, never the answer."""

    thread_id: str
    status: Literal["pending", "approved", "amended", "rejected", "unavailable", "expired_or_lost"]
    reason: str | None = None
    expires_at: str | None = None
    resolved_at: str | None = None
