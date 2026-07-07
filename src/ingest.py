"""Ingestion pipeline — populate Pinecone with a naive fixed-size chunking baseline.

Reads data/manifest.json, loads each ingest=true PDF, splits it PER PAGE with a fixed-size
RecursiveCharacterTextSplitter (500/50), embeds with text-embedding-3-small, and upserts to the
Pinecone namespace "fixed_500_50" with deterministic ids so re-runs are idempotent.

Page convention: eval/dataset.jsonl uses 1-based PDF page positions. PyPDFLoader page metadata is
0-based, so we store page = loader_page + 1 to keep eval rows, chunk metadata, and citations aligned.

KNOWN BASELINE LIMITATION (deliberate): pages are split independently, so no chunk spans a page
boundary and chunk_overlap never carries context across pages. Multi-page content — e.g. the
§1910.147(d) LOTO sequence, the Flowserve start-up sequence, the 1910.1000 Z-tables — can be
fragmented across chunks that never co-occur. This is the naive baseline Step 4 (semantic
chunking) is meant to improve; it is NOT presented as strictly correct.
"""

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.json"
NAMESPACE = "fixed_500_50"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
UPSERT_BATCH = 100
# text-embedding-3-small rate as of 2026-07-06 — labeled assumption, verify against current pricing.
EMBED_RATE_PER_1M = 0.02
EMBED_RATE_DATE = "2026-07-06"

load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(REPO_ROOT))


def ensure_index(pc, name: str) -> None:
    from pinecone import ServerlessSpec

    if pc.has_index(name):
        print(f"Index '{name}' already exists — reusing.")
        return
    print(f"Creating serverless index '{name}' (dim={EMBED_DIM}, cosine, aws/us-east-1)...")
    pc.create_index(
        name=name,
        dimension=EMBED_DIM,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    while not pc.describe_index(name).status.get("ready", False):
        time.sleep(1)
    print("Index ready.")


def build_chunks(splitter, doc_id: str, title: str, path: Path):
    """Return (chunks, zero_text_flag). Each chunk: (id, text, metadata). Split per page."""
    from langchain_community.document_loaders import PyPDFLoader

    pages = PyPDFLoader(str(path)).load()
    chunks = []
    total_chars = 0
    for page_doc in pages:
        page = page_doc.metadata.get("page", 0) + 1  # 0-based -> 1-based
        page_text = page_doc.page_content or ""
        total_chars += len(page_text.strip())
        for chunk_index, piece in enumerate(splitter.split_text(page_text)):
            if not piece.strip():
                continue
            vid = f"{doc_id}-p{page}-{chunk_index}"
            meta = {
                "source_doc_id": doc_id,
                "title": title,
                "page": page,
                "chunk_index": chunk_index,
                "text": piece,
            }
            chunks.append((vid, piece, meta))
    return chunks, (total_chars == 0)


def poll_vector_count(index, namespace: str, expected: int, timeout_s: int = 60) -> int:
    """Poll describe_index_stats until the namespace count stabilizes (serverless is eventually
    consistent). Returns the last observed count."""
    deadline = time.time() + timeout_s
    last = -1
    stable = 0
    while time.time() < deadline:
        stats = index.describe_index_stats()
        ns = stats.get("namespaces", {}).get(namespace)
        count = ns["vector_count"] if ns else 0
        if count == expected:
            return count
        stable = stable + 1 if count == last else 0
        last = count
        if stable >= 3:  # count held steady across polls but != expected
            return count
        time.sleep(2)
    return last


def main() -> None:
    from pinecone import Pinecone
    import tiktoken
    from langchain_openai import OpenAIEmbeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from src.config import get_settings

    settings = get_settings()
    pc = Pinecone(api_key=settings.pinecone_api_key)
    ensure_index(pc, settings.index_name)
    index = pc.Index(settings.index_name)

    docs = json.loads(MANIFEST_PATH.read_text())["docs"]
    ingest_docs = [d for d in docs if d.get("ingest") is True]
    print(f"\nManifest: {len(ingest_docs)} docs with ingest=true (of {len(docs)}).\n")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )

    all_chunks = []
    per_doc_counts = {}
    zero_text_docs = []
    for d in ingest_docs:
        doc_id, title = d["doc_id"], d["title"]
        path = REPO_ROOT / d["path"]
        if not path.exists():
            raise FileNotFoundError(f"{doc_id}: manifest path missing on disk: {path}")
        chunks, zero_text = build_chunks(splitter, doc_id, title, path)
        per_doc_counts[doc_id] = len(chunks)
        if zero_text:
            zero_text_docs.append(doc_id)
        all_chunks.extend(chunks)
        print(f"  {doc_id:34s} {len(chunks):5d} chunks")

    total_chunks = len(all_chunks)

    # Guard: deterministic ids must be unique BEFORE upsert (duplicate page nums -> silent overwrite).
    ids = [c[0] for c in all_chunks]
    if len(set(ids)) != len(ids):
        from collections import Counter
        dupes = [i for i, n in Counter(ids).items() if n > 1]
        raise RuntimeError(f"Duplicate deterministic ids generated ({len(dupes)}): {dupes[:5]} ...")

    # Token count (reliable) for cost estimate.
    enc = tiktoken.get_encoding("cl100k_base")
    total_tokens = sum(len(enc.encode(c[1])) for c in all_chunks)

    # Embed (batched by the client) and upsert in batches.
    print(f"\nEmbedding {total_chunks} chunks with {EMBED_MODEL}...")
    embedder = OpenAIEmbeddings(model=EMBED_MODEL)
    texts = [c[1] for c in all_chunks]
    vectors = embedder.embed_documents(texts)
    if len(vectors) != total_chunks:
        raise RuntimeError(f"Embedding count {len(vectors)} != chunk count {total_chunks}")

    print(f"Upserting to namespace '{NAMESPACE}'...")
    payload = [
        {"id": c[0], "values": v, "metadata": c[2]} for c, v in zip(all_chunks, vectors)
    ]
    for i in range(0, len(payload), UPSERT_BATCH):
        index.upsert(vectors=payload[i : i + UPSERT_BATCH], namespace=NAMESPACE)

    # Eventual-consistency-safe count read, then HARD reconciliation.
    observed = poll_vector_count(index, NAMESPACE, expected=total_chunks)

    # ---- Report ----
    print("\n" + "=" * 60)
    print("INGESTION REPORT")
    print("=" * 60)
    print(f"{'doc_id':34s} chunks")
    for doc_id, n in per_doc_counts.items():
        print(f"  {doc_id:34s} {n:5d}")
    print(f"\nTotal non-empty chunks generated : {total_chunks}")
    print(f"Namespace '{NAMESPACE}' vector_count: {observed}")
    est_cost = total_tokens / 1_000_000 * EMBED_RATE_PER_1M
    print(
        f"Embedding tokens (tiktoken)      : {total_tokens:,}\n"
        f"Estimated embedding cost         : ${est_cost:.4f} "
        f"(estimated at ${EMBED_RATE_PER_1M} per 1M tokens; {EMBED_MODEL} rate as of "
        f"{EMBED_RATE_DATE} — verify against current OpenAI pricing)"
    )
    if zero_text_docs:
        print(f"\n⚠️  ZERO extractable text (flagged): {zero_text_docs}")
    else:
        print("\nZero-text docs: none.")
    print(
        "\nBaseline limitation: per-page splitting — chunks never span page boundaries and overlap "
        "does not cross pages, so multi-page content (e.g. §1910.147(d) LOTO sequence, Flowserve "
        "start sequence, 1910.1000 Z-tables) may be fragmented. Naive baseline; Step 4 improves it."
    )

    if observed != total_chunks:
        raise SystemExit(
            f"\nRECONCILIATION FAILED: namespace vector_count ({observed}) != chunks generated "
            f"({total_chunks}). Possible id collision / silent overwrite — investigate."
        )
    print(f"\n✅ Reconciliation OK: {observed} vectors == {total_chunks} chunks generated.")


if __name__ == "__main__":
    main()
