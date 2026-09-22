"""Agent tools — plain importable functions + Pydantic arg schemas (G1 tool-calling loop).

These are the SOURCE-OF-TRUTH tool implementations, deliberately dependency-light (no LangChain / LangGraph
imports) so the G5 MCP server can re-export them directly. The agent graph binds the Pydantic *arg schemas*
to the model via `bind_tools` and dispatches each tool call by name to these functions
(`agent/graph.py:tool_exec_node`), validating args through the same schema.

Chemistry (`convert_exposure_limit`): ppm <-> mg/m3 depends on the substance's molar mass and a molar gas
volume. We use Vm = 24.45 L/mol (25 C, 1 atm) — the NIOSH/OSHA convention. The conversion is physically
meaningful ONLY for a gas/vapor; particulates/mists (e.g. sodium hydroxide) have no ppm and are rejected
rather than guessed. Molar masses for the corpus substances are curated below from authoritative physical
constants (NIST / NIOSH Pocket Guide / SDS Section 9); a caller may override with an explicit `molar_mass`.

Safety-corpus rule: every failure mode (unknown substance, non-gas, missing molar mass, bad units, unknown
doc) raises `ToolError` — the caller records it and answers without the tool. A tool NEVER returns a guessed
number. A wrong exposure limit is the failure this whole system is built to avoid.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

# Molar gas volume at 25 C, 1 atm (the NIOSH/OSHA convention for ppm<->mg/m3).
MOLAR_VOLUME_L_PER_MOL = 24.45

# Curated molar masses (g/mol) for the corpus substances, with phase. "gas"/"vapor" => ppm<->mg/m3 valid;
# any other phase => conversion is not physically meaningful and is REJECTED (present so we refuse, not guess).
MOLAR_MASS: dict[str, tuple[float, str]] = {
    "ammonia": (17.03, "gas"),
    "anhydrous ammonia": (17.03, "gas"),
    "chlorine": (70.90, "gas"),
    "acetone": (58.08, "vapor"),
    "sodium hydroxide": (40.00, "particulate"),
}


class ToolError(Exception):
    """A tool cannot produce an honest result (unknown substance, non-gas, bad args, unknown doc).

    `tool_exec_node` catches this, records it in `trace_notes`, and continues to generation WITHOUT a tool
    result — never a fabricated value.
    """


# --- Tool 1: exposure-limit unit conversion -------------------------------------------------------
class ConvertExposureLimit(BaseModel):
    """Convert an airborne exposure limit between ppm and mg/m3 for a GAS or VAPOR.

    ppm<->mg/m3 depends on molar mass, so this is real chemistry, not arithmetic the model should do in its
    head. Use ONLY when the source gives one unit and the answer needs the other. Not valid for
    particulates/mists (e.g. sodium hydroxide).
    """

    value: float = Field(description="the numeric exposure-limit value to convert")
    from_unit: Literal["ppm", "mg/m3"] = Field(description="the unit of `value`")
    to_unit: Literal["ppm", "mg/m3"] = Field(description="the unit to convert to")
    substance: Optional[str] = Field(
        default=None, description="substance name (e.g. 'ammonia', 'chlorine', 'acetone') for molar-mass lookup")
    molar_mass_g_per_mol: Optional[float] = Field(
        default=None, description="explicit molar mass (g/mol); overrides the table, required if substance is unknown")


def _resolve_molar_mass(substance: Optional[str], molar_mass: Optional[float]) -> tuple[float, str]:
    if molar_mass is not None:
        if molar_mass <= 0:
            raise ToolError(f"molar_mass_g_per_mol must be positive, got {molar_mass}")
        return float(molar_mass), "caller-supplied molar mass"
    if not substance:
        raise ToolError("provide either `substance` or `molar_mass_g_per_mol`")
    key = substance.strip().lower()
    if key not in MOLAR_MASS:
        raise ToolError(f"unknown substance {substance!r}; supply molar_mass_g_per_mol to convert")
    mw, phase = MOLAR_MASS[key]
    if phase not in ("gas", "vapor"):
        raise ToolError(f"{substance} is a {phase}; ppm<->mg/m3 is only valid for gases/vapors")
    return mw, f"curated molar mass for {key} ({mw} g/mol)"


def convert_exposure_limit(
    value: float,
    from_unit: str,
    to_unit: str,
    substance: Optional[str] = None,
    molar_mass_g_per_mol: Optional[float] = None,
) -> dict:
    """Convert `value` from `from_unit` to `to_unit`.

    Returns {value, unit, molar_mass, basis, assumptions}. Raises ToolError on unknown substance, non-gas,
    missing molar mass, or unsupported units — never guesses.
    """
    if from_unit not in ("ppm", "mg/m3") or to_unit not in ("ppm", "mg/m3"):
        raise ToolError(f"units must be ppm or mg/m3, got {from_unit!r}->{to_unit!r}")
    if from_unit == to_unit:
        return {"value": round(float(value), 4), "unit": to_unit, "molar_mass": None,
                "basis": "identity (from_unit == to_unit)", "assumptions": None}
    mw, basis = _resolve_molar_mass(substance, molar_mass_g_per_mol)
    if from_unit == "ppm":  # ppm -> mg/m3
        out = value * mw / MOLAR_VOLUME_L_PER_MOL
    else:  # mg/m3 -> ppm
        out = value * MOLAR_VOLUME_L_PER_MOL / mw
    return {"value": round(out, 4), "unit": to_unit, "molar_mass": mw, "basis": basis,
            "assumptions": f"Vm={MOLAR_VOLUME_L_PER_MOL} L/mol (25 C, 1 atm)"}


# --- Tool 2: document metadata lookup -------------------------------------------------------------
_DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "data" / "manifest.json"


class LookupDocumentMetadata(BaseModel):
    """Look up provenance for a corpus document by its source_doc_id: title, publisher, license tier.

    Use for provenance / "who published this, under what license" questions that need no retrieval.
    """

    source_doc_id: str = Field(description="the document id, e.g. 'sds-sigma-aldrich-acetone'")


def _load_docs() -> list[dict]:
    path = Path(os.environ.get("MANIFEST_PATH", str(_DEFAULT_MANIFEST)))
    return json.loads(path.read_text(encoding="utf-8")).get("docs", [])


def lookup_document_metadata(source_doc_id: str) -> dict:
    """Return {doc_id, title, publisher, tier, license, source_url, revision_date} from the manifest.

    `revision_date` is ALWAYS None: the manifest records no revision date, and this is a safety corpus —
    the tool returns null rather than fabricate one (CLAUDE.md: never fabricate provenance). Raises
    ToolError on an unknown source_doc_id.
    """
    for d in _load_docs():
        if d.get("doc_id") == source_doc_id:
            return {
                "doc_id": source_doc_id,
                "title": d.get("title"),
                "publisher": d.get("publisher"),
                "tier": d.get("tier"),
                "license": d.get("license"),
                "source_url": d.get("source_url"),
                "revision_date": None,  # not recorded in the manifest — never guessed
            }
    raise ToolError(f"unknown source_doc_id {source_doc_id!r}")


# --- Tool 3 (optional): threshold comparison — a thin wrapper over convert ------------------------
class CompareThresholds(BaseModel):
    """Compare two exposure limits by normalising both to mg/m3 first (needs substance/molar mass for ppm)."""

    value_a: float = Field(description="first value")
    unit_a: Literal["ppm", "mg/m3"] = Field(description="unit of the first value")
    value_b: float = Field(description="second value")
    unit_b: Literal["ppm", "mg/m3"] = Field(description="unit of the second value")
    substance: Optional[str] = Field(default=None, description="substance name for molar-mass lookup")
    molar_mass_g_per_mol: Optional[float] = Field(default=None, description="explicit molar mass override (g/mol)")


def compare_thresholds(
    value_a: float,
    unit_a: str,
    value_b: float,
    unit_b: str,
    substance: Optional[str] = None,
    molar_mass_g_per_mol: Optional[float] = None,
) -> dict:
    """Normalise both limits to mg/m3 and compare. Returns {a_mg_m3, b_mg_m3, relation, assumptions}.

    Reuses `convert_exposure_limit` (so all its ToolError guards apply). `relation` is 'a<b' / 'a>b' / 'a==b'.
    """
    a = convert_exposure_limit(value_a, unit_a, "mg/m3", substance, molar_mass_g_per_mol)["value"]
    b = convert_exposure_limit(value_b, unit_b, "mg/m3", substance, molar_mass_g_per_mol)["value"]
    relation = "a==b" if a == b else ("a<b" if a < b else "a>b")
    return {"a_mg_m3": a, "b_mg_m3": b, "relation": relation,
            "assumptions": f"Vm={MOLAR_VOLUME_L_PER_MOL} L/mol (25 C, 1 atm)"}


# --- Registry: schemas for bind_tools, functions for manual dispatch ------------------------------
# The Pydantic class NAME is the tool name the model calls; tool_exec_node maps it back to the function.
TOOL_SCHEMAS: list[type[BaseModel]] = [ConvertExposureLimit, LookupDocumentMetadata, CompareThresholds]
TOOL_FUNCS: dict[str, Callable[..., dict]] = {
    "ConvertExposureLimit": convert_exposure_limit,
    "LookupDocumentMetadata": lookup_document_metadata,
    "CompareThresholds": compare_thresholds,
}
_SCHEMA_BY_NAME: dict[str, type[BaseModel]] = {s.__name__: s for s in TOOL_SCHEMAS}


def run_tool(name: str, args: dict) -> dict:
    """Validate `args` against the named tool's Pydantic schema, then call its plain function.

    Raises ToolError on an unknown tool name or on validation failure (unparseable args = a tool failure,
    never a blind retry). This is the single dispatch point tool_exec_node uses.
    """
    schema = _SCHEMA_BY_NAME.get(name)
    fn = TOOL_FUNCS.get(name)
    if schema is None or fn is None:
        raise ToolError(f"unknown tool {name!r}")
    try:
        validated = schema(**args)
    except Exception as exc:  # pydantic ValidationError etc. -> treat as a tool failure, do not retry
        raise ToolError(f"invalid arguments for {name}: {exc}") from exc
    return fn(**validated.model_dump())
