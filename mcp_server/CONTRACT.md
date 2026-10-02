# MCP server contract — `mcp_server/` (G5)

This server exposes two read-only tools over the industrial-equipment-safety corpus. It's built on the
official MCP Python SDK (FastMCP), and the SDK version is pinned in [`pyproject.toml`](../pyproject.toml).
This file is the contract a client can rely on. The design and pre-registration behind it are in
[`eval/g5_design.md`](../eval/g5_design.md) and [`eval/g5_PREDICTION.md`](../eval/g5_PREDICTION.md).

| | |
|---|---|
| Server name | `equip-docs-rag` |
| Transport | stdio, started with `uv run python -m mcp_server` |
| Tools | `search_safety_docs` and `lookup_document_metadata` |
| Annotations (both tools) | `readOnlyHint: true`, `idempotentHint: true`, `openWorldHint: false` |

The live `tools/list` response is authoritative for the full JSON Schemas, which add titles and
descriptions. The schemas below are the normative part.

## `search_safety_docs(query, k?, source_doc_id?)`

Retrieval is the pipeline's own [`src.retrieve.dense_search`](../src/retrieve.py): the same index, the same
namespace and the same embedding model. The tool calls it with exactly
`(query, k=k, source_doc_id=source_doc_id)`.

**Input**

```json
{"type": "object", "required": ["query"], "properties": {
  "query":         {"type": "string", "minLength": 3, "maxLength": 1000},
  "k":             {"type": "integer", "minimum": 1, "maximum": 10, "default": 10},
  "source_doc_id": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null}}}
```

- **`query`** has the same constraint as `POST /ask` (`api.schemas.Question`): whitespace is stripped, and the
  result must be 3–1000 characters.
- **`k`**: k defaults to and is capped at the evaluated retrieval depth; the cap moves with the configured
  depth, never silently clamps a request. The depth is `RETRIEVAL_K` in [`src/config.py`](../src/config.py)
  (10 as shipped). Schema validation rejects any request above the cap.
- **`source_doc_id`** restricts the search to one document. It's checked against the manifest before any
  retrieval, and an unknown id returns an `unknown_source_doc_id` error.

**Output.** Every field of every result is always present:

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

| Field | Meaning |
|---|---|
| `kind` | Always `"document"` from this server, meaning the text is verbatim from the cited page. See [Why `kind` exists](#why-kind-exists). |
| `text` | The chunk text, as the [licensing policy](#licensing-policy) renders it for this tier. Today that's the full text for both tiers. |
| `source_doc_id` | The corpus document id. |
| `title`, `publisher` | Taken from the manifest ([`data/manifest.json`](../data/manifest.json)), never from a filename. |
| `page` | The 1-based page, from the chunk's metadata. |
| `tier`, `license` | Taken from the manifest and reported verbatim. See [Labels are reported, not certified](#labels-are-reported-not-certified). |
| `rank` | The 1-based position in similarity order; rank 1 is the most similar. |

An empty `results` list means a successful search found no matches. It isn't an error.

### Why `rank` and not a score

`dense_search` returns chunks in similarity order, but it doesn't return the similarity value. The value was
measured before deciding not to expose it. Top-1 similarity does not separate correct answers from weak ones on
this corpus; the Step-5 probe is recorded in [`api/confidence.py`](../api/confidence.py). A raw cosine score
handed to a client would read as a confidence value. `rank` carries the part that holds up, which is the order.

### Why `kind` exists

`kind` is a reserved enum:
- `"document"` means the text is a verbatim chunk of the cited page.
- `"computed"` is reserved for tool-derived values, such as a unit conversion. This server **never emits it**.

It exists because of a measured failure. In G1, `/ask/agent` returned a converted exposure value (35.61 ppm)
at high confidence. It cited nine SDS pages, none of which contains the value or a conversion factor. The
number came from a tool, and the citations came from the pages the model had read. This is recorded in
[`eval/KNOWN_LIMITATIONS.md`](../eval/KNOWN_LIMITATIONS.md) under G1, "misattribution of tool-derived values".

A client composing an answer from these results can't see that difference unless each result says what it is.
The field is in the schema now so that a client which later receives `"computed"` rows can tell a calculation
from a quote. For the same reason, the G1 conversion tools aren't exported until such rows carry their own
attribution.

## `lookup_document_metadata(source_doc_id)`

**Input** is `{"source_doc_id": string}` (required).

**Output** has exactly the shape the G1 agent tool returns ([`agent/tools.py`](../agent/tools.py)):

```json
{"doc_id": "…", "title": "…", "publisher": "…", "tier": 1, "license": "…", "source_url": "…", "revision_date": null}
```

`revision_date` is always `null`, because the manifest records no revision date and the tool never guesses
one. An unknown id returns an `unknown_source_doc_id` error, never a guessed record.

## Licensing policy

The policy is a data table in [`policy.py`](policy.py) that maps each tier to a text mode. There is no runtime
toggle.

| Tier | Manifest license | Text in each result |
|---|---|---|
| 1 | `public-domain` | full chunk text |
| 2 | `vendor-copyrighted` | full chunk text |

**Decision (Option A), recorded 2026-10-02.** Search results carry the full chunk text for both tiers. Every
result is labelled with its `tier` and `license`, so a client can see what it's receiving. The owner made this
decision. It reverses the earlier no-redistribution stance for chunk-level text, but source PDFs are still
never committed.

**Changing the policy** (Option B or C) means editing one value in `TEXT_POLICY`:
- `"excerpt"` returns the first 280 characters followed by "…".
- `"none"` returns an empty `text` but keeps every provenance field.

A change to `"excerpt"` or `"none"` must add a per-result `text_mode` field in the same change, so a client can
tell truncated text from complete text.

### Labels are reported, not certified

`tier` and `license` are the values recorded in the manifest. The manifest's own `_README` says those values
need manual verification. This server reports them verbatim and doesn't certify them.

## Error contract

Every tool error is a `CallToolResult` with **`isError: true`** and one text block. It has **no
`structuredContent` and returns zero rows**. The SDK formats the text as `Error executing tool <tool>: <detail>`.

- When the server raises an error, `<detail>` is a JSON object: `{"code": "…", "message": "…", …}`. The tool
  bodies catch every exception, so no raw exception text or traceback reaches the client. The full exception
  is written to the server's log on stderr.
- When a request violates the input schema (for example `k` above the cap, a query shorter than 3 characters,
  or a missing field), the SDK rejects it before the tool runs. In that case `<detail>` is the SDK's
  validation message.

| `code` | When | Notes |
|---|---|---|
| `unknown_source_doc_id` | `lookup_document_metadata` or the search filter names an id that isn't in the manifest. | The filter is checked before retrieval, so no embedding call is made. The detail echoes `source_doc_id`. |
| `retrieval_failed` | `dense_search` raised an exception (network, auth or index). | The message names the exception class only. |
| `provenance_unavailable` | A retrieved chunk's document, page or tier can't be resolved against the manifest or the policy table. | **The call fails closed:** the whole call errors and no partial rows come back, because a row without provenance would be unattributed text. |
| `internal_error` | Anything else. | The message names the exception class only. |

Unknown tool names and malformed JSON-RPC are protocol errors, and the SDK returns them.

Example, for an unknown id:

```text
isError: true
Error executing tool lookup_document_metadata: {"code": "unknown_source_doc_id", "message": "no document 'not-a-doc' in the manifest", "source_doc_id": "not-a-doc"}
```

## Running it

**Keys.** At startup the server reads `OPENAI_API_KEY` and `PINECONE_API_KEY` from the repo's `.env`. If a
variable is already set in the environment, that value is used instead. `lookup_document_metadata` needs no
network. Each search makes one embedding call and one Pinecone query.

**stdio**

```bash
uv run python -m mcp_server
```

**MCP Inspector (CLI, pinned).** The `--` separates the server command, which has its own flags, from the
Inspector's options:

```bash
npx -y @modelcontextprotocol/inspector@2.8.0 --cli .venv/bin/python -m mcp_server -- --method tools/list --format json
npx -y @modelcontextprotocol/inspector@2.8.0 --cli .venv/bin/python -m mcp_server -- \
  --method tools/call --tool-name search_safety_docs \
  --tool-args-json '{"query": "periodic inspection of the energy control procedure", "k": 3}' --format json
```

**Python client.** Use [`scripts/mcp_client_probe.py`](../scripts/mcp_client_probe.py):

```bash
uv run python scripts/mcp_client_probe.py                                  # initialize + tools/list
uv run python scripts/mcp_client_probe.py lookup_document_metadata '{"source_doc_id": "osha-1910-147"}'
```

**Desktop clients** (Claude Desktop and similar). Add this to the client's MCP config, using an absolute path.
If the client can't find `uv`, give its absolute path too (from `which uv`).

```json
{"mcpServers": {"equip-docs-rag": {
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/phase0", "run", "python", "-m", "mcp_server"]}}}
```
