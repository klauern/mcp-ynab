"""End-to-end MCP protocol smoke tests."""

from __future__ import annotations

import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_stdio_server_initializes_and_lists_public_tools(tmp_path) -> None:
    """Start the real CLI and complete an MCP initialize/list-tools exchange."""
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    env.pop("YNAB_API_KEY", None)
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_ynab"],
        cwd=tmp_path,
        env=env,
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()

    assert initialized.serverInfo.name == "YNAB"
    names = {tool.name for tool in listed.tools}
    assert {"execute", "search", "ping"}.issubset(names)
