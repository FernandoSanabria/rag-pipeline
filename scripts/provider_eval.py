"""G11 provider eval: P2-P6 of eval/g11_PREDICTION.md, live, both generation providers interleaved in one process.

Arms: GENERATION_PROVIDER=openai (the frozen src.generate.generate, gpt-4o-mini) and GENERATION_PROVIDER=bedrock
(us.amazon.nova-2-lite-v1:0 through agent/generation.py's Converse adapter). Everything else is shared:
- each row runs through api.main._answer over agent.graph.ask (the served path, output guard included);
- the query embedding, the router decision and the tool decision are memoized and shared, so the contexts handed to
  generation are byte-identical across arms (P2 counts it);
- the judge is run_eval.py's (gpt-4o-mini at temperature 0, text-embedding-3-small; faithfulness and answer
  correctness), one sample at a time, on each arm's raw generated answer.

The G10b review gate's trigger is switched off for this run, as in the test suite: G11 measures generation, and rows
9-11 would otherwise pause before it. Rows are 1-based; N=3 trials, alternating which arm goes first per row and trial.

Bedrock is held to <= 15 requests a minute (quota 20) by a client-side spacer; the SDK's own retries are off, so
ThrottlingException and transient errors surface here, back off and are counted. Bedrock latency is the successful
Converse call's client-side wall time (spacer and backoff excluded); OpenAI's is the generation call's wall time.
Cost is derived from usage at uncached list price (Nova 2 Lite $0.33 / $2.75 per 1M input / output; gpt-4o-mini
$0.15 / $0.60), with each arm's as-billed figure (cache reads) beside it.

Writes derived metrics only to eval/provider_metrics.json (no question, answer or context text). Raw answers, contexts
and the P6 answers go to the gitignored eval/results/g11_raw_<ts>.json, rewritten after the generation phase and as
judging proceeds; --judge-from resumes judging from it.

Run: uv run python scripts/provider_eval.py
     uv run python scripts/provider_eval.py --rows 9,21 --trials 1 --out <scratch>/smoke.json --raw-dir <scratch>
"""

import argparse
import json
import math
import os
import re
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = REPO_ROOT / "eval" / "dataset.jsonl"
METRICS_PATH = REPO_ROOT / "eval" / "provider_metrics.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
load_dotenv(REPO_ROOT / ".env")
os.environ["LANGCHAIN_TRACING_V2"] = "false"  # eval traffic stays out of the project R5's latency baseline came from
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["RAGAS_DO_NOT_TRACK"] = "true"
os.environ.pop("REVIEW_DB_PATH", None)  # no checkpointer; the review trigger is switched off below anyway
os.environ.pop("GENERATION_PROVIDER", None)  # set per arm

ARMS = ("openai", "bedrock")
TRIALS = 3
METRICS = ("faithfulness", "answer_correctness")
JUDGE_MODEL = "gpt-4o-mini"  # eval/run_eval.py's judge
EMBED_MODEL = "text-embedding-3-small"
PRICE = {  # USD per 1M tokens, list (eval/g11_design.md R2; eval/COST_LEDGER.md)
    "openai": {"input": 0.15, "cached": 0.075, "output": 0.60},
    "bedrock": {"input": 0.33, "cached": 0.0825, "output": 2.75},
    "embedding": 0.02,
}
BEDROCK_MIN_INTERVAL_S = 4.0  # 15 requests a minute against a quota of 20
BEDROCK_RETRYABLE = {"ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException",
                     "InternalServerException", "ModelTimeoutException", "ConnectionClosedError",
                     "EndpointConnectionError", "ReadTimeoutError"}
REFUSAL = "The provided context does not contain the answer."
MARKER = re.compile(r"source_doc_id\s*=")
P3_MARGIN = 0.03
P4_COST_BAND = (0.0015, 0.0060)
P4_LATENCY_BAND = (0.6, 2.4)
P6_ROW = 25
NAMED_ROWS = {9: "answer_correctness", 21: "faithfulness"}


def _rows() -> list[dict]:
    return [json.loads(line) for line in DATASET_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def _doc_ids() -> list[str]:
    manifest = json.loads((REPO_ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    return [d["doc_id"] for d in manifest["docs"] if d.get("doc_id")]


def _is_nan(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _p50(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _price(arm: str, input_tokens: int, cached: int, output: int) -> tuple[float, float]:
    """(uncached list, as billed) in USD. input_tokens counts ALL input tokens, cached ones included."""
    p = PRICE[arm]
    listed = (input_tokens * p["input"] + output * p["output"]) / 1e6
    billed = ((input_tokens - cached) * p["input"] + cached * p["cached"] + output * p["output"]) / 1e6
    return listed, billed


class Spend:
    """Token usage per part (serializable, so a resumed judge phase keeps the generation phase's figures)."""

    def __init__(self, parts: dict | None = None):
        self.parts = parts if parts is not None else {}

    def add(self, name, requests=0, input=0, cached=0, output=0):
        p = self.parts.setdefault(name, {"requests": 0, "input": 0, "cached": 0, "output": 0})
        p["requests"] += requests
        p["input"] += input
        p["cached"] += cached
        p["output"] += output

    def chat(self, name, cb):  # an OpenAI chat part, from a get_openai_callback handler
        self.add(name, cb.successful_requests, cb.prompt_tokens, cb.prompt_tokens_cached, cb.completion_tokens)

    def cost(self) -> dict:
        out = {}
        for name, p in self.parts.items():
            if name == "embeddings":
                listed = billed = p["input"] * PRICE["embedding"] / 1e6
            else:
                listed, billed = _price("bedrock" if name.startswith("bedrock") else "openai", p["input"], p["cached"],
                                        p["output"])
            out[name] = {**p, "usd_list_uncached": round(listed, 6), "usd_as_billed": round(billed, 6)}
        parts = list(out.values())
        out["total_usd_as_billed"] = round(sum(v["usd_as_billed"] for v in parts), 6)
        out["total_usd_list_uncached"] = round(sum(v["usd_list_uncached"] for v in parts), 6)
        return out


class SharedMemo:
    """One decision per prompt, shared by both arms (review_eval.py's), with the miss's usage added to the spend."""

    def __init__(self, runnable, spend: Spend):
        self.runnable, self.spend, self.cache, self.calls = runnable, spend, {}, 0

    def invoke(self, prompt, **kwargs):
        from langchain_community.callbacks.manager import get_openai_callback

        if prompt not in self.cache:
            self.calls += 1
            with get_openai_callback() as cb:
                self.cache[prompt] = self.runnable.invoke(prompt)
            self.spend.chat("decisions", cb)
        return self.cache[prompt]


class BedrockForEval:
    """The real bedrock-runtime client, built with SDK retries off so every throttle surfaces here. Calls are spaced to
    <= 15 a minute; retryable errors back off (5, 10, 20, 40, 80 s) and are counted; `last_call_s` is the successful
    call's client-side wall time, spacer and backoff excluded."""

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.region = os.environ.get("AWS_REGION", "").strip() or "us-east-1"
        self.client = boto3.client("bedrock-runtime", region_name=self.region,
                                   config=Config(retries={"mode": "standard", "max_attempts": 1}, connect_timeout=10,
                                                 read_timeout=120))
        self.next_at = 0.0
        self.retries: Counter = Counter()
        self.failures: Counter = Counter()
        self.last_call_s: float | None = None

    def converse(self, **kwargs):
        from botocore.exceptions import ClientError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError

        for attempt in range(6):
            wait = self.next_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.next_at = time.monotonic() + BEDROCK_MIN_INTERVAL_S
            started = time.perf_counter()
            try:
                response = self.client.converse(**kwargs)
                self.last_call_s = time.perf_counter() - started
                return response
            except (ClientError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError) as exc:
                code = (exc.response.get("Error", {}).get("Code") or "ClientError") if isinstance(exc, ClientError) \
                    else type(exc).__name__
                if code not in BEDROCK_RETRYABLE or attempt == 5:
                    self.failures[code] += 1
                    raise
                self.retries[code] += 1
                time.sleep(5 * 2 ** attempt)


class GenerationRecorder:
    """Stands in for agent.graph.generate (and src.pipeline.generate for P6): records what the generator received and
    returned, then calls the real seam (agent.generation.generate), so the provider dispatch measured is the
    production one. An exception is recorded and re-raised, so the caller's own handling (an empty answer) is
    unchanged."""

    def __init__(self, seam, frozen, bedrock: BedrockForEval, spend: Spend):
        self.seam, self.frozen, self.bedrock, self.spend = seam, frozen, bedrock, spend
        self.last: dict | None = None

    def __call__(self, question, contexts):
        from langchain_community.callbacks.manager import get_openai_callback

        from agent import generation

        arm = generation.provider()
        rec = {"arm": arm, "contexts": list(contexts)}
        self.last = rec
        n_calls = len(generation.calls())
        backends = Counter(self.frozen._generation_backends)
        self.bedrock.last_call_s = None
        started = time.perf_counter()
        try:
            with get_openai_callback() as cb:
                answer = self.seam(question, contexts)
        except Exception as exc:
            rec["error"] = type(exc).__name__
            raise
        wall = time.perf_counter() - started
        rec["answer"] = answer
        if arm == "openai":
            new = [k for k, v in self.frozen._generation_backends.items() if v > backends.get(k, 0)]
            model, fingerprint = new[0] if new else (None, None)
            rec.update(wall_s=round(wall, 3), model=model, system_fingerprint=fingerprint,
                       input_tokens=cb.prompt_tokens, cached_tokens=cb.prompt_tokens_cached,
                       output_tokens=cb.completion_tokens)
            self.spend.chat("openai_generation", cb)
        else:
            [call] = generation.calls()[n_calls:]
            cached = call["cache_read_tokens"] or 0
            rec.update(wall_s=round(self.bedrock.last_call_s, 3), model=call["model_id"],
                       request_id=call["request_id"], latency_ms=call["latency_ms"], stop_reason=call["stop_reason"],
                       service_tier=call["service_tier"], input_tokens=(call["input_tokens"] or 0) + cached,
                       cached_tokens=cached, output_tokens=call["output_tokens"] or 0)
            self.spend.add("bedrock_generation", 1, rec["input_tokens"], cached, rec["output_tokens"])
        listed, billed = _price(arm, rec["input_tokens"], rec["cached_tokens"], rec["output_tokens"])
        rec.update(usd_list_uncached=round(listed, 7), usd_as_billed=round(billed, 7))
        return answer


def _cites(answer: str, doc_ids: list[str]) -> dict:
    named = sorted({d for d in doc_ids if d in answer})
    return {"markers": len(MARKER.findall(answer)), "doc_ids": named,
            "cites_any": bool(MARKER.search(answer)) or bool(named)}


def _row21_misattributions(answer: str) -> int:
    """Sentences that make a claim about OSHA's air-contaminants table and source it inline to the NIOSH Pocket Guide
    without the OSHA document (the G9 row-21 instance, eval/KNOWN_LIMITATIONS.md). A heuristic flag; the raw answers
    are read to confirm the count."""
    count = 0
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        text = sentence.lower()
        about_osha_table = "osha" in text and any(t in text for t in ("air contaminant", "table z", "z-1", "1910.1000",
                                                                       "pel", "ceiling"))
        if about_osha_table and "niosh-pocket-guide" in sentence and "osha-1910-1000" not in sentence:
            count += 1
    return count


def _answer_with_retry(question, ask, answer_fn, recorder):
    """One arm's answer through the served path. A generation error is retried once after 30 s; the graph itself turns
    an error into an empty answer, so the recorder's error flag is what tells them apart."""
    for attempt in (1, 2):
        recorder.last = None
        fields, result = answer_fn(question, ask)
        gen = recorder.last
        if gen is not None and "error" not in gen:
            break
        if attempt == 1:
            time.sleep(30)
    return fields, result, gen, attempt


def generation_phase(state, rows, row_numbers, trials, deps) -> None:
    graph, answer_fn, recorder, is_refusal, doc_ids = (deps["graph"], deps["answer"], deps["recorder"],
                                                        deps["is_refusal"], deps["doc_ids"])
    for trial in range(1, trials + 1):
        for n in row_numbers:
            order = ARMS if (n + trial) % 2 else ARMS[::-1]
            line = []
            for arm in order:
                os.environ["GENERATION_PROVIDER"] = arm
                fields, result, gen, attempts = _answer_with_retry(rows[n - 1]["question"], graph.ask, answer_fn,
                                                                   recorder)
                gen = gen or {"arm": arm, "contexts": None, "error": "not_reached"}
                answer = result.get("answer", "")
                state["records"].append({
                    "trial": trial, "row": n, "arm": arm, "position": order.index(arm) + 1, "attempts": attempts,
                    "route": result.get("route"), "status": result.get("status"),
                    "answer": answer, "served_answer": fields["answer"], "contexts": gen.get("contexts"),
                    "contexts_match_result": gen.get("contexts") == result.get("contexts"),
                    "gen": {k: v for k, v in gen.items() if k not in ("contexts", "answer")},
                    "guard": fields["guard"].model_dump() if fields.get("guard") else None,
                    "n_citations": len(fields.get("citations") or []),
                    "refusal": is_refusal(answer),
                    "cites": _cites(answer, doc_ids),
                    "row21_misattributions": _row21_misattributions(answer) if n == 21 else None,
                })
                g = state["records"][-1]["gen"]
                line.append(f"{arm} {len(answer)}c {g.get('wall_s')}s {g.get('error', '')}".rstrip())
            print(f"trial {trial} row {n:2d}: " + " | ".join(line), flush=True)
    os.environ.pop("GENERATION_PROVIDER", None)


def p6_phase(state, rows, trials, deps) -> None:
    """Row 25 through the /ask path (src.pipeline.ask: unscoped dense_search at retrieval_k), its generate swapped for
    the recorder for this phase only, so each provider's generation sees /ask's contexts through the seam."""
    import src.pipeline as pipeline

    recorder, is_refusal = deps["recorder"], deps["is_refusal"]
    question = rows[P6_ROW - 1]["question"]
    original = pipeline.generate
    pipeline.generate = recorder
    try:
        for trial in range(1, trials + 1):
            for arm in (ARMS if trial % 2 else ARMS[::-1]):
                os.environ["GENERATION_PROVIDER"] = arm
                fields, result, gen, attempts = _answer_with_retry(question, None, lambda q, _: (None, pipeline.ask(q)),
                                                                   recorder)
                gen = gen or {"arm": arm, "contexts": None, "error": "not_reached"}
                state["p6"].append({
                    "trial": trial, "arm": arm, "attempts": attempts, "answer": result["answer"],
                    "contexts": gen.get("contexts"),
                    "gen": {k: v for k, v in gen.items() if k not in ("contexts", "answer")},
                    "is_refusal": is_refusal(result["answer"]), "byte_exact": result["answer"] == REFUSAL,
                })
                print(f"P6 trial {trial} {arm}: is_refusal={state['p6'][-1]['is_refusal']} "
                      f"byte_exact={state['p6'][-1]['byte_exact']}", flush=True)
    finally:
        pipeline.generate = original
        os.environ.pop("GENERATION_PROVIDER", None)


def judge_factory(state, spend: Spend):
    import tiktoken
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.embeddings import Embeddings
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.dataset_schema import SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_correctness, faithfulness

    encoding = tiktoken.get_encoding("cl100k_base")
    fingerprints = state.setdefault("judge_fingerprints", {})

    class Fingerprints(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            out = response.llm_output or {}
            key = f"{out.get('model_name')}|{out.get('system_fingerprint')}"
            fingerprints[key] = fingerprints.get(key, 0) + 1

    class CountingEmbeddings(Embeddings):
        def __init__(self, real):
            self.real = real

        def _count(self, texts):
            spend.add("embeddings", len(texts), sum(len(encoding.encode(t)) for t in texts))

        def embed_documents(self, texts):
            self._count(texts)
            return self.real.embed_documents(texts)

        def embed_query(self, text):
            self._count([text])
            return self.real.embed_query(text)

        async def aembed_documents(self, texts):
            self._count(texts)
            return await self.real.aembed_documents(texts)

        async def aembed_query(self, text):
            self._count([text])
            return await self.real.aembed_query(text)

    llm = LangchainLLMWrapper(ChatOpenAI(model=JUDGE_MODEL, temperature=0, callbacks=[Fingerprints()]))
    embeddings = LangchainEmbeddingsWrapper(CountingEmbeddings(OpenAIEmbeddings(model=EMBED_MODEL)))
    objects = {"faithfulness": faithfulness, "answer_correctness": answer_correctness}

    def score(question, answer, contexts, reference) -> dict:
        from langchain_community.callbacks.manager import get_openai_callback

        out, sources, need = {}, {}, list(METRICS)
        for source in ("judge", "judge_retry"):  # a judge NaN is retried once, then recorded as None (smoke_eval's rule)
            if not need:
                break
            sample = SingleTurnSample(user_input=question, response=answer, retrieved_contexts=contexts,
                                      reference=reference)
            with get_openai_callback() as cb:
                result = evaluate(dataset=EvaluationDataset(samples=[sample]), metrics=[objects[m] for m in need],
                                  llm=llm, embeddings=embeddings, raise_exceptions=False, show_progress=False)
            spend.chat("judge", cb)
            frame = result.to_pandas().iloc[0]
            still = []
            for m in need:
                value = frame.get(m)
                if _is_nan(None if value is None else float(value)):
                    still.append(m)
                else:
                    out[m], sources[m] = float(value), source
            need = still
        for m in need:
            out[m], sources[m] = None, "nan_twice"
        return out | {"sources": sources}

    return score


def judge_phase(state, rows, spend: Spend, save) -> None:
    score = judge_factory(state, spend)
    todo = [r for r in state["records"] if "scores" not in r]
    for i, r in enumerate(todo, 1):
        if r["gen"].get("error"):
            r["scores"] = {m: None for m in METRICS} | {"sources": {m: "generation_error" for m in METRICS}}
        else:
            row = rows[r["row"] - 1]
            r["scores"] = score(row["question"], r["answer"], r["contexts"], row["reference"])
        if i % 12 == 0 or i == len(todo):
            save()
            print(f"judged {i}/{len(todo)}", flush=True)


# --- verdicts (derived from the serializable state only) -------------------------------------------------------------


def p3(records) -> dict:
    items, aggregate = [], {}
    for metric in METRICS:
        means = {arm: [] for arm in ARMS}
        for n in sorted({r["row"] for r in records}):
            vals = {arm: [r["scores"][metric] for r in records if r["row"] == n and r["arm"] == arm
                          and r["scores"].get(metric) is not None] for arm in ARMS}
            if not vals["openai"] or not vals["bedrock"]:
                items.append({"row": n, "metric": metric, "verdict": "NOT TESTED", "reason": "no score in an arm",
                              "n": {arm: len(vals[arm]) for arm in ARMS}})
                continue
            lo, hi = min(vals["openai"]), max(vals["openai"])
            mo, mb = statistics.mean(vals["openai"]), statistics.mean(vals["bedrock"])
            falsified = mb < lo and round(mo - mb, 6) > P3_MARGIN  # rounded: float noise never decides a boundary
            items.append({"row": n, "metric": metric, "n": {arm: len(vals[arm]) for arm in ARMS},
                          "openai": [round(v, 4) for v in vals["openai"]],
                          "bedrock": [round(v, 4) for v in vals["bedrock"]],
                          "openai_mean": round(mo, 4), "bedrock_mean": round(mb, 4), "openai_min": round(lo, 4),
                          "openai_max": round(hi, 4), "delta_of_means": round(mb - mo, 4),
                          "bedrock_below_openai_min": mb < lo,
                          "verdict": "FALSIFIED" if falsified else "HOLDS"})
            for arm, m in (("openai", mo), ("bedrock", mb)):
                means[arm].append(m)
        if means["openai"]:
            ao, ab = statistics.mean(means["openai"]), statistics.mean(means["bedrock"])
            holds = round(ab - ao, 6) >= -P3_MARGIN
            aggregate[metric] = {"rows": len(means["openai"]), "openai": round(ao, 4), "bedrock": round(ab, 4),
                                 "delta": round(ab - ao, 4), "verdict": "HOLDS" if holds else "FALSIFIED"}
    falsified = [(i["row"], i["metric"]) for i in items if i["verdict"] == "FALSIFIED"]
    named = {n: next((i for i in items if i["row"] == n and i["metric"] == m), None) for n, m in NAMED_ROWS.items()}
    per_metric = {m: ("FALSIFIED" if any(f[1] == m for f in falsified)
                      or aggregate.get(m, {}).get("verdict") == "FALSIFIED" else "HOLDS") for m in METRICS}
    return {"items": items, "falsified_rows": falsified, "aggregate": aggregate, "named_rows": named,
            "per_metric": per_metric,
            "rows_with_bedrock_mean_below_openai_min": sum(1 for i in items if i.get("bedrock_below_openai_min")),
            "verdict": "FALSIFIED" if "FALSIFIED" in per_metric.values() else "HOLDS"}


def derive(state, rows, row_numbers, trials) -> dict:
    records, p6s = state["records"], state["p6"]
    ok = [r for r in records if not r["gen"].get("error")]

    def by(arm, key):
        return [r["gen"].get(key) for r in ok if r["arm"] == arm and r["gen"].get(key) is not None]

    pairs = {}
    for r in records:
        pairs.setdefault((r["trial"], r["row"]), {})[r["arm"]] = r["contexts"]
    identical = sum(1 for v in pairs.values() if v.get("openai") is not None and v.get("openai") == v.get("bedrock"))
    expected_pairs = len(row_numbers) * trials

    p4 = {}
    for arm in ARMS:
        walls = by(arm, "wall_s")
        p4[arm] = {"n": len(walls),
                   "usd_per_answer_p50_list_uncached": _p50(by(arm, "usd_list_uncached")),
                   "usd_per_answer_mean_list_uncached": (round(statistics.mean(by(arm, "usd_list_uncached")), 7)
                                                         if by(arm, "usd_list_uncached") else None),
                   "usd_per_answer_p50_as_billed": _p50(by(arm, "usd_as_billed")),
                   "input_tokens_p50": _p50(by(arm, "input_tokens")),
                   "input_tokens_range": [min(by(arm, "input_tokens"), default=None),
                                          max(by(arm, "input_tokens"), default=None)],
                   "output_tokens_p50": _p50(by(arm, "output_tokens")),
                   "output_tokens_range": [min(by(arm, "output_tokens"), default=None),
                                           max(by(arm, "output_tokens"), default=None)],
                   "wall_s_p50": _p50(walls),
                   "wall_s_p10_p90": ([round(q, 3) for q in statistics.quantiles(walls, n=10)[::8]]
                                      if len(walls) >= 2 else None)}
    p4["bedrock"]["latency_ms_p50_server"] = _p50(by("bedrock", "latency_ms"))
    cost, wall = p4["bedrock"]["usd_per_answer_p50_list_uncached"], p4["bedrock"]["wall_s_p50"]
    in_cost = cost is not None and P4_COST_BAND[0] <= cost <= P4_COST_BAND[1]
    in_latency = wall is not None and P4_LATENCY_BAND[0] <= wall <= P4_LATENCY_BAND[1]
    p4.update(cost_band=P4_COST_BAND, latency_band_s=P4_LATENCY_BAND, cost_in_band=in_cost, latency_in_band=in_latency,
              verdict="NOT TESTED" if cost is None else ("HOLDS" if in_cost and in_latency else "FALSIFIED"))
    base = p4["openai"]["usd_per_answer_p50_list_uncached"]
    p4["cost_ratio_p50_bedrock_over_openai"] = round(cost / base, 2) if cost and base else None
    mo, mb = p4["openai"]["usd_per_answer_mean_list_uncached"], p4["bedrock"]["usd_per_answer_mean_list_uncached"]
    p4["cost_ratio_mean_bedrock_over_openai"] = round(mb / mo, 2) if mo and mb else None

    per_trial = []
    for trial in range(1, trials + 1):
        t = {arm: [r for r in records if r["trial"] == trial and r["arm"] == arm] for arm in ARMS}
        cites = {arm: sum(r["cites"]["cites_any"] for r in t[arm]) for arm in ARMS}
        withheld = {arm: sum(1 for r in t[arm] if (r["guard"] or {}).get("stage") == "output") for arm in ARMS}
        per_trial.append({"trial": trial, "n": {arm: len(t[arm]) for arm in ARMS}, "cites_any": cites,
                          "cites_any_within_2": abs(cites["openai"] - cites["bedrock"]) <= 2,
                          "exact_marker_answers": {arm: sum(r["cites"]["markers"] > 0 for r in t[arm]) for arm in ARMS},
                          "withheld": withheld, "withheld_0_to_1": all(v <= 1 for v in withheld.values()),
                          "withheld_differ_by_more_than_2": abs(withheld["openai"] - withheld["bedrock"]) > 2})
    lost = sorted({(r["trial"], r["row"]) for r in records if r["arm"] == "bedrock" and r["n_citations"] == 0
                   and any(o["n_citations"] > 0 for o in records if o["arm"] == "openai" and o["row"] == r["row"])})
    p5_falsified = any(t["withheld_differ_by_more_than_2"] for t in per_trial) or bool(lost)
    p5 = {"per_trial": per_trial, "bedrock_rows_losing_structured_citations": lost,
          "verdict": "FALSIFIED" if p5_falsified else "HOLDS",
          "prediction_clauses_met": {"cites_any_within_2_every_trial": all(t["cites_any_within_2"] for t in per_trial),
                                     "withheld_0_to_1_every_trial": all(t["withheld_0_to_1"] for t in per_trial)}}

    p6 = {"row": P6_ROW, "path": "/ask (src.pipeline.ask, unscoped dense_search at retrieval_k)",
          "trials": [{k: v for k, v in x.items() if k not in ("answer", "contexts")} for x in p6s],
          "contexts_identical_across_calls": len({json.dumps(x["contexts"]) for x in p6s}) == 1 if p6s else None,
          "exact": {arm: f"{sum(x['is_refusal'] for x in p6s if x['arm'] == arm)}/"
                         f"{sum(1 for x in p6s if x['arm'] == arm)}" for arm in ARMS},
          "byte_exact": {arm: f"{sum(x['byte_exact'] for x in p6s if x['arm'] == arm)}/"
                              f"{sum(1 for x in p6s if x['arm'] == arm)}" for arm in ARMS},
          "agent_path_refusals": {arm: f"{sum(r['refusal'] for r in records if r['arm'] == arm)}/"
                                       f"{sum(1 for r in records if r['arm'] == arm)}" for arm in ARMS}}
    bedrock_p6 = [x for x in p6s if x["arm"] == "bedrock"]
    p6["verdict"] = ("NOT TESTED" if not bedrock_p6 else
                     "HOLDS" if all(x["is_refusal"] for x in bedrock_p6) else "FALSIFIED")

    p3_out = p3(ok)
    p7_cost_ok = (p4["cost_ratio_p50_bedrock_over_openai"] or math.inf) <= 1.5
    rule = "bedrock" if p3_out["verdict"] == "HOLDS" and p6["verdict"] == "HOLDS" and p7_cost_ok else "openai"
    p7 = {"bedrock_passes_P3_both_metrics": p3_out["verdict"] == "HOLDS", "bedrock_passes_P6": p6["verdict"] == "HOLDS",
          "cost_ratio_p50": p4["cost_ratio_p50_bedrock_over_openai"], "cost_ratio_le_1_5": p7_cost_ok,
          "rule_selects_default": rule, "shipped_default": "openai",  # refinement A: never flipped inside this PR
          "verdict": "HOLDS" if rule == "openai" else "FALSIFIED"}

    return {
        "timestamp_utc": state["started_at"], "rows": row_numbers, "trials": trials,
        "full_design": row_numbers == list(range(1, len(rows) + 1)) and trials == TRIALS,
        "arms": state["models"], "bedrock_region": state["bedrock_region"],
        "review_trigger": "switched off for this run (G11 measures generation; see the module docstring)",
        "openai_generation_backends": state["openai_backends"],
        "bedrock_model_ids": sorted({r["gen"]["model"] for r in ok if r["arm"] == "bedrock" and r["gen"].get("model")}),
        "bedrock_service_tiers": dict(Counter(str(r["gen"].get("service_tier")) for r in ok if r["arm"] == "bedrock")),
        "bedrock_stop_reasons": dict(Counter(str(r["gen"].get("stop_reason")) for r in ok if r["arm"] == "bedrock")),
        "bedrock_retries": state["bedrock_retries"], "bedrock_failures": state["bedrock_failures"],
        "generation_errors": {arm: sum(1 for r in records if r["arm"] == arm and r["gen"].get("error"))
                              for arm in ARMS},
        "retried_answers": sum(1 for r in records if r["attempts"] > 1),
        "statuses": dict(Counter(str(r["status"]) for r in records)),
        "contexts_match_graph_result": f"{sum(r['contexts_match_result'] for r in records)}/{len(records)}",
        "empty_answers": {arm: sum(1 for r in ok if r["arm"] == arm and not r["answer"].strip()) for arm in ARMS},
        "judge_fingerprints": state.get("judge_fingerprints", {}),
        "judge_nan_twice": sum(1 for r in ok for m in METRICS if r["scores"]["sources"].get(m) == "nan_twice"),
        "memo": state["memo"],
        "P2": {"pairs": len(pairs), "expected": expected_pairs, "identical": identical,
               "verdict": "HOLDS" if identical == len(pairs) == expected_pairs else "FALSIFIED"},
        "P3": p3_out, "P4": p4, "P5": p5, "P6": p6, "P7": p7,
        "row21_misattribution_sentences": {arm: [r["row21_misattributions"] for r in records
                                                 if r["row"] == 21 and r["arm"] == arm] for arm in ARMS},
        "spend": Spend(state["spend"]).cost(),
        "wall_s": state["wall_s"],
        "per_answer": [{k: v for k, v in r.items() if k not in ("answer", "served_answer", "contexts")}
                       for r in records],
    }


def print_summary(metrics) -> None:
    print("\nrow | metric | openai (3) | bedrock (3) | delta of means | verdict")
    for i in metrics["P3"]["items"]:
        print(f"{i['row']:>3} | {i['metric'][:12]:<12} | {i.get('openai')} | {i.get('bedrock')} | "
              f"{i.get('delta_of_means')} | {i['verdict']}")
    keys = ("P2", "P4", "P5", "P6", "P7", "generation_errors", "retried_answers", "bedrock_retries",
            "bedrock_failures", "empty_answers", "row21_misattribution_sentences", "judge_nan_twice", "statuses",
            "contexts_match_graph_result", "memo", "openai_generation_backends", "bedrock_model_ids",
            "bedrock_service_tiers", "bedrock_stop_reasons", "judge_fingerprints", "spend", "wall_s")
    summary = {k: metrics[k] for k in keys}
    summary["P3"] = {k: metrics["P3"][k] for k in ("verdict", "per_metric", "falsified_rows", "aggregate",
                                                     "rows_with_bedrock_mean_below_openai_min")}
    print(json.dumps(summary, indent=2, default=str))


def run_generation(rows, row_numbers, trials) -> dict:
    import tiktoken

    import api.main as api_main
    import src.generate as frozen
    import src.retrieve as retrieve
    from agent import generation, graph, review
    from api.confidence import is_refusal

    if api_main.INPUT_GUARD_ENABLED:
        raise SystemExit("the input guard is on; G11 measures generation behind the shipped (off) input guard")
    state = {"started_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "row_numbers": row_numbers,
             "trials": trials, "records": [], "p6": [], "spend": {}, "wall_s": {}}
    spend = Spend(state["spend"])
    encoding = tiktoken.get_encoding("cl100k_base")

    real_embedder = retrieve._embedder()
    embedded: dict[str, list[float]] = {}

    class EmbedMemo:  # review_eval.py's memoize_embeddings, with the misses' tokens counted
        def embed_query(self, text):
            if text not in embedded:
                spend.add("embeddings", 1, len(encoding.encode(text)))
                embedded[text] = real_embedder.embed_query(text)
            return embedded[text]

    memo = EmbedMemo()
    retrieve._embedder = lambda: memo
    router, tools = SharedMemo(graph._router_llm(), spend), SharedMemo(graph._tool_llm(), spend)
    graph._router_llm, graph._tool_llm = (lambda: router), (lambda: tools)
    review.trigger_reason = lambda question, trace_notes: None
    bedrock = BedrockForEval()
    generation.bedrock_client = lambda: bedrock
    recorder = GenerationRecorder(generation.generate, frozen, bedrock, spend)
    graph.generate = recorder
    frozen.reset_generation_backends()
    generation.reset_calls()
    deps = {"graph": graph, "answer": api_main._answer, "recorder": recorder, "is_refusal": is_refusal,
            "doc_ids": _doc_ids()}

    t0 = time.perf_counter()
    generation_phase(state, rows, row_numbers, trials, deps)
    state["wall_s"]["generation_phase"] = round(time.perf_counter() - t0, 1)
    t0 = time.perf_counter()
    p6_phase(state, rows, trials, deps)
    state["wall_s"]["p6_phase"] = round(time.perf_counter() - t0, 1)

    state["models"] = {"openai": frozen._llm().model_name, "bedrock": generation.bedrock_model_id()}
    state["bedrock_region"] = bedrock.region
    state["openai_backends"] = frozen.generation_backends()
    state["bedrock_retries"], state["bedrock_failures"] = dict(bedrock.retries), dict(bedrock.failures)
    state["memo"] = {"router_calls": router.calls, "tool_calls": tools.calls, "query_embeddings": len(embedded)}
    return state


def main() -> None:
    import logging

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rows", help="comma-separated 1-based rows (default: all 28)")
    parser.add_argument("--trials", type=int, default=TRIALS)
    parser.add_argument("--out", type=Path, default=METRICS_PATH)
    parser.add_argument("--raw-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--judge-from", type=Path, help="a raw file to resume judging from (no generation)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    rows = _rows()
    if args.judge_from:
        state = json.loads(args.judge_from.read_text(encoding="utf-8"))
        raw_path = args.judge_from
    else:
        row_numbers = [int(x) for x in args.rows.split(",")] if args.rows else list(range(1, len(rows) + 1))
        full = row_numbers == list(range(1, len(rows) + 1)) and args.trials == TRIALS
        if not full and args.out.resolve() == METRICS_PATH.resolve():
            raise SystemExit("a partial run must not write eval/provider_metrics.json; pass --out")
        state = run_generation(rows, row_numbers, args.trials)
        args.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = args.raw_dir / f"g11_raw_{state['started_at']}.json"
    spend = Spend(state["spend"])

    def save():
        raw_path.write_text(json.dumps(state, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    save()
    t0 = time.perf_counter()
    judge_phase(state, rows, spend, save)
    state["wall_s"]["judge_phase"] = round(state["wall_s"].get("judge_phase", 0) + time.perf_counter() - t0, 1)
    save()

    metrics = derive(state, rows, state["row_numbers"], state["trials"])
    args.out.write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print_summary(metrics)
    print(f"raw (gitignored): {raw_path}\nmetrics: {args.out}")


if __name__ == "__main__":
    main()
