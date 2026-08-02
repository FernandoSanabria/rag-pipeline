"""Build the `semantic_v2` namespace (Approach B) — copy 17 docs byte-identical + re-chunk the 2
targets structure-aware. Reuses src/ingest.py helpers (never forks them); NEVER writes `semantic`.

Reversibility: semantic_v2 is a NEW namespace in the existing index; revert = leave RETRIEVAL_NAMESPACE
on `semantic`. This script does NOT promote/flip any default.

Re-chunk design + pre-registration: eval/rechunk_2bc_design.md (on main). NIOSH = name-anchored
per-entry + prose fallback; acetone = per-SECTION.
"""

import math
import re
import statistics
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

import json  # noqa: E402

from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.ingest import (  # noqa: E402  (reuse, do not fork)
    EMBED_BATCH, EMBED_MAX_RETRIES, EMBED_MAX_TOKENS, EMBED_MODEL, EMBED_TIMEOUT,
    MAX_CHUNK_CHARS, _norm, load_pages, poll_vector_count,
)

SRC_NS, DST_NS = "semantic", "semantic_v2"
TARGETS = {"niosh-pocket-guide", "sds-sigma-aldrich-acetone"}
FALLBACK_CHARS = 1500  # prose fallback for non-entry spans
CAP = MAX_CHUNK_CHARS   # a single entry/section over this absorbed non-entry text -> prose fallback
_prose = RecursiveCharacterTextSplitter(chunk_size=FALLBACK_CHARS, chunk_overlap=0)


def prose_split(t):
    return [p for p in _prose.split_text(t) if p.strip()]


# ---------- splitters (design's boundary rules) ----------
def split_niosh(full):
    starts = []
    for m in re.finditer(r"Formula:", full):
        ls = full.rfind("\n", 0, m.start()) + 1
        before = full[ls:m.start()].strip()
        starts.append(ls if before else full.rfind("\n", 0, ls - 1) + 1)  # anchor on NAME line
    starts = sorted(set(starts))
    pieces = []
    if not starts:
        return prose_split(full)
    if starts[0] > 0:
        pieces += prose_split(full[: starts[0]])  # front-matter -> fallback
    for i, s in enumerate(starts):
        seg = full[s : (starts[i + 1] if i + 1 < len(starts) else len(full))]
        pieces += prose_split(seg) if len(seg) > CAP else [seg]  # oversized (appendix-absorbed) -> fallback
    return [p for p in pieces if p.strip()]


def split_acetone(full):
    sec = re.compile(r"SECTION\s+\d+\s*:", re.I)
    idxs = [m.start() for m in sec.finditer(full)]
    if not idxs:
        return prose_split(full)
    pieces = []
    if idxs[0] > 0:
        pieces.append(full[: idxs[0]])
    b = idxs + [len(full)]
    for i in range(len(idxs)):
        seg = full[b[i] : b[i + 1]]
        pieces += prose_split(seg) if len(seg) > CAP else [seg]
    return [p for p in pieces if p.strip()]


# ---------- page mapping (mirrors src/ingest.build_chunks_semantic) ----------
def build_target_chunks(doc_id, title, pages, splitter):
    full = "\n".join(t for _, t in pages)
    norm_full = _norm(full)
    spans, cursor = [], 0
    for page, ptext in pages:
        n = len(_norm(ptext))
        spans.append((cursor, cursor + n, page))
        cursor += n
    page_count = pages[-1][0] if pages else 1

    def page_at(off):
        for s, e, pg in spans:
            if s <= off < e:
                return pg
        return spans[-1][2]

    sub = RecursiveCharacterTextSplitter(chunk_size=MAX_CHUNK_CHARS, chunk_overlap=0)
    chunks, search, ci = [], 0, 0
    for piece in splitter(full):
        for sp in ([piece] if len(piece) <= MAX_CHUNK_CHARS else [x for x in sub.split_text(piece) if x.strip()]):
            nsp = _norm(sp)
            probe = nsp[:40] or nsp
            idx = norm_full.find(probe, search)
            if idx == -1:
                idx = norm_full.find(probe)
            if idx == -1:
                idx = search
            page = page_at(idx)
            search = idx + max(1, len(nsp))
            meta = {"source_doc_id": doc_id, "title": title, "page": page, "chunk_index": ci, "text": sp}
            chunks.append((f"{doc_id}-p{page}-{ci}", sp, meta))
            ci += 1
    assigned = [c[2]["page"] for c in chunks]
    assert all(1 <= p <= page_count for p in assigned), f"{doc_id}: page out of [1,{page_count}]"
    assert all(assigned[i] <= assigned[i + 1] for i in range(len(assigned) - 1)), f"{doc_id}: pages not non-decreasing"
    return chunks, full


def list_all_ids(index, namespace, prefix):
    """Exhaust the paginated list cursor — a doc with >~100 chunks paginates; partial copy must not pass."""
    ids = []
    for page in index.list(prefix=prefix, namespace=namespace):
        ids.extend([x if isinstance(x, str) else x.get("id", x) for x in page])
    return ids


def main():
    import tiktoken
    from langchain_openai import OpenAIEmbeddings
    from pinecone import Pinecone

    from src.ingest import CountingEmbeddings

    enc = tiktoken.get_encoding("cl100k_base")
    s = get_settings()
    pc = Pinecone(api_key=s.pinecone_api_key)
    index = pc.Index(s.index_name)

    stats = index.describe_index_stats()
    semantic_total = stats["namespaces"][SRC_NS]["vector_count"]
    if DST_NS in stats.get("namespaces", {}):
        raise SystemExit(f"REFUSING: namespace '{DST_NS}' already exists ({stats['namespaces'][DST_NS]['vector_count']} vectors). Delete it first or investigate.")
    docs = json.loads((REPO_ROOT / "data" / "manifest.json").read_text())["docs"]
    ingest_docs = [d for d in docs if d.get("ingest") is True]
    copy_docs = [d for d in ingest_docs if d["doc_id"] not in TARGETS]
    print(f"semantic total={semantic_total}; copy {len(copy_docs)} docs, re-chunk {len(TARGETS)}")

    # ---------- (a) COPY 17, byte-identical, pagination-exhausted ----------
    listed_count, copied_count, sample_ids = {}, {}, {}
    for d in copy_docs:
        did = d["doc_id"]
        ids = list_all_ids(index, SRC_NS, f"{did}-p")
        listed_count[did] = len(ids)
        # sample incl. highest-page id (where pagination truncation would bite)
        def pg(i):
            m = re.search(r"-p(\d+)-", i)
            return int(m.group(1)) if m else -1
        sample_ids[did] = list({ids[0], ids[len(ids) // 2], max(ids, key=pg)}) if ids else []
        copied = 0
        for i in range(0, len(ids), 100):
            batch = ids[i : i + 100]
            fr = index.fetch(ids=batch, namespace=SRC_NS)
            vecs = fr.vectors if hasattr(fr, "vectors") else fr["vectors"]
            payload = []
            for vid in batch:
                v = vecs[vid]
                vals = v.values if hasattr(v, "values") else v["values"]
                md = v.metadata if hasattr(v, "metadata") else v["metadata"]
                payload.append({"id": vid, "values": list(vals), "metadata": dict(md)})
            index.upsert(vectors=payload, namespace=DST_NS)
            copied += len(payload)
        copied_count[did] = copied
        assert copied == listed_count[did], f"{did}: copied {copied} != listed {listed_count[did]}"
        print(f"  copied {did:34s} {copied:5d}")
    total_copied = sum(copied_count.values())
    target_listed = sum(len(list_all_ids(index, SRC_NS, f"{t}-p")) for t in TARGETS)
    assert total_copied == semantic_total - target_listed, \
        f"total_copied {total_copied} != semantic({semantic_total}) - targets({target_listed})"
    print(f"  TOTAL copied={total_copied}  (== semantic {semantic_total} - targets {target_listed}) ✓")

    # ---------- (b) RE-CHUNK 2 ----------
    by_id = {d["doc_id"]: d for d in ingest_docs}
    niosh_pages = load_pages(REPO_ROOT / by_id["niosh-pocket-guide"]["path"])
    ace_pages = load_pages(REPO_ROOT / by_id["sds-sigma-aldrich-acetone"]["path"])
    niosh_chunks, niosh_full = build_target_chunks("niosh-pocket-guide", by_id["niosh-pocket-guide"]["title"], niosh_pages, split_niosh)
    ace_chunks, ace_full = build_target_chunks("sds-sigma-aldrich-acetone", by_id["sds-sigma-aldrich-acetone"]["title"], ace_pages, split_acetone)

    # in-script structural asserts (reproduce the design)
    def name_of(p): return " ".join(p.split("Formula:", 1)[0].split())
    entry_pieces = [c for c in niosh_chunks if "Formula:" in c[1] and "CAS#" in c[1][:400]]
    orphans = [c for c in entry_pieces if not (name_of(c[1]) and len(name_of(c[1])) < 70)]
    amm = [c for c in niosh_chunks if re.search(r"Ammonia\s+Formula", c[1]) and re.search(r"IDLH:\s*300", c[1]) and "Anhydrous ammonia" in c[1]]
    assert not orphans, f"NIOSH: {len(orphans)} orphaned entries (name split from value)"
    assert len(amm) == 1, f"NIOSH: ammonia entry not whole+unique ({len(amm)})"
    sec9 = [c for c in ace_chunks if re.search(r"SECTION\s+9\s*:", c[1], re.I)]
    assert len(sec9) == 1 and "-17" in sec9[0][1] and re.search(r"Flash point", sec9[0][1], re.I), "acetone Section-9/flash-point not clean"
    print(f"  niosh: {len(niosh_chunks)} chunks ({len(entry_pieces)} per-entry, 0 orphaned, ammonia whole ✓); "
          f"acetone: {len(ace_chunks)} chunks (Section-9 flash-point ✓)")
    lens = [len(c[1]) for c in niosh_chunks + ace_chunks]
    print(f"  target chunk chars: median={int(statistics.median(lens))} max={max(lens)}")

    # guards (mirror ingest): unique ids, token/char limits
    tgt = niosh_chunks + ace_chunks
    ids = [c[0] for c in tgt]
    assert len(set(ids)) == len(ids), "duplicate target ids"
    toks = [len(enc.encode(c[1])) for c in tgt]
    assert max(toks) <= EMBED_MAX_TOKENS and max(lens) <= MAX_CHUNK_CHARS, "chunk exceeds embed/char limit"
    total_tok = sum(toks)
    print(f"  PRE-EMBED CHECKPOINT: {len(tgt)} chunks, {total_tok:,} tokens ~ ${total_tok/1e6*0.02:.4f} (text-embedding-3-small)")

    # embed + upsert targets
    counter = CountingEmbeddings(OpenAIEmbeddings(model=EMBED_MODEL, chunk_size=EMBED_BATCH, timeout=EMBED_TIMEOUT, max_retries=EMBED_MAX_RETRIES))
    vectors = counter.embed_documents([c[1] for c in tgt])
    assert len(vectors) == len(tgt)
    payload = [{"id": c[0], "values": v, "metadata": c[2]} for c, v in zip(tgt, vectors)]
    for i in range(0, len(payload), 100):
        index.upsert(vectors=payload[i : i + 100], namespace=DST_NS)
    newly = len(tgt)
    print(f"  embedded+upserted {newly} target chunks ({counter.requests} embed requests)")

    # ---------- (c) RECONCILE (two-sided) ----------
    expected = total_copied + newly
    observed = poll_vector_count(index, DST_NS, expected=expected)
    print(f"\nRECONCILE: semantic_v2={observed}  expected copied({total_copied})+embedded({newly})={expected}")
    assert observed == expected, f"reconcile FAILED: {observed} != {expected}"

    # ---------- verify-by-refetch: copy is byte-identical ----------
    checked = 0
    for did, sids in sample_ids.items():
        for vid in sids:
            a = index.fetch(ids=[vid], namespace=SRC_NS)
            b = index.fetch(ids=[vid], namespace=DST_NS)
            av = (a.vectors if hasattr(a, "vectors") else a["vectors"])[vid]
            bv = (b.vectors if hasattr(b, "vectors") else b["vectors"])[vid]
            avals = list(av.values if hasattr(av, "values") else av["values"])
            bvals = list(bv.values if hasattr(bv, "values") else bv["values"])
            amd = dict(av.metadata if hasattr(av, "metadata") else av["metadata"])
            bmd = dict(bv.metadata if hasattr(bv, "metadata") else bv["metadata"])
            assert avals == bvals and amd == bmd, f"copy MISMATCH at {vid}"
            checked += 1
    print(f"VERIFY-BY-REFETCH: {checked} sampled ids (incl. highest-page) byte-identical across namespaces ✓")
    print(f"\n✅ semantic_v2 built: {observed} vectors ({total_copied} copied + {newly} re-chunked). semantic UNTOUCHED.")


if __name__ == "__main__":
    main()
