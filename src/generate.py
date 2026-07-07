"""Grounded answer generation.

Given a question and the already-formatted context strings, calls settings.llm_model and returns
an answer that must be grounded ONLY in the provided context, cite the source_doc_id(s) used, and
say so explicitly when the context lacks the answer.

Determinism/provenance: temperature=0 + a fixed seed. gpt-4o-mini is a floating alias, so the
resolved model string and system_fingerprint are logged (a provider repoint could shift scores and
be misattributed to a chunking/retrieval change).
"""

import logging
from functools import lru_cache

from langsmith import traceable

logger = logging.getLogger(__name__)

MODEL_SEED = 42

SYSTEM_PROMPT = (
    "You are a careful assistant answering questions about industrial equipment safety "
    "documents. Follow these rules strictly:\n"
    "1. Answer ONLY using the information in the provided context. Do not use outside knowledge.\n"
    "2. Cite the source_doc_id(s) you used, drawn from the [source_doc_id=... page=...] labels.\n"
    "3. If the context does not contain the answer, reply exactly that the provided context does "
    "not contain the answer — do not guess."
)

_logged_model = False


@lru_cache(maxsize=1)
def _llm():
    from langchain_openai import ChatOpenAI

    from src.config import get_settings

    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        temperature=0,
        seed=MODEL_SEED,
    )


@traceable(name="generate")
def generate(question: str, context_strings: list[str]) -> str:
    global _logged_model
    context_block = "\n\n---\n\n".join(context_strings) if context_strings else "(no context retrieved)"
    messages = [
        ("system", SYSTEM_PROMPT),
        ("human", f"Context:\n{context_block}\n\nQuestion: {question}"),
    ]
    response = _llm().invoke(messages)
    if not _logged_model:
        meta = getattr(response, "response_metadata", {}) or {}
        logger.info(
            "generator resolved model=%s system_fingerprint=%s seed=%s",
            meta.get("model_name"),
            meta.get("system_fingerprint"),
            MODEL_SEED,
        )
        _logged_model = True
    return response.content or ""
