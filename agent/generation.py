"""G11 provider seam for the agent path's generation (eval/g11_design.md).

`GENERATION_PROVIDER` selects the generator:
- "openai" (the default) DELEGATES to the frozen `src.generate.generate`: the shipped default is the frozen call itself
  (the same ChatOpenAI(seed=42) instance, traceable span and backend accumulator), not a re-implementation, so its
  request body is byte-identical to the pre-G11 one by construction (tests/test_generation.py proves it on the 28).
- "bedrock" sends the SAME two messages to Amazon Bedrock's Converse API through boto3. There is no langchain-aws:
  its langchain-core 0.3 line requires numpy<2, which would downgrade numpy under the whole project.

Only generation moves behind the seam. The router, tool decision, decomposer and input-guard classifier stay on
gpt-4o-mini, and /ask (src.pipeline) stays on OpenAI by design.

What Bedrock cannot record: Converse returns no system_fingerprint and no model version, and Nova has no seed
parameter. Each Bedrock call records instead the requested inference-profile id (its only version signal), the AWS
request id, latencyMs, stopReason, serviceTier and token usage (`calls()`).
"""

import logging
import os
import time
from functools import lru_cache

from langsmith import traceable

from src import generate as frozen

logger = logging.getLogger(__name__)

PROVIDERS = ("openai", "bedrock")
DEFAULT_BEDROCK_MODEL = "us.amazon.nova-2-lite-v1:0"
DEFAULT_REGION = "us-east-1"

_calls: list[dict] = []  # one derived record per Bedrock call (no text); read by the eval, never persisted here


def provider() -> str:
    """The configured provider. An unknown value raises rather than silently falling back (like run_eval's PIPELINE)."""
    name = os.environ.get("GENERATION_PROVIDER", "").strip().lower() or "openai"
    if name not in PROVIDERS:
        raise ValueError(f"unknown GENERATION_PROVIDER={name!r} (expected one of {PROVIDERS})")
    return name


def bedrock_model_id() -> str:
    return os.environ.get("BEDROCK_MODEL_ID", "").strip() or DEFAULT_BEDROCK_MODEL


def human_message(question: str, contexts: list[str]) -> str:
    """The frozen human message (copied from src.generate.generate; a hermetic test asserts it stays byte-identical)."""
    context_block = "\n\n---\n\n".join(contexts) if contexts else "(no context retrieved)"
    return f"Context:\n{context_block}\n\nQuestion: {question}"


def build_messages(question: str, contexts: list[str]) -> list[tuple[str, str]]:
    """The two messages every provider receives: the frozen SYSTEM_PROMPT (imported, not copied) and the human message."""
    return [("system", frozen.SYSTEM_PROMPT), ("human", human_message(question, contexts))]


def generate(question: str, contexts: list[str]) -> str:
    """The agent path's generator (agent/graph.py's generate node). openai: the frozen call; bedrock: Converse."""
    if provider() == "openai":
        return frozen.generate(question, contexts)
    return generate_with(bedrock_client(), question, contexts)


@lru_cache(maxsize=1)
def bedrock_client():
    """A bedrock-runtime client. boto3 is imported here only, so the default openai path never loads it. Credentials
    come from the environment (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY); the region from AWS_REGION."""
    import boto3
    from botocore.config import Config

    region = os.environ.get("AWS_REGION", "").strip() or DEFAULT_REGION
    return boto3.client("bedrock-runtime", region_name=region,
                        config=Config(retries={"mode": "standard", "max_attempts": 4}, connect_timeout=10,
                                      read_timeout=120))


@traceable(name="generate_bedrock")
def generate_with(client, question: str, contexts: list[str], model_id: str | None = None) -> str:
    """One Converse call with the frozen messages. temperature 0; topP and maxTokens unset, as in the OpenAI arm; Nova
    has no seed. Returns the concatenated text blocks, or "" (mirroring src.generate's `content or ""`)."""
    model_id = model_id or bedrock_model_id()
    (_, system_text), (_, human_text) = build_messages(question, contexts)
    started = time.perf_counter()
    response = client.converse(
        modelId=model_id,
        system=[{"text": system_text}],
        messages=[{"role": "user", "content": [{"text": human_text}]}],
        inferenceConfig={"temperature": 0},
    )
    wall_s = time.perf_counter() - started
    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    text = "".join(block["text"] for block in blocks if "text" in block)
    usage = response.get("usage") or {}
    _calls.append({
        "provider": "bedrock",
        "model_id": model_id,
        "request_id": (response.get("ResponseMetadata") or {}).get("RequestId"),
        "latency_ms": (response.get("metrics") or {}).get("latencyMs"),
        "wall_s": round(wall_s, 3),
        "stop_reason": response.get("stopReason"),
        "service_tier": (response.get("serviceTier") or {}).get("type"),
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        "cache_read_tokens": usage.get("cacheReadInputTokens"),
    })
    if not text:
        logger.warning("bedrock generation returned no text (stopReason=%s)", response.get("stopReason"))
    return text or ""


def calls() -> list[dict]:
    """The Bedrock call records since the last reset (derived values only: ids, tokens, latency, stop reason)."""
    return list(_calls)


def reset_calls() -> None:
    _calls.clear()
