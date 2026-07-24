"""Phase 2 agentic orchestration layer (LangGraph).

Thin orchestration package layered on the unchanged Phase 1 `src/` capabilities
(retrieval, generation). Intentionally empty at creation — the StateGraph, nodes,
and AgentState land in subsequent Phase 2A steps. Present now so the package ships
in the built wheel (`[tool.hatch.build.targets.wheel]`) and is importable.
"""
