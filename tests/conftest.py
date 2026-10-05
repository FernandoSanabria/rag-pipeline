"""Hermetic test config — no real secrets, no network.

Dummy credentials are set BEFORE any test module imports `api.main` (which imports `src.pipeline`).
`api.main` calls `load_dotenv()` with the default `override=False`, so these dummy values win even if a
real `.env` is present in the repo root. The integration test stubs `ask()`, so the OpenAI/Pinecone
clients (which read these env vars lazily) are never instantiated and no network call is made.
"""

import os

import pytest

# Force dummy values so the suite is identical with or without a real .env / exported keys.
os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["PINECONE_API_KEY"] = "test-pinecone-key"
os.environ["INDEX_NAME"] = "test-index"


@pytest.fixture(autouse=True)
def _classifier_in_scope(monkeypatch):
    """The G6 input guard calls gpt-4o-mini whenever it runs. It is off in the shipped app, but tests/test_guards.py
    switches it on, so stub that call to `in_scope` for every test to keep the suite hermetic; tests/test_guards.py
    overrides it where the label matters."""
    from api import guards

    monkeypatch.setattr(guards, "classify_with_meta", lambda question: ("in_scope", {}))
