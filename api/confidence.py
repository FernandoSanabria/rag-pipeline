"""Refusal-gated, 2-tier confidence for the API.

Design (see the Step-5 threshold probe): a read-only probe showed that top-1 retrieval similarity does
NOT separate correct from weak answers on this corpus (band 0.53–0.76; weak-answer median ≥ correct
median). So the similarity-graded tier was dropped — there is no honest threshold. Confidence is
therefore two tiers:

  refused / empty  -> LOW
  answered         -> HIGH

The refusal gate is anchored to the generator's ACTUAL clean-refusal output — normalized
WHOLE-ANSWER equality, NOT a substring match. That distinction matters: the generator sometimes emits
a real, grounded partial answer and THEN appends the refusal sentence (e.g. it gives the EPA RMP
endpoint for ammonia, then notes the NIOSH IDLH value is absent). A substring/ends-with test would
mis-score that partial answer LOW and discard the citation it earned; whole-answer equality does not.

The float anchors (0.0 / 0.25 / 0.9) are tier LABELS, not calibrated probabilities. `confidence_basis`
carries the human-readable reason and never claims the answer is correct — only how it was produced.
"""

# The exact sentence the generator replies with when the answer is absent (src/generate.py
# SYSTEM_PROMPT), normalized: lower-cased, trailing period stripped.
REFUSAL = "the provided context does not contain the answer"


def is_refusal(answer: str) -> bool:
    """True only when the WHOLE answer is the refusal sentence (normalized).

    A partial answer that merely contains or ends with the sentence is NOT a refusal.
    """
    return answer.strip().rstrip(".").strip().lower() == REFUSAL


def score_confidence(answer: str) -> tuple[float, str]:
    """Return (confidence_score, confidence_basis) for a generated answer."""
    a = answer.strip()
    if not a:
        return 0.0, "low: no answer generated"
    if is_refusal(a):
        return 0.25, "low: refused — answer not in retrieved context"
    return 0.9, "high: answer generated from retrieved context"
