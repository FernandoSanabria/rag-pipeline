# G5 — MCP server: design (GATE 1)

Recorded 2026-10-01. This is the read-only reconnaissance for G5, reviewed and accepted at GATE 1 **before any
build code**. The companion pre-registration is [`g5_PREDICTION.md`](g5_PREDICTION.md). The contract the build
ships lands with the server as `mcp_server/CONTRACT.md`.

**Goal.** An external MCP client discovers two tools, calls them, and gets grounded results with provenance.

**Shape.** A new package, `mcp_server/`, that **calls** the frozen
[`src.retrieve.dense_search`](../src/retrieve.py) and **reuses**
[`agent.tools.lookup_document_metadata`](../agent/tools.py).
- Nothing under `src/` or `agent/` changes.
- `eval/dataset.jsonl`, the five-metric set, the `semantic_v2` namespace and k are all untouched.
- There is no eval run; the only API spend is embedding calls.

**Licensing is decided by the owner, not designed here (Option A).** `search_safety_docs` returns **full chunk
text for all 19 documents in both tiers**, and every result carries `tier` and `license`.
- This reverses the earlier no-redistribution stance *for chunk text*.
- Source PDFs stay uncommitted, because `data/**/*.pdf` is gitignored.
- The policy is a data table mapping tier to text mode, so switching to Option B or C is a one-line change.

## Decisions accepted at GATE 1

| # | Decision | Why |
|---|---|---|
| D1 | Results carry **`rank`**, not `score`. | The frozen `dense_search` drops Pinecone's score and returns only `{text, source_doc_id, page}`. Exposing a score would mean either editing `src/retrieve.py` or running a second retrieval outside it. Also, [`api/confidence.py`](../api/confidence.py) records that top-1 similarity does **not** separate correct answers from weak ones, so an external client would read a raw cosine value as confidence. |
| D2 | SDK **`mcp==1.30.0`** (FastMCP). | It's the class the spec names. It resolves against every locked version with **zero** version changes and adds 11 packages. The 2.x line renamed FastMCP to `MCPServer` and adds a second HTTP stack. |
| D3 | `k` defaults to **and** is capped at `get_settings().retrieval_k` (10 as shipped). | It's the same depth as the evaluated pipeline, and both bounds come from one source. A request above the cap is rejected, never clamped. |
| D4 | Live verification (P5) happens after the merge and is recorded in a G5-closure PR. | Render deploys only `main`. |
| D5 | Pre-registration commits stay reachable through **annotated `prereg/<gate>` tags**. | Squash-merging keeps `main` linear, and the tag keeps the cited commit resolvable for the doc-guard. |
| D6 | The HTTP step (C4) can be **severed** at GATE 2. | The budget is ≤3 build-days. The wire-smoke changes move with C4 wherever it lands. |

## R1 — Licensing surface (for the record; §2 is decided)

The `tier` and `license` values below are what the manifest records. The manifest's own `_README` says they
need manual verification ("the public/ vs raw/ folder split is a guess, not proof"). **MCP reports them
verbatim and does not certify them.**

| doc_id | tier | publisher | license |
|---|---|---|---|
| osha-1910-119 | 1 | OSHA (US Department of Labor) | public-domain |
| osha-1910-147 | 1 | OSHA (US Department of Labor) | public-domain |
| osha-1910-1000 | 1 | OSHA (US Department of Labor) | public-domain |
| controls-hazardous-energies | 1 | DIR | public-domain ⚑ |
| epa-rmp-general-guidance | 1 | US EPA | public-domain |
| epa-rmp-ammonia-refrigeration | 1 | US EPA | public-domain |
| niosh-alert-hazardous-energy | 1 | NIOSH (CDC) | public-domain |
| niosh-pocket-guide | 1 | NIOSH (CDC) | public-domain |
| osha-otm-iv-4-robots | 1 | OSHA (US Department of Labor) | public-domain |
| osha-otm-v-2-excavations | 1 | OSHA (US Department of Labor) | public-domain |
| sds-airgas-chlorine | 2 | Airgas | vendor-copyrighted |
| atlas-copco-ga-compressors | 2 | Atlas Copco | vendor-copyrighted |
| fisher-657-actuator | 2 | Emerson (Fisher) | vendor-copyrighted |
| fisher-667-actuator | 2 | Emerson (Fisher) | vendor-copyrighted |
| sds-fisher-sodium-hydroxide | 2 | Fisher Scientific (Thermo Fisher) | vendor-copyrighted |
| flowserve-mark3-pump | 2 | Flowserve | vendor-copyrighted |
| micromotion-fseries-coriolis | 2 | Emerson (Micro Motion) | vendor-copyrighted |
| sds-nutrien-anhydrous-ammonia | 2 | Nutrien | vendor-copyrighted |
| sds-sigma-aldrich-acetone | 2 | Sigma-Aldrich (Merck) | vendor-copyrighted |

**Totals: 10 × `public-domain`, 9 × `vendor-copyrighted`.**

⚑ `controls-hazardous-energies` has an unverified label:
- The manifest's notes say "Publisher/identity unconfirmed", and a `_verification_checklist` item is still open
  for it.
- Its `source_url` is a California state agency (dir.ca.gov). The federal public-domain rule for US government
  works does not cover state works.

So this `public-domain` label is unverified. This is recorded as a fact, not a recommendation.

**The tension Option A accepts.** The manifest note on `sds-airgas-chlorine` reads, verbatim:

> "Filename typo: 'Chlorin' should be 'Chlorine'. Keep gitignored; do not redistribute."

That note records the vendor's term. Under Option A the PDF stays gitignored, but this document's chunk text
**is** served by `search_safety_docs`, labelled `tier: 2` and `license: "vendor-copyrighted"`. The owner accepts
that tension. The label on every result is how a client sees it.

**The existing surfaces never return chunk text.** From [`api/schemas.py`](../api/schemas.py):
- `AskResponse` is `{answer, citations[{document, page}], confidence_score, confidence_basis}`.
- `AgentAskResponse` adds `{route, source_doc_id, routing_reason}`.

No field carries a retrieved chunk. `answer` is generated prose, which may quote short spans. The eval result
JSONs do contain verbatim chunks, but they're gitignored and never served. **`search_safety_docs` is the first
surface that returns raw chunk text.**

## R2 — Tool surface

There are two tools. `ask_safety_question`, which would wrap `src.pipeline`, is **out of scope** and recorded
as a follow-on. Both tools are annotated `readOnlyHint: true`, `idempotentHint: true`, `openWorldHint: false`.

### `search_safety_docs(query, k?, source_doc_id?)`

Input schema (normative):

```json
{"type": "object", "required": ["query"], "properties": {
  "query":         {"type": "string", "minLength": 3, "maxLength": 1000},
  "k":             {"type": "integer", "minimum": 1, "maximum": 10, "default": 10},
  "source_doc_id": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null}}}
```

- `query` reuses the `/ask` constraint (`api.schemas.Question`): whitespace is stripped and the length must be
  3–1000 characters.
- For `k`, both `default` and `maximum` are `get_settings().retrieval_k` from
  [`src/config.py`](../src/config.py), which is 10 as shipped. **A request above the cap is rejected by schema
  validation, not clamped.** In the contract's words: "k defaults to and is capped at the evaluated retrieval
  depth; the cap moves with the configured depth, never silently clamps a request."
- `source_doc_id` restricts the search to one manifest document. It's checked against the manifest **before**
  retrieval.

Output schema (normative; every field is required):

```json
{"type": "object", "required": ["results"], "properties": {"results": {"type": "array", "items": {
  "type": "object",
  "required": ["kind", "text", "source_doc_id", "title", "publisher", "page", "tier", "license", "rank"],
  "properties": {
    "kind":          {"enum": ["document", "computed"]},
    "text":          {"type": "string"},
    "source_doc_id": {"type": "string"},
    "title":         {"type": "string"},
    "publisher":     {"type": "string"},
    "page":          {"type": "integer", "minimum": 1},
    "tier":          {"type": "integer"},
    "license":       {"type": "string"},
    "rank":          {"type": "integer", "minimum": 1}}}}}}
```

- `title`, `publisher`, `tier` and `license` come from `agent.tools.lookup_document_metadata`, which reads the
  manifest. They never come from a filename.
- `text` is `policy.render_text(tier, chunk_text)` (see the policy table below).
- `page` comes from chunk metadata. It's always set at ingest and is 1-based. Pinecone returns it as a float,
  so it's coerced to int.
- `rank` is the 1-based position in `dense_search`'s score-descending order (D1).
- An empty `results` list is a success, not an error.

### `lookup_document_metadata(source_doc_id)`

The input is `{"source_doc_id": string}` (required). The output is **exactly the agent tool's shape**:
`{doc_id, title, publisher, tier, license, source_url, revision_date}`. `revision_date` is always `null`,
because the manifest records no revision date and the tool never guesses one.

### Why `kind` exists

`kind` is a reserved enum:
- `document` means a verbatim chunk of a cited page.
- `computed` is reserved for tool-derived values.

G1 showed what happens when that distinction isn't carried. [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md)
records it under G1, "misattribution of tool-derived values". `/ask/agent` returned 35.61 ppm at confidence
0.9, with nine citations to SDS pages that contain neither the value nor a conversion factor. The number came
from `ConvertExposureLimit`.

Only `document` is emitted today. The field exists so that a client which later receives `computed` rows can
tell a calculation from a quote. It's also why the convert and compare tools are **not** exported until
`computed` rows carry their own attribution.

### Error contract

Every tool error is a `CallToolResult` with **`isError: true`**, one text block, no `structuredContent` and
**zero rows**. FastMCP 1.x formats the text as `Error executing tool <tool>: <detail>`.
- When our code raises the error, `<detail>` is JSON: `{"code": …, "message": …, …}`. The tool bodies catch
  every exception, so the text a client sees never contains raw exception text or a traceback. The full
  exception is logged to stderr.
- When a request violates the advertised input schema (k outside 1 to the cap, a query of the wrong length, a
  missing field), the SDK rejects it before our code runs. In that case `<detail>` is the SDK's validation
  message.

| `code` | When | Notes |
|---|---|---|
| `unknown_source_doc_id` | The lookup id isn't in the manifest, or the search filter id isn't in the manifest. | The filter is checked **before** retrieval, so no embedding call is made. |
| `retrieval_failed` | `dense_search` raised. | The message names the exception class only. |
| `provenance_unavailable` | A retrieved chunk's doc, page or tier can't be resolved against the manifest or the policy table. | **The whole call fails closed**, with no partial rows. |
| `internal_error` | Anything else. | Class name only. |

Unknown tool names and malformed JSON-RPC are protocol errors, handled by the SDK.

### Policy table (`mcp_server/policy.py`, data rather than a toggle)

```python
TEXT_POLICY = {1: "full", 2: "full"}   # Option A (owner's decision); B/C = change a value here
EXCERPT_CHARS = 280
```

`render_text(tier, text)` behaves by mode:
- `full` returns the text.
- `excerpt` returns the first `EXCERPT_CHARS` characters followed by "…".
- `none` returns `""`.

A tier missing from the table raises, which surfaces as `provenance_unavailable`. There's no env var and no
runtime switch. If a tier is ever moved to `excerpt` or `none`, the same change must add a per-result
`text_mode` field, for the same legibility reason `kind` exists.

## R3 — Transport and hosting

**SDK.** `mcp==1.30.0`, using `mcp.server.fastmcp.FastMCP("equip-docs-rag", instructions=…)`. It requires
Python >=3.10, and the project runs 3.11. I checked compatibility with fastapi 0.139.0 by resolving with every
locked version held fixed:
- 0 locked versions change.
- 11 packages are added: `mcp`, `sse-starlette`, `python-multipart`, `pyjwt`, `cryptography`, `cffi`,
  `pycparser`, `jsonschema`, `jsonschema-specifications`, `referencing`, `rpds-py`.
- It reuses the locked starlette 1.3.1, httpx, httpx-sse, pydantic, pydantic-settings, uvicorn and anyio.
- It supports protocol versions up to `2025-11-25`, which use the `initialize` handshake.

**Step 1: stdio.** Run `uv run python -m mcp_server`. `__main__.py` calls `load_dotenv()`, then `mcp.run()`.
- stdout is the protocol channel, so nothing may print there. FastMCP logs to stderr.
- `load_dotenv()` mirrors [`api/main.py`](../api/main.py), so the keys load regardless of the launching client's
  working directory.
- Desktop clients use:
  `{"command": "uv", "args": ["--directory", "<abs repo path>", "run", "python", "-m", "mcp_server"]}`.

**Async tools.** Both tools are `async def` and run `dense_search` through `anyio.to_thread.run_sync`. FastMCP
1.30.0 calls sync tools directly on the event loop. On the shared FastAPI app, that would stall `/health` and
`/ask` for the length of an embedding plus Pinecone round-trip.

**Step 2: streamable HTTP at exactly `/mcp`**, only after stdio is green. This is C4, which can be severed.
- `stateless_http=True` and `json_response=True`. Each POST gets one JSON response, with no session affinity
  (the free-tier instance restarts), and it can be probed with curl and jq.
- An explicit `TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=[...])`, where
  the list is `"equip-docs-rag-api.onrender.com"`, `"localhost:*"` and `"127.0.0.1:*"`. Without it, FastMCP
  auto-enables localhost-only protection, and Render's Host header would get **421**.
- **A Route, not a Mount.** The SDK's `/mcp` Route is added to FastAPI's router. `Mount("/mcp")` would
  307-redirect `/mcp` to `/mcp/`, and a root Mount would take over FastAPI's JSON 404s.
- A FastAPI `lifespan` runs `mcp.session_manager.run()`, because a mounted sub-app's lifespan never runs.
- An **eager import** in `api/main.py`, because a route can't be lazy. If the image is missing the package, the
  new image dies on boot and Render keeps serving the previous image, so `/ask` stays up. `/mcp` then 404s,
  which the wire-smoke catches.
- No auth. The tools are read-only and cost one embedding per call, which is the same exposure class as
  `/ask`. Recorded as a follow-on.

**What `/mcp` means for the wire-smoke**
([`post-deploy-wire-smoke.yml`](../.github/workflows/post-deploy-wire-smoke.yml)):
1. `mcp_server/**` joins the push `paths`. Otherwise a change to that package alone would never trigger the
   smoke, which is the 404 lesson again.
2. `hit()` takes an optional Accept header.
3. A new probe POSTs `initialize` (`protocolVersion: "2025-11-25"`) to `/mcp`, with the shape check
   `.result.serverInfo.name == "equip-docs-rag"`.
4. `classify()` treats **406** (Accept negotiation) and **421** (Host allow-list) as `hard`. Both are
   deterministic configuration signatures, never transient. Today they'd count as `retry`, use up the 600 s
   budget, and fail with a misleading "did not reach 200".
5. The dispatch-only negative proof gains an MCP path: `initialize` sent without the Accept header returns
   406, and the poll must return non-zero.

## R4 — Determinism and the mock seam

[`dense_search`](../src/retrieve.py) is a deterministic function of:
- `(query, k, source_doc_id)`;
- the settings `retrieval_namespace` and `index_name`, taken from the environment;
- the index state;
- **the query embedding**.

The embedding is the one input that isn't bit-reproducible, because it's a remote OpenAI call. Live parity
could therefore, in principle, flip near-tied ranks, so P2 records a direct-vs-direct control to attribute any
difference. `_embedder()` and `_index()` are cached client singletons that hold no per-call state, and
`@traceable` doesn't affect the output.

**There is one mock seam:** `mcp_server.server.dense_search`. That's the same convention as
`agent.graph.dense_search` in [`tests/test_agent_graph.py`](../tests/test_agent_graph.py). The tool calls it with
exactly `(query, k=k, source_doc_id=source_doc_id)`, so an unfiltered call is byte-identical to the v4 direct
path.

## R5 — Hermetic tests (`tests/test_mcp_server.py`; no secrets, no network)

The tests use the in-memory client (`mcp.shared.memory.create_connected_server_and_client_session`), mock
`dense_search` at the seam, and read the committed manifest locally.
1. Discovery lists exactly 2 tools, and their schemas and annotations match R2. `k`'s `default` and `maximum`
   both equal the `Settings.retrieval_k` field default (10).
2. Provenance: a fake returns one chunk for each of the 19 manifest docs. Every result must have:
   - non-empty text equal to the chunk;
   - `kind == "document"`;
   - the right `rank`;
   - tier, license, title and publisher equal to the manifest.

   No title may equal a manifest filename or end in `.pdf`.
3. The configured policy is `{1: "full", 2: "full"}`.
4. The policy table is live: patching it to `excerpt`, then to `none`, changes tier-2 text while the provenance
   fields stay present.
5. `source_doc_id` and `k` reach `dense_search` unchanged, with `None` when `source_doc_id` is omitted. **An
   omitted `k` arrives as 10.**
6. An unknown id returns `unknown_source_doc_id`, for both the lookup and the search filter, and `dense_search`
   is **not** called.
7. `k = 11` and `k = 0` are rejected (not clamped), and `dense_search` is not called.
8. When `dense_search` raises, the result is `retrieval_failed` with zero rows. The exception text and
   "Traceback" are both absent.
9. An unresolvable chunk (an unknown doc, or a missing page) returns `provenance_unavailable` with zero rows.
10. The lookup output equals `agent.tools.lookup_document_metadata(...)`.
11. *(C4)* `/mcp` over FastAPI's TestClient, with the lifespan running and Host `localhost:8000`: `initialize`
    returns 200 with the expected `serverInfo`.
12. *(C4)* The Render host gets 200 and a foreign host gets 421. FastAPI's JSON 404 for unknown routes is
    unchanged.

## Out of scope and follow-ons

**Out of scope** (stop and ask before doing any of these):
- a third tool;
- any change under `src/` or `agent/`;
- any eval run;
- any change to the namespace or k;
- any runtime toggle for the text policy beyond the data table.

**Follow-ons**, to be recorded in the G5 entry of [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) when it's
written:
- `ask_safety_question` is deferred.
- The convert and compare tools need `kind: "computed"` attribution before they can be exported.
- `/mcp` has no auth.
- CI can't see a missing `COPY`. A candidate fix is an in-image import step in CI.
