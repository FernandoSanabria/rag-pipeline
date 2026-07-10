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
    "1. Use ONLY the information in the provided context. Do not use outside knowledge or prior "
    "training.\n"
    "2. If the facts needed ARE present in the context, ANSWER the question — even when you must "
    "synthesize or combine information across multiple retrieved passages or sources. Do NOT "
    "refuse merely because the needed facts are split across passages or two documents.\n"
    "3. For a comparison question (e.g. \"do X and Y agree?\", \"how does X compare to Y?\"): if "
    "ALL of the values being compared are present in the context, state each source's value and "
    "give the comparison. But if ANY value being compared is NOT present, do NOT give the "
    "comparison — either reply with the exact refusal sentence (rule 6), or state only the "
    "value(s) that ARE present and explicitly say the other value is not in the provided context. "
    "NEVER supply a missing comparison value from outside knowledge.\n"
    "4. Ground every statement in the retrieved text: do not add numbers, steps, or claims that "
    "the context does not support.\n"
    "5. Cite the source_doc_id(s) you used, drawn from the [source_doc_id=... page=...] labels.\n"
    "6. Refuse ONLY when the specific information needed is genuinely NOT in the context. In that "
    "case reply exactly: \"The provided context does not contain the answer.\" Do not guess or "
    "fill gaps from outside knowledge."
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
