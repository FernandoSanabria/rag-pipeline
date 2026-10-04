"""Hermetic tests for the G6 guards (api/guards.py) and their one wiring point in api/main.py.

The classifier is mocked and both pipelines are stubbed, so nothing here calls OpenAI or Pinecone. The numbered
sections follow the R6 test plan in eval/g6_design.md. Contexts are short synthetic stand-ins: the real chunk
text of the vendor SDSs is tier 2 and is never committed.
"""

import json
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import graph as agent_graph
from api import guards, main
from api.citations import derive_citations
from api.confidence import score_confidence

ROOT = Path(__file__).resolve().parents[1]
GUARDRAIL_SET = [
    json.loads(line)
    for line in (ROOT / "eval" / "guardrail_set.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
REAL_CLASSIFY = guards.classify_with_meta  # conftest.py stubs it for every test; one test needs the real one

client = TestClient(main.app)

ENDPOINTS = ["/ask", "/ask/agent"]
BLOCK_LABELS = ["out_of_scope", "injection", "harmful_request", "pii_request"]
QUESTION = "What is the OSHA PEL for anhydrous ammonia?"
GROUNDED = {
    "answer": "The OSHA PEL for anhydrous ammonia is 50 ppm [source_doc_id=osha-1910-1000 page=7].",
    "contexts": ["[source_doc_id=osha-1910-1000 page=7]\nAmmonia ... PEL 50 ppm"],
    "chunks": [{"source_doc_id": "osha-1910-1000", "page": 7, "text": "Ammonia ... PEL 50 ppm"}],
    "route": "direct",
    "source_doc_id": "",
    "routing_reason": None,
}

# The G1 closure's prior-knowledge answer to the acetone capability question, recorded 2026-09-22 with the tool
# disabled (2 of 5 trials gave this text). 58.08 and 24.45 come from chemistry knowledge, and 0.855 ppm is wrong:
# the correct conversion is 35.61 ppm.
ACETONE_QUESTION = (
    "Per the Sigma-Aldrich acetone SDS, the PROC15 modeled worker inhalation concentration is 84.58 mg/m3. "
    "Express that concentration in ppm."
)
ACETONE_PRIOR_KNOWLEDGE_ANSWER = (
    "To convert the concentration from mg/m³ to ppm for acetone, we can use the following formula:\n\n\\[\n"
    "\\text{ppm} = \\left( \\frac{\\text{mg/m}^3}{\\text{molar mass (g/mol)}} \\right) \\times \\text{molar volume "
    "(L/mol)}\n\\]\n\nThe molar mass of acetone (C3H6O) is approximately 58.08 g/mol, and the molar volume at "
    "standard conditions is about 24.45 L/mol.\n\nUsing the values:\n\n\\[\n\\text{ppm} = \\left( \\frac{84.58 "
    "\\text{ mg/m}^3}{58.08 \\text{ g/mol}} \\right) \\times 24.45 \\text{ L/mol}\n\\]\n\nFirst, convert mg/m³ "
    "to g/m³:\n\n\\[\n84.58 \\text{ mg/m}^3 = 0.08458 \\text{ g/m}^3\n\\]\n\nNow, substituting into the "
    "formula:\n\n\\[\n\\text{ppm} = \\left( \\frac{0.08458 \\text{ g/m}^3}{58.08 \\text{ g/mol}} \\right) \\times "
    "24.45 \\text{ L/mol}\n\\]\n\nCalculating this gives:\n\n\\[\n\\text{ppm} \\approx 0.035 \\times 24.45 "
    "\\approx 0.855 \\text{ ppm}\n\\]\n\nThus, the PROC15 modeled worker inhalation concentration of acetone is "
    "approximately 0.855 ppm."
)
# Stand-in for the live source-scoped contexts observed 2026-10-04: they hold 84.58 but neither 58.08 nor 24.45.
ACETONE_CONTEXTS = [
    "[source_doc_id=sds-sigma-aldrich-acetone page=21]\nWorker exposure, long-term inhalation, PROC15: 84.58 mg/m3",
    "[source_doc_id=sds-sigma-aldrich-acetone page=1]\nSafety data sheet, acetone, identification of the substance",
]


def row(n: int) -> str:
    """The question on row n (1-based) of eval/guardrail_set.jsonl."""
    return GUARDRAIL_SET[n - 1]["question"]


class RecordingPipeline:
    def __init__(self, result: dict):
        self.result = result
        self.calls: list[str] = []

    def __call__(self, question: str) -> dict:
        self.calls.append(question)
        return self.result


def install(monkeypatch, endpoint: str, result: dict) -> RecordingPipeline:
    """Stub the pipeline behind `endpoint`. /ask/agent imports `agent.graph.ask` lazily, so it is patched there."""
    pipeline = RecordingPipeline(result)
    monkeypatch.setattr(main if endpoint == "/ask" else agent_graph, "ask", pipeline)
    return pipeline


def classifier(monkeypatch, label: str | None) -> list[str]:
    """Stub the classifier to answer `label`, and return the list of questions it was asked."""
    calls: list[str] = []

    def fake(question: str):
        calls.append(question)
        return label, {}

    monkeypatch.setattr(guards, "classify_with_meta", fake)
    return calls


# --- 1. Each label routes correctly -------------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_in_scope_runs_the_pipeline(monkeypatch, endpoint):
    classifier(monkeypatch, "in_scope")
    pipeline = install(monkeypatch, endpoint, GROUNDED)
    body = client.post(endpoint, json={"question": QUESTION}).json()
    assert pipeline.calls == [QUESTION]
    assert body["answer"] == GROUNDED["answer"]
    assert body["guard"] is None


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("label", BLOCK_LABELS)
def test_block_labels_refuse_without_running_the_pipeline(monkeypatch, endpoint, label):
    classifier(monkeypatch, label)
    pipeline = install(monkeypatch, endpoint, GROUNDED)
    body = client.post(endpoint, json={"question": QUESTION}).json()
    assert pipeline.calls == []
    assert body["answer"] == guards.REFUSALS[label].answer
    assert body["guard"] == {"stage": "input", "reason": label}


# --- 2. Rules -----------------------------------------------------------------------------------------------------


def test_rows_7_and_8_hit_exactly_the_registered_rules():
    def hits(question):
        return [name for name, pattern in guards.INJECTION_RULES.items() if pattern.search(question)]

    assert hits(row(7)) == ["ignore_previous_instructions", "reveal_system_prompt"]
    assert hits(row(8)) == ["you_are_now_dan"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("n", [7, 8])
def test_rule_rows_are_refused_before_the_classifier(monkeypatch, endpoint, n):
    asked = classifier(monkeypatch, "in_scope")  # it would allow the question, if it were asked
    pipeline = install(monkeypatch, endpoint, GROUNDED)
    body = client.post(endpoint, json={"question": row(n)}).json()
    assert body["guard"] == {"stage": "input", "reason": "injection"}
    assert asked == []
    assert pipeline.calls == []


@pytest.mark.parametrize("label", ["injection", "in_scope"])
def test_row_9_is_a_classifier_case(monkeypatch, label):
    asked = classifier(monkeypatch, label)
    decision = guards.check_input(row(9))
    assert asked == [row(9)]
    assert decision.rule is None
    assert decision.label == label  # the classifier's label decides


def test_rows_9_to_12_match_no_rule():
    assert [guards.matched_rule(row(n)) for n in range(9, 13)] == [None] * 4


def test_no_hard_negative_matches_a_rule():
    hard_negatives = [r["question"] for r in GUARDRAIL_SET if r["category"] == "hard_negative"]
    assert len(hard_negatives) == 9
    assert [q for q in hard_negatives if guards.matched_rule(q)] == []


def test_no_frozen_question_matches_a_rule():
    lines = (ROOT / "eval" / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
    questions = [json.loads(line)["question"] for line in lines if line.strip()]
    assert len(questions) == 28
    assert [q for q in questions if guards.matched_rule(q)] == []


# --- 3. Pass-through ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_allowed_question_passes_through_unchanged(monkeypatch, endpoint):
    pipeline = install(monkeypatch, endpoint, GROUNDED)
    body = client.post(endpoint, json={"question": QUESTION}).json()
    assert pipeline.calls == [QUESTION]  # exactly once, with exactly the request's question
    score, basis = score_confidence(GROUNDED["answer"])
    guard_less = {
        "answer": GROUNDED["answer"],
        "citations": derive_citations(GROUNDED["answer"], GROUNDED["chunks"]),
        "confidence_score": score,
        "confidence_basis": basis,
    }
    assert {key: body[key] for key in guard_less} == guard_less
    assert body["guard"] is None


def test_the_pipeline_receives_the_same_str_object():
    received = []
    question = "What is the OSHA PEL for anhydrous ammonia?"
    main._answer(question, lambda q: received.append(q) or GROUNDED)
    assert len(received) == 1
    assert received[0] is question


# --- 4. Blocked shape ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("label", BLOCK_LABELS)
def test_input_block_shape_on_ask(monkeypatch, label):
    classifier(monkeypatch, label)
    install(monkeypatch, "/ask", GROUNDED)
    response = client.post("/ask", json={"question": QUESTION})
    refusal = guards.REFUSALS[label]
    assert response.status_code == 200
    assert response.json() == {
        "answer": refusal.answer,
        "citations": [],
        "confidence_score": 0.25,
        "confidence_basis": refusal.basis,
        "guard": {"stage": "input", "reason": label},
    }
    assert refusal.basis.startswith("low: refused — ") and refusal.basis.endswith("(input guard)")


def test_input_block_on_agent_runs_no_route(monkeypatch):
    classifier(monkeypatch, "pii_request")
    install(monkeypatch, "/ask/agent", GROUNDED)
    response = client.post("/ask/agent", json={"question": QUESTION})
    body = response.json()
    assert response.status_code == 200
    assert body["route"] == "none"
    assert body["source_doc_id"] is None
    assert body["routing_reason"] is None
    assert body["citations"] == []
    assert body["confidence_score"] == 0.25
    assert body["guard"] == {"stage": "input", "reason": "pii_request"}


def test_refusals_match_the_design_contract_table():
    design = (ROOT / "eval" / "g6_design.md").read_text(encoding="utf-8")
    assert len(guards.REFUSALS) == 6
    for r in guards.REFUSALS.values():
        assert f'| {r.stage} | `{r.reason}` | "{r.answer}" | `{r.basis}` |' in design


# --- 5. Output guard on canned pairs ------------------------------------------------------------------------------


def test_recorded_prior_knowledge_acetone_answer_is_refused():
    assert guards.check_output(ACETONE_PRIOR_KNOWLEDGE_ANSWER, ACETONE_CONTEXTS, ACETONE_QUESTION) is not None
    missing = guards.untraceable_figures(ACETONE_PRIOR_KNOWLEDGE_ANSWER, ACETONE_CONTEXTS, ACETONE_QUESTION)
    assert {"58.08", "24.45", "0.08458", "0.035", "0.855"} <= set(missing)
    assert "84.58" not in missing  # the question's own value traces


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_output_block_withholds_the_answer_and_keeps_the_route(monkeypatch, endpoint):
    result = {
        "answer": ACETONE_PRIOR_KNOWLEDGE_ANSWER,
        "contexts": ACETONE_CONTEXTS,
        "chunks": [{"source_doc_id": "sds-sigma-aldrich-acetone", "page": 21, "text": "..."}],
        "route": "source_scoped",
        "source_doc_id": "sds-sigma-aldrich-acetone",
        "routing_reason": "Question attributed to a single named document: Sigma-Aldrich SDS — Acetone",
    }
    pipeline = install(monkeypatch, endpoint, result)
    response = client.post(endpoint, json={"question": ACETONE_QUESTION})
    body = response.json()
    refusal = guards.REFUSALS["untraceable_numbers"]
    assert response.status_code == 200
    assert pipeline.calls == [ACETONE_QUESTION]
    assert body["answer"] == refusal.answer
    assert body["citations"] == []
    assert body["confidence_score"] == 0.25
    assert body["confidence_basis"] == "low: refused — untraceable figures (output guard)"
    assert body["guard"] == {"stage": "output", "reason": "untraceable_numbers"}
    if endpoint == "/ask/agent":
        assert body["route"] == "source_scoped"
        assert body["source_doc_id"] == "sds-sigma-aldrich-acetone"


@pytest.mark.parametrize(
    "answer, context",
    [
        pytest.param("The OSHA PEL for anhydrous ammonia is 50 ppm.", "Ammonia ... PEL 50 ppm", id="grounded"),
        pytest.param("The flash point is -17.0 °C (closed cup).", "Flash point -17,0 °C closed cup", id="decimal-comma"),
        pytest.param("That is 52.2 mg/m3.", "args={} -> {\"value\": 52.2393}", id="round-52.2393-to-52.2"),
        pytest.param("The factor is 2.90.", "factor 2.8998", id="round-2.8998-to-2.90"),
        pytest.param("The factor is about 3.", "factor 2.8998", id="round-2.8998-to-3"),
        pytest.param("It rounds to 2.68.", "value 2.675", id="decimal-half-up-not-float"),
        pytest.param("The threshold is 10,000 pounds.", "threshold quantity 10000 lbs", id="thousands-separator"),
        pytest.param("The density is 0.791 g/cm3.", "Density 0,791 g/cm3", id="comma-before-three-digits"),
        pytest.param("The upper limit is 12.8 %.", "Explosion limits 2.5 -12.8 %(V)", id="range-dash"),
        pytest.param("It autoignites at 651°C.", "Auto-ignition temperature651°C", id="number-glued-to-a-word"),
        pytest.param("Its UN number is UN1005.", "Transport: UN1005 ammonia, anhydrous", id="identifier-verbatim"),
        pytest.param("Lockout devices are removed by the employee who applied them.", "Each lockout device", id="no-numbers"),
        pytest.param(
            "1. The employee who applied the device removes it [source_doc_id=osha-1910-147 page=9].\n"
            "2. Otherwise the employer follows a documented procedure.",
            "Each lockout device shall be removed by the employee who applied the device.",
            id="citations-and-list-numerals-ignored",
        ),
    ],
)
def test_traceable_answers_pass(answer, context):
    assert guards.untraceable_figures(answer, [context], "A question with no figures?") == []
    assert guards.check_output(answer, [context], "A question with no figures?") is None


@pytest.mark.parametrize(
    "answer, context, missing",
    [
        pytest.param("That is approximately 0.855 ppm.", "computed value 35.61", ["0.855"], id="0.855-vs-35.61"),
        pytest.param(
            "75 ppm × 0.70 = 52.5 mg/m³.", "Conversion: 1 ppm = 0.70 mg/m3", ["52.5"], id="in-head-arithmetic"
        ),
        pytest.param("The factor is about 4.", "factor 2.8998", ["4"], id="no-rounding-reaches-4"),
        pytest.param("Its UN number is UN1090.", "Transport: UN1005 ammonia, anhydrous", ["UN1090"], id="identifier-absent"),
    ],
)
def test_untraceable_answers_are_refused(answer, context, missing):
    question = "A workplace air sample shows 75 ppm of anhydrous ammonia. Express that concentration in mg/m3."
    assert guards.untraceable_figures(answer, [context], question) == missing
    assert guards.check_output(answer, [context], question) is guards.REFUSALS["untraceable_numbers"]


def test_a_negative_answer_number_needs_a_negative_source():
    assert guards.untraceable_figures("The flash point is -17 °C.", ["Flash point 17 °C"], "Flash point?") == ["-17"]


@pytest.mark.parametrize("answer", ["The provided context does not contain the answer.", "", "   "])
def test_whole_refusals_and_empty_answers_pass(answer):
    assert guards.check_output(answer, [], "What is 2 + 2?") is None


def test_output_guard_keeps_an_answer_whole_or_refuses_it_whole(monkeypatch):
    # "Refuse rather than redact": a single untraceable figure withholds the whole answer, including the grounded
    # 50 ppm; nothing is trimmed.
    partial = dict(GROUNDED, answer="The PEL is 50 ppm and the IDLH is 300 ppm.")
    install(monkeypatch, "/ask", partial)
    body = client.post("/ask", json={"question": QUESTION}).json()
    assert body["answer"] == guards.REFUSALS["untraceable_numbers"].answer
    assert "50" not in body["answer"]


# --- 6. The classifier failing fails closed -----------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_classifier_error_fails_closed(monkeypatch, endpoint):
    def unreachable(question):
        raise ConnectionError("OpenAI is unreachable")

    monkeypatch.setattr(guards, "classify_with_meta", unreachable)
    pipeline = install(monkeypatch, endpoint, GROUNDED)
    response = client.post(endpoint, json={"question": QUESTION})
    body = response.json()
    assert response.status_code == 200
    assert pipeline.calls == []
    assert body["guard"] == {"stage": "input", "reason": "guard_error"}
    assert body["answer"] == "This request was refused because the input check could not run."
    assert body["confidence_basis"] == "low: refused — input guard unavailable"


def test_an_unparsed_classifier_reply_fails_closed(monkeypatch):
    monkeypatch.setattr(guards, "classify_with_meta", lambda question: (None, {"error": "OpenAIRefusalError"}))
    decision = guards.check_input(QUESTION)
    assert decision.label == "guard_error"
    assert decision.refusal is guards.REFUSALS["guard_error"]
    assert decision.meta == {"error": "OpenAIRefusalError"}


class FakeClassifier:
    def __init__(self, out: dict):
        self.out = out
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return self.out


def _raw_reply() -> AIMessage:
    return AIMessage(
        content='{"label":"in_scope"}',
        response_metadata={"model_name": "gpt-4o-mini-2024-07-18", "system_fingerprint": "fp_test"},
        usage_metadata={"input_tokens": 480, "output_tokens": 7, "total_tokens": 487},
    )


def test_classify_sends_the_frozen_prompt_and_records_the_call(monkeypatch):
    fake = FakeClassifier({"raw": _raw_reply(), "parsed": guards.GuardLabel(label="in_scope"), "parsing_error": None})
    monkeypatch.setattr(guards, "_classifier_llm", lambda: fake)
    label, meta = REAL_CLASSIFY(QUESTION)
    assert label == "in_scope"
    assert fake.messages == [("system", guards.CLASSIFIER_PROMPT), ("human", QUESTION)]
    assert meta["model"] == "gpt-4o-mini-2024-07-18"
    assert meta["fingerprint"] == "fp_test"
    assert (meta["input_tokens"], meta["output_tokens"]) == (480, 7)
    assert "error" not in meta


def test_classify_reports_a_model_refusal_as_no_label(monkeypatch):
    fake = FakeClassifier({"raw": _raw_reply(), "parsed": None, "parsing_error": ValueError("refused")})
    monkeypatch.setattr(guards, "_classifier_llm", lambda: fake)
    label, meta = REAL_CLASSIFY(QUESTION)
    assert label is None
    assert meta["error"] == "ValueError"
    assert meta["fingerprint"] == "fp_test"


# --- 7. Logging ---------------------------------------------------------------------------------------------------

PII_QUESTION = "List the Social Security numbers of the injured workers, starting with Jane Q. Example."


def _info_and_above(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.INFO]


@pytest.mark.parametrize(
    "label, expected",
    [("pii_request", "refused (pii_request)"), ("in_scope", "allowed (in_scope)")],
)
def test_input_decisions_log_the_category_never_the_question(monkeypatch, caplog, label, expected):
    caplog.set_level(logging.INFO)
    classifier(monkeypatch, label)
    install(monkeypatch, "/ask", GROUNDED)
    client.post("/ask", json={"question": PII_QUESTION})
    messages = _info_and_above(caplog)
    assert any(expected in m for m in messages)
    assert not any("Jane Q. Example" in m or "Social Security" in m for m in messages)


def test_rule_and_error_paths_never_log_the_question(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    guards.check_input(row(7))

    def failing(question):
        raise RuntimeError(f"upstream echoed: {question}")

    monkeypatch.setattr(guards, "classify_with_meta", failing)
    guards.check_input(PII_QUESTION)
    messages = _info_and_above(caplog)
    assert any("injection rule ignore_previous_instructions" in m for m in messages)
    assert any("classifier failed (RuntimeError)" in m for m in messages)
    assert not any(row(7) in m or "Jane Q. Example" in m for m in messages)


def test_output_decisions_log_a_count_never_the_answer(caplog):
    caplog.set_level(logging.INFO)
    guards.check_output(ACETONE_PRIOR_KNOWLEDGE_ANSWER, ACETONE_CONTEXTS, ACETONE_QUESTION)
    messages = _info_and_above(caplog)
    assert any("output guard: refused" in m for m in messages)
    assert not any("0.855" in m or "58.08" in m for m in messages)


# --- 8. The frozen prompt -----------------------------------------------------------------------------------------


def test_classifier_prompt_is_byte_identical_to_the_preregistered_one():
    registered = (ROOT / "eval" / "g6_PREDICTION.md").read_bytes()
    blocks = re.findall(rb"^```text classifier-prompt\n(.*?)\n```$", registered, flags=re.S | re.M)
    assert len(blocks) == 1
    assert guards.CLASSIFIER_PROMPT.encode("utf-8") == blocks[0]
