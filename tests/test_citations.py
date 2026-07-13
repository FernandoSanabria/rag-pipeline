"""Unit tests for metadata-derived citations.

Pages come from chunk metadata (never the model's prose); document names resolve from the manifest;
citations dedup by (document, page); a refused/empty answer yields none. Uses the REAL committed
manifest (a local file read — no network).
"""

from api.citations import _titles, derive_citations

# a real doc_id present in data/manifest.json
KNOWN_DOC = "osha-1910-119"

NORMAL_ANSWER = "The OSHA PEL for anhydrous ammonia is 50 ppm."
CLEAN_REFUSAL = "The provided context does not contain the answer."
PARTIAL_IDLH = (
    "The EPA RMP toxic endpoint for anhydrous ammonia is 200 ppm as stated in the provided "
    "context.\n\nThe provided context does not contain the answer."
)


def _chunk(doc, page, text="..."):
    return {"source_doc_id": doc, "page": page, "text": text}


def test_manifest_title_mapping_resolves():
    titles = _titles()
    assert KNOWN_DOC in titles
    assert titles[KNOWN_DOC] and titles[KNOWN_DOC] != KNOWN_DOC  # a human title, not the raw id


def test_citation_uses_manifest_title_and_metadata_page():
    cites = derive_citations(NORMAL_ANSWER, [_chunk(KNOWN_DOC, 3)])
    assert cites == [{"document": _titles()[KNOWN_DOC], "page": 3}]


def test_dedup_by_document_and_page():
    chunks = [_chunk(KNOWN_DOC, 3), _chunk(KNOWN_DOC, 3), _chunk(KNOWN_DOC, 4)]
    cites = derive_citations(NORMAL_ANSWER, chunks)
    assert len(cites) == 2
    assert {c["page"] for c in cites} == {3, 4}


def test_refusal_yields_no_citations():
    assert derive_citations(CLEAN_REFUSAL, [_chunk(KNOWN_DOC, 3)]) == []


def test_empty_answer_yields_no_citations():
    assert derive_citations("   ", [_chunk(KNOWN_DOC, 3)]) == []


def test_partial_answer_keeps_earned_citations():
    # the partial-then-refuse answer is NOT a refusal, so it keeps its citations
    cites = derive_citations(PARTIAL_IDLH, [_chunk("epa-rmp-ammonia-refrigeration", 1)])
    assert len(cites) == 1
    assert cites[0]["page"] == 1


def test_missing_or_none_page_is_skipped():
    chunks = [_chunk(KNOWN_DOC, None), {"source_doc_id": KNOWN_DOC}, _chunk(KNOWN_DOC, 5)]
    cites = derive_citations(NORMAL_ANSWER, chunks)
    assert cites == [{"document": _titles()[KNOWN_DOC], "page": 5}]


def test_unknown_doc_id_falls_back_to_id_not_filename():
    cites = derive_citations(NORMAL_ANSWER, [_chunk("some-unmapped-doc-id", 1)])
    assert cites == [{"document": "some-unmapped-doc-id", "page": 1}]  # id, never a raw filename
