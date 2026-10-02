"""Minimal MCP client for the G5 server: initialize, list tools, optionally call one tool; prints one JSON doc.

Usage: uv run python scripts/mcp_client_probe.py [URL] [TOOL ARGS_JSON]
  no URL -> spawns `python -m mcp_server` over stdio with this process's environment (so PINECONE_API_KEY=invalid
  forces a retrieval failure); URL -> streamable HTTP, e.g. the deployed /mcp. Used for P1/P4/P5 in
  eval/g5_PREDICTION.md; `tools` holds exactly the P1 comparison fields, keys sorted.
"""

import asyncio
import json
import os
import platform
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

TOOL_FIELDS = {"name", "inputSchema", "outputSchema", "annotations"}


async def main(argv: list[str]) -> None:
    url = argv.pop(0) if argv and argv[0].startswith("http") else None
    server = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"], env=dict(os.environ))
    transport = streamable_http_client(url) if url else stdio_client(server)
    async with transport as (read, write, *_), ClientSession(read, write) as session:
        init = await session.initialize()
        tools = (await session.list_tools()).tools
        call = await session.call_tool(argv[0], json.loads(argv[1]) if len(argv) > 1 else {}) if argv else None
    dump = lambda model, **kw: model.model_dump(mode="json", by_alias=True, exclude_none=True, **kw)  # noqa: E731
    print(json.dumps({
        "client_host": platform.node(), "transport": url or "stdio",
        "initialize": {"protocolVersion": init.protocolVersion, "serverInfo": dump(init.serverInfo)},
        "tools": [dump(tool, include=TOOL_FIELDS) for tool in tools],
        "call": dump(call) if call else None,
    }, indent=1, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
