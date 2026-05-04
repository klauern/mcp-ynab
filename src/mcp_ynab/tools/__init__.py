"""Domain-grouped MCP tool modules for the YNAB server.

Importing the submodules registers their `@server.mcp.tool` decorators with
the FastMCP instance defined in `mcp_ynab.server`. The submodules are imported
from `server.py` (at the bottom of that file, after `mcp` and shared state are
bound) so the decorators run after their dependencies exist.
"""
