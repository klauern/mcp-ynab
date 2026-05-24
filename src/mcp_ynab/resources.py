"""MCP resource handlers exposed by the YNAB server.

Resource bodies access `ynab_resources` (the YNABResources singleton) via
attribute lookup on the `server` module so tests that do
`monkeypatch.setattr(server, "ynab_resources", ...)` propagate correctly.
"""

from importlib.resources import files as _resource_files
from pathlib import Path
from typing import Optional

import mcp.types as types

from . import server as _s
from .code_mode import generate_stubs
from .formatters import _build_markdown_table, _format_dollar_amount, _render_month_markdown
from .tools.budgeting import _resolve_month


@_s.mcp.resource("ynab://preferences/budget_id")
def get_preferred_budget_id() -> Optional[str]:
    """Get the preferred YNAB budget ID."""
    return _s.ynab_resources.get_preferred_budget_id()


@_s.mcp.resource("ynab://preferences")
def get_preferences_resource() -> list[types.TextContent]:
    """Return all preferences as a markdown table; mirrors the ``get_preferences`` tool."""
    from .tools.preferences import _format_preferences_markdown

    text = _format_preferences_markdown(_s.ynab_resources.preferences)
    return [types.TextContent(type="text", text=text)]


@_s.mcp.resource("ynab://code-mode/stubs")
def get_code_mode_stubs() -> list[types.TextContent]:
    """Return Python type stubs for the current Code Mode namespace."""
    text = generate_stubs(
        _s.mcp,
        mutations_enabled=_s.ynab_resources.preferences.code_mode_mutations_enabled,
    )
    return [types.TextContent(type="text", text=text)]


@_s.mcp.resource("ynab://code-mode/examples")
def get_code_mode_examples() -> list[types.TextContent]:
    """Return curated examples for the Python Code Mode runner."""
    examples = _read_code_mode_examples()
    return [types.TextContent(type="text", text=examples)]


def _read_code_mode_examples() -> str:
    # Primary: package data (works in installed wheel via importlib.resources)
    try:
        return _resource_files("mcp_ynab.code_mode").joinpath("examples.md").read_text("utf-8")
    except (FileNotFoundError, TypeError, ModuleNotFoundError):
        pass
    # Fallback: repo-relative paths for development without editable install
    candidates = [
        Path(__file__).resolve().parents[2] / "docs" / "code-mode-examples.md",
        Path.cwd() / "docs" / "code-mode-examples.md",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Code Mode examples file not found; searched: {searched}")


def _currency_iso(currency_format: object) -> str:
    """Best-effort ISO code extraction from a YNAB CurrencyFormat object."""
    if currency_format is None:
        return ""
    iso = getattr(currency_format, "iso_code", None)
    if iso:
        return str(iso)
    if isinstance(currency_format, dict):
        return str(currency_format.get("iso_code", ""))
    return ""


@_s.mcp.resource("ynab://budgets")
async def list_budgets_resource() -> list[types.TextContent]:
    """List all (non-closed/non-deleted) budgets as a markdown table."""
    async with await _s.get_ynab_client() as client:
        budgets_api = _s.BudgetsApi(client)
        response = budgets_api.get_budgets()
        budgets = response.data.budgets or []

        active = [
            b
            for b in budgets
            if not getattr(b, "deleted", False) and not getattr(b, "closed", False)
        ]

        markdown = "# YNAB Budgets\n\n"
        if not active:
            markdown += "_No budgets found._"
            return [types.TextContent(type="text", text=markdown)]

        headers = ["Name", "ID", "Last Modified", "Currency"]
        rows: list[list[str]] = []
        for budget in active:
            last_modified = getattr(budget, "last_modified_on", None)
            last_modified_str = (
                last_modified.isoformat()
                if hasattr(last_modified, "isoformat")
                else str(last_modified)
                if last_modified
                else ""
            )
            rows.append(
                [
                    str(getattr(budget, "name", "") or ""),
                    str(getattr(budget, "id", "") or ""),
                    last_modified_str,
                    _currency_iso(getattr(budget, "currency_format", None)),
                ]
            )

        markdown += _build_markdown_table(rows, headers)
        return [types.TextContent(type="text", text=markdown)]


@_s.mcp.resource("ynab://accounts/{budget_id}")
async def list_accounts_resource(budget_id: str) -> list[types.TextContent]:
    """List open, non-deleted accounts for a budget as a markdown table."""
    async with await _s.get_ynab_client() as client:
        accounts_api = _s.AccountsApi(client)
        response = accounts_api.get_accounts(budget_id)
        accounts = response.data.accounts or []

        active = [
            a
            for a in accounts
            if not getattr(a, "deleted", False) and not getattr(a, "closed", False)
        ]

        markdown = f"# YNAB Accounts ({budget_id})\n\n"
        if not active:
            markdown += "_No accounts found._"
            return [types.TextContent(type="text", text=markdown)]

        headers = ["Name", "Type", "Balance", "ID"]
        align = ["left", "left", "right", "left"]
        rows: list[list[str]] = []
        for account in active:
            balance_milliunits = getattr(account, "balance", 0) or 0
            balance_dollars = float(balance_milliunits) / 1000
            rows.append(
                [
                    str(getattr(account, "name", "") or ""),
                    str(getattr(account, "type", "") or ""),
                    _format_dollar_amount(balance_dollars),
                    str(getattr(account, "id", "") or ""),
                ]
            )

        markdown += _build_markdown_table(rows, headers, align)
        return [types.TextContent(type="text", text=markdown)]


@_s.mcp.resource("ynab://categories/{budget_id}")
def get_cached_categories(budget_id: str) -> list[types.TextContent]:
    """Get cached categories for a budget ID."""
    return _s.ynab_resources.get_cached_categories(budget_id)


@_s.mcp.resource("ynab://categories/{budget_id}/current")
async def list_enriched_categories_resource(budget_id: str) -> list[types.TextContent]:
    """Live category list with current-month budgeted, activity, and balance.

    Fetches all category groups from the YNAB API and returns a per-group
    markdown table so the model can answer 'what's left in Groceries?' without
    a tool call. The balance column shows what's available to spend this month.
    """
    async with await _s.get_ynab_client() as client:
        categories_api = _s.CategoriesApi(client)
        response = categories_api.get_categories(budget_id)
        groups = response.data.category_groups or []

    headers = ["Category", "Budgeted", "Activity", "Balance", "ID"]
    align = ["left", "right", "right", "right", "left"]

    sections: list[str] = []
    for group in groups:
        group_name = getattr(group, "name", None) or "Uncategorized"
        categories = getattr(group, "categories", None) or []
        active = [c for c in categories if not getattr(c, "deleted", False)]
        if not active:
            continue

        rows: list[list[str]] = []
        for cat in active:
            cat_id = str(getattr(cat, "id", "") or "")
            name = str(getattr(cat, "name", "") or "")
            budgeted = float(getattr(cat, "budgeted", 0) or 0) / 1000
            activity = float(getattr(cat, "activity", 0) or 0) / 1000
            balance = float(getattr(cat, "balance", 0) or 0) / 1000
            rows.append(
                [
                    name,
                    _format_dollar_amount(budgeted),
                    _format_dollar_amount(activity),
                    _format_dollar_amount(balance),
                    cat_id,
                ]
            )
        sections.append(f"## {group_name}\n\n" + _build_markdown_table(rows, headers, align))

    header = f"# YNAB Categories — Current Month ({budget_id})\n\n"
    if not sections:
        return [types.TextContent(type="text", text=header + "_No categories found._")]
    return [types.TextContent(type="text", text=header + "\n\n".join(sections))]


@_s.mcp.resource("ynab://payees/{budget_id}")
async def list_payees_resource(budget_id: str) -> list[types.TextContent]:
    """List payees for a budget — name, ID, and transfer_account_id.

    Fetches live from the YNAB API and caches the result in ynab_resources.
    """
    async with await _s.get_ynab_client() as client:
        payees_api = _s.PayeesApi(client)
        response = payees_api.get_payees(budget_id)
        payees = response.data.payees or []

    active = [p for p in payees if not getattr(p, "deleted", False)]
    raw = [
        {
            "id": getattr(p, "id", None),
            "name": getattr(p, "name", None),
            "transfer_account_id": getattr(p, "transfer_account_id", None),
        }
        for p in active
    ]
    _s.ynab_resources.cache_payees(budget_id, raw)

    if not active:
        return [
            types.TextContent(
                type="text", text=f"# YNAB Payees ({budget_id})\n\n_No payees found._"
            )
        ]

    headers = ["Name", "ID", "Transfer Account ID"]
    rows = [[r["name"] or "", r["id"] or "", r["transfer_account_id"] or ""] for r in raw]
    markdown = f"# YNAB Payees ({budget_id})\n\n" + _build_markdown_table(rows, headers)
    return [types.TextContent(type="text", text=markdown)]


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
