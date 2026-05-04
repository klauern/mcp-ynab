"""MCP resource handlers exposed by the YNAB server.

Resource bodies access `ynab_resources` (the YNABResources singleton) via
attribute lookup on the `server` module so tests that do
`monkeypatch.setattr(server, "ynab_resources", ...)` propagate correctly.
"""

from typing import Optional

import mcp.types as types

from . import server as _s


@_s.mcp.resource("ynab://preferences/budget_id")
def get_preferred_budget_id() -> Optional[str]:
    """Get the preferred YNAB budget ID."""
    return _s.ynab_resources.get_preferred_budget_id()


@_s.mcp.resource("ynab://categories/{budget_id}")
def get_cached_categories(budget_id: str) -> list[types.TextContent]:
    """Get cached categories for a budget ID."""
    return _s.ynab_resources.get_cached_categories(budget_id)
