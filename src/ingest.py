"""Ingestion pipeline — populate Pinecone with a selectable chunking strategy.

Strategy is chosen via the CHUNKING_STRATEGY env var:
  - "fixed_500_50" (default): naive per-page fixed-size RecursiveCharacterTextSplitter (500/50),
    namespace "fixed_500_50". Each chunk maps to exactly one page (per-page chunk_index).
  - "semantic": SemanticChunker (langchain_experimental, percentile breakpoints) over the WHOLE
    document text, namespace "semantic". Chunks may span page boundaries.

Both write the SAME metadata contract {source_doc_id, title, page, chunk_index, text} with
deterministic ids {doc_id}-p{page}-{chunk_index}, batched upsert, and a HARD post-upsert
reconciliation (namespace vector_count == non-empty chunks) behind an eventual-consistency poll.

Page convention: eval/dataset.jsonl uses 1-based PDF page positions (PyPDFLoader page metadata is
0-based, so page = loader_page + 1).
  - fixed_500_50: page = the (single) page the chunk came from.
  - semantic: page = the 1-based PDF page where the chunk STARTS (chunks span pages), mapped via a
    normalized-offset -> page table; chunk_index is per-document sequential so ids stay unique. A
    post-mapping assert requires assigned start-pages to be non-decreasing within each doc and in
    [1, page_count] (guards the cursor from latching onto repeated per-page boilerplate).

NOTE: the "semantic" strategy depends on the EXPERIMENTAL package langchain-experimental; a future
upgrade could shift chunk boundaries (and thus scores) — tracked in run provenance.
"""

import json
import math
import os
import re
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.json"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
EMBED_MAX_TOKENS = 8191  # text-embedding-3-small hard input limit; fail fast if a chunk exceeds it.
EMBED_BATCH = 32  # items per embed request: <=32*8191 ~=262K tokens < OpenAI's 300K/request cap
# (tabular docs like the NIOSH Pocket Guide yield huge pseudo-sentences). Also the accounting divisor.
FIXED_CHUNK_SIZE = 500
FIXED_CHUNK_OVERLAP = 50
UPSERT_BATCH = 100
# text-embedding-3-small rate as of 2026-07-09 — labeled assumption, verify against current pricing.
EMBED_RATE_PER_1M = 0.02
EMBED_RATE_DATE = "2026-07-09"

STRATEGY = os.environ.get("CHUNKING_STRATEGY", "fixed_500_50")
NAMESPACE_BY_STRATEGY = {"fixed_500_50": "fixed_500_50", "semantic": "semantic"}
SENTENCE_SPLIT_REGEX = r"(?<=[.?!])\s+"  # matches SemanticChunker's default sentence splitter

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


def poll_vector_count(index, namespace: str, expected: int, timeout_s: int = 60) -> int:
    """Poll describe_index_stats until the namespace count stabilizes (serverless is eventually
    consistent). Returns the last observed count."""
    deadline = time.time() + timeout_s
    last, stable = -1, 0
    while time.time() < deadline:
        stats = index.describe_index_stats()
        ns = stats.get("namespaces", {}).get(namespace)
        count = ns["vector_count"] if ns else 0
        if count == expected:
            return count
        stable = stable + 1 if count == last else 0
        last = count
        if stable >= 3:
            return count
        time.sleep(2)
    return last


# ---- fixed_500_50 (v1) — unchanged behavior ----
def build_chunks_fixed(splitter, doc_id: str, title: str, path: Path):
    """Per-page fixed-size chunks. Each chunk maps to exactly one page (per-page chunk_index)."""
    from langchain_community.document_loaders import PyPDFLoader

    pages = PyPDFLoader(str(path)).load()
    chunks, total_chars = [], 0
    for page_doc in pages:
        page = page_doc.metadata.get("page", 0) + 1
        page_text = page_doc.page_content or ""
        total_chars += len(page_text.strip())
        for chunk_index, piece in enumerate(splitter.split_text(page_text)):
            if not piece.strip():
                continue
            meta = {"source_doc_id": doc_id, "title": title, "page": page,
                    "chunk_index": chunk_index, "text": piece}
            chunks.append((f"{doc_id}-p{page}-{chunk_index}", piece, meta))
    return chunks, (total_chars == 0)


# ---- semantic ----
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def load_pages(path: Path):
    from langchain_community.document_loaders import PyPDFLoader

    return [
        (pg.metadata.get("page", 0) + 1, pg.page_content or "")
        for pg in PyPDFLoader(str(path)).load()
    ]


def estimate_sentences(pages) -> int:
    text = "\n".join(t for _, t in pages)
    return len([s for s in re.split(SENTENCE_SPLIT_REGEX, text) if s.strip()])


def build_chunks_semantic(chunker, doc_id: str, title: str, pages):
    """Semantic chunks over whole-doc text. page = 1-based PDF page where the chunk STARTS,
    via a normalized-offset -> page span table. chunk_index is per-document sequential."""
    # Normalized page spans over the concatenated (normalized) text.
    spans, cursor = [], 0
    for page, ptext in pages:
        n = len(_norm(ptext))
        spans.append((cursor, cursor + n, page))
        cursor += n
    full_text = "\n".join(t for _, t in pages)
    if len(_norm(full_text)) == 0:
        return [], True

    norm_full = _norm(full_text)
    page_count = pages[-1][0] if pages else 1

    def page_at(off: int) -> int:
        for s, e, pg in spans:
            if s <= off < e:
                return pg
        return spans[-1][2]

    pieces = chunker.split_text(full_text)
    chunks, search = [], 0
    ci = 0
    for piece in pieces:
        if not piece.strip():
            continue
        npiece = _norm(piece)
        probe = npiece[:40] or npiece
        idx = norm_full.find(probe, search)
        if idx == -1:
            idx = norm_full.find(probe)  # fallback: global
        if idx == -1:
            idx = search  # last resort
        page = page_at(idx)
        search = idx + max(1, len(npiece))
        meta = {"source_doc_id": doc_id, "title": title, "page": page,
                "chunk_index": ci, "text": piece}
        chunks.append((f"{doc_id}-p{page}-{ci}", piece, meta))
        ci += 1

    # Page-monotonicity + in-range assert (ALL docs) — guards cursor latching on boilerplate.
    assigned = [c[2]["page"] for c in chunks]
    if not all(1 <= p <= page_count for p in assigned):
        raise RuntimeError(f"{doc_id}: assigned page out of [1,{page_count}]: {assigned}")
    if any(assigned[i] > assigned[i + 1] for i in range(len(assigned) - 1)):
        raise RuntimeError(f"{doc_id}: start-pages not non-decreasing: {assigned}")
    return chunks, False


class CountingEmbeddings:
    """Wraps an Embeddings; tallies embed_documents calls/items and derived HTTP-batch requests
    so we can report actual embedding cost (SemanticChunker sentence embeds + chunk embeds)."""

    def __init__(self, inner, batch=EMBED_BATCH):
        self.inner, self.batch = inner, batch
        self.calls = self.items = self.requests = 0

    def embed_documents(self, texts):
        self.calls += 1
        self.items += len(texts)
        self.requests += math.ceil(len(texts) / self.batch) if texts else 0
        return self.inner.embed_documents(texts)

    def embed_query(self, text):
        return self.inner.embed_query(text)


def main() -> None:
    import tiktoken
    from langchain_openai import OpenAIEmbeddings
    from pinecone import Pinecone

    from src.config import get_settings

    if STRATEGY not in NAMESPACE_BY_STRATEGY:
        raise SystemExit(f"Unknown CHUNKING_STRATEGY={STRATEGY!r}; expected one of {list(NAMESPACE_BY_STRATEGY)}")
    namespace = NAMESPACE_BY_STRATEGY[STRATEGY]
    print(f"Strategy: {STRATEGY}  ->  namespace: {namespace}")

    settings = get_settings()
    pc = Pinecone(api_key=settings.pinecone_api_key)
    ensure_index(pc, settings.index_name)
    index = pc.Index(settings.index_name)

    docs = json.loads(MANIFEST_PATH.read_text())["docs"]
    ingest_docs = [d for d in docs if d.get("ingest") is True]
    print(f"\nManifest: {len(ingest_docs)} docs with ingest=true (of {len(docs)}).\n")

    enc = tiktoken.get_encoding("cl100k_base")
    all_chunks, per_doc_counts, zero_text_docs = [], {}, []
    counter = None
    sent_items = sent_reqs = 0

    if STRATEGY == "fixed_500_50":
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=FIXED_CHUNK_SIZE, chunk_overlap=FIXED_CHUNK_OVERLAP
        )
        for d in ingest_docs:
            doc_id, title, path = d["doc_id"], d["title"], REPO_ROOT / d["path"]
            if not path.exists():
                raise FileNotFoundError(f"{doc_id}: manifest path missing on disk: {path}")
            chunks, zero = build_chunks_fixed(splitter, doc_id, title, path)
            per_doc_counts[doc_id] = len(chunks)
            zero_text_docs += [doc_id] if zero else []
            all_chunks += chunks
            print(f"  {doc_id:34s} {len(chunks):5d} chunks")
        embedder = OpenAIEmbeddings(model=EMBED_MODEL, chunk_size=EMBED_BATCH)

    else:  # semantic
        from langchain_experimental.text_splitter import SemanticChunker

        # Load all docs once; pre-embed cost CHECKPOINT (analytical, no embedding yet).
        loaded = {}
        for d in ingest_docs:
            doc_id, path = d["doc_id"], REPO_ROOT / d["path"]
            if not path.exists():
                raise FileNotFoundError(f"{doc_id}: manifest path missing on disk: {path}")
            loaded[doc_id] = load_pages(path)
        est_sent = {did: estimate_sentences(pgs) for did, pgs in loaded.items()}
        est_tokens = {did: sum(len(enc.encode(t)) for _, t in pgs) for did, pgs in loaded.items()}
        total_est_sent = sum(est_sent.values())
        total_est_reqs = sum(math.ceil(s / EMBED_BATCH) for s in est_sent.values())
        print("\n--- PRE-EMBED CHECKPOINT (estimate; sentence embeds are BATCHED via embed_documents) ---")
        for did in ("niosh-pocket-guide", "epa-rmp-general-guidance"):
            if did in est_sent:
                print(f"  {did:30s} ~{est_sent[did]:6d} sentences  ~{math.ceil(est_sent[did]/EMBED_BATCH)} req  ~{est_tokens[did]:,} tok")
        est_cost = sum(est_tokens.values()) / 1_000_000 * EMBED_RATE_PER_1M
        print(f"  CORPUS TOTAL ~{total_est_sent} sentences, ~{total_est_reqs} sentence-embed requests, "
              f"~{sum(est_tokens.values()):,} tok  (~${est_cost:.4f} for the sentence pass at "
              f"${EMBED_RATE_PER_1M}/1M as of {EMBED_RATE_DATE}; chunk-embed pass adds ~similar)")

        counter = CountingEmbeddings(OpenAIEmbeddings(model=EMBED_MODEL, chunk_size=EMBED_BATCH))
        chunker = SemanticChunker(counter, breakpoint_threshold_type="percentile")
        for d in ingest_docs:
            doc_id, title = d["doc_id"], d["title"]
            chunks, zero = build_chunks_semantic(chunker, doc_id, title, loaded[doc_id])
            per_doc_counts[doc_id] = len(chunks)
            zero_text_docs += [doc_id] if zero else []
            all_chunks += chunks
            print(f"  {doc_id:34s} {len(chunks):5d} chunks")
        sent_items, sent_reqs = counter.items, counter.requests  # snapshot: sentence embeds
        embedder = counter

    total_chunks = len(all_chunks)

    # Unique-id guard BEFORE upsert.
    ids = [c[0] for c in all_chunks]
    if len(set(ids)) != len(ids):
        from collections import Counter
        dupes = [i for i, n in Counter(ids).items() if n > 1]
        raise RuntimeError(f"Duplicate deterministic ids ({len(dupes)}): {dupes[:5]} ...")

    # Fail fast if any chunk exceeds the embedding token limit (relevant for semantic).
    chunk_tokens = [len(enc.encode(c[1])) for c in all_chunks]
    if chunk_tokens and max(chunk_tokens) > EMBED_MAX_TOKENS:
        big = max(range(len(chunk_tokens)), key=lambda i: chunk_tokens[i])
        raise SystemExit(f"Chunk {all_chunks[big][0]} has {chunk_tokens[big]} tokens > {EMBED_MAX_TOKENS} "
                         f"embed limit — semantic chunk too large; needs a size cap.")
    total_tokens = sum(chunk_tokens)

    print(f"\nEmbedding {total_chunks} chunks with {EMBED_MODEL}...")
    texts = [c[1] for c in all_chunks]
    vectors = embedder.embed_documents(texts)
    if len(vectors) != total_chunks:
        raise RuntimeError(f"Embedding count {len(vectors)} != chunk count {total_chunks}")

    print(f"Upserting to namespace '{namespace}'...")
    payload = [{"id": c[0], "values": v, "metadata": c[2]} for c, v in zip(all_chunks, vectors)]
    for i in range(0, len(payload), UPSERT_BATCH):
        index.upsert(vectors=payload[i : i + UPSERT_BATCH], namespace=namespace)

    observed = poll_vector_count(index, namespace, expected=total_chunks)

    # ---- Report ----
    print("\n" + "=" * 60)
    print(f"INGESTION REPORT ({STRATEGY} -> {namespace})")
    print("=" * 60)
    for doc_id, n in per_doc_counts.items():
        print(f"  {doc_id:34s} {n:5d}")
    print(f"\nTotal non-empty chunks generated : {total_chunks}")
    print(f"Namespace '{namespace}' vector_count: {observed}")

    lens = [len(c[1]) for c in all_chunks]
    if lens:
        p95 = sorted(lens)[min(len(lens) - 1, int(0.95 * len(lens)))]
        print(f"Chunk char-length: mean={statistics.mean(lens):.0f} median={statistics.median(lens):.0f} "
              f"p95={p95} max={max(lens)}")

    est_cost = total_tokens / 1_000_000 * EMBED_RATE_PER_1M
    print(f"Chunk-embed tokens (tiktoken)    : {total_tokens:,}")
    if STRATEGY == "semantic" and counter is not None:
        chunk_items = counter.items - sent_items
        chunk_reqs = counter.requests - sent_reqs
        print(f"Sentence-embed pass (measured)   : items={sent_items:,} requests={sent_reqs}")
        print(f"Chunk-embed pass (measured)      : items={chunk_items:,} requests={chunk_reqs}")
        print(f"TOTAL embed requests (measured)  : {counter.requests}  (all via batched embed_documents)")
    print(f"Estimated chunk-embed cost       : ${est_cost:.4f} "
          f"(${EMBED_RATE_PER_1M}/1M {EMBED_MODEL} rate as of {EMBED_RATE_DATE} — verify current pricing)")
    print(f"\nZero-text docs: {zero_text_docs or 'none'}")

    if observed != total_chunks:
        raise SystemExit(f"\nRECONCILIATION FAILED: vector_count ({observed}) != chunks ({total_chunks}).")
    print(f"\n✅ Reconciliation OK: {observed} vectors == {total_chunks} chunks generated.")


if __name__ == "__main__":
    main()
