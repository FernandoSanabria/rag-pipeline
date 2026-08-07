"""Guard tests for src/ingest.py's intent-aware ingest/retrieval namespace guard.

Pure/unit + one subprocess. NO network, NO Pinecone, NO OpenAI: importing src.ingest runs only
load_dotenv + env reads (harmless; conftest sets dummy keys), and `_assert_ingest_target_intended`
is side-effect-free. The subprocess proves a mismatched `python src/ingest.py` aborts at the guard
BEFORE any network call (keys stripped -> if the guard ever failed to fire, we'd see a different
error, not the guard message).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.ingest import NAMESPACE_BY_STRATEGY, _assert_ingest_target_intended

REPO = Path(__file__).resolve().parents[1]


def test_explicit_matching_target_passes():
    # Each valid target, RETRIEVAL_NAMESPACE explicitly set to match -> clears the guard.
    assert _assert_ingest_target_intended("fixed_500_50", {"RETRIEVAL_NAMESPACE": "fixed_500_50"}) == "fixed_500_50"
    assert _assert_ingest_target_intended("semantic", {"RETRIEVAL_NAMESPACE": "semantic"}) == "semantic"


def test_readme_v1_reproduction_command_clears_the_guard():
    # The documented v1 ingest — `RETRIEVAL_NAMESPACE=fixed_500_50 uv run python src/ingest.py`
    # (default CHUNKING_STRATEGY=fixed_500_50) — must still pass verbatim.
    assert _assert_ingest_target_intended("fixed_500_50", {"RETRIEVAL_NAMESPACE": "fixed_500_50", "RETRIEVAL_K": "5"}) == "fixed_500_50"


def test_bare_defaults_only_is_refused():
    # RETRIEVAL_NAMESPACE absent from the environment -> at its default -> refuse (the footgun case).
    with pytest.raises(SystemExit) as exc:
        _assert_ingest_target_intended("fixed_500_50", {})
    assert "Refusing to ingest" in str(exc.value)


def test_explicit_mismatch_is_refused():
    # RETRIEVAL_NAMESPACE set but pointing at a different namespace than the write target -> refuse.
    with pytest.raises(SystemExit) as exc:
        _assert_ingest_target_intended("semantic", {"RETRIEVAL_NAMESPACE": "semantic_v2"})
    msg = str(exc.value)
    assert "Refusing to ingest" in msg and "semantic_v2" in msg


def test_unknown_strategy_is_refused_with_semantic_v2_hint():
    # Unknown CHUNKING_STRATEGY must fail loudly (no silent fallback), and point at the build script.
    with pytest.raises(SystemExit) as exc:
        _assert_ingest_target_intended("semantic_v2", {"RETRIEVAL_NAMESPACE": "semantic_v2"})
    msg = str(exc.value)
    assert "Unknown CHUNKING_STRATEGY" in msg and "build_semantic_v2.py" in msg


def test_semantic_v2_is_not_an_ingest_namespace():
    # ingest.py cannot produce the shipped namespace; it's a build-script artifact.
    assert "semantic_v2" not in NAMESPACE_BY_STRATEGY
    assert set(NAMESPACE_BY_STRATEGY) == {"fixed_500_50", "semantic"}


def test_mismatched_run_aborts_before_any_network():
    # End-to-end: a mismatched `python src/ingest.py` exits non-zero at the guard, fast, with NO api
    # keys in the environment -> proof it never reached Pinecone/OpenAI.
    env = {k: v for k, v in os.environ.items()
           if k not in ("CHUNKING_STRATEGY", "OPENAI_API_KEY", "PINECONE_API_KEY")}
    env["RETRIEVAL_NAMESPACE"] = "semantic"  # mismatched to the default fixed_500_50 write target
    r = subprocess.run(
        [sys.executable, "src/ingest.py"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode != 0
    assert "Refusing to ingest" in (r.stdout + r.stderr)
