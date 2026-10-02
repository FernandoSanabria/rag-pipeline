"""G5 P2/P3 probe: MCP `search_safety_docs` over stdio vs a direct in-process `dense_search`, plus provenance.

Pre-registered in eval/g5_PREDICTION.md (P2, P3). For each of the five frozen queries at k=10, interleaved:
direct call 1 -> the MCP call (one stdio session for all five) -> direct call 2.
- P2 compares the ordered (source_doc_id, page, text) sequences, MCP vs direct call 1. Direct 1 vs direct 2 is
  the control: it can attribute a P2 failure to embedding non-determinism, but never un-falsifies it.
- P3 checks every MCP row against data/manifest.json: non-empty text, kind == "document", and tier / license /
  title / publisher equal to the manifest; no title that is a manifest filename or ends in ".pdf"; both tiers
  present across the transcript.

Prints ids, pages and counts only — never chunk text — so the output is safe to paste.
Cost: 15 embedding calls (~$0). Usage: uv run python scripts/mcp_parity_probe.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from src.retrieve import dense_search  # noqa: E402

K = 10
QUERIES = [  # (dataset row, query, source_doc_id), verbatim from eval/g5_PREDICTION.md (P2)
    (1, "What is the RMP threshold quantity for anhydrous ammonia?", None),
    (16, "What is the required sequence of actions for applying lockout/tagout before servicing equipment?", None),
    (21, "Do the Airgas chlorine SDS and OSHA's air-contaminants table agree on the chlorine exposure ceiling?", None),
    (4, "What is the Maximum Diaphragm Casing Pressure for the Fisher 667 actuator size 30/30i?", None),
    (25, "What is the flash point of acetone per the Sigma-Aldrich SDS?", "sds-sigma-aldrich-acetone"),
]
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "manifest.json"
MANIFEST = {d["doc_id"]: d for d in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["docs"]}


def sequence(rows: list[dict]) -> list[tuple]:
    return [(r["source_doc_id"], r["page"], r["text"]) for r in rows]


def first_difference(a: list[tuple], b: list[tuple]) -> str:
    """Where two sequences first differ, by (doc, page) and a text-equality flag — never the text itself."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"index {i}: {x[:2]} vs {y[:2]} (text equal: {x[2] == y[2]})"
    return f"lengths {len(a)} vs {len(b)}" if len(a) != len(b) else "none"


def provenance_violations(rows: list[dict]) -> list[str]:
    bad = []
    for r in rows:
        doc = MANIFEST.get(r["source_doc_id"])
        problems = [f for f in ("tier", "license", "title", "publisher") if doc is None or r[f] != doc[f]]
        if not r["text"]:
            problems.append("empty text")
        if r["kind"] != "document":
            problems.append(f"kind={r['kind']}")
        if doc and (r["title"] == doc["filename"] or r["title"].endswith(".pdf")):
            problems.append("filename as title")
        if problems:
            bad.append(f"rank {r['rank']} {r['source_doc_id']}: {problems}")
    return bad


async def run() -> list[dict]:
    server = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"], env=dict(os.environ))
    report = []
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        for row, query, doc in QUERIES:
            direct_1 = dense_search(query, k=K, source_doc_id=doc)
            args = {"query": query, "k": K} | ({"source_doc_id": doc} if doc else {})
            result = await session.call_tool("search_safety_docs", args)
            direct_2 = dense_search(query, k=K, source_doc_id=doc)
            if result.isError:
                report.append({"row": row, "error": result.content[0].text})
                continue
            rows = result.structuredContent["results"]
            report.append({
                "row": row,
                "filter": doc,
                "n_mcp": len(rows),
                "n_direct": len(direct_1),
                "p2_mcp_eq_direct": sequence(rows) == sequence(direct_1),
                "p2_first_difference": first_difference(sequence(rows), sequence(direct_1)),
                "control_direct_eq_direct": sequence(direct_1) == sequence(direct_2),
                "tiers": sorted({r["tier"] for r in rows}),
                "p3_violations": provenance_violations(rows),
                "mcp_doc_pages": [f"{r['source_doc_id']}:{r['page']}" for r in rows],
            })
    return report


def main() -> None:
    report = asyncio.run(run())
    print(f"{'row':>3} | {'filter':<25} | n mcp/direct | P2 MCP==direct | control direct==direct | tiers  | P3 violations")
    for r in report:
        if "error" in r:
            print(f"{r['row']:>3} | MCP ERROR: {r['error']}")
            continue
        print(
            f"{r['row']:>3} | {str(r['filter'] or '-'):<25} | {r['n_mcp']:>5}/{r['n_direct']:<6} | "
            f"{str(r['p2_mcp_eq_direct']):<14} | {str(r['control_direct_eq_direct']):<22} | "
            f"{str(r['tiers']):<6} | {len(r['p3_violations'])}"
        )
    print("\nper-query MCP (doc:page) sequences, in rank order:")
    for r in report:
        if "error" not in r:
            print(f"  row {r['row']}: {', '.join(r['mcp_doc_pages'])}")
            if not r["p2_mcp_eq_direct"]:
                print(f"    P2 first difference: {r['p2_first_difference']}")
            for violation in r["p3_violations"]:
                print(f"    P3 violation: {violation}")
    ok = [r for r in report if "error" not in r]
    tiers = sorted({t for r in ok for t in r["tiers"]})
    print("\nsummary:")
    print(f"  queries answered without error: {len(ok)}/{len(QUERIES)}")
    print(f"  P2 (MCP == direct) on all queries: {len(ok) == len(QUERIES) and all(r['p2_mcp_eq_direct'] for r in ok)}")
    print(f"  control (direct == direct) on all queries: {all(r['control_direct_eq_direct'] for r in ok)}")
    print(f"  P3 rows checked: {sum(r['n_mcp'] for r in ok)}; violations: {sum(len(r['p3_violations']) for r in ok)}")
    print(f"  tiers present across the transcript: {tiers}")


if __name__ == "__main__":
    main()
