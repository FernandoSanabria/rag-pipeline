"""Dump per-page, NFKC-normalized text for every manifest doc.

Dev tooling — NOT part of the src/ runtime. Writes one .txt per doc_id to the (gitignored)
scratchpad/pages/ dir, with a `===== <doc_id> PAGE n/N =====` marker before each page. Used to
audit/regenerate eval-dataset grounding (see verify_eval_tokens.py). Regenerable on demand from
the PDFs + manifest, so the dumps are not committed.

Run from the repo root:  uv run python scripts/extract_pages.py
"""

import json
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "scratchpad" / "pages"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return s.replace("­", "")  # strip soft hyphen


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    docs = json.loads((REPO / "data" / "manifest.json").read_text())["docs"]

    index = []
    for d in docs:
        did, path = d["doc_id"], REPO / d["path"]
        if not path.exists():
            print("MISSING", did)
            continue
        reader = PdfReader(str(path))
        chunks = [
            f"\n===== {did} PAGE {i}/{len(reader.pages)} =====\n{norm(pg.extract_text() or '')}"
            for i, pg in enumerate(reader.pages, start=1)
        ]
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", did)
        (OUT / f"{safe}.txt").write_text("".join(chunks), encoding="utf-8")
        index.append((did, len(reader.pages), d["path"]))

    for did, n, p in index:
        print(f"{did:34s} {n:4d}p  {p}")
    print("TOTAL DOCS:", len(index))
    print("output:", OUT)


if __name__ == "__main__":
    main()
