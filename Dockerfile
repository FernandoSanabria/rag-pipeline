# FastAPI service for the industrial-equipment-safety RAG pipeline.
# Secrets (OPENAI_API_KEY, PINECONE_API_KEY) are provided at RUNTIME via the platform env store —
# never baked into the image. The corpus PDFs are never copied; only data/manifest.json (for citations).
FROM python:3.11-slim

# uv (pinned) — copied from the official image; used only to install the pinned deps at build time.
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 1) Dependency layer — cached until pyproject/uv.lock change. --no-dev excludes pytest/httpx;
#    --no-install-project because we import src/api via PYTHONPATH rather than installing the project.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) App code + the manifest the citation layer reads. NO PDFs, NO .env, NO tests, NO eval artifacts.
COPY src/ ./src/
COPY api/ ./api/
COPY data/manifest.json ./data/manifest.json

# 3) Drop privileges.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# $PORT is injected by Render/Railway; defaults to 8000 for local runs.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
