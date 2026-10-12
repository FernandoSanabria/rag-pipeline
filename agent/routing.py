"""G8 routing policy for the agent path's generation (eval/g8_design.md R3, policy A).

A deterministic policy on signals that exist before generation, not a quality signal. A request is eligible when:
- the router scoped it to one document (`route == "source_scoped"`; a scoped fallback downgrades the route);
- no tool ran;
- the question matches neither the G12 comparison gate nor the G10b exposure-limit pattern.

Eligible requests generate with `CHEAP_MODEL` through the G11 seam (`generate_with(…, model_id=CHEAP_MODEL)`).
`ROUTING_ENABLED` ships False: the pre-registered shipping rule (eval/g8_PREDICTION.md, P8) keeps routing off unless
quality, refusal and a 30% cost delta across the 28 all hold, and even then turning it on is a stop-and-ask.
"""

from agent.review import EXPOSURE_LIMIT_PATTERN

ROUTING_ENABLED = False
CHEAP_MODEL = "us.amazon.nova-micro-v1:0"


def enabled() -> bool:
    return ROUTING_ENABLED


def eligible(state) -> tuple[bool, str]:
    """(eligible, reason) for a state about to generate. Reads only the route, the tool results and the question."""
    from agent.graph import COMPARISON_GATE  # deferred: agent.graph imports this module

    question = state["question"] or ""
    if state["route"] != "source_scoped":
        return False, f"route={state['route'] or 'none'}"
    if state["tool_results"]:
        return False, "a tool ran"
    if COMPARISON_GATE.search(question):
        return False, "comparison"
    if EXPOSURE_LIMIT_PATTERN.search(question):
        return False, "exposure_limit_named"
    return True, "source_scoped, no tool, no comparison, no exposure limit"
