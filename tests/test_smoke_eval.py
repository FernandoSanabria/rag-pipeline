"""Hermetic tests for the G9 smoke evaluation (scripts/smoke_eval.py, .github/workflows/eval-smoke.yml).

No network: the live bindings are replaced by fakes, or the real wiring (api.main._answer over src.pipeline.ask) runs
with retrieval, generation and the judge stubbed. Rows are 1-based.
"""

import importlib.util
import json
import math
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("smoke_eval", REPO_ROOT / "scripts" / "smoke_eval.py")
smoke = importlib.util.module_from_spec(_spec)
sys.modules["smoke_eval"] = smoke
_spec.loader.exec_module(smoke)

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "eval-smoke.yml"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MARKER = "ZZ-TEXT-MARKER"  # planted in every question, answer and context: it must never reach an output


# --- the smoke set ----------------------------------------------------------------------------------------------------


def test_smoke_set_is_the_gate_1_eight_rows_and_matches_the_dataset():
    rows = smoke.load_smoke_set()
    assert [r["row"] for r in rows] == [1, 4, 15, 20, 21, 25, 26, 24]
    assert {r["row"]: r["endpoint"] for r in rows if r["endpoint"] != "/ask"} == {24: "/ask/agent"}
    assert [r["row"] for r in rows if r["refusal"]] == [25]
    assert all(r["question"] and r["reference"] for r in rows)
    # the negative proof's k override is wired for /ask rows only
    assert next(r for r in rows if r["row"] == smoke.SIM_T1_ROW)["endpoint"] == "/ask"


def test_a_renumbered_dataset_is_a_configuration_error(tmp_path):
    spec = json.loads(smoke.SMOKE_SET_PATH.read_text())
    spec["rows"][0]["question_sha256"] = "0" * 64
    path = tmp_path / "smoke_set.json"
    path.write_text(json.dumps(spec))
    with pytest.raises(smoke.ConfigError, match="row 1: question hash mismatch"):
        smoke.load_smoke_set(path)


def test_more_than_eight_rows_trips_the_row_cap(tmp_path):
    spec = json.loads(smoke.SMOKE_SET_PATH.read_text())
    spec["rows"].append(spec["rows"][0])
    path = tmp_path / "smoke_set.json"
    path.write_text(json.dumps(spec))
    with pytest.raises(smoke.CapExceeded, match="rows: 9 > cap 8"):
        smoke.load_smoke_set(path)


def test_the_judge_is_run_evals():
    source = (REPO_ROOT / "eval" / "run_eval.py").read_text()
    assert f'judge_model = "{smoke.JUDGE_MODEL}"' in source
    assert f'embed_model = "{smoke.EMBED_MODEL}"' in source
    assert "ChatOpenAI(model=judge_model, temperature=0)" in source
    for metric in smoke.METRICS:
        assert metric in source


# --- T1 / T2 ----------------------------------------------------------------------------------------------------------


def test_t1_is_set_equality_order_insensitive_and_ignores_tool_chunks():
    chunks = [{"source_doc_id": "b", "page": 2.0}, {"source_doc_id": "a", "page": 1}, {"source_doc_id": "a", "page": 1},
              {"source_doc_id": "tool:ConvertExposureLimit", "page": None}]
    pages = smoke.page_set(chunks)
    assert pages == [["a", 1], ["b", 2]]
    row = {"row": 1, "refusal": False}
    snap = {"pages": [["b", 2], ["a", 1]]}
    assert smoke.t1_failures(row, {"pages": pages, "is_refusal": False}, snap) == []
    fails = smoke.t1_failures(row, {"pages": [["a", 1]], "is_refusal": False}, snap)
    assert [(f["tier"], f["row"], f["kind"]) for f in fails] == [("T1", 1, "retrieval_set")]
    assert "left [['b', 2]]" in fails[0]["detail"]


def test_t1_refusal_identity_applies_to_the_refusal_row_only():
    snap = {"pages": [["a", 1]]}
    now = {"pages": [["a", 1]], "is_refusal": False}
    assert smoke.t1_failures({"row": 4, "refusal": False}, now, snap) == []
    fails = smoke.t1_failures({"row": 25, "refusal": True}, now, snap)
    assert [(f["tier"], f["row"], f["kind"]) for f in fails] == [("T1", 25, "refusal_identity")]


def test_t2_reds_only_below_snapshot_minus_delta():
    assert smoke.DELTA_F == 0.2
    assert smoke.t2_status(0.8, 1.0) == "pass"   # one unsupported statement in five: the measured noise
    assert smoke.t2_status(0.75, 1.0) == "red"
    assert smoke.t2_status(0.0, 0.0) == "pass"   # the refusal row: faithfulness cannot see it, T1 does
    assert smoke.t2_status(None, 1.0) == "not_scored"
    assert smoke.t2_status(1.0, None) == "not_scored"


# --- NaN retry, the cache -----------------------------------------------------------------------------------------------


class _Scorer:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, question, answer, contexts, reference, metrics):
        self.calls.append(metrics)
        return self.responses.pop(0)


ROW = {"row": 4, "question": "q", "reference": "r"}


def test_a_judge_nan_is_retried_once_then_not_scored(tmp_path):
    cache = smoke.JudgeCache(tmp_path / "judge.json")
    nan = float("nan")
    scorer = _Scorer({"faithfulness": nan, "answer_correctness": 0.5}, {"faithfulness": nan})
    out = smoke.judge_row(ROW, "an answer", ["ctx"], cache, scorer, "0.4.3")
    assert out["faithfulness"] == (None, "not_scored")
    assert out["answer_correctness"] == (0.5, "judge")
    assert scorer.calls == [("faithfulness", "answer_correctness"), ("faithfulness",)]
    assert len(cache.entries) == 1  # NaN is never cached


def test_a_nan_that_scores_on_retry_is_kept_and_cached(tmp_path):
    cache = smoke.JudgeCache(tmp_path / "judge.json")
    scorer = _Scorer({"faithfulness": float("nan"), "answer_correctness": 0.5}, {"faithfulness": 1.0})
    out = smoke.judge_row(ROW, "an answer", ["ctx"], cache, scorer, "0.4.3")
    assert out["faithfulness"] == (1.0, "judge_retry")
    again = smoke.judge_row(ROW, "an answer", ["ctx"], cache, _Scorer(), "0.4.3")  # no judge call left
    assert again == {"faithfulness": (1.0, "cache"), "answer_correctness": (0.5, "cache")}


def test_an_empty_answer_scores_zero_without_a_judge_call(tmp_path):
    out = smoke.judge_row(ROW, "  ", ["ctx"], smoke.JudgeCache(tmp_path / "judge.json"), _Scorer(), "0.4.3")
    assert out == {"faithfulness": (0.0, "empty_answer"), "answer_correctness": (0.0, "empty_answer")}
    assert smoke.t2_status(0.0, 1.0) == "red"


def test_the_cache_key_is_stable_and_sensitive_to_every_input():
    base = ("faithfulness", "q", "a", ["c1", "c2"], "r", "0.4.3")
    key = smoke.cache_key(*base)
    assert key == smoke.cache_key(*base) and re.fullmatch(r"[0-9a-f]{64}", key)
    variants = [("answer_correctness",) + base[1:], base[:1] + ("q2",) + base[2:], base[:2] + ("a2",) + base[3:],
                base[:3] + (["c2", "c1"],) + base[4:], base[:4] + ("r2",) + base[5:], base[:5] + ("0.4.4",)]
    assert len({smoke.cache_key(*v) for v in variants} | {key}) == 7


def test_the_cache_merges_and_never_replaces(tmp_path):
    path = tmp_path / ".smoke-cache" / "judge.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema": 1, "entries": {"a": 1.0, "shared": 0.5}}))
    cache = smoke.JudgeCache(path)
    assert cache.loaded == 2
    # another run saved a newer file in the meantime
    path.write_text(json.dumps({"schema": 1, "entries": {"a": 1.0, "shared": 0.5, "b": 0.25}}))
    cache.put("c", 0.75)
    cache.put("shared", 0.0)        # an entry already present wins
    cache.put("nan", float("nan"))  # NaN is never stored
    assert cache.save() == 4
    saved = json.loads(path.read_text())["entries"]
    assert saved == {"a": 1.0, "b": 0.25, "c": 0.75, "shared": 0.5}
    # a missing or corrupt file starts empty instead of failing the run
    path.write_text("{not json")
    assert smoke.JudgeCache(path).entries == {}
    assert smoke.JudgeCache(tmp_path / "absent.json").entries == {}


# --- whole runs with fake bindings ------------------------------------------------------------------------------------


def _rows():
    return [{"row": n, "endpoint": "/ask", "refusal": n == 25, "question": f"{MARKER} question {n}",
             "reference": f"{MARKER} reference {n}"} for n in (1, 4, 25)]


def _fake_deps(gen_calls=None, judge_calls=None, scorer=None):
    counts = {"gen": 0, "judge": 0}

    def answer(row, k=None):
        counts["gen"] += 1
        n = row["row"]
        pages = range(1, (k or 10) + 1)
        text = "The provided context does not contain the answer." if n == 25 else f"{MARKER} answer {n}"
        return {"answer": text, "contexts": [f"{MARKER} ctx {n} {p}" for p in pages],
                "chunks": [{"source_doc_id": f"doc-{n}", "page": p, "text": MARKER} for p in pages],
                "route": None, "source_doc_id": None}

    def score(question, answer_text, contexts, reference, metrics):
        counts["judge"] += 4
        faithful = 0.0 if answer_text == smoke.SIM_UNFAITHFUL_ANSWER or answer_text.startswith("The provided") else 1.0
        return {"faithfulness": faithful, "answer_correctness": 0.6}

    from api.confidence import is_refusal

    return {"answer": answer, "score": scorer or score, "advisory": lambda row, res: None, "is_refusal": is_refusal,
            "generation_calls": gen_calls or (lambda: counts["gen"]),
            "judge_calls": judge_calls or (lambda: counts["judge"]), "judge_cost": lambda: 0.0}


def _baseline(tmp_path):
    cache = smoke.JudgeCache(tmp_path / "j.json")
    code, report = smoke.execute("baseline", _rows(), None, _fake_deps(), cache, "v")
    assert code == 0
    cache.save()
    return smoke.snapshot_from(report["per_row"], {"run_number": "1"})


def test_baseline_then_gate_is_green_and_replays_the_cache(tmp_path):
    snapshot = _baseline(tmp_path)
    assert set(snapshot["rows"]) == {"1", "4", "25"}
    code, report = smoke.execute("gate", _rows(), snapshot, _fake_deps(), smoke.JudgeCache(tmp_path / "j.json"), "v")
    assert code == 0 and report["failures"] == []
    assert report["cache"]["faithfulness_hits"] == 3
    assert {r["row"]: (r["t1"], r["t2"]) for r in report["per_row"]} == {1: ("pass", "pass"), 4: ("pass", "pass"),
                                                                         25: ("pass", "pass")}


def test_caps_trip_with_exit_2(tmp_path):
    cache = smoke.JudgeCache(tmp_path / "j.json")
    code, report = smoke.execute("baseline", _rows(), None, _fake_deps(gen_calls=lambda: 11), cache, "v")
    assert code == 2 and "generation_calls: 11 > cap 10" in report["aborted"]
    code, report = smoke.execute("baseline", _rows(), None, _fake_deps(judge_calls=lambda: 121), cache, "v")
    assert code == 2 and "judge_calls: 121 > cap 120" in report["aborted"]


def test_gate_without_a_snapshot_is_a_configuration_error(tmp_path):
    code, report = smoke.execute("gate", _rows(), None, _fake_deps(), smoke.JudgeCache(tmp_path / "j.json"), "v")
    assert code == 2 and "no snapshot" in report["aborted"]


def test_outputs_carry_no_question_answer_or_context_text(tmp_path, monkeypatch, capsys):
    snapshot = _baseline(tmp_path)
    code, report = smoke.execute("simulate", _rows(), snapshot, _fake_deps(), smoke.JudgeCache(tmp_path / "j.json"), "v")
    monkeypatch.setattr(smoke, "OUT_DIR", tmp_path / "out")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    smoke.write_outputs(report, snapshot)
    written = "".join(p.read_text() for p in (tmp_path / "out").iterdir()) + (tmp_path / "summary.md").read_text()
    assert MARKER not in written + json.dumps(snapshot) + capsys.readouterr().out
    assert MARKER not in (tmp_path / "j.json").read_text()  # the cache holds hashes and floats only


# --- the negative proof, through the real wiring ----------------------------------------------------------------------


def test_simulate_regression_names_both_failures_through_the_real_wiring(tmp_path, monkeypatch):
    """api.main._answer over the real src.pipeline.ask; only retrieval, generation and the judge are fake. The k=2
    override must reach dense_search through the patched settings, and the canned answer must reach the judge."""
    import src.pipeline as pipeline
    import src.retrieve as retrieve

    seen_k = []

    def fake_dense_search(question, k=5, source_doc_id=None):
        seen_k.append(k)
        return [{"text": f"page {p} text", "source_doc_id": "doc", "page": float(p)} for p in range(1, k + 1)]

    monkeypatch.setattr(pipeline, "dense_search", fake_dense_search)
    monkeypatch.setattr(pipeline, "generate", lambda question, contexts: "A grounded answer without figures.")
    monkeypatch.setattr(retrieve, "_embedder", lambda: object())

    class _CB:
        prompt_tokens = prompt_tokens_cached = completion_tokens = 0

    deps, _ = smoke.live_deps(_CB())

    def score(question, answer_text, contexts, reference, metrics):
        return {m: (0.0 if answer_text == smoke.SIM_UNFAITHFUL_ANSWER else 1.0) for m in metrics}

    deps.update(score=score, advisory=lambda row, res: None)
    rows = [r for r in smoke.load_smoke_set() if r["row"] in (1, 4)]
    cache = smoke.JudgeCache(tmp_path / "j.json")
    code, base = smoke.execute("baseline", rows, None, deps, cache, "v")
    assert code == 0 and seen_k == [10, 10]
    snapshot = smoke.snapshot_from(base["per_row"], {})
    assert snapshot["rows"]["1"]["pages"] == [["doc", p] for p in range(1, 11)]

    code, report = smoke.execute("gate", rows, snapshot, deps, cache, "v")
    assert code == 0 and report["failures"] == [] and report["cache"]["faithfulness_hits"] == 2

    seen_k.clear()
    code, report = smoke.execute("simulate", rows, snapshot, deps, cache, "v")
    assert seen_k == [2, 10]                  # row 1 at k=2, row 4 untouched
    assert pipeline.get_settings().retrieval_k == 10  # the override did not leak
    assert code == 1
    assert [(f["tier"], f["row"]) for f in report["failures"]] == [("T1", 1), ("T2", 4)]
    assert report["negative_proof"] == {"t1_row1": True, "t2_row4": True, "held": True}
    row4 = next(r for r in report["per_row"] if r["row"] == 4)
    assert row4["sources"]["faithfulness"] == "judge" and row4["simulated"] == "T2: canned unfaithful answer"


def test_a_failed_negative_proof_is_still_red(tmp_path):
    snapshot = _baseline(tmp_path)

    def lenient(question, answer_text, contexts, reference, metrics):
        return {m: 1.0 for m in metrics}  # a judge that cannot see the canned answer

    rows = _rows()
    deps = _fake_deps(scorer=lenient)
    code, report = smoke.execute("simulate", rows, snapshot, deps, smoke.JudgeCache(tmp_path / "x.json"), "v")
    assert code == 1 and report["negative_proof"] == {"t1_row1": True, "t2_row4": False, "held": False}


def test_baseline_and_simulate_are_exclusive():
    assert smoke.main(["--baseline", "--simulate-regression"]) == 2


# --- the gate job -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path, gated", [
    ("src/pipeline.py", True), ("agent/graph.py", True), ("api/main.py", True), ("eval/smoke_snapshot.json", True),
    ("eval/smoke_set.json", True), ("scripts/smoke_eval.py", True), ("eval/run_eval.py", True),
    ("pyproject.toml", True), ("uv.lock", True), (".github/workflows/eval-smoke.yml", True),
    ("README.md", False), ("eval/METRICS_HISTORY.md", False), ("tests/test_api.py", False),
    (".github/workflows/ci.yml", False), ("eval/dataset.jsonl", False),
])
def test_gated_paths(path, gated):
    assert smoke.is_gated(path) is gated


def test_snapshot_plus_code_diff_warns():
    assert smoke.snapshot_warning(["eval/smoke_snapshot.json", "README.md"]) is None
    assert smoke.snapshot_warning(["src/retrieve.py"]) is None
    warning = smoke.snapshot_warning(["eval/smoke_snapshot.json", "src/retrieve.py"])
    assert warning and "src/retrieve.py" in warning


@pytest.mark.parametrize("event, files, secrets, snapshot, baseline, expected", [
    ("pull_request", ["src/x.py"], False, True, False, (False, "not available")),
    ("pull_request", ["README.md"], True, True, False, (False, "no gated path")),
    ("pull_request", ["src/x.py"], True, False, False, (False, "baseline pending")),
    ("pull_request", ["src/x.py"], True, True, False, (True, "gated paths changed")),
    ("pull_request", None, True, True, False, (True, "could not be computed")),
    ("push", ["agent/graph.py"], True, True, False, (True, "gated paths changed")),
    ("workflow_dispatch", None, True, True, False, (True, "dispatched")),
    ("workflow_dispatch", None, True, False, True, (True, "baseline requested")),
    ("workflow_dispatch", None, False, False, True, (False, "not available")),
])
def test_gate_decision(event, files, secrets, snapshot, baseline, expected):
    run, reason = smoke.gate_decision(event, files, secrets, snapshot, baseline)
    assert run is expected[0] and expected[1] in reason


def test_gate_job_writes_its_output_and_summary(tmp_path, monkeypatch):
    out, summary = tmp_path / "out", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert smoke.main(["gate", "--event", "workflow_dispatch", "--has-secrets", "false"]) == 0
    assert out.read_text() == "run=false\n" and "neutral" in summary.read_text()


# --- the workflow, and ci.yml (P6) ------------------------------------------------------------------------------------


def _workflow():
    data = yaml.safe_load(WORKFLOW.read_text())
    return data, data.get("on", data.get(True))  # YAML 1.1 reads a bare `on` key as True


def test_workflow_triggers_and_inputs():
    data, on = _workflow()
    assert set(on) == {"pull_request", "push", "workflow_dispatch"}
    assert on["pull_request"] == {"branches": ["main"]} and on["push"] == {"branches": ["main"]}  # no on.paths
    assert set(on["workflow_dispatch"]["inputs"]) == {"simulate_regression", "baseline"}
    assert all(i["type"] == "boolean" and i["default"] is False for i in on["workflow_dispatch"]["inputs"].values())
    assert "SIMULATED REGRESSION" in data["run-name"]
    assert data["permissions"] == {"contents": "read"}
    assert data["concurrency"]["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_workflow_jobs_skip_neutrally_and_cache_merges():
    data, _ = _workflow()
    gate, job = data["jobs"]["gate"], data["jobs"]["smoke"]
    assert "secrets.OPENAI_API_KEY != ''" in gate["steps"][1]["env"]["HAS_SECRETS"]
    assert "secrets.PINECONE_API_KEY != ''" in gate["steps"][1]["env"]["HAS_SECRETS"]
    assert "python3 scripts/smoke_eval.py gate" in gate["steps"][1]["run"]
    assert job["needs"] == "gate" and job["if"] == "needs.gate.outputs.run == 'true'"
    assert job["timeout-minutes"] == 15
    assert job["env"]["INDEX_NAME"] == "equip-docs-rag" and job["env"]["LANGCHAIN_TRACING_V2"] == "false"
    steps = {s.get("name"): s for s in job["steps"]}
    setup = next(s for s in job["steps"] if s.get("uses", "").startswith("astral-sh/setup-uv"))
    assert setup["uses"] == "astral-sh/setup-uv@v5" and setup["with"]["version"] == "0.11.25"
    restore = next(s for s in job["steps"] if s.get("uses") == "actions/cache/restore@v4")
    save = next(s for s in job["steps"] if s.get("uses") == "actions/cache/save@v4")
    assert restore["with"]["restore-keys"] == "smoke-judge-"
    assert restore["with"]["key"] == save["with"]["key"] == "smoke-judge-${{ github.run_id }}-${{ github.run_attempt }}"
    assert save["if"] == "always()" and restore["with"]["path"] == save["with"]["path"] == ".smoke-cache"
    assert any("scripts/smoke_eval.py" in s.get("run", "") for s in steps.values())
    upload = next(s for s in job["steps"] if s.get("uses") == "actions/upload-artifact@v4")
    # .smoke-out/ is a hidden path; without this, v4 uploads nothing (the first baseline run lost its snapshot this way)
    assert upload["with"]["path"] == ".smoke-out/" and upload["with"]["include-hidden-files"] is True
    assert upload["if"] == "always()"


def test_secrets_appear_only_in_this_workflow_and_ci_yml_is_hermetic():
    text = WORKFLOW.read_text()
    assert set(re.findall(r"secrets\.([A-Z_]+)", text)) == {"OPENAI_API_KEY", "PINECONE_API_KEY"}
    assert "secrets." not in CI.read_text()
    assert "pull_request_target" not in text  # fork PRs must never receive the keys


# --- the committed snapshot -------------------------------------------------------------------------------------------


def test_the_committed_snapshot_covers_the_smoke_set_with_derived_values_only():
    text = smoke.SNAPSHOT_PATH.read_text()
    snap = json.loads(text)
    rows = smoke.load_smoke_set()
    assert set(snap["rows"]) == {str(r["row"]) for r in rows}
    assert snap["delta_f"] == smoke.DELTA_F and snap["justified_by"].startswith("eval/METRICS_HISTORY.md, G9 block")
    assert snap["run"]["event"] == "workflow_dispatch" and snap["run"]["retrieval_k"] == 10
    assert snap["run"]["retrieval_namespace"] == "semantic_v2" and snap["run"]["index_name"] == "equip-docs-rag"
    for n, row in snap["rows"].items():
        assert isinstance(row["faithfulness"], float) and row["pages"], n  # a baseline with a NOT SCORED row exits 2
        assert re.fullmatch(r"[0-9a-f]{64}", row["answer_sha256"])
    assert snap["rows"]["25"]["is_refusal"] is True and snap["rows"]["24"]["route"] == "source_scoped"
    for r in rows:  # derived values only: the repository is public
        assert r["question"] not in text and r["reference"][:60] not in text
