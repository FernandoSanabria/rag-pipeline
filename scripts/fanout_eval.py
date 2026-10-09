"""G12 fan-out eval: runs the pre-registered P1–P6 of eval/g12_PREDICTION.md against the live backends.

Two arms in one process:
- fan-out: `agent.graph` with FANOUT_ENABLED set (the shipped default is off), the G12 enabled build;
- current: `agent/graph.py` as it stands at the branch point (`git merge-base HEAD origin/main`), loaded as the module
  `agent.graph_baseline`. Both use the same `src/` and `agent.state.fresh_state`.

Parts:
- P1 (with P2, P3 and P6 read from the same runs): rows 9, 10, 11 and 21, three trials; within a row the two arms run
  back to back, alternating which goes first. P3 scores every run with RAGAS (context_precision, context_recall,
  answer_correctness), one sample at a time, with run_eval.py's judge.
- P4: the same four rows, three trials, fan-out arm only, with a third sub-question scoped to `no-such-doc` appended at
  dispatch (it raises at validation, before any retrieval call).
- P5: the other 24 rows, three trials, both arms. The query embedding, the router decision and the tool decision are
  memoized and shared across arms, and generation is stubbed: only the contexts handed to `generate` are compared.
  P1's 0/72 is read from the fan-out arm's traces here.

Writes derived metrics only to eval/fanout_metrics.json (no answers, contexts or question text). Raw answers, contexts
and traces of every run go to the gitignored eval/results/g12_raw_<ts>.json. About $0.5.
Run: `uv run python scripts/fanout_eval.py`.
"""

import json
import logging
import re
import statistics
import subprocess
import sys
import time
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = REPO_ROOT / "eval" / "dataset.jsonl"
METRICS_PATH = REPO_ROOT / "eval" / "fanout_metrics.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"

load_dotenv(REPO_ROOT / ".env")

TRIALS = 3
COMPARISON_ROWS = (9, 10, 11, 21)
COMPARED = {9: ("niosh-pocket-guide", "epa-rmp-ammonia-refrigeration"), 10: ("osha-1910-1000", "niosh-pocket-guide"),
            11: ("osha-1910-1000", "niosh-pocket-guide"), 21: ("sds-airgas-chlorine", "osha-1910-1000")}
# Each source's own value line, recorded for P2 but not scored (source scoping makes source presence near-mechanical).
VALUE_LINES = {
    9: {"niosh-pocket-guide": r"Ammonia\s+Formula[\s\S]{0,200}IDLH:\s*300",
        "epa-rmp-ammonia-refrigeration": r"toxic endpoint for ammonia is 200 ppm"},
    10: {"osha-1910-1000": r"Chlorine\s+7782-50-5", "niosh-pocket-guide": r"Chlorine\s+Formula[\s\S]{0,300}C 0\.5 ppm"},
    11: {"osha-1910-1000": r"Ammonia\s+7664-41-7", "niosh-pocket-guide": r"Ammonia\s+Formula[\s\S]{0,400}25 ppm"},
    21: {"sds-airgas-chlorine": r"CEIL", "osha-1910-1000": r"Chlorine\s+7782-50-5"},
}
METRICS = ("context_recall", "context_precision", "answer_correctness")
# P3's registered directions. "no_drop": the opposite direction is below the current minimum.
P3_PREDICTIONS = {**{("context_recall", n): "no_drop" for n in COMPARISON_ROWS},
                  ("context_precision", 9): "up", ("context_precision", 10): "down",
                  ("context_precision", 11): "up", ("context_precision", 21): "down",
                  ("answer_correctness", 9): "no_drop"}
P3_MARGIN = 0.03
P4_DOC = "no-such-doc"
PRICE_PER_M = {"input": 0.15, "cached_input": 0.075, "output": 0.60, "embedding": 0.02}  # eval/COST_LEDGER.md


def _load_rows() -> list[dict]:
    return [json.loads(line) for line in DATASET_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def _summary(values: list[float], digits: int = 3) -> dict:
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    return {"n": len(values), "p50": round(statistics.median(values), digits), "min": round(min(values), digits),
            "max": round(max(values), digits)}


def _chat_cost(prompt: int, cached: int, completion: int) -> float:
    return ((prompt - cached) * PRICE_PER_M["input"] + cached * PRICE_PER_M["cached_input"]
            + completion * PRICE_PER_M["output"]) / 1e6


def load_baseline():
    """The current path: agent/graph.py at the branch point, executed as its own module in this process."""
    base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=REPO_ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()
    source = subprocess.run(["git", "show", f"{base}:agent/graph.py"], cwd=REPO_ROOT, capture_output=True, text=True,
                            check=True).stdout
    module = types.ModuleType("agent.graph_baseline")
    module.__file__ = str(REPO_ROOT / "agent" / "graph_baseline.py")  # so its manifest path resolves to data/
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module, base[:7]


def fingerprint_handler():
    """A LangChain callback handler counting (model, system_fingerprint) per completed LLM call."""
    from langchain_core.callbacks import BaseCallbackHandler

    class FingerprintHandler(BaseCallbackHandler):
        def __init__(self):
            self.counts = Counter()

        def on_llm_end(self, response, **kwargs):
            out = response.llm_output or {}
            self.counts[f"{out.get('model_name')}|{out.get('system_fingerprint')}"] += 1

    return FingerprintHandler()


class _WithCallbacks:
    """Wraps a runnable so every .invoke passes our callback handler (the node code calls .invoke(prompt))."""

    def __init__(self, runnable, handler):
        self.runnable, self.handler = runnable, handler

    def invoke(self, prompt, **kwargs):
        return self.runnable.invoke(prompt, config={"callbacks": [self.handler]})


class _SharedMemo:
    """One decision per prompt, shared by both arms (P5): tests the layer, not backend reproducibility."""

    def __init__(self, runnable):
        self.runnable, self.cache, self.calls = runnable, {}, 0

    def invoke(self, prompt, **kwargs):
        if prompt not in self.cache:
            self.calls += 1
            self.cache[prompt] = self.runnable.invoke(prompt)
        return self.cache[prompt]


def count_embeddings(real, memoize: bool) -> dict:
    """Point src.retrieve._embedder at a wrapper around the real embedder that counts query-embedding tokens, and
    memoizes per text when asked (P5 only). `real` is captured once, so wrappers never wrap each other."""
    import tiktoken

    import src.retrieve as retrieve

    encoding = tiktoken.get_encoding("cl100k_base")
    stats = {"calls": 0, "tokens": 0}
    cache: dict[str, list[float]] = {}

    class Counting:
        def embed_query(self, text):
            if memoize and text in cache:
                return cache[text]
            vector = real.embed_query(text)
            stats["calls"] += 1
            stats["tokens"] += len(encoding.encode(text))
            if memoize:
                cache[text] = vector
            return vector

    counting = Counting()
    retrieve._embedder = lambda: counting
    return stats


def score(question: str, answer: str, contexts: list[str], reference: str, judge) -> dict:
    """RAGAS on one sample, P3's three metrics, run_eval.py's judge construction; NaN-safe."""
    import math

    from ragas import EvaluationDataset, evaluate
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import answer_correctness, context_precision, context_recall

    sample = SingleTurnSample(user_input=question, response=answer, retrieved_contexts=contexts, reference=reference)
    result = evaluate(dataset=EvaluationDataset(samples=[sample]),
                      metrics=[context_recall, context_precision, answer_correctness],
                      llm=judge["llm"], embeddings=judge["embeddings"], raise_exceptions=False, show_progress=False)
    row = result.to_pandas().iloc[0]
    out = {}
    for name in METRICS:
        value = row.get(name)
        out[name] = None if value is None or (isinstance(value, float) and math.isnan(value)) else round(float(value), 4)
    return out


class Run:
    def __init__(self, new, cur):
        from api.confidence import is_refusal
        from api.guards import check_output
        from src.retrieve import format_contexts

        self.new, self.cur = new, cur
        new.FANOUT_ENABLED = True  # the shipped graph has the fan-out off; this arm measures the enabled build
        self.format_contexts, self.is_refusal, self.check_output = format_contexts, is_refusal, check_output
        self.decomposer_fp = fingerprint_handler()
        real = new._decomposer_llm()
        new._decomposer_llm = lambda: _WithCallbacks(real, self.decomposer_fp)
        self.spend: dict[str, dict] = {}
        self.backends: dict[str, list] = {}
        self.parts: dict[str, list] = {}
        self.embeddings: dict[str, dict] = {}

    # --- one run of one arm -------------------------------------------------------------------------------------------

    def invoke(self, module, question: str) -> tuple[dict, float, dict]:
        """One graph run. Tokens are deltas on the part's single callback handler: nesting get_openai_callback
        contexts would hide the inner calls from the part's totals."""
        from agent.state import fresh_state

        cb = self._cb
        before = (cb.successful_requests, cb.prompt_tokens, cb.prompt_tokens_cached, cb.completion_tokens)
        started = time.perf_counter()
        state = module._compiled_graph().invoke(fresh_state(question))
        wall = time.perf_counter() - started
        after = (cb.successful_requests, cb.prompt_tokens, cb.prompt_tokens_cached, cb.completion_tokens)
        tokens = dict(zip(("requests", "prompt", "cached", "completion"), (a - b for a, b in zip(after, before))))
        return state, wall, tokens

    def describe(self, n: int, state: dict) -> dict:
        import tiktoken

        retrieved = state["retrieved"]
        contexts = self.format_contexts(retrieved)
        sources = sorted({c["source_doc_id"] for c in retrieved if not str(c["source_doc_id"]).startswith("tool:")})
        value_lines = {doc: any(re.search(p, c["text"], re.I) for c in retrieved if c["source_doc_id"] == doc)
                       for doc, p in VALUE_LINES.get(n, {}).items()}
        encoding = tiktoken.get_encoding("o200k_base")
        return {
            "route": state["route"],
            "fanned_out": state["route"] == "decomposed",
            "n_branches": len(state["fanout"]),
            "branch_errors": [r["error"] for r in state["fanout"] if r["error"]],
            "n_contexts": len(contexts),
            "context_tokens": sum(len(encoding.encode(c)) for c in contexts),
            "sources": sources,
            "both_sources": all(doc in sources for doc in COMPARED.get(n, ())),
            "value_lines": value_lines,
            "tool_fired": any(str(c["source_doc_id"]).startswith("tool:") for c in retrieved),
            "decompose_notes": [t for t in state["trace_notes"] if t.startswith(("decompose", "fanout[", "join"))],
        }

    # --- the parts ------------------------------------------------------------------------------------------------------

    def p1(self, rows):
        records = []
        for t in range(1, TRIALS + 1):
            for n in COMPARISON_ROWS:
                arms = (("fanout", self.new), ("current", self.cur))
                for arm, module in (arms if t % 2 else arms[::-1]):
                    question = rows[n - 1]["question"]
                    state, wall, tokens = self.invoke(module, question)
                    contexts = self.format_contexts(state["retrieved"])
                    records.append({"trial": t, "row": n, "arm": arm, "wall_s": round(wall, 3), "tokens": tokens,
                                    **self.describe(n, state), "answer": state["answer"], "contexts": contexts,
                                    "trace_notes": state["trace_notes"],
                                    "fanout": [{k: v for k, v in r.items() if k != "chunks"} | {"n_chunks": len(r["chunks"])}
                                               for r in state["fanout"]]})
                    print(f"  P1 t{t} row {n} {arm:8s} {wall:5.2f}s route={state['route']} "
                          f"contexts={len(contexts)} both_sources={records[-1]['both_sources']}", flush=True)
        self.parts["P1"] = records

    def p3_score(self, rows):
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper

        self.judge_fp = fingerprint_handler()
        judge = {"llm": LangchainLLMWrapper(ChatOpenAI(model="gpt-4o-mini", temperature=0, callbacks=[self.judge_fp])),
                 "embeddings": LangchainEmbeddingsWrapper(OpenAIEmbeddings(model="text-embedding-3-small"))}
        for rec in self.parts["P1"]:
            row = rows[rec["row"] - 1]
            rec["scores"] = score(row["question"], rec["answer"], rec["contexts"], row["reference"], judge)
            print(f"  P3 t{rec['trial']} row {rec['row']} {rec['arm']:8s} {rec['scores']}", flush=True)

    def p4(self, rows):
        new = self.new
        real_decompose = new.decompose_node

        def injecting(state):
            out = real_decompose(state)
            plans = out.get("sub_questions") or []
            if plans and len(plans) < new.MAX_BRANCHES:
                out = {**out, "sub_questions": plans + [{"question": f"[P4 injected] {state['question']}",
                                                         "source_doc_id": P4_DOC}]}
            return out

        new.decompose_node = injecting
        new._build_graph.cache_clear()  # rebuild so the builder picks up the wrapped decompose_node
        records = []
        try:
            for t in range(1, TRIALS + 1):
                for n in COMPARISON_ROWS:
                    question = rows[n - 1]["question"]
                    try:
                        state, wall, tokens = self.invoke(new, question)
                        error = None
                    except Exception as exc:  # P4's falsifier includes an exception, so record it, don't abort
                        state, wall, tokens, error = None, 0.0, {}, type(exc).__name__
                    rec = {"trial": t, "row": n, "exception": error}
                    if state is not None:
                        answer = state["answer"]
                        notes = state["trace_notes"]
                        rec.update({
                            "answer": answer, "wall_s": round(wall, 3), "tokens": tokens,
                            "empty": not answer.strip(), "whole_refusal": self.is_refusal(answer),
                            "injected": any(r["source_doc_id"] == P4_DOC for r in state["fanout"]),
                            "failure_named": any(t_.startswith("fanout[3/3]") and t_.endswith("ERROR ValueError")
                                                 for t_ in notes) and any("fanout[3/3] ValueError" in t_ for t_ in notes
                                                                          if t_.startswith("join:")),
                            "output_guard_would_withhold": self.check_output(
                                answer, self.format_contexts(state["retrieved"]), question) is not None,
                            "trace_notes": [t_ for t_ in notes if t_.startswith(("decompose", "fanout[", "join"))],
                        })
                    records.append(rec)
                    print(f"  P4 t{t} row {n}: exception={error} empty={rec.get('empty')} "
                          f"refusal={rec.get('whole_refusal')} named={rec.get('failure_named')}", flush=True)
        finally:
            new.decompose_node = real_decompose
            new._build_graph.cache_clear()
        self.parts["P4"] = records

    def p5(self, rows):
        new, cur = self.new, self.cur
        router = _SharedMemo(new._router_llm())
        tools = _SharedMemo(new._tool_llm())
        captured: dict[str, list] = {"fanout": [], "current": []}
        saved = {m: (m._router_llm, m._tool_llm, m.generate) for m in (new, cur)}

        def recorder(arm):
            def fake_generate(question, contexts):
                captured[arm].append(list(contexts))
                return "STUB"
            return fake_generate

        for module, arm in ((new, "fanout"), (cur, "current")):
            module._router_llm = lambda: router
            module._tool_llm = lambda: tools
            module.generate = recorder(arm)
        records = []
        try:
            others = [n for n in range(1, len(rows) + 1) if n not in COMPARISON_ROWS]
            for t in range(1, TRIALS + 1):
                for n in others:
                    question = rows[n - 1]["question"]
                    contexts, routes, notes = {}, {}, {}
                    arms = (("fanout", new), ("current", cur))
                    for arm, module in (arms if t % 2 else arms[::-1]):
                        before = len(captured[arm])
                        state, _, _ = self.invoke(module, question)
                        contexts[arm] = captured[arm][-1] if len(captured[arm]) > before else None
                        routes[arm] = state["route"]
                        notes[arm] = [x for x in state["trace_notes"] if x.startswith(("decompose", "fanout[", "join"))]
                    records.append({"trial": t, "row": n, "identical": contexts["fanout"] == contexts["current"],
                                    "fanned_out": routes["fanout"] == "decomposed" or bool(notes["fanout"]),
                                    "route_fanout": routes["fanout"], "route_current": routes["current"],
                                    "n_contexts": len(contexts["fanout"] or [])})
            print(f"  P5: {sum(r['identical'] for r in records)}/{len(records)} identical; "
                  f"fan-out fired on {sum(r['fanned_out'] for r in records)}/{len(records)}", flush=True)
        finally:
            for module, (r, t_, g) in saved.items():
                module._router_llm, module._tool_llm, module.generate = r, t_, g
        self.parts["P5"] = records
        self.memo_calls = {"router": router.calls, "tool": tools.calls}

    def run_part(self, name, fn, *args):
        from langchain_community.callbacks.manager import get_openai_callback

        from src.generate import generation_backends, reset_generation_backends

        reset_generation_backends()
        started = time.perf_counter()
        with get_openai_callback() as cb:
            self._cb = cb
            fn(*args)
        self.backends[name] = generation_backends()
        self.spend[name] = {"chat_requests": cb.successful_requests, "prompt_tokens": cb.prompt_tokens,
                            "prompt_tokens_cached": cb.prompt_tokens_cached, "completion_tokens": cb.completion_tokens,
                            "cost_usd": round(_chat_cost(cb.prompt_tokens, cb.prompt_tokens_cached,
                                                         cb.completion_tokens), 6),
                            "wall_s": round(time.perf_counter() - started, 1)}
        print(f"{name}: done in {self.spend[name]['wall_s']} s, ${self.spend[name]['cost_usd']:.4f}", flush=True)


# --- derived metrics and verdict helpers ---------------------------------------------------------------------------


def p3_verdicts(records: list[dict]) -> list[dict]:
    """Refinement A: FALSIFIED only if the fan-out mean is outside the current arm's observed [min, max] in the
    direction opposite to the prediction AND the difference of means exceeds 0.03."""
    out = []
    for (metric, n), direction in P3_PREDICTIONS.items():
        cur = [r["scores"][metric] for r in records if r["row"] == n and r["arm"] == "current"]
        fan = [r["scores"][metric] for r in records if r["row"] == n and r["arm"] == "fanout"]
        cur_v, fan_v = [v for v in cur if v is not None], [v for v in fan if v is not None]
        entry = {"row": n, "metric": metric, "prediction": direction, "current_trials": cur, "fanout_trials": fan}
        if not cur_v or not fan_v:
            entry.update({"verdict": "NOT TESTED", "reason": "no scored trials in one arm"})
            out.append(entry)
            continue
        lo, hi = min(cur_v), max(cur_v)
        cur_mean, fan_mean = statistics.mean(cur_v), statistics.mean(fan_v)
        diff = fan_mean - cur_mean
        if direction == "down":
            opposite = fan_mean > hi and diff > P3_MARGIN
        else:  # "up" and "no_drop" share the opposite direction: below the current minimum
            opposite = fan_mean < lo and -diff > P3_MARGIN
        entry.update({"current_range": [round(lo, 4), round(hi, 4)], "current_mean": round(cur_mean, 4),
                      "fanout_mean": round(fan_mean, 4), "difference": round(diff, 4),
                      "verdict": "FALSIFIED" if opposite else "HOLDS"})
        out.append(entry)
    return out


def derive(run: Run, embed_stats: dict, base: str) -> dict:
    from src.config import get_settings

    settings = get_settings()
    p1 = run.parts.get("P1", [])
    metrics = {
        "kind": "g12_fanout_metrics",
        "note": "Derived metrics only: no answer text, no retrieved contexts, no question text. Rows are 1-based "
                "indexes into eval/dataset.jsonl.",
        "preregistration": "eval/g12_PREDICTION.md (tag prereg/g12)",
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "trials": TRIALS,
        "current_arm": f"agent/graph.py at the branch point ({base})",
        "retrieval_namespace": settings.retrieval_namespace,
        "retrieval_k": settings.retrieval_k,
        "generation_backends": run.backends,
        "decomposer_fingerprints": dict(run.decomposer_fp.counts),
        "judge_fingerprints": dict(run.judge_fp.counts) if hasattr(run, "judge_fp") else {},
    }
    if p1:
        fan = [r for r in p1 if r["arm"] == "fanout"]
        cur = [r for r in p1 if r["arm"] == "current"]
        metrics["P1"] = {"comparison_rows_fired": sum(r["fanned_out"] for r in fan), "comparison_runs": len(fan),
                         "per_row": {n: sum(r["fanned_out"] for r in fan if r["row"] == n) for n in COMPARISON_ROWS}}
        metrics["P2"] = {
            "fanout_both_sources": sum(r["both_sources"] for r in fan), "fanout_runs": len(fan),
            "current_both_sources": sum(r["both_sources"] for r in cur), "current_runs": len(cur),
            "per_row": {n: {"fanout": sum(r["both_sources"] for r in fan if r["row"] == n),
                            "current": sum(r["both_sources"] for r in cur if r["row"] == n),
                            "fanout_value_lines": [r["value_lines"] for r in fan if r["row"] == n],
                            "current_value_lines": [r["value_lines"] for r in cur if r["row"] == n]}
                        for n in COMPARISON_ROWS},
        }
        if all("scores" in r for r in p1):
            metrics["P3"] = p3_verdicts(p1)
        fw, cw = [r["wall_s"] for r in fan], [r["wall_s"] for r in cur]
        metrics["P6"] = {
            "fanout_wall_s": _summary(fw), "current_wall_s": _summary(cw),
            "p50_difference_s": round(statistics.median(fw) - statistics.median(cw), 3) if fw and cw else None,
            "fanout_context_tokens": _summary([r["context_tokens"] for r in fan], 0),
            "current_context_tokens": _summary([r["context_tokens"] for r in cur], 0),
            "fanout_prompt_tokens_per_run": _summary([r["tokens"]["prompt"] for r in fan], 0),
            "current_prompt_tokens_per_run": _summary([r["tokens"]["prompt"] for r in cur], 0),
            "fanout_contexts": _summary([r["n_contexts"] for r in fan], 0),
            "current_contexts": _summary([r["n_contexts"] for r in cur], 0),
            "branch_intervals": [[(b["t_start"], b["t_end"]) for b in r["fanout"]] for r in fan],
            "tool_fired": {"fanout": sum(r["tool_fired"] for r in fan), "current": sum(r["tool_fired"] for r in cur)},
        }
    if "P4" in run.parts:
        p4 = run.parts["P4"]
        metrics["P4"] = {
            "runs": len(p4), "exceptions": sum(r["exception"] is not None for r in p4),
            "injected": sum(bool(r.get("injected")) for r in p4),
            "answered": sum(r["exception"] is None and not r["empty"] and not r["whole_refusal"] for r in p4),
            "empty": sum(bool(r.get("empty")) for r in p4), "whole_refusals": sum(bool(r.get("whole_refusal")) for r in p4),
            "failure_named": sum(bool(r.get("failure_named")) for r in p4),
            "output_guard_would_withhold": sum(bool(r.get("output_guard_would_withhold")) for r in p4),
        }
    if "P5" in run.parts:
        p5 = run.parts["P5"]
        metrics["P5"] = {"pairs": len(p5), "identical": sum(r["identical"] for r in p5),
                         "differences": [{"trial": r["trial"], "row": r["row"]} for r in p5 if not r["identical"]],
                         "fanout_fired_on_non_comparison_rows": sum(r["fanned_out"] for r in p5),
                         "memo_live_calls": getattr(run, "memo_calls", {})}
    chat = {k: sum(s[k] for s in run.spend.values())
            for k in ("chat_requests", "prompt_tokens", "prompt_tokens_cached", "completion_tokens")}
    emb_tokens = sum(s["tokens"] for s in embed_stats.values())
    metrics["spend"] = {"per_part": run.spend, "chat": chat, "embedding": embed_stats,
                        "cost_usd_derived": round(sum(s["cost_usd"] for s in run.spend.values())
                                                  + emb_tokens * PRICE_PER_M["embedding"] / 1e6, 4),
                        "flag": "d (token counts x eval/COST_LEDGER.md list prices; RAGAS answer_correctness "
                                "embeddings not counted)"}
    return metrics


def report(metrics: dict, run: Run) -> None:
    if "P1" in metrics:
        print(f"\nP1 fan-out fired on comparison rows: {metrics['P1']['comparison_rows_fired']}/"
              f"{metrics['P1']['comparison_runs']} {metrics['P1']['per_row']}")
        print(f"P2 both sources: fan-out {metrics['P2']['fanout_both_sources']}/{metrics['P2']['fanout_runs']}, "
              f"current {metrics['P2']['current_both_sources']}/{metrics['P2']['current_runs']}")
    if "P3" in metrics:
        print("\nP3 per row (trial values, current range, means, verdict):")
        for e in metrics["P3"]:
            print(f"  row {e['row']:2d} {e['metric']:19s} {e['prediction']:8s} current={e['current_trials']} "
                  f"fanout={e['fanout_trials']} range={e.get('current_range')} diff={e.get('difference')} "
                  f"-> {e['verdict']}")
    if "P4" in metrics:
        print(f"\nP4: {metrics['P4']}")
    if "P5" in metrics:
        print(f"P5: {metrics['P5']}")
    if "P6" in metrics:
        p6 = metrics["P6"]
        print(f"\nP6: fan-out p50 {p6['fanout_wall_s'].get('p50')}s, current p50 {p6['current_wall_s'].get('p50')}s, "
              f"difference {p6['p50_difference_s']}s; contexts {p6['fanout_contexts']} vs {p6['current_contexts']}; "
              f"context tokens {p6['fanout_context_tokens']} vs {p6['current_context_tokens']}")
    print(f"\ngeneration backends: {metrics['generation_backends']}")
    print(f"decomposer fingerprints: {metrics['decomposer_fingerprints']}; judge: {metrics['judge_fingerprints']}")
    print(f"spend: ${metrics['spend']['cost_usd_derived']} derived; chat {metrics['spend']['chat']}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    from agent import graph as new

    rows = _load_rows()
    assert len(rows) == 28
    cur, base = load_baseline()
    run = Run(new, cur)
    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    embed_stats = {}

    import src.retrieve as retrieve

    real_embedder = retrieve._embedder()
    embed_stats["P1_P4"] = count_embeddings(real_embedder, memoize=False)
    run.run_part("P1", run.p1, rows)
    run.run_part("P3", run.p3_score, rows)
    run.run_part("P4", run.p4, rows)
    embed_stats["P5"] = count_embeddings(real_embedder, memoize=True)
    run.run_part("P5", run.p5, rows)

    metrics = derive(run, embed_stats, base)
    report(metrics, run)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RESULTS_DIR / f"g12_raw_{started}.json"
    raw_path.write_text(json.dumps(run.parts, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    METRICS_PATH.write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    def shown(path: Path) -> str:
        return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)

    print(f"\nraw (gitignored): {shown(raw_path)}\nmetrics: {shown(METRICS_PATH)}")


if __name__ == "__main__":
    main()
