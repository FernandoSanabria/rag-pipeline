"""Hermetic tests for the G11 provider seam (agent/generation.py; eval/g11_design.md R1, P1).

No network: OpenAI requests are captured by a recording fake transport (httpx.Client.send patched) that returns a
canned completion, and Bedrock is a stub client that records the Converse kwargs.
"""

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

import src.generate as frozen
from agent import generation, graph
from agent.state import fresh_state
from api.confidence import is_refusal
from src.retrieve import format_contexts

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [json.loads(line)["question"] for line in (ROOT / "eval" / "dataset.jsonl").read_text().splitlines()
             if line.strip()]
REFUSAL = "The provided context does not contain the answer."


@pytest.fixture(autouse=True)
def _reset_accumulators():
    """The frozen generator's backend accumulator and the seam's call log are process-global: leave them as found, so
    other tests' call-count caps (tests/test_smoke_eval.py) are unaffected."""
    yield
    frozen.reset_generation_backends()
    generation.reset_calls()


def _chunks(n: int) -> list[dict]:
    """Synthetic chunks (real ones carry tier-2 text); the request body does not depend on their content."""
    return [{"text": f"synthetic chunk {n}.{p} with a figure {p * 7} ppm", "source_doc_id": f"doc-{n % 5}", "page": p}
            for p in range(1, 11)]


# --- R1: the prompt is the frozen one ---------------------------------------------------------------------------------


class _RecordingLLM:
    def __init__(self):
        self.messages = []

    def invoke(self, messages):
        self.messages.append(messages)
        return type("R", (), {"content": "ok", "response_metadata": {}})()


@pytest.mark.parametrize("contexts", [[], ["[source_doc_id=a page=1]\nx"], format_contexts(_chunks(3))])
def test_build_messages_is_byte_identical_to_what_the_frozen_generator_sends(monkeypatch, contexts):
    recorder = _RecordingLLM()
    monkeypatch.setattr(frozen, "_llm", lambda: recorder)
    frozen.generate("What is X?", contexts)
    assert generation.build_messages("What is X?", contexts) == recorder.messages[0]
    assert generation.build_messages("q", [])[0] == ("system", frozen.SYSTEM_PROMPT)  # imported, not copied


# --- P1: the openai request body is byte-identical on all 28 ----------------------------------------------------------


@pytest.fixture
def captured_bodies(monkeypatch):
    bodies = []

    def send(self, request, *args, **kwargs):
        bodies.append(request.content)
        return httpx.Response(200, request=request, json={
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "gpt-4o-mini-2024-07-18",
            "system_fingerprint": "fp_test",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.delenv("GENERATION_PROVIDER", raising=False)
    return bodies


def test_openai_request_body_is_byte_identical_on_all_28_questions(captured_bodies):
    """Three paths per question: the frozen generator (today), the seam with GENERATION_PROVIDER=openai, and the graph's
    generate node. The bytes sent must be identical, and carry the frozen model, temperature 0 and seed 42."""
    assert len(QUESTIONS) == 28
    for n, question in enumerate(QUESTIONS, 1):
        chunks = _chunks(n)
        contexts = format_contexts(chunks)
        start = len(captured_bodies)
        frozen.generate(question, contexts)
        generation.generate(question, contexts)
        state = fresh_state(question)
        state["retrieved"] = chunks
        assert graph.generate_node(state)["answer"] == "ok"
        today, seam, node = captured_bodies[start:start + 3]
        assert today == seam == node, f"row {n}: request bodies differ"
        body = json.loads(today)
        assert body["model"] == frozen._llm().model_name and body["temperature"] == 0 and body["seed"] == 42
        assert body["messages"] == [{"role": "system", "content": frozen.SYSTEM_PROMPT},
                                    {"role": "user", "content": generation.human_message(question, contexts)}]


# --- provider selection ------------------------------------------------------------------------------------------------


def test_the_default_provider_delegates_to_the_frozen_generator(monkeypatch):
    monkeypatch.delenv("GENERATION_PROVIDER", raising=False)
    monkeypatch.setattr(frozen, "generate", lambda q, c: f"frozen:{q}:{len(c)}")
    monkeypatch.setattr(generation, "bedrock_client", lambda: pytest.fail("bedrock must not be touched"))
    assert generation.provider() == "openai"
    assert generation.generate("q", ["a", "b"]) == "frozen:q:2"
    assert graph.generate is generation.generate  # the graph calls the seam


def test_bedrock_is_selected_by_env_and_an_unknown_provider_raises(monkeypatch):
    stub = _StubBedrock("from nova")
    monkeypatch.setattr(generation, "bedrock_client", lambda: stub)
    monkeypatch.setattr(frozen, "generate", lambda q, c: pytest.fail("openai must not be called"))
    monkeypatch.setenv("GENERATION_PROVIDER", "Bedrock ")
    assert generation.generate("q", []) == "from nova"
    monkeypatch.setenv("GENERATION_PROVIDER", "anthropic")
    with pytest.raises(ValueError, match="unknown GENERATION_PROVIDER"):
        generation.generate("q", [])


# --- the Bedrock Converse shape ----------------------------------------------------------------------------------------


class _StubBedrock:
    def __init__(self, *texts, stop="end_turn"):
        self.texts, self.stop, self.kwargs = texts, stop, []

    def converse(self, **kwargs):
        self.kwargs.append(kwargs)
        return {"output": {"message": {"role": "assistant", "content": [{"text": t} for t in self.texts]}},
                "stopReason": self.stop, "usage": {"inputTokens": 120, "outputTokens": 7, "totalTokens": 127},
                "metrics": {"latencyMs": 345}, "serviceTier": {"type": "default"},
                "ResponseMetadata": {"RequestId": "req-123", "HTTPStatusCode": 200}}


def test_the_converse_request_carries_the_frozen_messages_and_only_temperature(monkeypatch):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    generation.reset_calls()
    stub = _StubBedrock("Part one. ", "Part two.")
    contexts = format_contexts(_chunks(4))
    out = generation.generate_with(stub, "What is X?", contexts)
    assert out == "Part one. Part two."
    [kwargs] = stub.kwargs
    assert kwargs == {
        "modelId": "us.amazon.nova-2-lite-v1:0",
        "system": [{"text": frozen.SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": [{"text": generation.human_message("What is X?", contexts)}]}],
        "inferenceConfig": {"temperature": 0},  # topP and maxTokens unset, as in the OpenAI arm; Nova has no seed
    }
    [record] = generation.calls()
    assert record == {"provider": "bedrock", "model_id": "us.amazon.nova-2-lite-v1:0", "request_id": "req-123",
                      "latency_ms": 345, "wall_s": record["wall_s"], "stop_reason": "end_turn",
                      "service_tier": "default", "input_tokens": 120, "output_tokens": 7, "cache_read_tokens": None}
    assert "What is X?" not in json.dumps(record)  # derived values only


def test_an_empty_converse_answer_is_the_empty_string(monkeypatch):
    assert generation.generate_with(_StubBedrock(stop="max_tokens"), "q", []) == ""


def test_the_model_id_is_configurable(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")
    stub = _StubBedrock("x")
    generation.generate_with(stub, "q", [])
    assert stub.kwargs[0]["modelId"] == "us.amazon.nova-pro-v1:0"


# --- refusals pass through untouched (P6's hazard, made explicit) -----------------------------------------------------


def test_an_exact_refusal_passes_through_and_a_paraphrase_does_not_count(monkeypatch):
    monkeypatch.setenv("GENERATION_PROVIDER", "bedrock")
    monkeypatch.setattr(generation, "bedrock_client", lambda: _StubBedrock(REFUSAL))
    exact = generation.generate("q", [])
    assert exact == REFUSAL and is_refusal(exact)
    monkeypatch.setattr(generation, "bedrock_client",
                        lambda: _StubBedrock("I'm sorry, but the provided context does not contain the answer."))
    paraphrase = generation.generate("q", [])
    assert not is_refusal(paraphrase)  # served as a high-confidence answer with citations: what P6 measures


# --- boto3 stays off the default path ----------------------------------------------------------------------------------


def test_boto3_is_not_imported_by_the_default_path():
    code = ("import os, sys; os.environ.update(OPENAI_API_KEY='t', PINECONE_API_KEY='t'); "
            "import api.main, agent.graph; print('boto3' in sys.modules)")
    out = subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=ROOT, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_env_example_lists_the_names_with_empty_values():
    lines = [line for line in (ROOT / ".env.example").read_text().splitlines() if line and not line.startswith("#")]
    names = {line.split("=", 1)[0] for line in lines}
    assert {"GENERATION_PROVIDER", "BEDROCK_MODEL_ID", "AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY", "PINECONE_API_KEY"} <= names
    assert all(line.endswith("=") for line in lines)  # never a value
