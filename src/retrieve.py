"""Dense retrieval over a configurable Pinecone namespace (default semantic_v2, from src/config.py).
[corrected 2026-10-12: was 'default fixed_500_50'; the record shows the default is semantic_v2, promoted after the
like-for-like gate and pinned in render.yaml (src/config.py, retrieval_namespace)]

Embeds the query with text-embedding-3-small and returns the top-k chunks with their
source_doc_id and (1-based) page. `format_contexts` produces the ONE canonical context
representation that is used both in the generation prompt AND returned by the pipeline, so
RAGAS grades exactly the text the model reasoned over.

The retrieval namespace is read from settings.retrieval_namespace (env RETRIEVAL_NAMESPACE),
defaulting to "semantic_v2" (src/config.py) — this lets us A/B different chunking strategies
(each in its own namespace) without editing code or the default.
[corrected 2026-10-12: was 'defaulting to the v1 baseline "fixed_500_50"'; the record shows the v1 baseline is still
selectable with RETRIEVAL_NAMESPACE=fixed_500_50, but the default is semantic_v2 (src/config.py)]
"""

from functools import lru_cache

from langsmith import traceable

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
def dense_search(query: str, k: int = DEFAULT_K, source_doc_id: str | None = None) -> list[dict]:
    """Return up to k chunks as [{"text", "source_doc_id", "page"}], ordered by score.

    source_doc_id (optional): restrict retrieval to ONE document via a metadata filter — used by the
    agent's source-scoped route for single-document questions ("per the X SDS"). Default None =
    unfiltered (the v4 path, byte-for-byte — preserves the 5a repro).
    """
    from src.config import get_settings

    vector = _embedder().embed_query(query)
    query_kwargs = dict(
        vector=vector,
        top_k=k,
        namespace=get_settings().retrieval_namespace,
        include_metadata=True,
    )
    if source_doc_id is not None:
        query_kwargs["filter"] = {"source_doc_id": {"$eq": source_doc_id}}
    res = _index().query(**query_kwargs)
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
