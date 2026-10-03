"""Licensing policy for search_safety_docs results: which text each tier's results carry.

This is DATA, not a runtime toggle — there is no env var or flag. The configured mode is the owner's decision
(Option A): full chunk text for both tiers, with every result labelled with its tier and license. Option B or
C is a one-value change to TEXT_POLICY. If a tier is ever moved to "excerpt" or "none", the same change must
add a per-result `text_mode` field, so a client can tell a truncated text from a complete one — the same
legibility argument as the `kind` field (CONTRACT.md, "Licensing policy").
"""

from typing import Literal

TextMode = Literal["full", "excerpt", "none"]

TEXT_POLICY: dict[int, TextMode] = {1: "full", 2: "full"}
EXCERPT_CHARS = 280


def render_text(tier: int, text: str) -> str:
    """Apply the tier's text mode. A tier missing from TEXT_POLICY raises KeyError; the caller fails closed."""
    mode = TEXT_POLICY[tier]
    if mode == "full":
        return text
    if mode == "excerpt":
        return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + "…"
    return ""
