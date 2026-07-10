"""Enrich a raw run_eval.py output into a versioned metrics-history record + audit.

Reusable across optimization rounds (v3/v4/…). Reads a raw eval_<ts>.json (default: newest),
adds provenance (dual-model probe, git, namespace), a 3-way refusal-integrity audit, a per-row
correctness diff vs a chosen baseline, an optional prediction check, and per-category aggregates;
writes eval/results/<out_prefix>_<ts>.json (gitignored) and prints a report.

Dev tooling — NOT part of the src/ runtime. Run from the repo root, e.g.:
  uv run python scripts/enrich_eval.py --label v3 --out-prefix v3_prompt \
      --namespace semantic --baseline v2_semantic \
      --change-note "generation prompt only; chunking unchanged" \
      --predictions-json '[{"label":"Flowserve","match":"starting a Flowserve","expectation":"recover"}]'

Provenance note: the "pipeline ran clean" invariant is checked against PIPELINE PATHS only
(src/, eval/run_eval.py, eval/dataset.jsonl, pyproject.toml, uv.lock) — an untracked analysis
script or a docs edit does not violate it, so git_commit stays the commit the eval ran from.
"""

import argparse
import glob
import json
import math
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "eval" / "results"
METRICS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall", "answer_correctness"]
PIPELINE_PATHS = ["src", "eval/run_eval.py", "eval/dataset.jsonl", "pyproject.toml", "uv.lock"]

EXACT_REFUSAL = "the provided context does not contain the answer"
# Anchored refusal constructions — always count as a refusal wherever they appear.
ANCHORED_REFUSAL = [
    "does not contain the answer", "not in the provided context",
    "cannot be determined from the context", "not contained in the provided context",
    "context does not contain",
]
# Loose fragments — only count when the WHOLE response is short (real refusals are terse); avoids
# misclassifying substantive answers that happen to say e.g. "the standard does not specify a value
# below 19.5% oxygen" as refusals.
SHORT_FRAGMENTS = [
    "does not contain", "not in the context", "cannot determine", "cannot be determined",
    "not provided", "no information", "does not provide", "does not specify", "not available",
    "unable to answer", "not mentioned", "not found in",
]
SHORT_LEN = 150


def classify(resp: str) -> str:
    r = (resp or "").strip().lower()
    if not r:
        return "empty"
    if EXACT_REFUSAL in r:
        return "exact_refusal"
    if any(p in r for p in ANCHORED_REFUSAL):
        return "near_miss_refusal"
    if len(r) < SHORT_LEN and any(p in r for p in SHORT_FRAGMENTS):
        return "near_miss_refusal"
    return "attempt"


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True).stdout.strip()


def is_num(v):
    return isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))


def mean(xs):
    xs = [x for x in xs if is_num(x)]
    return sum(xs) / len(xs) if xs else None


def fmt(v):
    return f"{v:.4f}" if is_num(v) else str(v)


def probe(model, seed):
    from langchain_openai import ChatOpenAI
    kw = dict(model=model, temperature=0)
    if seed is not None:
        kw["seed"] = seed
    md = ChatOpenAI(**kw).invoke("ping").response_metadata or {}
    return {"resolved": md.get("model_name"), "system_fingerprint": md.get("system_fingerprint")}


def find_row(rows, sub):
    for q, r in rows.items():
        if sub.lower() in q.lower():
            return q, r
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-file", default=None, help="raw eval_*.json (default: newest)")
    ap.add_argument("--label", required=True, help="metrics-history row label, e.g. v3")
    ap.add_argument("--out-prefix", required=True, help="output file prefix, e.g. v3_prompt")
    ap.add_argument("--namespace", required=True, help="retrieval namespace the eval used")
    ap.add_argument("--baseline", default=None, help="baseline result glob prefix, e.g. v2_semantic")
    ap.add_argument("--change-note", default="")
    ap.add_argument("--gen-model", default="gpt-4o-mini")
    ap.add_argument("--gen-seed", type=int, default=42)
    ap.add_argument("--judge-model", default="gpt-4o-mini")
    ap.add_argument("--predictions-json", default="[]", help="JSON list of {label,match,expectation}")
    args = ap.parse_args()

    load_dotenv(REPO / ".env")
    eval_file = args.eval_file or max(glob.glob(str(RESULTS / "eval_*.json")), key=os.path.getmtime)
    raw = json.loads(Path(eval_file).read_text())
    per_row = raw["per_row"]
    rows_evaluated = len(per_row)
    assert rows_evaluated == raw["n_rows"], f"row mismatch {rows_evaluated}/{raw['n_rows']}"

    commit = git("rev-parse", "HEAD")
    pipeline_dirty = git("status", "--porcelain", "--", *PIPELINE_PATHS)
    assert not pipeline_dirty, f"PIPELINE files dirty (eval did not run from a clean pipeline):\n{pipeline_dirty}"
    tree_dirty = bool(git("status", "--porcelain"))

    from pinecone import Pinecone
    from src.config import get_settings
    settings = get_settings()
    st = Pinecone(api_key=settings.pinecone_api_key).Index(settings.index_name).describe_index_stats()
    ns_count = (st.get("namespaces", {}).get(args.namespace) or {}).get("vector_count", 0)

    gen = probe(args.gen_model, args.gen_seed)
    jud = probe(args.judge_model, None)

    aggregate = {m: mean([r.get(m) for r in per_row]) for m in METRICS}
    metric_health = {m: {"numeric": sum(1 for r in per_row if is_num(r.get(m))),
                         "nan_or_none": sum(1 for r in per_row if not is_num(r.get(m)))} for m in METRICS}

    ds = [json.loads(l) for l in (REPO / "eval" / "dataset.jsonl").read_text().splitlines() if l.strip()]
    q2cat = {d["question"]: d.get("category", "?") for d in ds}
    by_cat = defaultdict(lambda: defaultdict(list)); cat_counts = defaultdict(int)
    for r in per_row:
        cat = q2cat.get(r["user_input"], "UNMATCHED"); cat_counts[cat] += 1
        for m in METRICS:
            by_cat[cat][m].append(r.get(m))
    category_breakdown = {c: {m: mean(v) for m, v in mets.items()} for c, mets in by_cat.items()}

    base = None
    if args.baseline:
        bf = sorted(glob.glob(str(RESULTS / f"{args.baseline}_*.json")), key=os.path.getmtime)
        if bf:
            base = json.loads(Path(bf[-1]).read_text())
    b_agg = base["aggregate_scores"] if base else {}
    b_cb = base.get("category_breakdown", {}) if base else {}
    b_rows = {r["user_input"]: r for r in base["per_row"]} if base else {}
    v_rows = {r["user_input"]: r for r in per_row}
    delta = {m: (round(aggregate[m] - b_agg[m], 4) if is_num(aggregate.get(m)) and is_num(b_agg.get(m)) else None) for m in METRICS}

    # refusal-integrity audit (flips, quoting current response)
    audit = []
    for q, r in v_rows.items():
        c_now = classify(r.get("response"))
        c_base = classify(b_rows.get(q, {}).get("response")) if q in b_rows else None
        if c_base is not None and c_base != c_now:
            audit.append({"question": q, "cat": q2cat.get(q, "?"), "base_class": c_base, "class": c_now,
                          "ac_base": b_rows[q].get("answer_correctness"), "ac": r.get("answer_correctness"),
                          "faith_base": b_rows[q].get("faithfulness"), "faith": r.get("faithfulness"),
                          "response": (r.get("response") or "")[:800]})
    near_miss = [{"question": q, "response": (r.get("response") or "")[:400]}
                 for q, r in v_rows.items() if classify(r.get("response")) == "near_miss_refusal"]

    corr = []
    for q, r in v_rows.items():
        ab, an = b_rows.get(q, {}).get("answer_correctness"), r.get("answer_correctness")
        if is_num(ab) and is_num(an):
            corr.append((q, ab, an, round(an - ab, 4)))
    regressions = [c for c in corr if c[3] <= -0.15]
    gains = [c for c in corr if c[3] >= 0.15]

    preds = json.loads(args.predictions_json)
    pred_check = []
    for p in preds:
        q, r = find_row(v_rows, p["match"])
        if r is None:
            pred_check.append({"label": p["label"], "expectation": p["expectation"], "status": "UNMATCHED"})
            continue
        rb = b_rows.get(q, {})
        pred_check.append({"label": p["label"], "expectation": p["expectation"], "matched_question": q,
                           "class_base": classify(rb.get("response")), "class": classify(r.get("response")),
                           "ac_base": rb.get("answer_correctness"), "ac": r.get("answer_correctness"),
                           "faith_base": rb.get("faithfulness"), "faith": r.get("faithfulness"),
                           "response": (r.get("response") or "")[:800]})

    ts = raw["timestamp_utc"]
    iso = datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    record = {
        "metrics_history_row": args.label, "change": args.change_note,
        "namespace": args.namespace, "namespace_vector_count": ns_count, "top_k_configured": 5,
        "generation_model": {"alias": args.gen_model, "resolved": gen["resolved"], "seed": args.gen_seed, "system_fingerprint": gen["system_fingerprint"]},
        "judge_model": {"alias": args.judge_model, "resolved": jud["resolved"], "seed": None, "system_fingerprint": jud["system_fingerprint"]},
        "ragas_version": raw["ragas_version"], "git_commit": commit,
        "git_pipeline_clean": True, "git_tree_dirty_incl_tooling": tree_dirty,
        "rows_evaluated": rows_evaluated, "timestamp_iso": iso, "source_run_file": os.path.basename(eval_file),
        "note_retrieval_metrics": ("If retrieval is unchanged from baseline, context_precision/recall "
                                   "movement is EXPECTED to be RAGAS judge nondeterminism (judge unseeded) "
                                   "— verify the magnitude is small rather than assuming it."),
        "metrics": METRICS, "aggregate_scores": aggregate, "metric_health": metric_health,
        "category_breakdown": category_breakdown, "baseline": args.baseline, "baseline_aggregate": b_agg,
        "delta_vs_baseline": delta, "refusal_audit_flips": audit, "near_miss_refusals": near_miss,
        "correctness_regressions": regressions, "correctness_gains": gains,
        "prediction_check": pred_check, "per_row": per_row,
    }
    out = RESULTS / f"{args.out_prefix}_{ts}.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print(f"WROTE {out.name} | rows {rows_evaluated} | commit {commit[:12]} | pipeline_clean=True tree_dirty={tree_dirty} | ns {ns_count}")
    print(f"gen fp {gen['system_fingerprint']} | judge fp {jud['system_fingerprint']}")
    print(f"\n=== AGGREGATE {args.label} vs {args.baseline} ===")
    print(f"{'metric':20s} {args.label:>9s} {'base':>9s} {'delta':>9s}")
    for m in METRICS:
        print(f"{m:20s} {fmt(aggregate[m]):>9s} {fmt(b_agg.get(m)):>9s} {fmt(delta[m]):>9s}")
    print("\n=== BY CATEGORY faith / ans_corr (base -> now) ===")
    for c in ["fact", "procedure", "conditional", "narrative"]:
        if c in category_breakdown:
            print(f"{c:12s} n={cat_counts[c]}  faith {fmt(b_cb.get(c,{}).get('faithfulness'))}->{fmt(category_breakdown[c]['faithfulness'])}   ans_corr {fmt(b_cb.get(c,{}).get('answer_correctness'))}->{fmt(category_breakdown[c]['answer_correctness'])}")
    print(f"\n=== REFUSAL-INTEGRITY AUDIT: {len(audit)} flips ===")
    for a in audit:
        print(f"  [{a['cat']}] {a['question'][:62]}\n     {a['base_class']} -> {a['class']} | ac {fmt(a['ac_base'])}->{fmt(a['ac'])} faith {fmt(a['faith_base'])}->{fmt(a['faith'])}\n     resp: {a['response'][:260]!r}")
    print(f"\n=== NEAR-MISS REFUSALS (flagged separately): {len(near_miss)} ===")
    for nm in near_miss:
        print(f"  {nm['question'][:60]}: {nm['response'][:150]!r}")
    print(f"\n=== CORRECTNESS vs baseline: gains(>=+.15)={len(gains)} regressions(<=-.15)={len(regressions)} ===")
    for q, ab, an, d in sorted(corr, key=lambda x: x[3]):
        if abs(d) >= 0.15:
            print(f"  {'REGRESS' if d<0 else 'gain':8s} {fmt(ab)}->{fmt(an)} ({d:+.3f})  {q[:64]}")
    print("\n=== PREDICTION CHECK ===")
    for p in pred_check:
        if p.get("status") == "UNMATCHED":
            print(f"  [{p['expectation']:12s}] {p['label']:18s} UNMATCHED (no question matched)")
            continue
        print(f"  [{p['expectation']:12s}] {p['label']:18s} {p['class_base']}->{p['class']}  ac {fmt(p['ac_base'])}->{fmt(p['ac'])} faith {fmt(p['faith_base'])}->{fmt(p['faith'])}\n     resp: {p['response'][:260]!r}")
    print("\nmetric_health:", json.dumps(metric_health))


if __name__ == "__main__":
    main()
