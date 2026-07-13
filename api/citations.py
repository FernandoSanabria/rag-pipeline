"""Citations derived from retrieved-chunk METADATA — never parsed from the model's prose.

The generator has been observed to cite the wrong page (e.g. page 46 for a value on page 44). So the
API ignores the model's stated pages entirely and builds citations from the retrieved chunks'
`source_doc_id` + `page` metadata (ground truth), deduped by (document, page). `document` renders from
the manifest `title` (per CLAUDE.md), never a raw filename. A refused/empty answer yields no citations.

Selection rule: ALL retrieved chunks (deduped) — fully deterministic, no dependence on model output.
This can over-cite (e.g. a neighbour doc pulled in at k=10), but every citation is a document the
pipeline actually retrieved to answer the question.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

from api.confidence import is_refusal

# api/citations.py -> parents[1] is the repo root; data/manifest.json is committed (PDFs are not).
_DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "data" / "manifest.json"


@lru_cache
def _titles() -> dict[str, str]:
    """Map source_doc_id -> human title from the manifest (loaded once)."""
    path = Path(os.environ.get("MANIFEST_PATH", str(_DEFAULT_MANIFEST)))
    docs = json.loads(path.read_text(encoding="utf-8")).get("docs", [])
    return {d["doc_id"]: d["title"] for d in docs if d.get("doc_id")}


def derive_citations(answer: str, chunks: list[dict]) -> list[dict]:
    """Build [{document, page}] from retrieved chunk metadata, deduped. Empty on refusal/empty answer."""
    if not answer.strip() or is_refusal(answer):
        return []
    titles = _titles()
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    for chunk in chunks:
        doc = chunk.get("source_doc_id")
        page = chunk.get("page")
        if not doc or page is None:  # skip chunks missing provenance rather than emitting a bad citation
            continue
        key = (doc, page)
        if key in seen:
            continue
        seen.add(key)
        out.append({"document": titles.get(doc, doc), "page": page})
    return out
