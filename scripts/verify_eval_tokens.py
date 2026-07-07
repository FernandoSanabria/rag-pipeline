"""Verify that eval-candidate key-value tokens actually appear on their cited pages.

Dev tooling — NOT part of the src/ runtime. Reads a candidates JSON (list of objects with
source_doc_id / source_page / key_value_token / question; each may be scalar or a list for
cross-doc rows) and checks, after NFKC + whitespace normalization, that each token is present in
the extracted text of its cited page. Pairs with extract_pages.py (run that first to populate
scratchpad/pages/).

Usage from repo root:  uv run python scripts/verify_eval_tokens.py <candidates.json>
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

PAGES = Path(__file__).resolve().parents[1] / "scratchpad" / "pages"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace("­", "")
    # collapse all whitespace so line-breaks / double-spaces / spaced-out words match
    return re.sub(r"\s+", "", s).lower()


def page_text(doc_id: str, page: int) -> str:
    f = PAGES / f"{re.sub(r'[^A-Za-z0-9._-]', '_', doc_id)}.txt"
    txt = f.read_text(encoding="utf-8")
    parts = re.split(r"===== .*? PAGE (\d+)/\d+ =====", txt)  # [pre, '1', text1, '2', text2, ...]
    for i in range(1, len(parts), 2):
        if int(parts[i]) == page:
            return parts[i + 1]
    return ""


def check(doc_id: str, page: int, token: str) -> bool:
    return norm(token) in norm(page_text(doc_id, page))


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: uv run python scripts/verify_eval_tokens.py <candidates.json>")
    cands = json.loads(Path(sys.argv[1]).read_text())
    ok = bad = 0
    for c in cands:
        as_list = lambda v: v if isinstance(v, list) else [v]
        doc_ids = as_list(c["source_doc_id"])
        pages = as_list(c["source_page"])
        tokens = as_list(c["key_value_token"])
        allok = True
        for did, pg, tok in zip(doc_ids, pages, tokens):
            if not check(did, pg, tok):
                allok = False
                print(f"  MISS  {did} p{pg}  token={tok!r}")
        if allok:
            ok += 1
        else:
            bad += 1
            print(f"FAIL: {c.get('question', '')[:70]}")
    print(f"\nverified={ok}  failed={bad}  total={len(cands)}")


if __name__ == "__main__":
    main()
