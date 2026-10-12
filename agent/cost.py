"""G8 per-request cost accounting (eval/g8_design.md R1).

`api.main._answer` (both endpoints) and the resume endpoint open one record per request with `request()`. Inside it:
- every OpenAI chat call reports its usage through `_UsageHandler`, registered once with LangChain's configure hook
  (the mechanism `get_openai_callback` uses), so it reaches every runnable invoked while the request is active. A call
  is attributed to the `phase()` its call site set: the agent nodes set `router`, `tool_decide` and `generate`;
- a Bedrock generation reports its Converse usage through `add_call` (agent/generation.py);
- each retrieval adds one embedding, counted with tiktoken (`cl100k_base`) and labelled derived: the embedding API's
  usage never reaches LangChain.

The record is context-local, so concurrent requests never mix. Prices are list prices, kept as data with their source;
`usd_billed` charges cached input at the cached rate, `usd_list` charges every input token at the uncached rate. The log
line carries tokens, prices and ids only, never question or answer text.
"""

import json
import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

logger = logging.getLogger(__name__)

# USD per 1M tokens, list price. `cached` is the cached-input (OpenAI) or cache-read (Bedrock) rate; None = no discount.
# tests/test_cost.py asserts these equal eval/COST_LEDGER.md's price table.
PRICES = {
    "gpt-4o-mini": {"input": 0.15, "cached": 0.075, "output": 0.60,
                    "source": "eval/COST_LEDGER.md (OpenAI list price)", "as_of": "2026-08-08"},
    "text-embedding-3-small": {"input": 0.02, "cached": None, "output": 0.0,
                               "source": "eval/COST_LEDGER.md (OpenAI list price)", "as_of": "2026-08-08"},
    "us.amazon.nova-2-lite-v1:0": {"input": 0.33, "cached": 0.0825, "output": 2.75,
                                   "source": "AWS Price List API, AmazonBedrock us-east-1", "as_of": "2026-10-10"},
    "us.amazon.nova-micro-v1:0": {"input": 0.035, "cached": 0.00875, "output": 0.14,
                                  "source": "AWS Price List API, AmazonBedrock us-east-1", "as_of": "2026-10-10"},
}
COMPONENTS = ("embedding", "router", "tool_decide", "generate")  # always present; other phases appear when used
EMBED_MODEL = "text-embedding-3-small"  # src.retrieve.EMBED_MODEL (not imported, so this module stays light)

_record: ContextVar["RequestCost | None"] = ContextVar("g8_cost_record", default=None)
_handler: ContextVar["_UsageHandler | None"] = ContextVar("g8_cost_handler", default=None)
_phase: ContextVar["str | None"] = ContextVar("g8_cost_phase", default=None)


def price_for(model: str | None) -> dict | None:
    """The price row for a model id: an exact key, else the longest key it starts with (dated OpenAI snapshots)."""
    if not model:
        return None
    if model in PRICES:
        return PRICES[model]
    matches = [key for key in PRICES if model.startswith(key)]
    return PRICES[max(matches, key=len)] if matches else None


def usd(model: str | None, input_tokens: int, cached: int, output: int) -> tuple[float | None, float | None]:
    """(list, billed) USD for one call. `input_tokens` counts all input tokens, cached ones included."""
    price = price_for(model)
    if price is None:
        return None, None
    listed = (input_tokens * price["input"] + output * price["output"]) / 1e6
    cached_rate = price["input"] if price["cached"] is None else price["cached"]
    billed = ((input_tokens - cached) * price["input"] + cached * cached_rate + output * price["output"]) / 1e6
    return listed, billed


def _empty() -> dict:
    return {"calls": 0, "input": 0, "cached": 0, "output": 0, "usd_list": 0.0, "usd_billed": 0.0, "models": []}


class RequestCost:
    """One request's bill, by component. Thread-safe: graph nodes and the tool pool may run in worker threads."""

    def __init__(self, endpoint: str = ""):
        self.endpoint = endpoint
        self.parts = {name: _empty() for name in COMPONENTS}
        self.meta: dict = {}
        self.unpriced = 0
        self._lock = threading.Lock()

    def add(self, component: str, model: str | None, input_tokens: int = 0, cached: int = 0, output: int = 0,
            derived: bool = False) -> None:
        listed, billed = usd(model, input_tokens, cached, output)
        with self._lock:
            part = self.parts.setdefault(component, _empty())
            part["calls"] += 1
            part["input"] += input_tokens
            part["cached"] += cached
            part["output"] += output
            if listed is None:
                self.unpriced += 1
            else:
                part["usd_list"] += listed
                part["usd_billed"] += billed
            if model and model not in part["models"]:
                part["models"].append(model)
            if derived:
                part["derived"] = True

    def total(self) -> dict:
        with self._lock:
            parts = list(self.parts.values())
        return {key: sum(p[key] for p in parts) for key in ("calls", "input", "cached", "output", "usd_list",
                                                            "usd_billed")}

    def as_dict(self) -> dict:
        with self._lock:
            parts = {name: dict(part, models=list(part["models"])) for name, part in self.parts.items()}
        return {"endpoint": self.endpoint, "components": parts, "total": self.total(), "unpriced_calls": self.unpriced,
                **self.meta}


def _usage(response) -> tuple[str | None, int, int, int]:
    """(model, input, cached, output) from an LLMResult, as `get_openai_callback` reads it."""
    message = None
    try:
        message = response.generations[0][0].message
    except (AttributeError, IndexError, TypeError):
        pass
    usage = getattr(message, "usage_metadata", None)
    llm_output = response.llm_output or {}
    model = ((getattr(message, "response_metadata", None) or {}).get("model_name") or llm_output.get("model_name"))
    if usage:
        cached = (usage.get("input_token_details") or {}).get("cache_read") or 0
        return model, usage.get("input_tokens", 0), cached, usage.get("output_tokens", 0)
    token_usage = llm_output.get("token_usage") or {}
    cached = (token_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return model, token_usage.get("prompt_tokens", 0), cached, token_usage.get("completion_tokens", 0)


class _UsageHandler(BaseCallbackHandler):
    """Adds every chat call's usage to one request's record, under the phase active when the call started."""

    def __init__(self, record: RequestCost):
        self.record = record
        self._phases: dict = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self._phases[run_id] = _phase.get()

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._phases[run_id] = _phase.get()

    def on_llm_end(self, response, *, run_id, **kwargs):
        phase = self._phases.pop(run_id, None) or _phase.get() or "other"
        model, input_tokens, cached, output = _usage(response)
        self.record.add(phase, model, input_tokens, cached, output)


register_configure_hook(_handler, inheritable=True)


@contextmanager
def request(endpoint: str):
    """Open the record for one request. Nested use reuses the active record, so one request is never split."""
    existing = _record.get()
    if existing is not None:
        yield existing
        return
    record = RequestCost(endpoint)
    record_token, handler_token = _record.set(record), _handler.set(_UsageHandler(record))
    try:
        yield record
    finally:
        _handler.reset(handler_token)
        _record.reset(record_token)


@contextmanager
def phase(name: str):
    """Attribute the calls made inside to `name` (router, tool_decide, generate, …)."""
    token = _phase.set(name)
    try:
        yield
    finally:
        _phase.reset(token)


def current() -> RequestCost | None:
    return _record.get()


def current_phase() -> str | None:
    return _phase.get()


def add_call(component: str | None, model: str | None, input_tokens: int, cached: int, output: int) -> None:
    """A call whose usage LangChain never sees (Bedrock's Converse). No-op outside a request."""
    record = _record.get()
    if record is not None:
        record.add(component or _phase.get() or "other", model, input_tokens, cached, output)


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")  # text-embedding-3-small's tokenizer; OpenAIEmbeddings loads it too


def add_embedding(text: str) -> None:
    """One query embedding, counted with tiktoken (derived: the embeddings API's usage never reaches LangChain)."""
    record = _record.get()
    if record is None:
        return
    try:
        tokens = len(_encoding().encode(text or ""))
    except Exception:  # noqa: BLE001 — accounting must never fail a request
        tokens = max(1, len(text or "") // 4)
    record.add("embedding", EMBED_MODEL, tokens, 0, 0, derived=True)


def annotate(**fields) -> None:
    """Attach derived fields (never question text) to the active record, e.g. the routing decision."""
    record = _record.get()
    if record is not None:
        record.meta.update(fields)


def trace_note() -> str:
    """A compact trace_notes line for the record so far."""
    record = _record.get()
    if record is None:
        return "cost: no request record"
    total = record.total()
    counts = ", ".join(f"{name} {part['calls']}" for name, part in record.as_dict()["components"].items())
    return f"cost: ${total['usd_list']:.6f} list, ${total['usd_billed']:.6f} billed ({counts})"


def log(record: RequestCost, **fields) -> None:
    """One structured INFO line per request: tokens, prices and ids only, never question or answer text."""
    payload = record.as_dict() | fields
    for part in list(payload["components"].values()) + [payload["total"]]:
        for key in ("usd_list", "usd_billed"):
            part[key] = round(part[key], 8)
    logger.info("cost %s", json.dumps(payload, sort_keys=True, default=str))
