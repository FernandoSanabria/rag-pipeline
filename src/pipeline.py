def ask(question: str) -> dict:
    """Empty-pipeline stub.

    Exists only to produce the ~0 RAGAS baseline the real pipeline must beat.
    No retrieval or generation yet — always returns an empty answer and no contexts.
    """
    return {"answer": "", "contexts": []}
