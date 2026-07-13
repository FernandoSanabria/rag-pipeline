"""Hermetic test config — no real secrets, no network.

Dummy credentials are set BEFORE any test module imports `api.main` (which imports `src.pipeline`).
`api.main` calls `load_dotenv()` with the default `override=False`, so these dummy values win even if a
real `.env` is present in the repo root. The integration test stubs `ask()`, so the OpenAI/Pinecone
clients (which read these env vars lazily) are never instantiated and no network call is made.
"""

import os

# Force dummy values so the suite is identical with or without a real .env / exported keys.
os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["PINECONE_API_KEY"] = "test-pinecone-key"
os.environ["INDEX_NAME"] = "test-index"
