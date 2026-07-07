"""Quick end-to-end sanity check of the RAG pipeline's ask().

Dev tooling — NOT part of the src/ runtime. Runs a handful of representative questions through
ask(), asserts the return shape, and prints retrieved doc_ids/pages + the answer. Includes one
known-hard multi-page case (LOTO sequence) that the naive per-page baseline is expected to fumble.

Run from the repo root:  uv run python scripts/smoke_test.py
(The project is installed editable, so `from src.pipeline import ask` resolves with no path tricks.)
"""

import logging
import os

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
load_dotenv()  # find .env from cwd (repo root)

from src.pipeline import ask
from src.retrieve import DEFAULT_K

QUESTIONS = [
    ("ammonia PEL (single-doc fact)", "What is the OSHA permissible exposure limit (PEL) for anhydrous ammonia?"),
    ("Fisher 657 torque (single-doc)", "What is the maximum torque for the Fisher 657 diaphragm casing cap screws and nuts (keys 22 and 23)?"),
    ("RMP Program 1 (conditional)", "What are the eligibility conditions for RMP Program 1?"),
    ("HARD multi-page: LOTO sequence", "What is the required sequence of actions for applying lockout/tagout before servicing equipment?"),
]


def main() -> None:
    print(f"retrieval knob: k={DEFAULT_K}\n" + "=" * 70)
    for label, q in QUESTIONS:
        res = ask(q)
        # shape assertions — ask() must always return {"answer": str, "contexts": list[str]}
        assert isinstance(res, dict) and set(res) == {"answer", "contexts"}, f"bad shape: {res.keys()}"
        assert isinstance(res["answer"], str), "answer not str"
        assert isinstance(res["contexts"], list) and all(
            isinstance(c, str) for c in res["contexts"]
        ), "contexts not list[str]"
        print(f"\n### {label}\nQ: {q}")
        print(f"retrieved (k={len(res['contexts'])}):")
        for c in res["contexts"]:
            # print the canonical "[source_doc_id=... page=...]" label line of each context
            print("   ", c.split("\n", 1)[0])
        print("ANSWER:", res["answer"][:600])

    print("\n" + "=" * 70)
    print("shape assertions passed: {'answer': str, 'contexts': list[str]}")
    tracing = os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in {"1", "true", "yes"}
    print(
        f"LangSmith tracing: {'ON' if tracing else 'OFF'} "
        f"(project={os.environ.get('LANGCHAIN_PROJECT', '<unset>')}) — traces logged if ON."
    )


if __name__ == "__main__":
    main()
