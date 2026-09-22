"""Unit tests for agent/tools.py — deterministic, no network, no LLM.

Bad inputs and unknown/invalid substances are covered because the graph relies on ToolError to refuse
rather than fabricate: on a safety corpus, a guessed exposure limit is the failure mode to avoid.
"""
import pytest

from agent.tools import (
    ToolError,
    compare_thresholds,
    convert_exposure_limit,
    lookup_document_metadata,
    run_tool,
)

# --- convert_exposure_limit ---


def test_convert_ppm_to_mg_m3_ammonia():
    out = convert_exposure_limit(50, "ppm", "mg/m3", substance="ammonia")
    assert out["value"] == pytest.approx(34.8262, abs=1e-3)  # 50 * 17.03 / 24.45
    assert out["unit"] == "mg/m3"
    assert out["molar_mass"] == 17.03


def test_convert_roundtrip_recovers_input():
    fwd = convert_exposure_limit(50, "ppm", "mg/m3", substance="ammonia")["value"]
    back = convert_exposure_limit(fwd, "mg/m3", "ppm", substance="ammonia")["value"]
    assert back == pytest.approx(50, abs=1e-2)


def test_convert_identity_same_unit_needs_no_molar_mass():
    out = convert_exposure_limit(3.0, "ppm", "ppm", substance="ammonia")
    assert out["value"] == 3.0
    assert out["molar_mass"] is None


def test_convert_molar_mass_override_for_unknown_substance():
    out = convert_exposure_limit(1, "ppm", "mg/m3", molar_mass_g_per_mol=24.45)
    assert out["value"] == pytest.approx(1.0, abs=1e-6)  # MW == Vm -> 1:1
    assert "caller-supplied" in out["basis"]


def test_convert_unknown_substance_raises():
    with pytest.raises(ToolError, match="unknown substance"):
        convert_exposure_limit(1, "ppm", "mg/m3", substance="unobtainium")


def test_convert_particulate_rejected():
    with pytest.raises(ToolError, match="only valid for gases"):
        convert_exposure_limit(2, "mg/m3", "ppm", substance="sodium hydroxide")


def test_convert_missing_molar_mass_raises():
    with pytest.raises(ToolError, match="provide either"):
        convert_exposure_limit(1, "ppm", "mg/m3")


def test_convert_bad_unit_raises():
    with pytest.raises(ToolError, match="units must be"):
        convert_exposure_limit(1, "ppm", "percent", substance="ammonia")


# --- lookup_document_metadata ---


def test_metadata_known_doc_returns_provenance():
    out = lookup_document_metadata("sds-sigma-aldrich-acetone")
    assert out["title"]
    assert out["publisher"]
    assert out["tier"] in (1, 2)
    assert out["license"]
    assert out["revision_date"] is None  # not recorded in the manifest — never fabricated


def test_metadata_unknown_doc_raises():
    with pytest.raises(ToolError, match="unknown source_doc_id"):
        lookup_document_metadata("not-a-real-doc")


# --- compare_thresholds ---


def test_compare_normalizes_units_and_orders():
    # 25 ppm ammonia = 25 * 17.03 / 24.45 = 17.41 mg/m3 < 18 mg/m3 -> a<b
    out = compare_thresholds(25, "ppm", 18, "mg/m3", substance="ammonia")
    assert out["relation"] == "a<b"
    assert out["a_mg_m3"] == pytest.approx(17.41, abs=0.1)


# --- run_tool dispatch (schema validation is the boundary) ---


def test_run_tool_dispatches_and_validates():
    out = run_tool("LookupDocumentMetadata", {"source_doc_id": "sds-sigma-aldrich-acetone"})
    assert out["title"]


def test_run_tool_unknown_tool_raises():
    with pytest.raises(ToolError, match="unknown tool"):
        run_tool("NoSuchTool", {})


def test_run_tool_invalid_args_raises():
    with pytest.raises(ToolError, match="invalid arguments"):
        run_tool("ConvertExposureLimit",
                 {"value": "not-a-number", "from_unit": "ppm", "to_unit": "mg/m3", "substance": "ammonia"})
