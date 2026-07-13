"""Unit tests for the request/response contract — malformed request => validation error (=> 422)."""

import pytest
from pydantic import ValidationError

from api.schemas import AskRequest, AskResponse, Citation


def test_valid_question_accepted():
    req = AskRequest(question="What is the OSHA PEL for anhydrous ammonia?")
    assert req.question == "What is the OSHA PEL for anhydrous ammonia?"


def test_question_is_stripped():
    assert AskRequest(question="  What is the PEL?  ").question == "What is the PEL?"


@pytest.mark.parametrize("bad", ["", "  ", "\n\t ", "ab"])
def test_blank_or_too_short_question_rejected(bad):
    with pytest.raises(ValidationError):
        AskRequest(question=bad)


def test_over_length_question_rejected():
    with pytest.raises(ValidationError):
        AskRequest(question="x" * 1001)


def test_response_serializes_full_contract():
    resp = AskResponse(
        answer="The OSHA PEL is 50 ppm.",
        citations=[Citation(document="29 CFR 1910.1000 — Air contaminants", page=7)],
        confidence_score=0.9,
        confidence_basis="high: answer generated from retrieved context",
    )
    dumped = resp.model_dump()
    assert set(dumped) == {"answer", "citations", "confidence_score", "confidence_basis"}
    assert dumped["citations"] == [{"document": "29 CFR 1910.1000 — Air contaminants", "page": 7}]


def test_citation_requires_int_page():
    with pytest.raises(ValidationError):
        Citation(document="d", page="not-an-int")
