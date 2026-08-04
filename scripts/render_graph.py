"""Print the agent graph as Mermaid, straight from the LIVE compiled graph.

The README's Architecture diagram is a PROJECTION of this output, not a hand-authored parallel
artifact — so it can't silently drift from the code. After any node/edge change in agent/graph.py,
regenerate the fenced ```mermaid block in README.md from this script's output:

    uv run python scripts/render_graph.py

Read-only: it compiles the graph in-process and prints; no network, no LLM, no file writes. (We
deliberately do NOT render the mermaid.ink PNG or the grandalf ASCII — both add a network/dependency
cost; a fenced Mermaid block renders natively on GitHub with zero deps.)

The only allowed cosmetic diffs between this output and the README block are: the START/END nodes are
relabeled from LangGraph's `__start__`/`__end__` for readability, and Mermaid's config frontmatter is
stripped. Nodes and edges must match exactly — that is what keeps "regenerable" true, not aspirational.
"""

from agent.graph import _compiled_graph

if __name__ == "__main__":
    print(_compiled_graph().get_graph().draw_mermaid())
