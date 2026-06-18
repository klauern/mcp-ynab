"""Budget, account, and category MCP tools.

Tool bodies look up YNAB SDK API classes (`PlansApi`, `AccountsApi`,
`CategoriesApi`) and `ynab_resources` via the `server` module so that
`monkeypatch.setattr(server, "PlansApi", ...)` in tests propagates here
through late attribute lookup. Pure formatting helpers are imported from
`mcp_ynab.formatters` since tests do not patch them.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Literal, Optional, cast

from mcp.server.fastmcp import Context
from ynab.models.account import Account
from ynab.models.category_group_with_categories import CategoryGroupWithCategories
from ynab.models.patch_category_wrapper import PatchCategoryWrapper
from ynab.models.patch_payee_wrapper import PatchPayeeWrapper
from ynab.models.patch_transactions_wrapper import PatchTransactionsWrapper
from ynab.models.existing_category import ExistingCategory
from ynab.models.save_payee import SavePayee
from ynab.models.save_transaction_with_id_or_import_id import SaveTransactionWithIdOrImportId

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
async def get_account_balance(account_id: str, ctx: Optional[Context] = None) -> float:
    """Get the current balance of a YNAB account (in dollars)."""
    async with await _s.get_ynab_client() as client:
        accounts_api = _s.AccountsApi(client)
        budget_id = await _s._resolve_budget_id(client, ctx)
        response = accounts_api.get_account_by_id(budget_id, account_id)
        return float(response.data.account.balance) / 1000


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_budgets() -> str:
    """List all YNAB budgets in Markdown format."""
    async with await _s.get_ynab_client() as client:
        plans_api = _s.PlansApi(client)
        budgets_response = plans_api.get_plans()
        budgets_list = budgets_response.data.plans

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
    """List all transaction categories for a given YNAB budget in Markdown format.

    Note: YNAB API v1 does not expose a create-category endpoint. Categories
    can only be created in the YNAB web or mobile app; this tool is read-only.
    """
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


async def _fetch_and_cache_categories(budget_id: str) -> int:
    """Fetch categories for ``budget_id`` from YNAB and write the envelope. Returns count."""
    async with await _s.get_ynab_client() as client:
        categories_api = _s.CategoriesApi(client)
        response = categories_api.get_categories(budget_id)
        categories: list[Any] = []
        for group in response.data.category_groups:
            if isinstance(group, CategoryGroupWithCategories):
                categories.extend(group.categories)
        _s.ynab_resources.cache_categories(budget_id, [cat.to_dict() for cat in categories])
        return len(categories)


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def cache_categories(budget_id: str) -> str:
    """Force-fetch and cache categories for a budget id. See also: ``refresh_categories``."""
    count = await _fetch_and_cache_categories(budget_id)
    return f"Cached {count} categories for budget ID {budget_id}"


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def refresh_categories(budget_id: str, force: bool = False) -> str:
    """Refresh the category cache for ``budget_id`` if stale (or always when ``force=True``).

    Staleness is decided by ``preferences.category_cache_ttl_minutes``. When
    the cache is fresh and ``force=False``, this is a no-op that reports the
    cached count — cheap to call from a chain of tools that just want to
    "make sure the cache is warm before I look something up."
    """
    if not force and not _s.ynab_resources.is_cache_stale(budget_id):
        cached = _s.ynab_resources.get_cached_category_records(budget_id)
        return f"Cache fresh for budget ID {budget_id} ({len(cached)} categories); no refetch."
    count = await _fetch_and_cache_categories(budget_id)
    return f"Refreshed {count} categories for budget ID {budget_id}"


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_month(budget_id: str, month: str = "current") -> str:
    """Return a budget month snapshot: RTA, Age of Money, totals, per-group table.

    `month` is "current" (default) or ISO YYYY-MM-DD (first-of-month).
    """
    async with await _s.get_ynab_client() as client:
        months_api = _s.MonthsApi(client)
        response = months_api.get_plan_month(budget_id, _resolve_month(month))
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
    from_category_id: Optional[str] = None,
    to_category_id: Optional[str] = None,
    amount: Optional[float] = None,
    month: str = "current",
    ctx: Optional[Context] = None,
) -> str:
    """Reallocate money from one category to another in a month (YNAB Rule 3).

    When ``from_category_id`` or ``to_category_id`` are omitted and an MCP
    context is available, the user is prompted to choose from the cached
    category list (refreshed from the API if the cache is empty).

    NOT idempotent — running twice doubles the move. Not transactional in
    YNAB: if the credit step fails after the debit succeeds, the error
    message includes the partially-applied state for manual recovery.
    """
    if amount is None:
        raise ValueError("move_money requires an amount.")

    # Elicit missing category IDs before opening the YNAB client for the move.
    if (from_category_id is None or to_category_id is None) and ctx is not None:
        records = _s.ynab_resources.get_cached_category_records(budget_id)
        if not records:
            async with await _s.get_ynab_client() as _client:
                cats_api = _s.CategoriesApi(_client)
                response = cats_api.get_categories(budget_id)
                raw: List[Any] = []
                for group in response.data.category_groups:
                    if isinstance(group, CategoryGroupWithCategories):
                        raw.extend(group.categories)
                _s.ynab_resources.cache_categories(budget_id, [c.to_dict() for c in raw])
                records = _s.ynab_resources.get_cached_category_records(budget_id)

        if records:
            options = "\n".join(
                f"{i + 1}. {r.get('name', 'Unknown')}"
                + (f" — {r.get('group')}" if r.get("group") else "")
                for i, r in enumerate(records)
            )

            if from_category_id is None:
                msg = f"Choose the source category (move money FROM):\n{options}"
                result = await ctx.elicit(message=msg, schema=_s._CategoryChoice)
                if result.action != "accept" or result.data.index == 0:
                    return "move_money cancelled: no source category selected."
                idx = result.data.index
                if idx < 1 or idx > len(records):
                    raise ValueError(f"Source index {idx} out of range 1..{len(records)}.")
                from_category_id = records[idx - 1]["id"]

            if to_category_id is None:
                msg = f"Choose the destination category (move money TO):\n{options}"
                result = await ctx.elicit(message=msg, schema=_s._CategoryChoice)
                if result.action != "accept" or result.data.index == 0:
                    return "move_money cancelled: no destination category selected."
                idx = result.data.index
                if idx < 1 or idx > len(records):
                    raise ValueError(f"Destination index {idx} out of range 1..{len(records)}.")
                to_category_id = records[idx - 1]["id"]

    if from_category_id is None or to_category_id is None:
        raise ValueError(
            "move_money requires from_category_id and to_category_id. "
            "Provide them explicitly or pass an MCP context for interactive selection."
        )
    if from_category_id == to_category_id:
        raise ValueError("from_category_id and to_category_id must be different.")

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


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def update_category(
    budget_id: str,
    category_id: str,
    name: Optional[str] = None,
    note: Optional[str] = None,
    category_group_id: Optional[str] = None,
    goal_target: Optional[float] = None,
    goal_target_date: Optional[str] = None,
    goal_needs_whole_amount: Optional[bool] = None,
) -> str:
    """Rename a category, edit its note/group, or set its recurring monthly goal.

    At least one updatable field must be provided. Idempotent: applying the
    same values twice leaves the category unchanged.

    The `goal_*` fields map onto YNAB's category PATCH endpoint, which now
    accepts them (this was previously an API limitation). Setting `goal_target`
    on a category that has no goal creates a monthly NEED goal by default; it
    only applies to goal types with a target amount (NEED/TB/TBD/MF). This is
    distinct from `assign_money`, which sets the per-month *assigned* amount —
    here we change the recurring *target*.

    Args:
        budget_id: The YNAB budget ID.
        category_id: The ID of the category to update.
        name: New display name for the category (optional).
        note: New note/memo for the category (optional).
        category_group_id: ID of the category group to move the category into (optional).
        goal_target: Goal target in dollars (converted to milliunits). Creates a
            monthly NEED goal when the category has no goal yet.
        goal_target_date: Goal target date as an ISO date string (e.g. "2026-07-01").
        goal_needs_whole_amount: NEED goals only — True selects "Set Aside" (the
            whole amount each period), False selects "Refill" (up to the target).
    """
    if (
        name is None
        and note is None
        and category_group_id is None
        and goal_target is None
        and goal_target_date is None
        and goal_needs_whole_amount is None
    ):
        raise ValueError(
            "At least one of name, note, category_group_id, goal_target, "
            "goal_target_date, or goal_needs_whole_amount must be provided."
        )
    goal_target_milliunits = int(round(goal_target * 1000)) if goal_target is not None else None
    # `is not None` (not truthiness) so an empty string raises a clear ValueError
    # rather than silently slipping past the guard above as a no-op update.
    parsed_goal_date = (
        date.fromisoformat(goal_target_date) if goal_target_date is not None else None
    )
    # ExistingCategory serializes with exclude_none, so only the fields set here
    # reach the wire; omitted fields are left untouched by YNAB's PATCH.
    wrapper = PatchCategoryWrapper(
        category=ExistingCategory(
            name=name,
            note=note,
            category_group_id=category_group_id,
            goal_target=goal_target_milliunits,
            goal_target_date=parsed_goal_date,
            goal_needs_whole_amount=goal_needs_whole_amount,
        )
    )
    async with await _s.get_ynab_client() as client:
        cats = _s.CategoriesApi(client)
        response = cats.update_category(budget_id, category_id, wrapper)
        cat = response.data.category
    parts: list[str] = []
    if name is not None:
        parts.append(f"renamed to **{cat.name}**")
    if note is not None:
        parts.append(f"note set to `{cat.note}`")
    if category_group_id is not None:
        parts.append(f"moved to group `{category_group_id}`")
    if goal_target is not None:
        parts.append(f"goal target set to ${goal_target:,.2f}")
    if parsed_goal_date is not None:
        parts.append(f"goal target date set to `{parsed_goal_date.isoformat()}`")
    if goal_needs_whole_amount is not None:
        parts.append(f"goal mode set to {'Set Aside' if goal_needs_whole_amount else 'Refill'}")
    return f"Category `{category_id}` updated: {', '.join(parts)}."


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_payees(budget_id: str, include_deleted: bool = False) -> str:
    """List payees for a YNAB budget in Markdown table form.

    Args:
        budget_id: The YNAB budget ID.
        include_deleted: If False (default), payees with `deleted=True` are
            filtered out. Set True to include tombstoned payees.
    """
    async with await _s.get_ynab_client() as client:
        payees_api = _s.PayeesApi(client)
        response = payees_api.get_payees(budget_id)
        payees = response.data.payees

        headers = ["ID", "Name", "Transfer Account ID"]
        align = ["left", "left", "left"]
        rows: List[List[str]] = []
        for payee in payees:
            if not include_deleted and getattr(payee, "deleted", False):
                continue
            rows.append(
                [
                    getattr(payee, "id", "") or "",
                    getattr(payee, "name", "") or "",
                    getattr(payee, "transfer_account_id", None) or "",
                ]
            )

        markdown = "# YNAB Payees\n\n"
        if not rows:
            return markdown + "_No payees found._"
        markdown += _build_markdown_table(rows, headers, align)
        return markdown


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def rename_payee(budget_id: str, payee_id: str, new_name: str) -> str:
    """Rename a YNAB payee. Idempotent: re-renaming to the same name is a no-op
    on the server side.
    """
    async with await _s.get_ynab_client() as client:
        payees_api = _s.PayeesApi(client)
        wrapper = PatchPayeeWrapper(payee=SavePayee(name=new_name))
        payees_api.update_payee(budget_id, payee_id, wrapper)
    return f"Payee `{payee_id}` renamed to **{new_name}** in budget `{budget_id}`."


@_s.mcp.tool(annotations=_s.MUTATING_TOOL)
async def merge_payees(
    budget_id: str,
    source_payee_id: str,
    destination_payee_id: str,
    delete_source: bool = False,
) -> str:
    """Move every transaction from `source_payee_id` to `destination_payee_id`.

    Iterates the source payee's transactions and PATCHes their `payee_id` to
    the destination via the bulk PATCH endpoint.

    Args:
        budget_id: The YNAB budget ID.
        source_payee_id: Payee whose transactions will be reassigned.
        destination_payee_id: Payee that the transactions will be reassigned to.
        delete_source: No-op flag. YNAB has no source-payee delete endpoint, so
            the source payee cannot be removed via the API and the flag is
            documented but not acted upon. The flag is reported in the summary
            for transparency.
    """
    moved_ids: List[str] = []
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)
        response = transactions_api.get_transactions_by_payee(budget_id, source_payee_id)
        source_txns = response.data.transactions or []
        txn_ids = [getattr(t, "id", None) for t in source_txns]
        txn_ids = [tid for tid in txn_ids if tid]

        if txn_ids:
            patch_payload = PatchTransactionsWrapper(
                transactions=[
                    SaveTransactionWithIdOrImportId(id=tid, payee_id=destination_payee_id)
                    for tid in txn_ids
                ]
            )
            patch_response = transactions_api.update_transactions(budget_id, patch_payload)
            moved_ids = list(patch_response.data.transaction_ids or [])

    markdown = "# Merge Payees\n\n"
    markdown += (
        f"Moved **{len(moved_ids)}** transaction(s) from payee `{source_payee_id}` "
        f"to payee `{destination_payee_id}` in budget `{budget_id}`.\n\n"
    )
    if delete_source:
        markdown += (
            "_Note: `delete_source=True` was requested, but YNAB does not expose "
            "a payee-delete endpoint, so the source payee was **not** deleted._\n"
        )
    else:
        markdown += "_Source payee retained (delete_source=False)._\n"
    return markdown


# ---------------------------------------------------------------------------
# Spending analysis helpers and tools
# ---------------------------------------------------------------------------


_Period = Literal["this_month", "last_month", "last_30d", "last_90d", "ytd"]


def _resolve_period_range(period: _Period) -> tuple[date, Optional[date]]:
    """Return ``(since_date, until_date)`` for a named period.

    ``until_date`` is exclusive — `None` means "no upper bound" (run through
    today). YNAB's ``get_transactions`` only takes ``since_date``; the upper
    bound is enforced client-side after the fetch.
    """
    today = date.today()
    if period == "this_month":
        return today.replace(day=1), None
    if period == "last_month":
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - timedelta(days=1)
        first_of_last_month = last_month_end.replace(day=1)
        return first_of_last_month, first_of_this_month
    if period == "last_30d":
        return today - timedelta(days=30), None
    if period == "last_90d":
        return today - timedelta(days=90), None
    if period == "ytd":
        return today.replace(month=1, day=1), None
    raise ValueError(f"Unknown period: {period!r}")


def _aggregate_spending(
    transactions: List[Any],
    *,
    key_attr_id: str,
    key_attr_name: str,
    until_date: Optional[date],
    account_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Group outflow transactions by a key, summing absolute spent.

    Only transactions with ``amount < 0`` (outflows) contribute. Returns a
    list of dicts with ``id``, ``name``, ``total`` (dollars), ``count``,
    and ``avg`` (dollars), unsorted.
    """
    buckets: Dict[Optional[str], Dict[str, Any]] = {}
    for txn in transactions:
        amount = getattr(txn, "amount", 0) or 0
        if amount >= 0:
            continue
        if account_id is not None and getattr(txn, "account_id", None) != account_id:
            continue
        if until_date is not None:
            txn_date = getattr(txn, "var_date", None)
            if txn_date is not None and txn_date >= until_date:
                continue
        bucket_id = getattr(txn, key_attr_id, None)
        bucket_name = getattr(txn, key_attr_name, None) or "(uncategorized)"
        bucket = buckets.setdefault(
            bucket_id,
            {"id": bucket_id, "name": bucket_name, "total_milliunits": 0, "count": 0},
        )
        bucket["total_milliunits"] += abs(int(amount))
        bucket["count"] += 1

    results: List[Dict[str, Any]] = []
    for bucket in buckets.values():
        count = bucket["count"]
        total_dollars = bucket["total_milliunits"] / 1000.0
        avg_dollars = total_dollars / count if count else 0.0
        results.append(
            {
                "id": bucket["id"],
                "name": bucket["name"],
                "total": total_dollars,
                "count": count,
                "avg": avg_dollars,
            }
        )
    return results


def _render_spending_table(
    rows: List[Dict[str, Any]],
    *,
    title: str,
    key_label: str,
    period: str,
    top_n: int,
) -> str:
    """Render a spending-aggregation result list as a markdown table."""
    rows_sorted = sorted(rows, key=lambda r: r["total"], reverse=True)[:top_n]
    markdown = f"# {title}\n\n_Period: {period} (top {top_n})_\n\n"
    if not rows_sorted:
        return markdown + "_No outflow transactions in the selected period._"
    headers = [key_label, "Total Spent", "Txn Count", "Avg"]
    align = ["left", "right", "right", "right"]
    table_rows = [
        [
            row["name"],
            _format_dollar_amount(row["total"]),
            str(row["count"]),
            _format_dollar_amount(row["avg"]),
        ]
        for row in rows_sorted
    ]
    return markdown + _build_markdown_table(table_rows, headers, align)


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def spending_by_category(
    budget_id: str,
    period: _Period,
    top_n: int = 20,
) -> str:
    """Aggregate outflow spending by category over a named period.

    Sums absolute outflow amounts (txns with ``amount < 0``) per category,
    counts transactions, computes per-txn average, sorts descending, and
    returns the top-N as a markdown table.
    """
    since_date, until_date = _resolve_period_range(period)
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)
        response = transactions_api.get_transactions(budget_id, since_date=since_date)
        rows = _aggregate_spending(
            response.data.transactions,
            key_attr_id="category_id",
            key_attr_name="category_name",
            until_date=until_date,
        )
    return _render_spending_table(
        rows,
        title="Spending by Category",
        key_label="Category",
        period=period,
        top_n=top_n,
    )


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def spending_by_payee(
    budget_id: str,
    period: _Period,
    top_n: int = 20,
    account_id: Optional[str] = None,
) -> str:
    """Aggregate outflow spending by payee over a named period.

    Same shape as ``spending_by_category`` but groups by payee. When
    ``account_id`` is provided, restricts the aggregation to transactions
    on that account.
    """
    since_date, until_date = _resolve_period_range(period)
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)
        response = transactions_api.get_transactions(budget_id, since_date=since_date)
        rows = _aggregate_spending(
            response.data.transactions,
            key_attr_id="payee_id",
            key_attr_name="payee_name",
            until_date=until_date,
            account_id=account_id,
        )
    return _render_spending_table(
        rows,
        title="Spending by Payee",
        key_label="Payee",
        period=period,
        top_n=top_n,
    )


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def ping() -> str:
    """Verify YNAB API auth by fetching the current user's id.

    Useful for confirming that ``YNAB_API_KEY`` is set and valid without
    touching budget data.
    """
    async with await _s.get_ynab_client() as client:
        user_api = _s.UserApi(client)
        response = user_api.get_user()
        return f"ok (user_id={response.data.user.id})"
