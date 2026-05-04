"""Budget, account, and category MCP tools.

Tool bodies look up YNAB SDK API classes (`BudgetsApi`, `AccountsApi`,
`CategoriesApi`) and `ynab_resources` via the `server` module so that
`monkeypatch.setattr(server, "BudgetsApi", ...)` in tests propagates here
through late attribute lookup. Pure formatting helpers are imported from
`mcp_ynab.formatters` since tests do not patch them.
"""

from datetime import date
from typing import Any, Dict, List, cast

from ynab.models.account import Account
from ynab.models.category_group_with_categories import CategoryGroupWithCategories

from .. import server as _s
from ..formatters import (
    _build_markdown_table,
    _format_accounts_output,
    _format_dollar_amount,
    _process_category_data,
    _render_month_category_markdown,
    _render_month_markdown,
)


def _resolve_month(month: str) -> date:
    """Resolve a month string into a `date` (first-of-month).

    Accepts the literal ``"current"`` (UTC current month) or any ISO date
    string YYYY-MM-DD. Resolving client-side avoids relying on the SDK's
    Pydantic-strict `datetime.date` annotation accepting raw strings.
    """
    if month == "current":
        return date.today().replace(day=1)
    return date.fromisoformat(month)


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_account_balance(account_id: str) -> float:
    """Get the current balance of a YNAB account (in dollars)."""
    async with await _s.get_ynab_client() as client:
        accounts_api = _s.AccountsApi(client)
        budgets_api = _s.BudgetsApi(client)
        budgets_response = budgets_api.get_budgets()
        budget_id = budgets_response.data.budgets[0].id

        response = accounts_api.get_account_by_id(budget_id, account_id)
        return float(response.data.account.balance) / 1000


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_budgets() -> str:
    """List all YNAB budgets in Markdown format."""
    async with await _s.get_ynab_client() as client:
        budgets_api = _s.BudgetsApi(client)
        budgets_response = budgets_api.get_budgets()
        budgets_list = budgets_response.data.budgets

        markdown = "# YNAB Budgets\n\n"
        if not budgets_list:
            markdown += "_No budgets found._"
        else:
            for budget in budgets_list:
                b = budget.to_dict()
                markdown += f"- **{b.get('name', 'Unnamed Budget')}** (ID: {b.get('id')})\n"
        return markdown


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_accounts(budget_id: str) -> str:
    """List all YNAB accounts in a specific budget in Markdown format."""
    async with await _s.get_ynab_client() as client:
        accounts_api = _s.AccountsApi(client)
        all_accounts: List[Dict[str, Any]] = []
        response = accounts_api.get_accounts(budget_id)
        for account in response.data.accounts:
            if isinstance(account, Account):
                all_accounts.append(account.to_dict())

        formatted = _format_accounts_output(all_accounts)

        markdown = "# YNAB Account Summary\n\n"
        markdown += "## Summary\n"
        markdown += f"- **Total Assets:** {formatted['summary']['total_assets']}\n"
        markdown += f"- **Total Liabilities:** {formatted['summary']['total_liabilities']}\n"
        markdown += f"- **Net Worth:** {formatted['summary']['net_worth']}\n\n"

        for group in formatted["accounts"]:
            markdown += f"## {group['type']}\n"
            markdown += f"**Group Total:** {group['total']}\n\n"

            rows = []
            for acct in group["accounts"]:
                rows.append([acct["name"], acct["balance"], acct["id"]])

            markdown += _build_markdown_table(
                rows, ["Account Name", "Balance", "ID"], ["left", "right", "left"]
            )
            markdown += "\n"

        return markdown


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_categories(budget_id: str) -> str:
    """List all transaction categories for a given YNAB budget in Markdown format."""
    async with await _s.get_ynab_client() as client:
        categories_api = _s.CategoriesApi(client)
        response = categories_api.get_categories(budget_id)
        groups = response.data.category_groups

        markdown = "# YNAB Categories\n\n"
        headers = ["Category ID", "Category Name", "Budgeted", "Activity"]
        align = ["left", "left", "right", "right"]

        for group in groups:
            if isinstance(group, CategoryGroupWithCategories):
                categories_list = group.categories
                group_name = group.name
            else:
                group_dict = cast(Dict[str, Any], group.to_dict())
                categories_list = group_dict["categories"]
                group_name = group_dict["name"]

            if not categories_list:
                continue

            markdown += f"## {group_name}\n\n"
            rows = []

            for category in categories_list:
                cat_id, name, budgeted, activity = _process_category_data(category)
                budgeted_dollars = float(budgeted) / 1000 if budgeted else 0
                activity_dollars = float(activity) / 1000 if activity else 0

                rows.append(
                    [
                        cat_id,
                        name,
                        _format_dollar_amount(budgeted_dollars),
                        _format_dollar_amount(activity_dollars),
                    ]
                )

            table_md = _build_markdown_table(rows, headers, align)
            markdown += table_md + "\n"
        return markdown


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def set_preferred_budget_id(budget_id: str) -> str:
    """Set the preferred YNAB budget ID."""
    _s.ynab_resources.set_preferred_budget_id(budget_id)
    return f"Preferred budget ID set to {budget_id}"


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def cache_categories(budget_id: str) -> str:
    """Cache all categories for a given YNAB budget ID."""
    async with await _s.get_ynab_client() as client:
        categories_api = _s.CategoriesApi(client)
        response = categories_api.get_categories(budget_id)
        groups = response.data.category_groups
        categories = []
        for group in groups:
            if isinstance(group, CategoryGroupWithCategories):
                categories.extend(group.categories)

        _s.ynab_resources.cache_categories(budget_id, [cat.to_dict() for cat in categories])
        return f"Categories cached for budget ID {budget_id}"


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_month(budget_id: str, month: str = "current") -> str:
    """Return a budget month snapshot: RTA, Age of Money, totals, per-group table.

    `month` is "current" (default) or ISO YYYY-MM-DD (first-of-month).
    """
    async with await _s.get_ynab_client() as client:
        months_api = _s.MonthsApi(client)
        response = months_api.get_budget_month(budget_id, _resolve_month(month))
        return _render_month_markdown(response.data.month)


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_category_for_month(budget_id: str, category_id: str, month: str = "current") -> str:
    """Return budgeted/activity/balance/goal for a single category in a month."""
    async with await _s.get_ynab_client() as client:
        cats = _s.CategoriesApi(client)
        response = cats.get_month_category_by_id(budget_id, _resolve_month(month), category_id)
        return _render_month_category_markdown(response.data.category)


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def assign_money(
    budget_id: str,
    category_id: str,
    amount: float,
    month: str = "current",
) -> str:
    """Set the budgeted amount for a category in a month (YNAB Rule 1).

    `amount` is in dollars and will be converted to milliunits. This *sets*
    (does not delta) the budgeted value, so calling twice with the same
    amount is idempotent.
    """
    body = _s.PatchMonthCategoryWrapper(category=_s.SaveMonthCategory(budgeted=int(amount * 1000)))
    async with await _s.get_ynab_client() as client:
        cats = _s.CategoriesApi(client)
        response = cats.update_month_category(budget_id, _resolve_month(month), category_id, body)
        cat = response.data.category
        return (
            f"Assigned {_format_dollar_amount(amount)} to "
            f"**{getattr(cat, 'name', category_id)}** for {month}."
        )


@_s.mcp.tool(annotations=_s.MUTATING_TOOL)
async def move_money(
    budget_id: str,
    from_category_id: str,
    to_category_id: str,
    amount: float,
    month: str = "current",
) -> str:
    """Reallocate money from one category to another in a month (YNAB Rule 3).

    NOT idempotent — running twice doubles the move. Not transactional in
    YNAB: if the credit step fails after the debit succeeds, the error
    message includes the partially-applied state for manual recovery.
    """
    delta = int(amount * 1000)
    m = _resolve_month(month)
    async with await _s.get_ynab_client() as client:
        cats = _s.CategoriesApi(client)
        src = cats.get_month_category_by_id(budget_id, m, from_category_id).data.category
        dst = cats.get_month_category_by_id(budget_id, m, to_category_id).data.category
        new_src = int(src.budgeted) - delta
        new_dst = int(dst.budgeted) + delta

        cats.update_month_category(
            budget_id,
            m,
            from_category_id,
            _s.PatchMonthCategoryWrapper(category=_s.SaveMonthCategory(budgeted=new_src)),
        )
        try:
            cats.update_month_category(
                budget_id,
                m,
                to_category_id,
                _s.PatchMonthCategoryWrapper(category=_s.SaveMonthCategory(budgeted=new_dst)),
            )
        except _s.ApiException as exc:
            raise RuntimeError(
                f"move_money partially applied: source category {from_category_id} "
                f"debited to {new_src / 1000:.2f}, but credit to {to_category_id} "
                f"failed ({exc}). Recover by manually setting {to_category_id} "
                f"budgeted to {new_dst / 1000:.2f}, or reverse with "
                f"move_money(to={from_category_id}, from={to_category_id}, "
                f"amount={amount})."
            ) from exc

    return (
        f"Moved {_format_dollar_amount(amount)} from "
        f"**{getattr(src, 'name', from_category_id)}** → "
        f"**{getattr(dst, 'name', to_category_id)}** ({month})."
    )
