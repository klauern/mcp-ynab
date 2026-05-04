"""MCP resource handlers exposed by the YNAB server.

Resource bodies access `ynab_resources` (the YNABResources singleton) via
attribute lookup on the `server` module so tests that do
`monkeypatch.setattr(server, "ynab_resources", ...)` propagate correctly.
"""

from typing import Optional

import mcp.types as types

from . import server as _s
from .formatters import _render_month_markdown
from .tools.budgeting import _resolve_month


@_s.mcp.resource("ynab://preferences/budget_id")
def get_preferred_budget_id() -> Optional[str]:
    """Get the preferred YNAB budget ID."""
    return _s.ynab_resources.get_preferred_budget_id()


@_s.mcp.resource("ynab://categories/{budget_id}")
def get_cached_categories(budget_id: str) -> list[types.TextContent]:
    """Get cached categories for a budget ID."""
    return _s.ynab_resources.get_cached_categories(budget_id)


async def _fetch_month_text(budget_id: str, month: str) -> list[types.TextContent]:
    """Fetch a month snapshot and return it as a single TextContent block."""
    async with await _s.get_ynab_client() as client:
        months_api = _s.MonthsApi(client)
        response = months_api.get_budget_month(budget_id, _resolve_month(month))
        return [types.TextContent(type="text", text=_render_month_markdown(response.data.month))]


@_s.mcp.resource("ynab://months/{budget_id}/current")
async def get_current_month_resource(budget_id: str) -> list[types.TextContent]:
    """Current month's snapshot (RTA, Age of Money, totals, per-group table)."""
    return await _fetch_month_text(budget_id, "current")


@_s.mcp.resource("ynab://months/{budget_id}/{month}")
async def get_month_resource(budget_id: str, month: str) -> list[types.TextContent]:
    """Month snapshot for an arbitrary YYYY-MM-DD (first-of-month)."""
    return await _fetch_month_text(budget_id, month)
