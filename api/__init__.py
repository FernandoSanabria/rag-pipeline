"""FastAPI service layer for the industrial-equipment-safety RAG pipeline.

Wraps the frozen `src.pipeline.ask` with two derived, deterministic layers — a refusal-gated
confidence signal (`api.confidence`) and metadata-derived citations (`api.citations`) — and exposes
them over a small JSON API (`api.main`). No retrieval or generation logic lives here.
"""
