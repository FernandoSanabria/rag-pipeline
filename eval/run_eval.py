"""Evaluation harness for the RAG pipeline.

Runs the (currently empty) pipeline over eval/dataset.jsonl and scores it with the four
RAGAS metrics mandated by CLAUDE.md: faithfulness, answer_relevancy, context_precision,
context_recall. Writes a timestamped JSON result to eval/results/ and traces to LangSmith.

RAGAS changes its dataset/metric API between releases, so this file targets the *installed*
version's API (verified against ragas 0.4.3): SingleTurnSample / EvaluationDataset with columns
user_input / retrieved_contexts / response / reference, and the classic evaluate() batch call.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = REPO_ROOT / "eval" / "dataset.jsonl"
RESULTS_DIR = REPO_ROOT / "eval" / "results"

# Load .env into os.environ BEFORE importing langchain/ragas so LANGCHAIN_* tracing vars
# are picked up (uv run does not auto-load .env). The project is installed editable, so `src`
# imports resolve with no sys.path manipulation.
load_dotenv(REPO_ROOT / ".env")


def _load_dataset(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> None:
    import os

    import ragas

    # Per instruction: print the installed ragas version and use THAT version's API.
    print(f"ragas version: {ragas.__version__}")

    tracing_on = os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in {"1", "true", "yes"}
    project = os.environ.get("LANGCHAIN_PROJECT", "<unset>")
    print(f"LangSmith tracing: {'ON' if tracing_on else 'OFF'} (project={project})")

    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.dataset_schema import SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    from src.pipeline import ask

    rows = _load_dataset(DATASET_PATH)
    print(f"Loaded {len(rows)} rows from {DATASET_PATH.relative_to(REPO_ROOT)}")

    samples = []
    for row in rows:
        question = row.get("question") or row.get("user_input")
        result = ask(question)
        samples.append(
            SingleTurnSample(
                user_input=question,
                response=result["answer"],
                retrieved_contexts=result["contexts"],
                reference=row.get("reference"),
            )
        )
    dataset = EvaluationDataset(samples=samples)

    judge_model = "gpt-4o-mini"
    embed_model = "text-embedding-3-small"
    llm = LangchainLLMWrapper(ChatOpenAI(model=judge_model, temperature=0))
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=embed_model))

    metrics = [faithfulness, answer_relevancy, context_precision, context_recall]

    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=llm,
        embeddings=embeddings,
        raise_exceptions=False,
    )

    df = result.to_pandas()
    metric_cols = [m.name for m in metrics]
    aggregate = {}
    for col in metric_cols:
        if col in df.columns:
            series = df[col].dropna()
            aggregate[col] = float(series.mean()) if len(series) else None
        else:
            aggregate[col] = None

    print("\nAggregate scores (empty-pipeline baseline):")
    for col in metric_cols:
        print(f"  {col:20s} = {aggregate[col]}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"eval_{timestamp}.json"
    payload = {
        "ragas_version": ragas.__version__,
        "timestamp_utc": timestamp,
        "judge_model": judge_model,
        "embedding_model": embed_model,
        "langsmith_tracing": tracing_on,
        "langsmith_project": project,
        "n_rows": len(rows),
        "metrics": metric_cols,
        "aggregate_scores": aggregate,
        "per_row": json.loads(df.to_json(orient="records")),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote results to {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
