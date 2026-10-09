"""G10b review eval: runs P1, P2, P3a, P4 and P5 of eval/g10b_PREDICTION.md against the live backends.

Parts (rows are 1-based):
- P1: the fire set. The frozen 28, the capability set (3) and the guardrail hard negatives (9), 3 trials each, through
  agent.graph.ask with a SQLite checkpointer. The router, retrieval and tool decision are real; generation is stubbed,
  because P1 asks only whether the gate fires.
- P2: four paths, live, through the real FastAPI endpoints in-process (TestClient), on rows 10 and 11, 3 trials each.
  The query embedding, router decision and tool decision are memoized and shared with an ungated arm of the same
  question, so "byte-identical" tests the gate, not backend reproducibility. Approve and amend generate for real (the
  output guard then runs; a withhold there is expected, refinement C); reject and expired must not generate.
- P4 and P5: pass-through and latency. The 25 non-trigger frozen rows, 3 trials, three arms: the pre-G10b graph and
  state (agent/graph.py and agent/state.py at the merge-base, loaded as modules), G10b without a checkpointer, and G10b
  with the SqliteSaver. Generation is stubbed and decisions are memoized; the contexts handed to generate are compared,
  and each run's wall time is recorded. The pre-G10b arm runs first in each row-trial, so the two G10b arms (alternating
  order) both run on warm memos and their difference is the checkpointer's cost.
- P3a: a real uvicorn process with REVIEW_DB_PATH pauses row 10, is killed, restarts on the same file, and resumes.

Writes derived metrics only to eval/review_metrics.json (no answers, contexts or question text). Raw records go to the
gitignored eval/results/g10b_raw_<ts>.json. Run: `uv run python scripts/review_eval.py`.
"""

import json
import logging
import os
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import types
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
METRICS_PATH = REPO_ROOT / "eval" / "review_metrics.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
load_dotenv(REPO_ROOT / ".env")
os.environ["LANGCHAIN_TRACING_V2"] = "false"

TRIALS = 3
P2_ROWS = (10, 11)
PREDICTED_FIRE = {("frozen", 9), ("frozen", 10), ("frozen", 11)}
PRICE_PER_M = {"input": 0.15, "cached_input": 0.075, "output": 0.60}  # eval/COST_LEDGER.md


def _jsonl(path, keep=lambda r: True):
    out = []
    for n, line in enumerate((REPO_ROOT / path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip() and keep(json.loads(line)):
            out.append((n, json.loads(line)["question"]))
    return out


def question_sets() -> dict[str, list[tuple[int, str]]]:
    return {"frozen": _jsonl("eval/dataset.jsonl"), "capability": _jsonl("eval/capability_set.jsonl"),
            "hard_negative": _jsonl("eval/guardrail_set.jsonl", keep=lambda r: r["category"] == "hard_negative")}


def smoke_rows() -> set[int]:
    return {r["row"] for r in json.loads((REPO_ROOT / "eval" / "smoke_set.json").read_text())["rows"]}


def _chat_cost(cb) -> float:
    return round(((cb.prompt_tokens - cb.prompt_tokens_cached) * PRICE_PER_M["input"]
                  + cb.prompt_tokens_cached * PRICE_PER_M["cached_input"]
                  + cb.completion_tokens * PRICE_PER_M["output"]) / 1e6, 6)


class SharedMemo:
    """One decision per prompt, shared by every arm that holds it (G12's P5 discipline)."""

    def __init__(self, runnable):
        self.runnable, self.cache, self.calls = runnable, {}, 0

    def invoke(self, prompt, **kwargs):
        if prompt not in self.cache:
            self.calls += 1
            self.cache[prompt] = self.runnable.invoke(prompt)
        return self.cache[prompt]


class Recorder:
    """Stands in for a module's `generate`: records (question, contexts), then answers via `inner` (real or a stub)."""

    def __init__(self, inner=None):
        self.inner, self.calls = inner, []

    def __call__(self, question, contexts):
        self.calls.append((question, list(contexts)))
        return self.inner(question, contexts) if self.inner else ""


def memoize_embeddings():
    import src.retrieve as retrieve

    real = retrieve._embedder()
    cache = {}

    class Memo:
        def embed_query(self, text):
            if text not in cache:
                cache[text] = real.embed_query(text)
            return cache[text]

    memo = Memo()
    retrieve._embedder = lambda: memo
    return lambda: setattr(retrieve, "_embedder", lambda: real)


def load_baseline():
    """agent/state.py and agent/graph.py at the merge-base, executed as their own modules (the pre-G10b arm)."""
    base = subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=REPO_ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()
    show = lambda path: subprocess.run(["git", "show", f"{base}:{path}"], cwd=REPO_ROOT, capture_output=True,
                                       text=True, check=True).stdout
    state = types.ModuleType("agent.state_baseline")
    state.__file__ = str(REPO_ROOT / "agent" / "state_baseline.py")
    sys.modules[state.__name__] = state
    exec(compile(show("agent/state.py"), state.__file__, "exec"), state.__dict__)
    source = show("agent/graph.py")
    assert source.count("from agent.state import") == 1
    source = source.replace("from agent.state import", "from agent.state_baseline import")
    graph = types.ModuleType("agent.graph_baseline")
    graph.__file__ = str(REPO_ROOT / "agent" / "graph_baseline.py")  # so its manifest path resolves to data/
    sys.modules[graph.__name__] = graph
    exec(compile(source, graph.__file__, "exec"), graph.__dict__)
    return graph, base[:7]


# --- P1 ----------------------------------------------------------------------------------------------------------------


def p1(graph, review, db) -> dict:
    os.environ["REVIEW_DB_PATH"] = db
    real_generate = graph.generate
    graph.generate = lambda q, c: ""  # P1 asks only whether the gate fires
    records = []
    try:
        for trial in range(1, TRIALS + 1):
            for set_name, items in question_sets().items():
                for n, question in items:
                    out = graph.ask(question)
                    records.append({"trial": trial, "set": set_name, "row": n, "status": out.get("status", "answered"),
                                    "reason": out.get("reason"), "route": out["route"]})
    finally:
        graph.generate = real_generate
    fired = {(r["set"], r["row"]) for r in records if r["status"] == "pending_review"}
    per_row = {}
    for r in records:
        key = f"{r['set']}:{r['row']}"
        per_row.setdefault(key, 0)
        per_row[key] += r["status"] == "pending_review"
    smoke = smoke_rows()
    return {
        "records": records,
        "fired_rows": sorted(f"{s}:{n}" for s, n in fired),
        "fires_per_predicted_row": {f"frozen:{n}": per_row[f"frozen:{n}"] for _, n in sorted(PREDICTED_FIRE)},
        "off_prediction": sorted(f"{s}:{n}" for s, n in fired ^ PREDICTED_FIRE),
        "smoke_fires": sum(per_row[f"frozen:{n}"] for n in smoke),
        "hard_negative_fires": sum(v for k, v in per_row.items() if k.startswith("hard_negative")),
        "capability_fires": sum(v for k, v in per_row.items() if k.startswith("capability")),
        "other_frozen_fires": sum(v for k, v in per_row.items() if k.startswith("frozen")
                                  and ("frozen", int(k.split(":")[1])) not in PREDICTED_FIRE),
        "reasons": sorted({r["reason"] for r in records if r["reason"]}),
        "statuses": sorted({r["status"] for r in records}),
    }


# --- P2 ----------------------------------------------------------------------------------------------------------------


def p2(graph, review, db) -> dict:
    from fastapi.testclient import TestClient

    from agent.state import chunk_key
    from api import main
    from src.retrieve import format_contexts

    os.environ["REVIEW_DB_PATH"] = db
    client = TestClient(main.app)
    rows = dict(question_sets()["frozen"])
    real_generate = graph.generate
    real_trigger = review.trigger_reason
    records = []
    try:
        for trial in range(1, TRIALS + 1):
            for n in P2_ROWS:
                question = rows[n]
                # ungated arm: same process, same memoized decisions; generation stubbed (only its input matters)
                ungated = Recorder()
                graph.generate = ungated
                review.trigger_reason = lambda q, t: None
                graph.ask(question)
                review.trigger_reason = real_trigger
                reference = ungated.calls[-1][1]

                for path in ("approve", "reject", "amend", "expired"):
                    rec = Recorder(real_generate if path in ("approve", "amend") else None)
                    graph.generate = rec
                    if path == "expired":
                        os.environ["REVIEW_TTL_S"] = "5"
                    paused = client.post("/ask/agent", json={"question": question})
                    os.environ.pop("REVIEW_TTL_S", None)
                    body = paused.json()
                    entry = {"trial": trial, "row": n, "path": path, "paused_status": paused.status_code,
                             "reason": body.get("reason"), "generate_calls_before_resume": len(rec.calls)}
                    if paused.status_code != 202:
                        entry["error"] = "did not pause"
                        records.append(entry)
                        continue
                    # the paused set exactly as checkpointed (the evidence is rendered from these same chunks)
                    chunks = graph._compiled_graph().get_state(graph._thread_config(body["thread_id"])).values[
                        "retrieved"]
                    paused_contexts = format_contexts(chunks)
                    entry["paused_equals_ungated"] = paused_contexts == reference
                    entry["evidence_keys_match"] = [e["key"] for e in body["evidence"]] == [chunk_key(c) for c in chunks]
                    payload = {"thread_id": body["thread_id"], "op": "approve" if path == "expired" else path}
                    expected = paused_contexts
                    if path == "amend":
                        lowest = [c for c in chunks if c.get("page") is not None][-1]  # lowest-ranked document chunk
                        payload["removals"] = [chunk_key(lowest)]
                        expected = format_contexts([c for c in chunks if c is not lowest])
                    if path == "expired":
                        time.sleep(6)
                    resumed = client.post("/ask/agent/resume", json=payload)
                    out = resumed.json()
                    entry.update(status_code=resumed.status_code, guard=out.get("guard"),
                                 generate_calls=len(rec.calls), answer=out.get("answer"))
                    if rec.calls:
                        entry["generate_input_equals_expected"] = rec.calls[-1][1] == expected
                        entry["approve_equals_ungated"] = rec.calls[-1][1] == reference if path == "approve" else None
                    records.append(entry)
    finally:
        graph.generate = real_generate
        review.trigger_reason = real_trigger
        os.environ.pop("REVIEW_TTL_S", None)

    def verdict(path):
        rs = [r for r in records if r["path"] == path]
        if path == "approve":
            ok = [r for r in rs if r.get("generate_calls") == 1 and r.get("approve_equals_ungated")]
        elif path == "amend":
            ok = [r for r in rs if r.get("generate_calls") == 1 and r.get("generate_input_equals_expected")]
        else:
            reason = "rejected" if path == "reject" else "expired_or_lost"
            ok = [r for r in rs if r.get("generate_calls") == 0 and (r.get("guard") or {}).get("reason") == reason]
        withheld = [r for r in rs if (r.get("guard") or {}).get("stage") == "output"]
        return {"n": len(rs), "as_predicted": len(ok), "output_guard_withheld": len(withheld)}

    return {"records": records, "paths": {p: verdict(p) for p in ("approve", "reject", "amend", "expired")},
            "paused_equals_ungated": sum(bool(r.get("paused_equals_ungated")) for r in records),
            "n_paused": sum(r["paused_status"] == 202 for r in records)}


# --- P4 / P5 -------------------------------------------------------------------------------------------------------------


def p4_p5(graph, review, base, db) -> dict:
    rows = [(n, q) for n, q in question_sets()["frozen"] if ("frozen", n) not in PREDICTED_FIRE]
    recorders = {"pre_g10b": Recorder(), "g10b_no_cp": Recorder(), "g10b_cp": Recorder()}
    saved = (graph.generate, base.generate)
    records = []
    try:
        for trial in range(1, TRIALS + 1):
            for i, (n, question) in enumerate(rows):
                order = ["g10b_no_cp", "g10b_cp"] if (i + trial) % 2 else ["g10b_cp", "g10b_no_cp"]
                times, outs = {}, {}
                for arm in ["pre_g10b"] + order:
                    if arm == "pre_g10b":
                        base.generate = recorders[arm]
                        ask = base.ask
                    else:
                        graph.generate = recorders[arm]
                        ask = graph.ask
                        if arm == "g10b_cp":
                            os.environ["REVIEW_DB_PATH"] = db
                        else:
                            os.environ.pop("REVIEW_DB_PATH", None)
                    started = time.perf_counter()
                    outs[arm] = ask(question)
                    times[arm] = time.perf_counter() - started
                contexts = {arm: recorders[arm].calls[-1][1] for arm in recorders}
                records.append({"trial": trial, "row": n, "wall_s": {k: round(v, 4) for k, v in times.items()},
                                "identical": contexts["pre_g10b"] == contexts["g10b_no_cp"] == contexts["g10b_cp"],
                                "status": {k: v.get("status", "answered") for k, v in outs.items()}})
    finally:
        graph.generate, base.generate = saved
        os.environ.pop("REVIEW_DB_PATH", None)
    med = lambda arm: statistics.median(r["wall_s"][arm] for r in records)
    added = [r["wall_s"]["g10b_cp"] - r["wall_s"]["g10b_no_cp"] for r in records]
    return {"records": records, "n": len(records), "identical": sum(r["identical"] for r in records),
            "non_identical_rows": sorted({r["row"] for r in records if not r["identical"]}),
            "paused": sum(any(s == "pending_review" for s in r["status"].values()) for r in records),
            "p50_wall_s": {arm: round(med(arm), 4) for arm in recorders},
            "added_p50_ms": round((med("g10b_cp") - med("g10b_no_cp")) * 1000, 1),
            "paired_added_p50_ms": round(statistics.median(added) * 1000, 1)}


# --- P3a ----------------------------------------------------------------------------------------------------------------


def _http(method, url, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def _serve(db, port):
    env = {**os.environ, "REVIEW_DB_PATH": db}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port)], cwd=REPO_ROOT,
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            if _http("GET", f"http://127.0.0.1:{port}/health", timeout=2)[0] == 200:
                return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("uvicorn did not start")


def p3a(db) -> dict:
    port = 8765
    question = dict(question_sets()["frozen"])[10]
    first = _serve(db, port)
    try:
        code, body = _http("POST", f"http://127.0.0.1:{port}/ask/agent", {"question": question})
    finally:
        first.send_signal(signal.SIGTERM)
        first.wait(timeout=30)
    result = {"paused_status": code, "first_process_exit": first.returncode}
    if code != 202:
        return {**result, "holds": False, "error": "did not pause"}
    second = _serve(db, port)
    try:
        status_code, status = _http("GET", f"http://127.0.0.1:{port}/ask/agent/review/{body['thread_id']}")
        code2, out = _http("POST", f"http://127.0.0.1:{port}/ask/agent/resume",
                           {"thread_id": body["thread_id"], "op": "approve"})
        _, after = _http("GET", f"http://127.0.0.1:{port}/ask/agent/review/{body['thread_id']}")
    finally:
        second.send_signal(signal.SIGTERM)
        second.wait(timeout=30)
    answered = code2 == 200 and (out.get("guard") or {}).get("stage") != "review" and bool(out.get("answer"))
    return {**result, "status_after_restart": status.get("status"), "resume_status": code2,
            "resume_guard": out.get("guard"), "answered": answered, "status_after_resume": after.get("status"),
            "holds": answered and status.get("status") == "pending"}


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    from langchain_community.callbacks.manager import get_openai_callback

    from agent import graph, review
    from src.generate import generation_backends, reset_generation_backends

    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts = (sys.argv[1].split(",") if len(sys.argv) > 1 else ["P1", "P2", "P4", "P3a"])
    tmp = tempfile.mkdtemp(prefix="g10b-")
    raw, metrics, spend = {}, {"timestamp_utc": started, "parts": parts}, {}

    def run(name, fn, *args):
        reset_generation_backends()
        t0 = time.perf_counter()
        with get_openai_callback() as cb:
            out = fn(*args)
        spend[name] = {"chat_requests": cb.successful_requests, "prompt_tokens": cb.prompt_tokens,
                       "prompt_tokens_cached": cb.prompt_tokens_cached, "completion_tokens": cb.completion_tokens,
                       "cost_usd": _chat_cost(cb), "wall_s": round(time.perf_counter() - t0, 1),
                       "generation_backends": generation_backends()}
        print(f"{name}: {spend[name]['wall_s']} s, ${spend[name]['cost_usd']}", flush=True)
        raw[name] = out.pop("records", None)
        metrics[name] = out

    if "P1" in parts:
        run("P1", p1, graph, review, str(Path(tmp) / "p1.sqlite"))
    if "P2" in parts or "P4" in parts:
        restore = memoize_embeddings()
        router = SharedMemo(graph._router_llm())
        tools = SharedMemo(graph._tool_llm())
        graph._router_llm, graph._tool_llm = (lambda: router), (lambda: tools)
        if "P2" in parts:
            run("P2", p2, graph, review, str(Path(tmp) / "p2.sqlite"))
        if "P4" in parts:
            base, base_sha = load_baseline()
            base._router_llm, base._tool_llm = (lambda: router), (lambda: tools)
            metrics["pre_g10b_commit"] = base_sha
            run("P4_P5", p4_p5, graph, review, base, str(Path(tmp) / "p4.sqlite"))
        restore()
    if "P3a" in parts:
        t0 = time.perf_counter()
        metrics["P3a"] = p3a(str(Path(tmp) / "p3a.sqlite"))
        spend["P3a"] = {"wall_s": round(time.perf_counter() - t0, 1),
                        "cost_usd": "not counted (a separate uvicorn process); one router, tool and generate call"}
        print(f"P3a: {metrics['P3a']}", flush=True)
    metrics["spend"] = spend
    metrics["cost_usd_derived"] = round(sum(v["cost_usd"] for v in spend.values() if isinstance(v.get("cost_usd"),
                                                                                                  float)), 6)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RESULTS_DIR / f"g10b_raw_{started}.json"
    raw_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    METRICS_PATH.write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in metrics.items() if k != "spend"}, indent=2, default=str))
    print(f"raw (gitignored): {raw_path.relative_to(REPO_ROOT)}\nmetrics: {METRICS_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
