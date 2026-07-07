"""Dense retrieval over the Pinecone namespace fixed_500_50.

Embeds the query with text-embedding-3-small and returns the top-k chunks with their
source_doc_id and (1-based) page. `format_contexts` produces the ONE canonical context
representation that is used both in the generation prompt AND returned by the pipeline, so
RAGAS grades exactly the text the model reasoned over.
"""

from functools import lru_cache

from langsmith import traceable

NAMESPACE = "fixed_500_50"
EMBED_MODEL = "text-embedding-3-small"
DEFAULT_K = 5  # explicit retrieval knob — fixed for the baseline, recorded in provenance; not tuned here.


@lru_cache(maxsize=1)
def _embedder():
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(model=EMBED_MODEL)


@lru_cache(maxsize=1)
def _index():
    from pinecone import Pinecone

    from src.config import get_settings

    settings = get_settings()
    return Pinecone(api_key=settings.pinecone_api_key).Index(settings.index_name)


@traceable(name="dense_search")
def dense_search(query: str, k: int = DEFAULT_K) -> list[dict]:
    """Return up to k chunks as [{"text", "source_doc_id", "page"}], ordered by score."""
    vector = _embedder().embed_query(query)
    res = _index().query(
        vector=vector, top_k=k, namespace=NAMESPACE, include_metadata=True
    )
    out = []
    for match in res["matches"]:
        md = match.get("metadata") or {}
        out.append(
            {
                "text": md.get("text", ""),
                "source_doc_id": md.get("source_doc_id"),
                "page": md.get("page"),
            }
        )
    return out


def format_contexts(retrieved: list[dict]) -> list[str]:
    """Canonical context representation, used identically in the prompt and in the returned
    contexts list so the graded text == the text the model saw."""
    return [
        f"[source_doc_id={c['source_doc_id']} page={c['page']}]\n{c['text']}"
        for c in retrieved
    ]
