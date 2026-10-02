"""stdio entry point for the G5 MCP server: `uv run python -m mcp_server` (MCP Inspector, desktop clients).

stdout is the MCP protocol channel, so nothing else may print there; FastMCP logs to stderr.
"""

from dotenv import load_dotenv

# Same as api/main.py: populate os.environ from the repo's .env so the OpenAI/Pinecone clients find their keys,
# whatever working directory the launching client uses. Variables that are already set win (override=False) —
# which is how a caller forces a retrieval failure with PINECONE_API_KEY=invalid.
load_dotenv()

from mcp_server.server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run()  # stdio transport
