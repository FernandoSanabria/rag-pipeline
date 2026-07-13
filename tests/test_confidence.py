"""Unit tests for the refusal-gated confidence layer.

These encode the Step-5 refusal-detection bug so it can never regress: the generator emits real,
grounded PARTIAL answers that also end with the refusal sentence (acetone, IDLH). Those must NOT be
treated as refusals (they earned their citations). Strings below are the ACTUAL generator outputs
captured from eval results.
"""

from api.confidence import is_refusal, score_confidence

# --- real generator outputs (from eval/results/*.json) ---
CLEAN_REFUSAL = "The provided context does not contain the answer."

PARTIAL_ACETONE = (
    "The flash point of acetone is 0°F, as stated in the NIOSH Pocket Guide. However, the "
    "Sigma-Aldrich SDS does not provide a specific flash point value for acetone in the provided "
    "context. Therefore, the flash point from the Sigma-Aldrich SDS is not available. \n\n"
    "The provided context does not contain the answer."
)
PARTIAL_IDLH = (
    "The NIOSH IDLH (Immediately Dangerous to Life or Health) value for anhydrous ammonia is not "
    "provided in the context. However, the EPA RMP (Risk Management Program) toxic endpoint for "
    "anhydrous ammonia is 200 ppm (0.14 mg/L) as stated in the provided context. \n\n"
    "Since the NIOSH IDLH value is not available in the provided context, I cannot make a "
    "comparison. \n\nThe provided context does not contain the answer."
)

NORMAL_ANSWER = "The threshold quantity for anhydrous ammonia is 10,000 pounds."


# --- is_refusal: both sides pinned to reality ---

def test_clean_refusal_is_refusal():
    assert is_refusal(CLEAN_REFUSAL) is True


def test_partial_then_refuse_is_not_refusal():
    # regression: substring/ends-with matching would wrongly flag these
    assert is_refusal(PARTIAL_ACETONE) is False
    assert is_refusal(PARTIAL_IDLH) is False


def test_normal_answer_is_not_refusal():
    assert is_refusal(NORMAL_ANSWER) is False


def test_answer_merely_mentioning_the_phrase_is_not_refusal():
    mentions = 'The manual says "the provided context does not contain the answer" only when a value is absent.'
    assert is_refusal(mentions) is False


def test_is_refusal_normalization_edge_cases():
    assert is_refusal("The provided context does not contain the answer") is True   # no period
    assert is_refusal("  the provided context does not contain the answer.  ") is True  # whitespace/case
    assert is_refusal("The provided context does not contain the answer .") is True  # space-then-period
    assert is_refusal("the provided context does not contain the answer...") is True  # ellipsis


# --- score_confidence: 2 tiers ---

def test_clean_refusal_scores_low():
    score, basis = score_confidence(CLEAN_REFUSAL)
    assert score == 0.25
    assert basis.startswith("low")


def test_empty_answer_scores_low():
    score, basis = score_confidence("   ")
    assert score == 0.0
    assert "no answer" in basis


def test_normal_answer_scores_high():
    score, basis = score_confidence(NORMAL_ANSWER)
    assert score == 0.9
    assert basis.startswith("high")


def test_partial_then_refuse_scores_high_not_low():
    # the crux: a grounded partial answer keeps HIGH confidence (and, elsewhere, its citations)
    for partial in (PARTIAL_ACETONE, PARTIAL_IDLH):
        score, basis = score_confidence(partial)
        assert score == 0.9, f"partial answer wrongly scored {score}"
        assert basis.startswith("high")
