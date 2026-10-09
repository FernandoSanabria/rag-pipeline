"""G9b smoke evaluation: 8 rows of the frozen dataset on every PR, gated on retrieval; faithfulness reported.

The design is eval/g9_design.md; the registered predictions are eval/g9_PREDICTION.md (G9, then G9b). Rows are 1-based.
G9 gated T2 hard and was falsified (P1: 2 of 5 runs red, generation variance on row 24), so the registered demotion
rule fired; G9b keeps T1 as the only hard tier.

Tiers, against the committed eval/smoke_snapshot.json:
- T1, hard: each row's (source_doc_id, page) set of document chunks equals the snapshot's. On the refusal row the
  served answer must also still be the whole refusal.
- T2, reported: faithfulness < snapshot - 0.2 is a "below floor" warning, never red. A judge NaN is retried once;
  still NaN -> NOT SCORED. An empty served answer scores 0.0 without a judge call: there is nothing in it to support.
- T3, reported: answer correctness, as a delta against the snapshot.

Breach rows (a T1 failure or a T2 floor breach) have their answer text, and only that, encrypted to the OpenPGP key
at eval/smoke_pubkey.asc; the ciphertext goes in the artifact. Without gpg nothing is written, never plaintext. The
generation call's openai-organization / openai-project response headers are logged (a diagnostic; not credentials).

What runs: each row goes through api.main._answer, the one wiring point of /ask and /ask/agent, so the judged answer is
the served answer (after the output guard). The judge is eval/run_eval.py's: gpt-4o-mini at temperature 0,
text-embedding-3-small, and its faithfulness and answer_correctness metric objects, one sample at a time. Judged scores
are cached by content in .smoke-cache/judge.json, merged and never replaced, so an unchanged row replays its own score.

The repository is public, so everything this writes (log, step summary, the .smoke-out/ artifact, the snapshot) is
derived: page sets, scores, hashes and counts, plus the breach ciphertext. Never question, answer or context text.

Modes:
  uv run python scripts/smoke_eval.py                        gate against the snapshot: exit 0 green, 1 red
  uv run python scripts/smoke_eval.py --baseline             write .smoke-out/smoke_snapshot.json; no gate
  uv run python scripts/smoke_eval.py --simulate-regression  the negative proof: row 1 at k=2 (red), row 4 unfaithful
  uv run python scripts/smoke_eval.py --rows 24              DIAGNOSTIC: gate a subset of the 8 (outside the window)
  python3 scripts/smoke_eval.py gate --event ...             the workflow's gate job (stdlib only, reads no secret)
Exit 2: a cap breached (8 rows, 10 generation calls, 120 judge calls) or a configuration error.
"""

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = REPO_ROOT / "eval" / "dataset.jsonl"
SMOKE_SET_PATH = REPO_ROOT / "eval" / "smoke_set.json"
SNAPSHOT_PATH = REPO_ROOT / "eval" / "smoke_snapshot.json"
CACHE_PATH = REPO_ROOT / ".smoke-cache" / "judge.json"
OUT_DIR = REPO_ROOT / ".smoke-out"
PUBKEY_PATH = REPO_ROOT / "eval" / "smoke_pubkey.asc"
# The committed key's primary fingerprint (tests/test_smoke_eval.py checks the file against it). The private key is held
# off-repo by the maintainer; see eval/g9_design.md.
PUBKEY_FINGERPRINT = "0A23108E5E612C30A84874FC1A47B75AB89F83AE"

DELTA_F = 0.2
HARD_TIERS = ("T1",)  # G9b: T2 and T3 are reported (G9's P1 was falsified on T2)
GENERATION_HEADERS = ("openai-organization", "openai-project")
CAPS = {"rows": 8, "generation_calls": 10, "judge_calls": 120}
JUDGE_MODEL = "gpt-4o-mini"  # eval/run_eval.py's judge; tests/test_smoke_eval.py pins the match
EMBED_MODEL = "text-embedding-3-small"
METRICS = ("faithfulness", "answer_correctness")
ADVISORY_TOP_K = 11  # refinement B: the top-11 scores are advisory, for P3's near-tie attribution only
PRICE_PER_M = {"input": 0.15, "cached_input": 0.075, "output": 0.60, "embedding": 0.02}  # eval/COST_LEDGER.md

# The negative proof (R6). Dispatch-only; nothing here touches a production code path.
SIM_T1_ROW, SIM_T1_K = 1, 2
SIM_T2_ROW = 4
# Deliberately unfaithful: neither figure nor the rating is in the retrieved context. It is judged, never served.
SIM_UNFAITHFUL_ANSWER = (
    "The Fisher 667 actuator size 30/30i has a maximum diaphragm casing pressure of 9,999 psig, and the manual rates "
    "it for continuous submerged service at a depth of 300 meters."
)

# The gate job's paths filter (C1 note 4: a job, not on.paths, so a required check always reports).
GATED_PREFIXES = ("src/", "agent/", "api/")
GATED_GLOBS = ("eval/smoke_*",)
GATED_FILES = ("scripts/smoke_eval.py", "eval/run_eval.py", "pyproject.toml", "uv.lock",
               ".github/workflows/eval-smoke.yml")
CODE_PREFIXES = ("src/", "agent/", "api/")
SNAPSHOT_REL = "eval/smoke_snapshot.json"


class CapExceeded(Exception):
    """A hard cap was breached; the run aborts with exit 2."""


class ConfigError(Exception):
    """The smoke set, dataset, snapshot or flags are inconsistent; the run aborts with exit 2."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_nan(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


# --- the smoke set -------------------------------------------------------------------------------------------------


def load_smoke_set(smoke_path: Path = SMOKE_SET_PATH, dataset_path: Path = DATASET_PATH) -> list[dict]:
    """The smoke rows, joined to their dataset question and reference; each row's text is pinned by sha256."""
    spec = json.loads(smoke_path.read_text(encoding="utf-8"))["rows"]
    if len(spec) > CAPS["rows"]:
        raise CapExceeded(f"rows: {len(spec)} > cap {CAPS['rows']}")
    dataset = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    for entry in spec:
        n = entry["row"]
        if not 1 <= n <= len(dataset):
            raise ConfigError(f"row {n}: not in eval/dataset.jsonl ({len(dataset)} rows)")
        item = dataset[n - 1]
        if sha256_text(item["question"]) != entry["question_sha256"]:
            raise ConfigError(f"row {n}: question hash mismatch; eval/dataset.jsonl is frozen, was it renumbered?")
        if entry["endpoint"] not in ("/ask", "/ask/agent"):
            raise ConfigError(f"row {n}: unknown endpoint {entry['endpoint']!r}")
        rows.append({**entry, "question": item["question"], "reference": item["reference"]})
    return rows


# --- T1 / T2 / T3 --------------------------------------------------------------------------------------------------


def _page(value):
    """Pinecone returns numeric metadata as floats; 3.0 -> 3."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def page_set(chunks: list[dict]) -> list[list]:
    """The sorted (source_doc_id, page) set of document chunks. Synthetic tool chunks (page None) are excluded."""
    pairs = {(c.get("source_doc_id"), _page(c.get("page"))) for c in chunks if c.get("page") is not None}
    return [list(p) for p in sorted(pairs, key=_pair_order)]


def _pair_order(pair) -> tuple:
    doc, page = pair
    return (str(doc), (0, page, "") if isinstance(page, (int, float)) else (1, 0, str(page)))


def t1_failures(row: dict, now: dict, snap: dict) -> list[dict]:
    """Set equality on (source_doc_id, page), order-insensitive; plus the refusal identity on the refusal row."""
    n = row["row"]
    out = []
    a = {tuple(p) for p in now["pages"]}
    b = {tuple(p) for p in snap["pages"]}
    if a != b:
        entered = [list(p) for p in sorted(a - b, key=_pair_order)]
        left = [list(p) for p in sorted(b - a, key=_pair_order)]
        out.append({"tier": "T1", "row": n, "kind": "retrieval_set",
                    "detail": f"retrieval set changed: entered {entered}, left {left}"})
    if row.get("refusal") and not now["is_refusal"]:
        out.append({"tier": "T1", "row": n, "kind": "refusal_identity",
                    "detail": "the refusal row no longer serves the whole refusal"})
    return out


def t2_status(now_f, snap_f, delta: float = DELTA_F) -> str:
    """'below_floor' iff faithfulness < snapshot - delta; 'not_scored' if either side has no score; else 'pass'.
    Under G9b a breach is reported, never red (HARD_TIERS)."""
    if now_f is None or snap_f is None:
        return "not_scored"
    return "below_floor" if now_f < snap_f - delta else "pass"


# --- the judge cache (refinement A) --------------------------------------------------------------------------------


def cache_key(metric: str, question: str, answer: str, contexts: list[str], reference: str,
              ragas_version: str) -> str:
    """sha256 of [metric, judge model, embedding model, ragas version, question, answer, contexts, reference]."""
    payload = [metric, JUDGE_MODEL, EMBED_MODEL, ragas_version, question, answer, list(contexts), reference]
    return sha256_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


class JudgeCache:
    """Content-keyed judged scores, merged and never replaced.

    At save, the file on disk (whatever is there now), the entries read at load and this run's new entries are
    unioned; an entry already present wins. NaN is never stored. Only hashes and floats are kept."""

    def __init__(self, path: Path = CACHE_PATH):
        self.path = path
        self.entries = self._read(path)
        self.loaded = len(self.entries)
        self.new: dict[str, float] = {}

    @staticmethod
    def _read(path: Path) -> dict[str, float]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        entries = data.get("entries", {}) if isinstance(data, dict) else {}
        return {k: float(v) for k, v in entries.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}

    def get(self, key: str):
        return self.entries.get(key)

    def put(self, key: str, value) -> None:
        if _is_nan(value) or not math.isfinite(value) or key in self.entries:
            return
        self.entries[key] = value
        self.new[key] = value

    def save(self) -> int:
        merged = self._read(self.path)
        for key, value in self.entries.items():
            merged.setdefault(key, value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"schema": 1, "entries": merged}, sort_keys=True, indent=0) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        return len(merged)


def judge_row(row: dict, answer: str, contexts: list[str], cache: JudgeCache, score_fn, ragas_version: str) -> dict:
    """Faithfulness and answer correctness for one row: from the cache, else judged. A NaN is retried once, then NOT
    SCORED. An empty answer scores 0.0 on both without a judge call. Returns {metric: (value, source)}."""
    if not answer.strip():
        return {m: (0.0, "empty_answer") for m in METRICS}
    keys = {m: cache_key(m, row["question"], answer, contexts, row["reference"], ragas_version) for m in METRICS}
    out = {}
    need = []
    for m in METRICS:
        value = cache.get(keys[m])
        if value is None:
            need.append(m)
        else:
            out[m] = (value, "cache")
    for source in ("judge", "judge_retry"):
        if not need:
            break
        scores = score_fn(row["question"], answer, contexts, row["reference"], tuple(need))
        still = []
        for m in need:
            value = scores.get(m)
            if _is_nan(value):
                still.append(m)
            else:
                value = round(float(value), 4)
                out[m] = (value, source)
                cache.put(keys[m], value)
        need = still
    for m in need:
        out[m] = (None, "not_scored")
    return out


# --- caps ----------------------------------------------------------------------------------------------------------


class Budget:
    """Hard caps, checked after every pipeline call and every judged row. A breach raises CapExceeded (exit 2)."""

    def __init__(self, caps: dict = CAPS):
        self.caps = dict(caps)
        self.counts = {"generation_calls": 0, "judge_calls": 0}

    def check(self, **counts) -> None:
        self.counts.update(counts)
        for name, n in self.counts.items():
            if n > self.caps[name]:
                raise CapExceeded(f"{name}: {n} > cap {self.caps[name]}")


# --- one run -------------------------------------------------------------------------------------------------------


def run_rows(rows: list[dict], deps: dict, cache: JudgeCache, budget: Budget, ragas_version: str,
             simulate: bool = False) -> tuple[list[dict], dict[int, str]]:
    """Answer, then judge, every row. Returns the derived results and, separately, each row's served answer text, which
    stays in memory (it is only ever encrypted, on a breach). `deps` holds the bindings, live or fake:
    answer(row, k) -> {answer, contexts, chunks, route, source_doc_id}; score(question, answer, contexts, reference,
    metrics) -> {metric: float|NaN}; advisory(row, result) -> top-11 list|None; is_refusal(answer) -> bool;
    generation_calls() / judge_calls() -> int; judge_cost() -> float (cumulative, for the per-row judge spend)."""
    results, answers = [], {}
    for row in rows:
        n = row["row"]
        k = SIM_T1_K if simulate and n == SIM_T1_ROW else None
        res = deps["answer"](row, k)
        budget.check(generation_calls=deps["generation_calls"]())
        canned = simulate and n == SIM_T2_ROW
        answer = SIM_UNFAITHFUL_ANSWER if canned else res["answer"]
        answers[n] = answer
        cost_before = deps["judge_cost"]()
        scores = judge_row(row, answer, res["contexts"], cache, deps["score"], ragas_version)
        budget.check(judge_calls=deps["judge_calls"]())
        try:
            top = deps["advisory"](row, res)
        except Exception as exc:  # advisory only: a failed score query never fails the run
            top = None
            print(f"::warning::row {n}: advisory top-{ADVISORY_TOP_K} query failed ({type(exc).__name__})")
        results.append({
            "row": n,
            "endpoint": row["endpoint"],
            "route": res.get("route"),
            "pages": page_set(res["chunks"]),
            "tool_chunks": sum(1 for c in res["chunks"] if c.get("page") is None),
            "is_refusal": bool(deps["is_refusal"](answer)),
            "answer_sha256": sha256_text(answer),
            "answer_chars": len(answer),
            "contexts_sha256": sha256_text(json.dumps(res["contexts"], ensure_ascii=False)),
            "faithfulness": scores["faithfulness"][0],
            "answer_correctness": scores["answer_correctness"][0],
            "sources": {m: scores[m][1] for m in METRICS},
            "judge_cost_usd": round(deps["judge_cost"]() - cost_before, 6),
            "top11": top,
            "simulated": f"T1: k={k}" if k else "T2: canned unfaithful answer" if canned else None,
        })
    return results, answers


def compare(rows: list[dict], results: list[dict], snapshot: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Per-row verdicts against the snapshot, the hard-tier failures (T1) and the reported breaches (T2)."""
    per_row, failures, reported = [], [], []
    for row, now in zip(rows, results):
        snap = snapshot["rows"].get(str(row["row"]))
        if snap is None:
            raise ConfigError(f"row {row['row']}: not in the snapshot; re-baseline deliberately")
        t1 = t1_failures(row, now, snap)
        t2 = t2_status(now["faithfulness"], snap["faithfulness"])
        failures += t1
        if t2 == "below_floor":
            breach = {"tier": "T2", "row": row["row"], "kind": "faithfulness_floor",
                      "detail": f"faithfulness {now['faithfulness']} < snapshot {snap['faithfulness']} - {DELTA_F}"}
            (failures if "T2" in HARD_TIERS else reported).append(breach)
        c_now, c_snap = now["answer_correctness"], snap["answer_correctness"]
        per_row.append({
            **now,
            "t1": "red" if t1 else "pass",
            "t2": t2,
            "snapshot_faithfulness": snap["faithfulness"],
            "answer_changed": now["answer_sha256"] != snap["answer_sha256"],
            "t3_correctness_delta": None if c_now is None or c_snap is None else round(c_now - c_snap, 4),
            "snapshot_top11": snap.get("top11"),
        })
    return per_row, failures, reported


def negative_proof(failures: list[dict], reported: list[dict]) -> dict:
    """G9b's P2b: the injected T1 regression (row 1) must go red; T2's reported line on row 4 is recorded."""
    t1 = any(f["tier"] == "T1" and f["row"] == SIM_T1_ROW and f["kind"] == "retrieval_set" for f in failures)
    t2 = any(f["tier"] == "T2" and f["row"] == SIM_T2_ROW for f in reported)
    return {"t1_row1": t1, "t2_row4_reported": t2, "held": t1}


def encrypt_breaches(per_row: list[dict], failures: list[dict], reported: list[dict], answers: dict[int, str],
                     encrypt) -> tuple[list[dict], dict[int, str]]:
    """Encrypt the answer text, and only that, of every row with a T1 failure or a T2 floor breach.
    Returns the derived breach records and the ciphertexts by row. A failed encryption writes nothing, never plaintext."""
    tiers: dict[int, list[str]] = {}
    for b in failures + reported:
        tiers.setdefault(b["row"], [])
        if b["tier"] not in tiers[b["row"]]:
            tiers[b["row"]].append(b["tier"])
    sha = {r["row"]: r["answer_sha256"] for r in per_row}
    records, ciphertexts = [], {}
    for n in sorted(tiers):
        ciphertext, error = encrypt(answers[n])
        if ciphertext is not None and answers[n] and answers[n] in ciphertext:
            ciphertext, error = None, "encryption output contained the plaintext; discarded"
        if ciphertext is not None:
            ciphertexts[n] = ciphertext
        else:
            print(f"::warning::row {n}: breach answer not encrypted ({error}); nothing written")
        records.append({"row": n, "tiers": tiers[n], "answer_sha256": sha[n],
                        "ciphertext": f"breaches/row-{n}.asc" if ciphertext is not None else None,
                        "error": error, "key_fingerprint": PUBKEY_FINGERPRINT})
    return records, ciphertexts


def snapshot_from(results: list[dict], provenance: dict) -> dict:
    keep = ("endpoint", "route", "pages", "top11", "tool_chunks", "is_refusal", "faithfulness", "answer_correctness",
            "answer_sha256", "contexts_sha256")
    run_ref = provenance.get("run_number") or provenance.get("run_id") or "local"
    return {
        "schema": 1,
        "justified_by": f"eval/METRICS_HISTORY.md, G9 block: the baseline (eval-smoke run #{run_ref})",
        "delta_f": DELTA_F,
        "run": provenance,
        "rows": {str(r["row"]): {k: r[k] for k in keep} for r in results},
    }


def execute(mode: str, rows: list[dict], snapshot: dict | None, deps: dict, cache: JudgeCache,
            ragas_version: str) -> tuple[int, dict]:
    """One smoke run with the given bindings. mode: 'gate' | 'baseline' | 'simulate'. Returns (exit code, report)."""
    budget = Budget()
    report = {"mode": mode, "delta_f": DELTA_F, "caps": CAPS}
    started = time.perf_counter()
    try:
        if mode != "baseline" and snapshot is None:
            raise ConfigError("no snapshot; run a baseline first")
        results, answers = run_rows(rows, deps, cache, budget, ragas_version, simulate=(mode == "simulate"))
    except (CapExceeded, ConfigError) as exc:
        label = "cap breached" if isinstance(exc, CapExceeded) else "configuration error"
        print(f"::error::{label}: {exc}")
        report.update(aborted=f"{label}: {exc}", counts=budget.counts)
        return 2, report
    finally:
        report["wall_s"] = round(time.perf_counter() - started, 1)
    report["counts"] = budget.counts
    hits = sum(r["sources"]["faithfulness"] == "cache" for r in results)
    judged = [r["judge_cost_usd"] for r in results if r["sources"]["faithfulness"] in ("judge", "judge_retry")]
    report["cache"] = {"loaded": cache.loaded, "new": len(cache.new), "faithfulness_hits": hits,
                       "correctness_hits": sum(r["sources"]["answer_correctness"] == "cache" for r in results),
                       # P4's dollar saving: each hit avoided about one judged row's cost (the mean over this run's misses)
                       "saving_usd_estimate": round(hits * sum(judged) / len(judged), 6) if judged else None}
    not_scored = [r["row"] for r in results if r["faithfulness"] is None]
    for n in not_scored:
        print(f"::warning::row {n}: faithfulness NOT SCORED (judge NaN twice); reported, not red")
    if mode == "baseline":
        report["per_row"] = results
        if not_scored:
            print(f"::error::baseline incomplete: rows {not_scored} have no faithfulness score; re-run the baseline")
            return 2, report
        return 0, report
    try:
        per_row, failures, reported = compare(rows, results, snapshot)
    except ConfigError as exc:
        print(f"::error::configuration error: {exc}")
        report.update(aborted=f"configuration error: {exc}", per_row=results)
        return 2, report
    breaches, ciphertexts = encrypt_breaches(per_row, failures, reported, answers, deps["encrypt"])
    report.update(per_row=per_row, failures=failures, reported=reported, breaches=breaches,
                  _ciphertexts=ciphertexts)  # written to breaches/ by write_outputs, never into report.json
    prefix = "SIMULATED — " if mode == "simulate" else ""
    for f in failures:
        print(f"::error::{prefix}{f['tier']} row {f['row']}: {f['detail']}")
    for f in reported:
        print(f"::warning::{prefix}{f['tier']} row {f['row']} (reported, not red): {f['detail']}")
    if mode == "simulate":
        proof = negative_proof(failures, reported)
        report["negative_proof"] = proof
        if proof["held"]:
            print(f"::error::SIMULATED REGRESSION (negative proof): T1 row {SIM_T1_ROW} went red as required; T2 row "
                  f"{SIM_T2_ROW}'s reported line present: {proof['t2_row4_reported']}; failing the job intentionally")
        else:
            print(f"::error::SIMULATED — the negative proof did NOT hold: {proof}")
        return 1, report
    return (1 if failures else 0), report


# --- reporting (derived values only) -------------------------------------------------------------------------------


def _fmt(value) -> str:
    return "—" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


def summary_markdown(report: dict) -> str:
    label = report["mode"] + (f", DIAGNOSTIC rows={report['rows_filter']}" if report.get("diagnostic") else "")
    lines = [f"### eval-smoke ({label})", ""]
    if "aborted" in report:
        lines.append(f"**Aborted (exit 2):** {report['aborted']}")
        return "\n".join(lines) + "\n"
    rows = report.get("per_row", [])
    if report["mode"] == "baseline":
        lines += ["| row | endpoint | pages | refusal | faithfulness | correctness | judged by |", "|--:|---|--:|---|--:|--:|---|"]
        for r in rows:
            lines.append(f"| {r['row']} | {r['endpoint']} | {len(r['pages'])} | {r['is_refusal']} | "
                         f"{_fmt(r['faithfulness'])} | {_fmt(r['answer_correctness'])} | {r['sources']['faithfulness']} |")
    else:
        lines += ["| row | endpoint | T1 | T2 | faithfulness (snapshot → now) | T3 correctness Δ | answer changed | "
                  "faithfulness from |", "|--:|---|---|---|---|--:|---|---|"]
        for r in rows:
            t2 = "below floor (reported)" if r["t2"] == "below_floor" else r["t2"]
            lines.append(f"| {r['row']} | {r['endpoint']} | {r['t1']} | {t2} | "
                         f"{_fmt(r['snapshot_faithfulness'])} → {_fmt(r['faithfulness'])} | "
                         f"{_fmt(r['t3_correctness_delta'])} | {r['answer_changed']} | {r['sources']['faithfulness']} |")
        failures = report.get("failures", [])
        lines += ["", f"**Hard-tier (T1) failures: {len(failures)}**"]
        lines += [f"- {f['tier']} row {f['row']}: {f['detail']}" for f in failures]
        reported = report.get("reported", [])
        lines += ["", f"**Reported, not red (T2 floor): {len(reported)}**"]
        lines += [f"- {f['tier']} row {f['row']}: {f['detail']}" for f in reported]
        for b in report.get("breaches", []):
            lines.append(f"- breach row {b['row']} ({', '.join(b['tiers'])}): answer "
                         f"{'encrypted to ' + b['ciphertext'] if b['ciphertext'] else 'NOT encrypted: ' + str(b['error'])}")
    cache = report.get("cache", {})
    spend = report.get("spend", {})
    lines += ["", f"Cache: {cache.get('faithfulness_hits')} faithfulness hits of {len(rows)} rows "
                  f"({cache.get('loaded')} entries restored, {cache.get('new')} new).",
              f"Calls: {report.get('counts')}; caps {CAPS}.",
              f"Spend (derived): ${spend.get('cost_usd')}; runner wall time {report.get('wall_s')} s."]
    if "negative_proof" in report:
        lines.append(f"Negative proof: {report['negative_proof']}")
    if report.get("openai_headers") is not None:
        lines.append(f"Generation call headers: {report['openai_headers']}")
    return "\n".join(lines) + "\n"


def write_outputs(report: dict, snapshot: dict | None = None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ciphertexts = report.pop("_ciphertexts", {})
    if ciphertexts:
        (OUT_DIR / "breaches").mkdir(exist_ok=True)
        for n, text in ciphertexts.items():
            (OUT_DIR / "breaches" / f"row-{n}.asc").write_text(text, encoding="utf-8")
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if snapshot is not None:
        (OUT_DIR / "smoke_snapshot.json").write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
                                                     encoding="utf-8")
    text = summary_markdown(report)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)


# --- live bindings -------------------------------------------------------------------------------------------------


def _chat_cost(prompt: int, cached: int, completion: int) -> float:
    return ((prompt - cached) * PRICE_PER_M["input"] + cached * PRICE_PER_M["cached_input"]
            + completion * PRICE_PER_M["output"]) / 1e6


def gpg_encrypt(text: str, pubkey: Path = PUBKEY_PATH) -> tuple[str | None, str | None]:
    """ASCII-armored OpenPGP ciphertext of `text` for the committed public key, or (None, reason). A throwaway gpg home
    is used, so no keyring is read or written; no private key is involved."""
    import shutil
    import tempfile

    gpg = shutil.which("gpg")
    if gpg is None:
        return None, "gpg not found"
    if not pubkey.exists():
        return None, f"{pubkey.name} not found"
    with tempfile.TemporaryDirectory() as home:
        try:
            out = subprocess.run([gpg, "--homedir", home, "--batch", "--yes", "--quiet", "--no-tty", "--trust-model",
                                  "always", "--armor", "--recipient-file", str(pubkey), "--encrypt"],
                                 input=text.encode("utf-8"), capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"gpg failed: {type(exc).__name__}"
    if out.returncode != 0 or b"BEGIN PGP MESSAGE" not in out.stdout:
        return None, f"gpg exit {out.returncode}"
    return out.stdout.decode("ascii"), None


def generation_headers(path: str, body: bytes | None, headers) -> dict | None:
    """The account-identifying response headers of a generation call, else None. The generation call is the only chat
    completion this pipeline sends with a `seed` (src/generate.py); the router, tool and guard calls send none."""
    if not path.endswith("/chat/completions") or b'"seed"' not in (body or b""):
        return None
    return {h: headers.get(h) for h in GENERATION_HEADERS if headers.get(h) is not None}


def install_generation_header_probe(store: list) -> None:
    """Record generation calls' account headers (in this process only). Requests and responses pass through unchanged."""
    import httpx

    original = httpx.Client.send

    def send(self, request, *args, **kwargs):
        response = original(self, request, *args, **kwargs)
        try:
            found = generation_headers(request.url.path, request.content, response.headers)
        except Exception:  # a diagnostic must never fail a call
            found = None
        if found is not None:
            store.append(found)
        return response

    httpx.Client.send = send


def live_deps(cb) -> tuple[dict, dict]:
    """The real bindings: api.main._answer over src.pipeline.ask / agent.graph.ask, RAGAS with run_eval.py's judge,
    and a direct Pinecone query for the advisory top-11. `cb` is the run's get_openai_callback handler."""
    import tiktoken
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.embeddings import Embeddings
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.dataset_schema import SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import answer_correctness, faithfulness

    import src.pipeline as pipeline
    import src.retrieve as retrieve
    from api.confidence import is_refusal
    from api.main import _answer
    from src.config import get_settings
    from src.generate import generation_backends

    encoding = tiktoken.get_encoding("cl100k_base")
    stats = {"judge_calls": 0, "judge_fingerprints": {}, "retrieval_embed_tokens": 0, "judge_embed_tokens": 0,
             "judge_chat": [0, 0, 0], "generation_headers": []}
    install_generation_header_probe(stats["generation_headers"])

    class JudgeCounter(BaseCallbackHandler):
        # Only on_llm_start: LangChain falls back to it for chat models, so each call is counted once.
        def on_llm_start(self, serialized, prompts, **kwargs):
            stats["judge_calls"] += 1

        def on_llm_end(self, response, **kwargs):
            out = response.llm_output or {}
            fp = f"{out.get('model_name')}|{out.get('system_fingerprint')}"
            stats["judge_fingerprints"][fp] = stats["judge_fingerprints"].get(fp, 0) + 1

    class CountingEmbeddings(Embeddings):
        """Delegates to the real embedder and counts tokens; the vectors are the real ones."""

        def __init__(self, real):
            self.real = real

        def _count(self, texts):
            stats["judge_embed_tokens"] += sum(len(encoding.encode(t)) for t in texts)

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

    class RecordingEmbedder:
        """Wraps the retrieval embedder: counts tokens and keeps each query's vector for the advisory query."""

        def __init__(self, real):
            self.real, self.vectors = real, {}

        def embed_query(self, text):
            vector = self.real.embed_query(text)
            stats["retrieval_embed_tokens"] += len(encoding.encode(text))
            self.vectors[text] = vector
            return vector

    recorder = RecordingEmbedder(retrieve._embedder())
    retrieve._embedder = lambda: recorder

    judge_llm = LangchainLLMWrapper(ChatOpenAI(model=JUDGE_MODEL, temperature=0, callbacks=[JudgeCounter()]))
    judge_emb = LangchainEmbeddingsWrapper(CountingEmbeddings(OpenAIEmbeddings(model=EMBED_MODEL)))
    metric_objects = {"faithfulness": faithfulness, "answer_correctness": answer_correctness}

    def answer(row, k=None):
        if row["endpoint"] == "/ask/agent":
            if k is not None:
                raise ConfigError(f"row {row['row']}: a k override is only wired for /ask rows")
            from agent.graph import ask as agent_ask
            fn = agent_ask
        else:
            fn = pipeline.ask
        if k is None:
            fields, result = _answer(row["question"], fn)
        else:  # the negative proof: this one call retrieves at k (src/ untouched; the settings object is copied)
            patched = pipeline.get_settings().model_copy(update={"retrieval_k": k})
            original = pipeline.get_settings
            pipeline.get_settings = lambda: patched
            try:
                fields, result = _answer(row["question"], fn)
            finally:
                pipeline.get_settings = original
        result = result or {}
        return {"answer": fields["answer"], "contexts": result.get("contexts", []), "chunks": result.get("chunks", []),
                "route": result.get("route"), "source_doc_id": result.get("source_doc_id") or None}

    def score(question, answer_text, contexts, reference, metrics):
        before = (cb.prompt_tokens, cb.prompt_tokens_cached, cb.completion_tokens)
        sample = SingleTurnSample(user_input=question, response=answer_text, retrieved_contexts=contexts,
                                  reference=reference)
        result = evaluate(dataset=EvaluationDataset(samples=[sample]), metrics=[metric_objects[m] for m in metrics],
                          llm=judge_llm, embeddings=judge_emb, raise_exceptions=False, show_progress=False)
        after = (cb.prompt_tokens, cb.prompt_tokens_cached, cb.completion_tokens)
        for i, (a, b) in enumerate(zip(after, before)):
            stats["judge_chat"][i] += a - b
        frame = result.to_pandas().iloc[0]
        return {m: (None if _is_nan(frame.get(m)) else float(frame.get(m))) for m in metrics}

    def advisory(row, res):
        vector = recorder.vectors.get(row["question"])
        if vector is None:
            return None
        kwargs = dict(vector=vector, top_k=ADVISORY_TOP_K, namespace=get_settings().retrieval_namespace,
                      include_metadata=True)
        if res.get("route") == "source_scoped" and res.get("source_doc_id"):
            kwargs["filter"] = {"source_doc_id": {"$eq": res["source_doc_id"]}}
        matches = retrieve._index().query(**kwargs)["matches"]
        return [{"source_doc_id": (m.get("metadata") or {}).get("source_doc_id"),
                 "page": _page((m.get("metadata") or {}).get("page")), "score": round(float(m["score"]), 6)}
                for m in matches]

    def judge_cost():
        p, c, o = stats["judge_chat"]
        return _chat_cost(p, c, o) + stats["judge_embed_tokens"] * PRICE_PER_M["embedding"] / 1e6

    deps = {
        "answer": answer,
        "score": score,
        "advisory": advisory,
        "is_refusal": is_refusal,
        "generation_calls": lambda: sum(b["n_calls"] for b in generation_backends()),
        "judge_calls": lambda: stats["judge_calls"],
        "judge_cost": judge_cost,
        "encrypt": gpg_encrypt,
    }
    return deps, stats


def provenance(stats: dict | None = None) -> dict:
    from src.config import get_settings
    from src.generate import generation_backends

    def git_head():
        try:
            return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    import ragas

    settings = get_settings()
    return {
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "event": os.environ.get("GITHUB_EVENT_NAME"),
        "ref": os.environ.get("GITHUB_REF"),
        "commit": os.environ.get("GITHUB_SHA") or git_head(),
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ragas_version": ragas.__version__,
        "judge_model": JUDGE_MODEL,
        "embedding_model": EMBED_MODEL,
        "index_name": settings.index_name,
        "retrieval_namespace": settings.retrieval_namespace,
        "retrieval_k": settings.retrieval_k,
        "generation_backends": generation_backends(),
        "judge_fingerprints": (stats or {}).get("judge_fingerprints", {}),
    }


def select_rows(rows: list[dict], spec: str) -> list[dict]:
    """A DIAGNOSTIC subset of the smoke rows, e.g. "24" or "1,4"; every row must be one of the 8."""
    try:
        wanted = [int(x) for x in spec.split(",") if x.strip()]
    except ValueError:
        raise ConfigError(f"--rows {spec!r}: expected comma-separated row numbers") from None
    known = {r["row"] for r in rows}
    if not wanted or not set(wanted) <= known:
        raise ConfigError(f"--rows {spec!r}: must be a non-empty subset of the smoke rows {sorted(known)}")
    return [r for r in rows if r["row"] in wanted]


def headers_summary(found: list[dict]) -> list[dict]:
    distinct: dict[str, dict] = {}
    for h in found:
        key = json.dumps(h, sort_keys=True)
        distinct.setdefault(key, {**h, "calls": 0})["calls"] += 1
    return list(distinct.values())


def run_live(mode: str, rows_spec: str = "") -> int:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")  # local runs; in CI the keys come from the environment
    import ragas
    from langchain_community.callbacks.manager import get_openai_callback

    from src.generate import reset_generation_backends

    snapshot = None
    try:
        rows = load_smoke_set()
        if rows_spec:
            rows = select_rows(rows, rows_spec)
        if mode != "baseline":
            if not SNAPSHOT_PATH.exists():
                raise ConfigError("eval/smoke_snapshot.json is missing; run a baseline first")
            snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (CapExceeded, ConfigError) as exc:
        print(f"::error::{'cap breached' if isinstance(exc, CapExceeded) else 'configuration error'}: {exc}")
        write_outputs({"mode": mode, "aborted": str(exc)})
        return 2

    cache = JudgeCache()
    reset_generation_backends()
    try:
        with get_openai_callback() as cb:
            deps, stats = live_deps(cb)
            code, report = execute(mode, rows, snapshot, deps, cache, ragas.__version__)
        chat = (cb.prompt_tokens, cb.prompt_tokens_cached, cb.completion_tokens)
        embed_tokens = stats["retrieval_embed_tokens"] + stats["judge_embed_tokens"]
        report["spend"] = {
            "chat_requests": cb.successful_requests, "prompt_tokens": chat[0], "prompt_tokens_cached": chat[1],
            "completion_tokens": chat[2], "embedding_tokens": embed_tokens,
            "judge_calls": stats["judge_calls"],
            "cost_usd": round(_chat_cost(*chat) + embed_tokens * PRICE_PER_M["embedding"] / 1e6, 6),
        }
        report["provenance"] = provenance(stats)
        report["openai_headers"] = headers_summary(stats["generation_headers"])
        for h in report["openai_headers"]:
            print(f"::notice title=generation call headers::" + ", ".join(f"{k}={v}" for k, v in h.items()))
        if rows_spec:
            report.update(diagnostic=True, rows_filter=[r["row"] for r in rows])
    finally:
        report_entries = cache.save()
    report.setdefault("cache", {})["entries_after_save"] = report_entries
    snap = snapshot_from(report["per_row"], report["provenance"]) if mode == "baseline" and code == 0 else None
    write_outputs(report, snap)
    return code


# --- the workflow's gate job (stdlib only; reads no secret value) --------------------------------------------------


def is_gated(path: str) -> bool:
    return (path.startswith(GATED_PREFIXES) or path in GATED_FILES
            or any(fnmatch(path, pattern) for pattern in GATED_GLOBS))


def snapshot_warning(files: list[str]) -> str | None:
    code = sorted(f for f in files if f.startswith(CODE_PREFIXES))
    if SNAPSHOT_REL in files and code:
        return (f"this diff changes {SNAPSHOT_REL} AND code under test ({', '.join(code[:5])}"
                f"{', …' if len(code) > 5 else ''}). A re-baseline must be its own deliberate commit naming its "
                "ledger row; check that the snapshot change is not hiding a regression.")
    return None


def changed_files(base: str, head: str, three_dot: bool) -> list[str] | None:
    """The diff's file list, or None when it cannot be computed (then the smoke runs: never a silent skip)."""
    if not base or not head or set(base) == {"0"}:
        return None
    spec = f"{base}...{head}" if three_dot else f"{base}..{head}"
    try:
        out = subprocess.run(["git", "diff", "--name-only", spec], cwd=REPO_ROOT, capture_output=True, text=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line for line in out.splitlines() if line]


def gate_decision(event: str, files: list[str] | None, has_secrets: bool, snapshot_exists: bool,
                  baseline: bool) -> tuple[bool, str]:
    if not has_secrets:
        return False, ("OPENAI_API_KEY / PINECONE_API_KEY are not available to this run (a fork PR, or the secrets "
                       "are not configured): smoke skipped, neutral")
    if event != "workflow_dispatch" and files is not None and not any(is_gated(f) for f in files):
        return False, "no gated path changed: smoke skipped, neutral"
    if not snapshot_exists and not baseline:
        return False, "no snapshot (eval/smoke_snapshot.json): baseline pending, smoke skipped, neutral"
    if baseline:
        return True, "baseline requested: the smoke job writes a snapshot artifact"
    if event == "workflow_dispatch":
        return True, "dispatched: the smoke job runs"
    if files is None:
        return True, "the diff could not be computed: the smoke job runs"
    return True, f"gated paths changed ({sum(is_gated(f) for f in files)} files): the smoke job runs"


def gate_main(args) -> int:
    files = None if args.event == "workflow_dispatch" else changed_files(args.base, args.head,
                                                                         three_dot=args.event == "pull_request")
    warning = snapshot_warning(files or [])
    if warning:
        print(f"::warning::{warning}")
    run, reason = gate_decision(args.event, files, args.has_secrets == "true",
                                (REPO_ROOT / SNAPSHOT_REL).exists(), args.baseline == "true")
    print(f"::notice title=eval-smoke gate::{reason}")
    for name, value in (("GITHUB_OUTPUT", f"run={'true' if run else 'false'}\n"),
                        ("GITHUB_STEP_SUMMARY", f"### eval-smoke gate\n\n{reason}\n")):
        target = os.environ.get(name)
        if target:
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(value)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command")
    gate = sub.add_parser("gate", help="the workflow's gate job")
    gate.add_argument("--event", required=True)
    gate.add_argument("--base", default="")
    gate.add_argument("--head", default="")
    gate.add_argument("--baseline", default="false")
    gate.add_argument("--has-secrets", default="false")
    parser.add_argument("--baseline", action="store_true", help="write a new snapshot artifact; no gate")
    parser.add_argument("--simulate-regression", action="store_true", help="the negative proof (intentionally red)")
    parser.add_argument("--rows", default="", help="DIAGNOSTIC: gate only these smoke rows, e.g. 24 (gate mode only)")
    args = parser.parse_args(argv)
    if args.command == "gate":
        return gate_main(args)
    if args.baseline and args.simulate_regression:
        print("::error::configuration error: --baseline and --simulate-regression are exclusive")
        return 2
    if args.rows and (args.baseline or args.simulate_regression):
        print("::error::configuration error: --rows is a gate-mode diagnostic; not with --baseline or "
              "--simulate-regression")
        return 2
    return run_live("baseline" if args.baseline else "simulate" if args.simulate_regression else "gate", args.rows)


if __name__ == "__main__":
    sys.exit(main())
