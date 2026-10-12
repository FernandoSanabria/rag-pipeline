"""G8 cost eval: P1-P7 of eval/g8_PREDICTION.md, live.

Pass A (accounting): routing OFF, nothing memoized. 28 rows x 3 trials, trial-major, through api.main._answer over
agent.graph.ask. Each request's cost record (agent/cost.py) is compared with an independent capture: get_openai_callback
around the same request (OpenAI chat usage) and a counting embedder wrapped around src.retrieve._embedder at runtime.
-> P1, P2.

Pass B (routing): baseline (routing OFF) and routed (routing ON, policy A, Nova Micro on eligible rows), interleaved row
by row, alternating which arm goes first. The query embedding and the router and tool decisions are memoized and shared,
so contexts are identical across arms. Each OpenAI generation request body is hashed (sha256) by a recording transport.
The judge (run_eval.py's: gpt-4o-mini, faithfulness and answer correctness, one sample at a time) scores the eligible
rows in both arms. -> P3, P4, P5, P7(b).

P6: row 25 under the /ask path's contexts (dense_search at retrieval_k, then format_contexts, as src.pipeline.ask does).
Nova Micro through generate_with and gpt-4o-mini (the frozen generator) as the control, 3 trials each.

The G10b review trigger is switched off for the run, as in G11. Bedrock is held to <= 15 requests a minute with retries
and throttles counted (scripts/provider_eval.py's client). Reuses provider_eval.py's memo, judge and P3 rule.

Writes derived values only to eval/cost_metrics.json (no question, answer or context text). Raw answers, contexts and
P6's texts go to the gitignored eval/results/g8_raw_<ts>.json; --judge-from resumes judging from it.

Run: uv run python scripts/cost_eval.py
     uv run python scripts/cost_eval.py --rows 9,22 --trials 1 --out <scratch>/smoke.json --raw-dir <scratch>
"""

import argparse
import hashlib
import json
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import provider_eval as pe  # sets the eval environment (tracing off, no review DB) on import

REPO_ROOT = pe.REPO_ROOT
METRICS_PATH = REPO_ROOT / "eval" / "cost_metrics.json"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
LEDGER = REPO_ROOT / "eval" / "COST_LEDGER.md"
TRIALS = 3
ELIGIBLE = (22, 23, 24, 25)
P6_ROW = 25
SMOKE_AGENT_ROW = 24
P5_PREDICTED = {"generation_per_routed_request": -0.77, "request_per_routed_request": -0.35,
                "across_28_list": -0.037, "across_28_billed": -0.028}
P2_THRESHOLD, P2_FALSIFIER = 0.40, 0.20
CHAT = ("router", "tool_decide", "generate", "decompose", "input_guard", "other")


def ledger_prices() -> dict:
    """COST_LEDGER's price table, parsed independently of agent/cost.py (P1(d))."""
    labels = {"`gpt-4o-mini`": "gpt-4o-mini", "`text-embedding-3-small`": "text-embedding-3-small",
              "Amazon Nova 2 Lite (Bedrock, the `us.` profile)": "us.amazon.nova-2-lite-v1:0",
              "Amazon Nova Micro (Bedrock, the `us.` profile)": "us.amazon.nova-micro-v1:0"}
    money = lambda cell: None if cell.strip() == "—" else float(re.match(r"\s*\$([\d.]+) / 1M", cell).group(1))
    out = {}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0] in labels:
            out[labels[cells[0]]] = {"input": money(cells[1]), "cached": money(cells[2]), "output": money(cells[3]) or 0}
    return out


def ledger_usd(prices, model, input_tokens, cached, output):
    key = next((k for k in sorted(prices, key=len, reverse=True) if model == k or (model or "").startswith(k)), None)
    if key is None:
        return None, None
    p = prices[key]
    cached_rate = p["input"] if p["cached"] is None else p["cached"]
    return ((input_tokens * p["input"] + output * p["output"]) / 1e6,
            ((input_tokens - cached) * p["input"] + cached * cached_rate + output * p["output"]) / 1e6)


def rel(a, b) -> float:
    return 0.0 if a == b else abs(a - b) / max(abs(a), abs(b), 1e-12)


# --- Pass A ---------------------------------------------------------------------------------------------------------------


def pass_a(state, rows, row_numbers, trials, deps) -> None:
    from langchain_community.callbacks.manager import get_openai_callback

    api_main, graph, cost, counting = deps["api_main"], deps["graph"], deps["cost"], deps["counting"]
    prices = ledger_prices()
    for trial in range(1, trials + 1):
        for n in row_numbers:
            counting["calls"] = 0
            started = time.perf_counter()
            with cost.request("/ask/agent") as record, get_openai_callback() as cb:
                fields, result = api_main._answer(rows[n - 1]["question"], graph.ask)
            wall = time.perf_counter() - started
            rec = record.as_dict()
            parts, total = rec["components"], rec["total"]
            chat = {k: sum(parts[c][k] for c in CHAT if c in parts) for k in ("input", "cached", "output")}
            checks = {
                "a_sum": max(rel(total[k], sum(p[k] for p in parts.values()))
                             for k in ("calls", "input", "cached", "output", "usd_list", "usd_billed")),
                "b_callback": max(rel(chat["input"], cb.prompt_tokens), rel(chat["cached"], cb.prompt_tokens_cached),
                                  rel(chat["output"], cb.completion_tokens)),
                "c_embeddings": rel(parts["embedding"]["calls"], counting["calls"]),
            }
            d = 0.0
            for name, part in parts.items():
                if not part["calls"]:
                    continue
                listed = billed = 0.0
                for model in part["models"] or [None]:
                    l, b = ledger_usd(prices, model, part["input"], part["cached"], part["output"])
                    listed, billed = (l or 0.0), (b or 0.0)
                d = max(d, rel(listed, part["usd_list"]), rel(billed, part["usd_billed"]))
            checks["d_ledger"] = d
            state["pass_a"].append({
                "trial": trial, "row": n, "route": result.get("route"), "routing": rec.get("routing"),
                "components": parts, "total": total, "unpriced": rec["unpriced_calls"],
                "independent": {"requests": cb.successful_requests, "prompt": cb.prompt_tokens,
                                "cached": cb.prompt_tokens_cached, "completion": cb.completion_tokens,
                                "embedding_calls": counting["calls"]},
                "checks": checks, "wall_s": round(wall, 3),
                "guard": fields["guard"].stage if fields.get("guard") else None,
            })
            print(f"A t{trial} row {n:2d}: ${total['usd_list']:.5f} list, ${total['usd_billed']:.5f} billed, "
                  f"worst check {max(checks.values()):.2e}", flush=True)


# --- Pass B ---------------------------------------------------------------------------------------------------------------


def pass_b(state, rows, row_numbers, trials, deps) -> None:
    api_main, graph, cost, routing, hashes = (deps["api_main"], deps["graph"], deps["cost"], deps["routing"],
                                              deps["hashes"])
    arms = ("baseline", "routed")
    for trial in range(1, trials + 1):
        for n in row_numbers:
            order = arms if (n + trial) % 2 else arms[::-1]
            for arm in order:
                routing.ROUTING_ENABLED = arm == "routed"
                hashes.clear()
                with cost.request("/ask/agent") as record:
                    fields, result = api_main._answer(rows[n - 1]["question"], graph.ask)
                rec = record.as_dict()
                gen = rec["components"]["generate"]
                state["pass_b"].append({
                    "trial": trial, "row": n, "arm": arm, "position": order.index(arm) + 1,
                    "route": result.get("route"), "routing": rec.get("routing"),
                    "generator": gen["models"], "generate": {k: gen[k] for k in ("input", "cached", "output",
                                                                                 "usd_list", "usd_billed")},
                    "body_sha256": list(hashes),
                    "pages": [[c.get("source_doc_id"), c.get("page")] for c in result.get("chunks", [])],
                    "answer": result.get("answer", ""), "contexts": result.get("contexts", []),
                    "guard": fields["guard"].stage if fields.get("guard") else None,
                })
            last = state["pass_b"][-2:]
            print(f"B t{trial} row {n:2d}: " + " | ".join(f"{r['arm']} {r['routing']} {r['generator']}" for r in last),
                  flush=True)
    routing.ROUTING_ENABLED = False


# --- P6 -------------------------------------------------------------------------------------------------------------------


def p6(state, rows, trials, deps) -> None:
    from api.confidence import is_refusal
    from src.config import get_settings
    from src.retrieve import dense_search, format_contexts

    generation, routing, frozen, bedrock = deps["generation"], deps["routing"], deps["frozen"], deps["bedrock"]
    question = rows[P6_ROW - 1]["question"]
    contexts = format_contexts(dense_search(question, k=get_settings().retrieval_k))
    for trial in range(1, trials + 1):
        for arm in (("nova_micro", "gpt-4o-mini") if trial % 2 else ("gpt-4o-mini", "nova_micro")):
            if arm == "nova_micro":
                answer = generation.generate_with(bedrock, question, contexts, model_id=routing.CHEAP_MODEL)
            else:
                answer = frozen.generate(question, contexts)
            state["p6"].append({"trial": trial, "arm": arm, "answer": answer, "is_refusal": is_refusal(answer),
                                "byte_exact": answer == pe.REFUSAL, "contexts_sha256":
                                hashlib.sha256(json.dumps(contexts).encode()).hexdigest()})
            print(f"P6 t{trial} {arm}: is_refusal={state['p6'][-1]['is_refusal']}", flush=True)


# --- judge and verdicts -----------------------------------------------------------------------------------------------------


def judge(state, rows, save) -> None:
    spend = pe.Spend(state["judge_spend"])
    score = pe.judge_factory(state, spend)
    todo = [r for r in state["pass_b"] if r["row"] in ELIGIBLE and "scores" not in r]
    for i, r in enumerate(todo, 1):
        row = rows[r["row"] - 1]
        r["scores"] = score(row["question"], r["answer"], r["contexts"], row["reference"])
        if i % 6 == 0 or i == len(todo):
            save()
            print(f"judged {i}/{len(todo)}", flush=True)


def derive(state, rows, row_numbers, trials) -> dict:
    A, B, P6 = state["pass_a"], state["pass_b"], state["p6"]

    worst = {k: max((r["checks"][k] for r in A), default=None) for k in ("a_sum", "b_callback", "c_embeddings",
                                                                          "d_ledger")}
    off = [(r["trial"], r["row"]) for r in A if max(r["checks"].values()) > 0.01]
    p1 = {"requests": len(A), "requests_off_by_more_than_1pct": off, "worst_relative_error": worst,
          "unpriced_calls": sum(r["unpriced"] for r in A),
          "verdict": "HOLDS" if A and not off else ("NOT TESTED" if not A else "FALSIFIED")}

    p2 = {"by_trial": {}, "by_call_site": {}}
    for t in range(1, trials + 1):
        rs = [r for r in A if r["trial"] == t]
        inp = sum(r["components"][c]["input"] for r in rs for c in ("router", "tool_decide", "generate"))
        cac = sum(r["components"][c]["cached"] for r in rs for c in ("router", "tool_decide", "generate"))
        p2["by_trial"][t] = {"cached": cac, "input": inp, "rate": round(cac / inp, 4) if inp else None}
        p2["by_call_site"][t] = {c: (round(sum(r["components"][c]["cached"] for r in rs)
                                           / max(1, sum(r["components"][c]["input"] for r in rs)), 4))
                                 for c in ("router", "tool_decide", "generate")}
    later = [p2["by_trial"][t]["rate"] for t in (2, 3) if t in p2["by_trial"] and p2["by_trial"][t]["rate"] is not None]
    p2["verdict"] = ("NOT TESTED" if not later else "FALSIFIED" if min(later) < P2_FALSIFIER else "HOLDS")
    p2["threshold"], p2["falsifier"] = P2_THRESHOLD, P2_FALSIFIER

    routed = sorted({r["row"] for r in B if r["arm"] == "routed" and r["routing"] == "routed"})
    routed_count = {n: sum(1 for r in B if r["arm"] == "routed" and r["row"] == n and r["routing"] == "routed")
                    for n in row_numbers}
    baseline_routed = sum(1 for r in B if r["arm"] == "baseline" and r["routing"] == "routed")
    expected = [n for n in row_numbers if n in ELIGIBLE]
    off_set = [n for n in row_numbers if (routed_count[n] == trials) != (n in ELIGIBLE) or 0 < routed_count[n] < trials]
    smoke_pages = {}
    for t in range(1, trials + 1):
        pair = [r for r in B if r["row"] == SMOKE_AGENT_ROW and r["trial"] == t]
        if len(pair) == 2:
            smoke_pages[t] = pair[0]["pages"] == pair[1]["pages"]
    p3 = {"routed_rows": routed, "routed_count_by_row": routed_count, "baseline_routed": baseline_routed,
          "expected": expected, "rows_off_the_set": off_set, "row24_pages_identical_by_trial": smoke_pages,
          "verdict": ("HOLDS" if not off_set and baseline_routed == 0 and all(smoke_pages.values()) else "FALSIFIED")}

    judged = [dict(r, arm="openai" if r["arm"] == "baseline" else "bedrock") for r in B
              if r["row"] in ELIGIBLE and r.get("scores")]
    p4 = pe.p3(judged) if judged else {"verdict": "NOT TESTED"}

    gen = lambda arm, n, key: [r["generate"][key] for r in B if r["arm"] == arm and r["row"] == n]
    a_total = lambda n, key: statistics.mean(r["total"][key] for r in A if r["row"] == n)
    a_gen = lambda n, key: statistics.mean(r["components"]["generate"][key] for r in A if r["row"] == n)
    elig = [n for n in ELIGIBLE if n in row_numbers]
    measured = {}
    if elig and all(gen("routed", n, "usd_list") for n in elig):
        base_gen = statistics.mean(v for n in elig for v in gen("baseline", n, "usd_list"))
        cheap_gen = statistics.mean(v for n in elig for v in gen("routed", n, "usd_list"))
        measured["generation_per_routed_request"] = cheap_gen / base_gen - 1
        if A:
            base_req = statistics.mean(a_total(n, "usd_list") for n in elig)
            routed_req = statistics.mean(a_total(n, "usd_list") - a_gen(n, "usd_list")
                                         + statistics.mean(gen("routed", n, "usd_list")) for n in elig)
            measured["request_per_routed_request"] = routed_req / base_req - 1
            for key, name in (("usd_list", "across_28_list"), ("usd_billed", "across_28_billed")):
                base = sum(a_total(n, key) for n in row_numbers)
                saving = sum(a_gen(n, key) - statistics.mean(gen("routed", n, key)) for n in elig)
                measured[name] = -saving / base
        p5_items = {k: {"predicted": P5_PREDICTED[k], "measured": round(v, 4),
                        "ratio": round(v / P5_PREDICTED[k], 2),
                        "holds": 0.5 <= v / P5_PREDICTED[k] <= 2} for k, v in measured.items()}
        p5 = {"items": p5_items, "dollars": {
            "baseline_generation_per_routed_answer": round(base_gen, 6),
            "routed_generation_per_routed_answer": round(cheap_gen, 6),
            "pass_a_per_pass_list": round(sum(a_total(n, "usd_list") for n in row_numbers), 5) if A else None,
            "pass_a_per_pass_billed": round(sum(a_total(n, "usd_billed") for n in row_numbers), 5) if A else None},
            "verdict": "HOLDS" if all(i["holds"] for i in p5_items.values()) and len(p5_items) == 4 else
            ("FALSIFIED" if any(not i["holds"] for i in p5_items.values()) else "NOT TESTED")}
    else:
        p5 = {"verdict": "NOT TESTED", "reason": "no routed generation measured"}

    nova = [x for x in P6 if x["arm"] == "nova_micro"]
    p6 = {"trials": [{k: v for k, v in x.items() if k != "answer"} for x in P6],
          "exact": {arm: f"{sum(x['is_refusal'] for x in P6 if x['arm'] == arm)}/{sum(1 for x in P6 if x['arm'] == arm)}"
                    for arm in ("nova_micro", "gpt-4o-mini")},
          "contexts_identical": len({x["contexts_sha256"] for x in P6}) <= 1,
          "verdict": "NOT TESTED" if not nova else ("HOLDS" if all(x["is_refusal"] for x in nova) else "FALSIFIED")}

    pairs, identical = 0, 0
    for t in range(1, trials + 1):
        for n in row_numbers:
            if n in ELIGIBLE:
                continue
            arms = {r["arm"]: r["body_sha256"] for r in B if r["trial"] == t and r["row"] == n}
            if len(arms) == 2:
                pairs += 1
                identical += bool(arms["baseline"]) and arms["baseline"] == arms["routed"]
    p7 = {"pairs": pairs, "identical": identical, "expected": (len(row_numbers) - len(elig)) * trials,
          "verdict": "HOLDS" if pairs and identical == pairs == (len(row_numbers) - len(elig)) * trials else
          ("NOT TESTED" if not pairs else "FALSIFIED")}

    p8_cost = measured.get("across_28_list")
    p8 = {"rule": "routing on only if P4 and P6 hold and the cost delta across the 28 is at least 30%",
          "P4": p4.get("verdict"), "P6": p6["verdict"], "cost_delta_across_28_list": p8_cost,
          "routing_would_turn_on": (p4.get("verdict") == "HOLDS" and p6["verdict"] == "HOLDS"
                                    and p8_cost is not None and -p8_cost >= 0.30),
          "shipped": "accounting ON; routing OFF"}

    a_spend_billed = sum(r["total"]["usd_billed"] for r in A)
    b_spend_billed = sum(r["generate"]["usd_billed"] for r in B)  # plus Pass B's memoized decisions (below)
    return {
        "timestamp_utc": state["started_at"], "rows": row_numbers, "trials": trials,
        "full_design": row_numbers == list(range(1, len(rows) + 1)) and trials == TRIALS,
        "review_trigger": "switched off for the run (as in G11)",
        "bedrock": {"model": deps_model(state), "retries": state["bedrock_retries"],
                    "failures": state["bedrock_failures"]},
        "openai_generation_backends": state["openai_backends"],
        "judge_fingerprints": state.get("judge_fingerprints", {}),
        "memo": state["memo"],
        "P1": p1, "P2": p2, "P3": p3, "P4": p4, "P5": p5, "P6": p6, "P7b": p7, "P8": p8,
        "spend": {"pass_a_billed": round(a_spend_billed, 4), "pass_b_generation_billed": round(b_spend_billed, 4),
                  "pass_b_decisions_billed": round(state["pass_b_decisions_billed"], 4),
                  "judge": pe.Spend(state["judge_spend"]).cost(), "p6_billed": round(state["p6_billed"], 5)},
        "wall_s": state["wall_s"],
        "pass_a": [{k: v for k, v in r.items()} for r in A],
        "pass_b": [{k: v for k, v in r.items() if k not in ("answer", "contexts")} for r in B],
    }


def deps_model(state):
    return state.get("cheap_model")


def latency(raw_path: Path, out_path: Path, trials: int = TRIALS) -> dict:
    """Supplementary, not registered (eval/g8_design.md R4: "recorded, with no falsifier"). The run itself did not time
    Bedrock calls, so this times generation on the eligible rows' stored contexts from a run's raw file: Nova Micro
    through generate_with (the successful Converse call's client-side wall time, spacer excluded, plus Bedrock's
    latencyMs) and gpt-4o-mini through the frozen generator, interleaved, `trials` per row and arm."""
    import src.generate as frozen
    from agent import generation, routing

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    rows = pe._rows()
    contexts = {r["row"]: r["contexts"] for r in raw["pass_b"]
                if r["row"] in ELIGIBLE and r["arm"] == "baseline" and r["trial"] == 1}
    bedrock = pe.BedrockForEval()
    calls = []
    for trial in range(1, trials + 1):
        for n in ELIGIBLE:
            question = rows[n - 1]["question"]
            for arm in (("nova_micro", "gpt-4o-mini") if (n + trial) % 2 else ("gpt-4o-mini", "nova_micro")):
                if arm == "nova_micro":
                    generation.generate_with(bedrock, question, contexts[n], model_id=routing.CHEAP_MODEL)
                    calls.append({"trial": trial, "row": n, "arm": arm, "wall_s": round(bedrock.last_call_s, 3),
                                  "latency_ms": generation.calls()[-1]["latency_ms"]})
                else:
                    started = time.perf_counter()
                    frozen.generate(question, contexts[n])
                    calls.append({"trial": trial, "row": n, "arm": arm,
                                  "wall_s": round(time.perf_counter() - started, 3)})
    summary = {}
    for arm in ("nova_micro", "gpt-4o-mini"):
        walls = [c["wall_s"] for c in calls if c["arm"] == arm]
        summary[arm] = {"n": len(walls), "wall_s_p50": statistics.median(walls), "wall_s_range": [min(walls), max(walls)]}
    ms = [c["latency_ms"] for c in calls if c.get("latency_ms") is not None]
    summary["nova_micro"]["latency_ms_p50_server"] = statistics.median(ms) if ms else None
    result = {"timestamp_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "source_run": raw["started_at"],
              "registered": False, "rows": list(ELIGIBLE), "trials": trials, "summary": summary,
              "bedrock_retries": dict(bedrock.retries), "calls": calls}
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return result


def run(rows, row_numbers, trials) -> dict:
    import httpx

    import api.main as api_main
    import src.generate as frozen
    import src.retrieve as retrieve
    from agent import cost, generation, graph, review, routing

    state = {"started_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "row_numbers": row_numbers,
             "trials": trials, "pass_a": [], "pass_b": [], "p6": [], "judge_spend": {}, "wall_s": {},
             "cheap_model": routing.CHEAP_MODEL}
    if api_main.INPUT_GUARD_ENABLED or routing.ROUTING_ENABLED:
        raise SystemExit("the input guard and routing must both ship off")
    review.trigger_reason = lambda question, trace_notes: None
    bedrock = pe.BedrockForEval()
    generation.bedrock_client = lambda: bedrock
    frozen.reset_generation_backends()
    generation.reset_calls()

    # Pass A: a counting embedder around the real one; nothing memoized
    real_embedder = retrieve._embedder()
    counting = {"calls": 0}

    class CountingEmbedder:
        def embed_query(self, text):
            counting["calls"] += 1
            return real_embedder.embed_query(text)

    counter = CountingEmbedder()
    retrieve._embedder = lambda: counter
    deps = {"api_main": api_main, "graph": graph, "cost": cost, "routing": routing, "generation": generation,
            "frozen": frozen, "bedrock": bedrock, "counting": counting}
    t0 = time.perf_counter()
    pass_a(state, rows, row_numbers, trials, deps)
    state["wall_s"]["pass_a"] = round(time.perf_counter() - t0, 1)

    # Pass B: memoized embedding and decisions shared across arms; generation bodies hashed
    embedded = {}

    class EmbedMemo:
        def embed_query(self, text):
            if text not in embedded:
                embedded[text] = real_embedder.embed_query(text)
            return embedded[text]

    memo = EmbedMemo()
    retrieve._embedder = lambda: memo
    throwaway = pe.Spend()
    router, tools = pe.SharedMemo(graph._router_llm(), throwaway), pe.SharedMemo(graph._tool_llm(), throwaway)
    graph._router_llm, graph._tool_llm = (lambda: router), (lambda: tools)
    hashes = []
    real_send = httpx.Client.send

    def send(self, request, *args, **kwargs):
        if cost.current_phase() == "generate" and request.url.path.endswith("/chat/completions"):
            hashes.append(hashlib.sha256(request.content).hexdigest())
        return real_send(self, request, *args, **kwargs)

    httpx.Client.send = send
    deps["hashes"] = hashes
    t0 = time.perf_counter()
    pass_b(state, rows, row_numbers, trials, deps)
    state["wall_s"]["pass_b"] = round(time.perf_counter() - t0, 1)
    httpx.Client.send = real_send
    state["pass_b_decisions_billed"] = throwaway.cost()["total_usd_as_billed"]

    t0 = time.perf_counter()
    with cost.request("eval-p6") as p6_record:
        p6(state, rows, trials, deps)
    state["p6_billed"] = p6_record.total()["usd_billed"]
    state["wall_s"]["p6"] = round(time.perf_counter() - t0, 1)

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
    parser.add_argument("--latency-from", type=Path,
                        help="a raw file: time generation on its eligible rows' contexts (supplementary, unregistered)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    rows = pe._rows()
    if args.latency_from:
        latency(args.latency_from, REPO_ROOT / "eval" / "cost_latency.json")
        return
    if args.judge_from:
        state = json.loads(args.judge_from.read_text(encoding="utf-8"))
        raw_path = args.judge_from
    else:
        row_numbers = [int(x) for x in args.rows.split(",")] if args.rows else list(range(1, len(rows) + 1))
        full = row_numbers == list(range(1, len(rows) + 1)) and args.trials == TRIALS
        if not full and args.out.resolve() == METRICS_PATH.resolve():
            raise SystemExit("a partial run must not write eval/cost_metrics.json; pass --out")
        state = run(rows, row_numbers, args.trials)
        args.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = args.raw_dir / f"g8_raw_{state['started_at']}.json"

    def save():
        raw_path.write_text(json.dumps(state, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    save()
    t0 = time.perf_counter()
    judge(state, rows, save)
    state["wall_s"]["judge"] = round(state["wall_s"].get("judge", 0) + time.perf_counter() - t0, 1)
    save()
    metrics = derive(state, rows, state["row_numbers"], state["trials"])
    args.out.write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    summary = {k: metrics[k] for k in ("P1", "P2", "P3", "P5", "P6", "P7b", "P8", "bedrock", "memo", "spend",
                                       "wall_s")}
    summary["P4"] = {k: metrics["P4"].get(k) for k in ("verdict", "per_metric", "falsified_rows", "aggregate")}
    print(json.dumps(summary, indent=2, default=str))
    for item in metrics["P4"].get("items", []):
        print(f"P4 row {item['row']:>2} {item['metric'][:12]:<12} baseline {item.get('openai')} "
              f"routed {item.get('bedrock')} {item['verdict']}")
    print(f"raw (gitignored): {raw_path}\nmetrics: {args.out}")


if __name__ == "__main__":
    main()
