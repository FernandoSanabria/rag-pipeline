"""Capability eval runner — G1 tool loop, SEPARATE from the frozen 28-row eval.

Runs `agent.graph.ask` (PIPELINE=agent) over `eval/capability_set.jsonl` — conversion questions the corpus
does NOT pre-tabulate — and scores them with the same five RAGAS metrics / pinned version / wrappers as
`eval/run_eval.py`. It exists because `run_eval.py:20` hardcodes DATASET_PATH (and is out of scope to edit),
so this points the identical scoring construction at a different file.

These rows are a CAPABILITY CHECK, explicitly NOT comparable to the 28-row history (different set). Each row's
trace_notes is inspected so we can confirm the tool actually fired. Result JSON lands in eval/results/
(gitignored). Run: `uv run python scripts/run_capability_eval.py`.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_PATH = REPO_ROOT / "eval" / "capability_set.jsonl"
RESULTS_DIR = REPO_ROOT / "eval" / "results"

load_dotenv(REPO_ROOT / ".env")


def _load(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main() -> None:
    import ragas
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.dataset_schema import SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        answer_correctness,
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    from agent.graph import ask  # PIPELINE=agent path (the tool loop lives here)
    from src.config import get_settings
    from src.generate import generation_backends, reset_generation_backends

    rows = _load(CAPABILITY_PATH)
    print(f"ragas {ragas.__version__} — capability eval over {len(rows)} rows (NOT the frozen 28)")

    reset_generation_backends()
    samples, fired = [], []
    for row in rows:
        q = row["question"]
        result = ask(q)
        # ask() returns the frozen contract; re-run to inspect trace_notes would double cost, so we surface
        # tool use from chunks instead: a tool chunk has source_doc_id starting "tool:".
        used_tool = any(str(c.get("source_doc_id", "")).startswith("tool:") for c in result["chunks"])
        fired.append(used_tool)
        print(f"  - {q[:60]!r} tool_used={used_tool} answer={result['answer'][:80]!r}")
        samples.append(SingleTurnSample(
            user_input=q, response=result["answer"],
            retrieved_contexts=result["contexts"], reference=row.get("reference")))

    llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4o-mini", temperature=0))
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(model="text-embedding-3-small"))
    metrics = [faithfulness, answer_relevancy, context_precision, context_recall, answer_correctness]
    result = evaluate(dataset=EvaluationDataset(samples=samples), metrics=metrics,
                      llm=llm, embeddings=embeddings, raise_exceptions=False)

    df = result.to_pandas()
    print("\nAggregate (capability set — NOT comparable to the 28-row ledger):")
    for m in metrics:
        col = m.name
        if col in df.columns:
            s = df[col].dropna()
            print(f"  {col:20s} = {float(s.mean()) if len(s) else None}  (n={len(s)})")
    print(f"\ntool fired on {sum(fired)}/{len(fired)} capability rows")
    print(f"generation backends: {generation_backends()}")
    print(f"retrieval: namespace={get_settings().retrieval_namespace} k={get_settings().retrieval_k}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    metric_names = [m.name for m in metrics]

    def _cell(i, col):
        if col not in df.columns:
            return None
        v = df.iloc[i][col]
        return None if v != v else float(v)  # NaN -> None

    # (1) full result -> gitignored eval/results/ (contains answer + retrieved contexts; debug only)
    out = RESULTS_DIR / f"capability_eval_{ts}.json"
    out.write_text(json.dumps({
        "kind": "capability_set", "ragas_version": ragas.__version__, "timestamp_utc": ts,
        "n_rows": len(rows), "tool_fired": sum(fired),
        "generation_backends": generation_backends(),
        "per_row": json.loads(df.to_json(orient="records")),
    }, indent=2), encoding="utf-8")

    # (2) DERIVED, metrics-only -> COMMITTED eval/capability_perrow_metrics.json. Per-row scores + fingerprint
    #     + per-metric n + tool_fired, with NO answer text and NO retrieved contexts (no vendor content
    #     redistributed). Makes the ledger row recomputable — same precedent as eval/likeforlike_perrow_metrics.json.
    perrow = [{"i": i, "question": rows[i]["question"], "tool_fired": bool(fired[i]),
               **{col: _cell(i, col) for col in metric_names}} for i in range(len(rows))]
    committed = {
        "kind": "capability_set_perrow_metrics",
        "note": "Derived metrics only (no answer text, no retrieved contexts). Capability set — NOT comparable to the 28-row ledger.",
        "ragas_version": ragas.__version__, "timestamp_utc": ts,
        "generation_backends": generation_backends(),
        "retrieval_namespace": get_settings().retrieval_namespace, "retrieval_k": get_settings().retrieval_k,
        "n_rows": len(rows), "tool_fired": sum(fired),
        "per_metric_n": {col: int(df[col].notna().sum()) if col in df.columns else 0 for col in metric_names},
        "aggregate": {col: (float(df[col].dropna().mean()) if col in df.columns and len(df[col].dropna()) else None) for col in metric_names},
        "rows": perrow,
    }
    (REPO_ROOT / "eval" / "capability_perrow_metrics.json").write_text(json.dumps(committed, indent=2), encoding="utf-8")
    print(f"wrote {out.relative_to(REPO_ROOT)} (gitignored) + eval/capability_perrow_metrics.json (committed)")


if __name__ == "__main__":
    main()
