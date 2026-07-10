"""RAG pipeline entry point.

ask() runs dense retrieval -> grounded generation and returns the frozen shape
{"answer": str, "contexts": list[str]} so eval/run_eval.py needs no changes.

The `contexts` returned are the EXACT strings passed into the generator (retrieve.format_contexts),
so RAGAS grades the same representation the model reasoned over.

Fail-safe: if retrieval or generation errors (or the LLM returns empty), ask() still returns a
scoreable row (answer="") and logs the failing question — it never aborts the run or drops a row,
which would shrink the RAGAS denominator and break baseline-vs-later comparisons.
"""

import logging

from langsmith import traceable

from src.config import get_settings
from src.generate import generate
from src.retrieve import dense_search, format_contexts

logger = logging.getLogger(__name__)


@traceable(name="rag_pipeline")
def ask(question: str) -> dict:
    contexts: list[str] = []
    try:
        retrieved = dense_search(question, k=get_settings().retrieval_k)
        contexts = format_contexts(retrieved)
        answer = generate(question, contexts)
        if not answer:
            logger.warning("Empty generation for question: %r", question)
            answer = ""
    except Exception as exc:  # never abort the run / drop a row
        logger.warning("Pipeline error for question %r: %s", question, exc)
        answer = ""
    return {"answer": answer, "contexts": contexts}
