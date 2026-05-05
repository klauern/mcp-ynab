"""Transaction-domain MCP tools.

`create_transaction`, `get_transactions`, `get_transactions_needing_attention`,
`categorize_transaction`, `bulk_categorize`, and `update_transaction`. SDK
API classes (`TransactionsApi`, `BudgetsApi`, `AccountsApi`,
`CategoriesApi`), `ExistingTransaction`, `PutTransactionWrapper`, and
`ynab_resources` are looked up via the `server` module so test
monkeypatches propagate.
"""

import difflib
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Dict, List, Literal, Optional

from mcp.server.fastmcp import Context
from pydantic import Field
from ynab.api_client import ApiClient
from ynab.models.category_group_with_categories import CategoryGroupWithCategories
from ynab.models.new_transaction import NewTransaction
from ynab.models.patch_transactions_wrapper import PatchTransactionsWrapper
from ynab.models.post_transactions_wrapper import PostTransactionsWrapper
from ynab.models.save_sub_transaction import SaveSubTransaction
from ynab.models.save_transaction_with_id_or_import_id import SaveTransactionWithIdOrImportId
from ynab.models.transaction_detail import TransactionDetail
from ynab.rest import ApiException

from .. import server as _s
from ..formatters import _build_markdown_table


def _refresh_category_cache(client: ApiClient, budget_id: str) -> List[Dict[str, Any]]:
    """Fetch categories from YNAB and write them to the cache; return the records."""
    categories_api = _s.CategoriesApi(client)
    response = categories_api.get_categories(budget_id)
    raw_categories: List[Any] = []
    for group in response.data.category_groups:
        if isinstance(group, CategoryGroupWithCategories):
            raw_categories.extend(group.categories)
    _s.ynab_resources.cache_categories(budget_id, [cat.to_dict() for cat in raw_categories])
    return _s.ynab_resources.get_cached_category_records(budget_id)


def _match_category(records: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Return matching category records, in priority order:

    1. Exact case-insensitive match → single record.
    2. Substring match (query in name) → all hits — handles 'groceries' →
       'Groceries 🛒' / 'Groceries (& Household)'.
    3. Fuzzy `difflib.get_close_matches(cutoff=0.6)` → typo recovery.
    """
    q = query.lower()
    names_lower = [(r.get("name") or "").lower() for r in records]

    exact = [r for r, n in zip(records, names_lower) if n == q]
    if exact:
        return exact[:1]

    substring = [r for r, n in zip(records, names_lower) if q and q in n]
    if substring:
        return substring

    close = difflib.get_close_matches(q, names_lower, n=5, cutoff=0.6)
    if not close:
        return []
    matched: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for name_lower in close:
        for r, rn in zip(records, names_lower):
            if rn == name_lower and r.get("id") not in seen_ids:
                matched.append(r)
                seen_ids.add(r.get("id"))
                break
    return matched


async def _find_category_id(
    client: ApiClient, budget_id: str, category_name: str
) -> List[Dict[str, Any]]:
    """Find category candidates by name using the cache, with API refresh on miss.

    Returns a list of `{id, name, group}` records: empty = no match,
    single = unambiguous, multiple = caller should elicit a choice.
    """
    records = _s.ynab_resources.get_cached_category_records(budget_id)
    refreshed = False
    if not records:
        records = _refresh_category_cache(client, budget_id)
        refreshed = True

    matches = _match_category(records, category_name)
    if matches or refreshed:
        return matches

    # Cache had data but matched nothing — refresh once in case it's stale.
    records = _refresh_category_cache(client, budget_id)
    return _match_category(records, category_name)


@_s.mcp.tool(annotations=_s.MUTATING_TOOL)
async def create_transaction(
    account_id: str,
    amount: Annotated[float, Field(description="Amount in dollars")],
    payee_name: str,
    category_name: Optional[str] = None,
    memo: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """Create a new transaction in YNAB."""
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)

        amount_milliunits = int(amount * 1000)

        budget_id = await _s._resolve_budget_id(client, ctx)

        category_id: Optional[str] = None
        if category_name:
            candidates = await _find_category_id(client, budget_id, category_name)
            if len(candidates) == 1:
                category_id = candidates[0]["id"]
            # 0 or 2+ candidates → leave uncategorized; qlh.2 will add elicitation.

        # Create transaction data
        transaction = NewTransaction(
            account_id=account_id,
            date=date.today(),
            amount=amount_milliunits,
            payee_name=payee_name,
            memo=memo,
            category_id=category_id,
        )

        wrapper = PostTransactionsWrapper(transaction=transaction)
        response = transactions_api.create_transaction(budget_id, wrapper)
        if response.data and response.data.transaction:
            return response.data.transaction.to_dict()
        return {}


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_transactions(
    budget_id: str,
    account_id: str,
    since_date: Annotated[
        Optional[date],
        Field(
            description=(
                "ISO date (YYYY-MM-DD) to fetch transactions since. "
                "Defaults to the first day of the current month."
            )
        ),
    ] = None,
) -> str:
    """Get recent transactions for a specific account in a specific budget."""
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)
        all_transactions: List[TransactionDetail] = []
        if since_date is None:
            since_date = datetime.now().replace(day=1).date()
        response = transactions_api.get_transactions_by_account(
            budget_id, account_id, since_date=since_date
        )
        all_transactions.extend(response.data.transactions)

        markdown = "# Recent Transactions\n\n"
        if not all_transactions:
            return markdown + "_No recent transactions found._\n"

        headers = ["ID", "Date", "Amount", "Payee Name", "Category Name", "Memo"]
        align = ["left", "left", "right", "left", "left", "left"]
        rows = []

        for txn in all_transactions:
            amount_str = f"${txn.amount / 1000:,.2f}"
            rows.append(
                [
                    txn.id,
                    txn.var_date.strftime("%Y-%m-%d"),
                    amount_str,
                    txn.payee_name or "N/A",
                    txn.category_name or "N/A",
                    txn.memo or "",
                ]
            )

        markdown += _build_markdown_table(rows, headers, align)
        return markdown


def _get_transaction_row(
    txn: TransactionDetail, account_map: Dict[str, str], filter_type: str
) -> List[str]:
    """Format a transaction into a row for the markdown table."""
    amount_dollars = float(txn.amount) / 1000
    amount_str = f"${abs(amount_dollars):,.2f}"
    if amount_dollars < 0:
        amount_str = f"-{amount_str}"

    status = []
    if not txn.category_id:
        status.append("Uncategorized")
    if not txn.approved:
        status.append("Unapproved")

    return [
        txn.id,
        txn.var_date.strftime("%Y-%m-%d"),
        account_map.get(txn.account_id, "Unknown"),
        amount_str,
        txn.payee_name or "N/A",
        ", ".join(status),
        txn.memo or "",
    ]


def _filter_transactions(
    transactions: List[TransactionDetail], filter_type: str
) -> List[TransactionDetail]:
    """Filter transactions based on the filter type."""
    needs_attention = []
    for txn in transactions:
        if isinstance(txn, TransactionDetail):
            needs_category = filter_type in ["uncategorized", "both"] and not txn.category_id
            needs_approval = filter_type in ["unapproved", "both"] and not txn.approved
            if needs_category or needs_approval:
                needs_attention.append(txn)
    return needs_attention


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_transactions_needing_attention(
    budget_id: str,
    filter_type: Annotated[
        Literal["uncategorized", "unapproved", "both"],
        Field(
            description="Type of transactions to show. One of: 'uncategorized', 'unapproved', 'both'"
        ),
    ] = "both",
    days_back: Annotated[
        Optional[int], Field(description="Number of days to look back (default 30, None for all)")
    ] = 30,
) -> str:
    """List transactions that need attention based on specified filter type in a YNAB budget."""
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)
        accounts_api = _s.AccountsApi(client)

        accounts_response = accounts_api.get_accounts(budget_id)
        account_map = {
            account.id: account.name
            for account in accounts_response.data.accounts
            if not account.closed and not account.deleted
        }

        since_date = (datetime.now() - timedelta(days=days_back)).date() if days_back else None
        response = transactions_api.get_transactions(budget_id, since_date=since_date)
        needs_attention = _filter_transactions(response.data.transactions, filter_type)

        markdown = f"# Transactions Needing Attention ({filter_type.title()})\n\n"
        if not needs_attention:
            return markdown + "_No transactions need attention._"

        markdown += "**Filters Applied:**\n"
        markdown += f"- Filter type: {filter_type}\n"
        if days_back:
            markdown += f"- Looking back {days_back} days\n"
        markdown += "\n"

        headers = ["ID", "Date", "Account", "Amount", "Payee", "Status", "Memo"]
        align = ["left", "left", "left", "right", "left", "left", "left"]
        rows = [_get_transaction_row(txn, account_map, filter_type) for txn in needs_attention]

        markdown += _build_markdown_table(rows, headers, align)
        return markdown


def _find_transaction_by_id(
    transactions: List[TransactionDetail], transaction_id: str, id_type: str
) -> Optional[TransactionDetail]:
    """Find a transaction by its ID and ID type."""
    for txn in transactions:
        if (
            (id_type == "id" and txn.id == transaction_id)
            or (id_type == "import_id" and txn.import_id == transaction_id)
            or (
                id_type == "transfer_transaction_id"
                and txn.transfer_transaction_id == transaction_id
            )
            or (
                id_type == "matched_transaction_id" and txn.matched_transaction_id == transaction_id
            )
        ):
            return txn
    return None


@_s.mcp.tool(annotations=_s.MUTATING_TOOL)
async def categorize_transaction(
    budget_id: str,
    transaction_id: str,
    category_id: str,
    id_type: str = "id",  # One of: "id", "import_id", "transfer_transaction_id", "matched_transaction_id"
) -> str:
    """Categorize a transaction for a given YNAB budget with the provided category ID.

    Args:
        budget_id: The YNAB budget ID
        transaction_id: The transaction identifier
        category_id: The category ID to assign
        id_type: The type of transaction ID being provided. One of:
                - "id": Direct transaction ID (default)
                - "import_id": YNAB import ID format (YNAB:[milliunit_amount]:[iso_date]:[occurrence])
                - "transfer_transaction_id": ID of a transfer transaction
                - "matched_transaction_id": ID of a matched transaction
    """
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)

        # Resolve the canonical transaction id. The "id" path skips any
        # fetch entirely so there's no GET-then-PUT race; alternate id types
        # still require a scan to find the underlying transaction id, but
        # we only ship `category_id` on the wire (PATCH semantics) so a
        # concurrent edit to memo/cleared/flag elsewhere on the transaction
        # is preserved.
        resolved_id: Optional[str] = None
        if id_type == "id":
            resolved_id = transaction_id
        else:
            since_date = None
            if id_type == "import_id" and ":" in transaction_id:
                try:
                    since_date = datetime.strptime(transaction_id.split(":")[2], "%Y-%m-%d").date()
                except (ValueError, IndexError):
                    pass
            response = transactions_api.get_transactions(budget_id, since_date=since_date)
            target_transaction = _find_transaction_by_id(
                response.data.transactions, transaction_id, id_type
            )
            if target_transaction is not None:
                resolved_id = target_transaction.id

        if resolved_id is None:
            return f"Transaction {transaction_id} (type: {id_type}) not found."

        # PATCH-only: ExistingTransaction's other fields default to None and
        # the SDK's to_dict() uses exclude_none=True, so only category_id is
        # serialized into the request body. This avoids clobbering concurrent
        # edits to memo/cleared/flag_color/subtransactions in YNAB.
        wrapper = _s.PutTransactionWrapper(
            transaction=_s.ExistingTransaction(category_id=category_id)
        )
        try:
            transactions_api.update_transaction(
                budget_id=budget_id,
                transaction_id=resolved_id,
                data=wrapper,
            )
        except ApiException as exc:
            if exc.status == 404:
                return f"Transaction {transaction_id} (type: {id_type}) not found."
            raise

        return f"Transaction {transaction_id} (type: {id_type}) categorized as {category_id}."


def _validate_assignment(entry: Any) -> Optional[str]:
    """Return an error message if the assignment is malformed, else None."""
    if not isinstance(entry, dict):
        return "entry is not a dict"
    if not entry.get("transaction_id"):
        return "missing transaction_id"
    if not entry.get("category_id"):
        return "missing category_id"
    return None


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def bulk_categorize(
    budget_id: str,
    assignments: Annotated[
        List[Dict[str, str]],
        Field(
            description=(
                "List of {transaction_id, category_id} dicts. Each entry assigns "
                "the given category to the given transaction in a single bulk PATCH."
            )
        ),
    ],
) -> str:
    """Categorize many transactions in one round-trip via the bulk PATCH endpoint.

    Skips and reports malformed entries (missing keys) without aborting the
    rest of the batch. The response table marks each input id as Updated,
    Not found (server didn't acknowledge it), or Invalid (skipped client-side).
    """
    headers = ["Transaction ID", "Category ID", "Result"]
    align = ["left", "left", "left"]

    if not assignments:
        return "# Bulk Categorize\n\n_No assignments provided._"

    valid_entries: List[Dict[str, str]] = []
    invalid_rows: List[List[str]] = []
    for entry in assignments:
        err = _validate_assignment(entry)
        if err is None:
            valid_entries.append(entry)
        else:
            invalid_rows.append(
                [
                    str(entry.get("transaction_id", "")) if isinstance(entry, dict) else "",
                    str(entry.get("category_id", "")) if isinstance(entry, dict) else "",
                    f"Invalid ({err})",
                ]
            )

    saved_ids: set[str] = set()
    if valid_entries:
        async with await _s.get_ynab_client() as client:
            transactions_api = _s.TransactionsApi(client)
            patch_payload = PatchTransactionsWrapper(
                transactions=[
                    SaveTransactionWithIdOrImportId(
                        id=entry["transaction_id"],
                        category_id=entry["category_id"],
                    )
                    for entry in valid_entries
                ]
            )
            response = transactions_api.update_transactions(budget_id, patch_payload)
            saved_ids = set(response.data.transaction_ids or [])

    rows: List[List[str]] = []
    for entry in valid_entries:
        txn_id = entry["transaction_id"]
        result = "Updated" if txn_id in saved_ids else "Not found"
        rows.append([txn_id, entry["category_id"], result])
    rows.extend(invalid_rows)

    updated_count = sum(1 for r in rows if r[2] == "Updated")
    markdown = "# Bulk Categorize\n\n"
    markdown += f"**{updated_count} of {len(rows)} updated** (budget `{budget_id}`).\n\n"
    markdown += _build_markdown_table(rows, headers, align)
    return markdown


def _validate_transaction_id(entry: Any) -> Optional[str]:
    """Return an error message if the id is malformed, else None."""
    if not isinstance(entry, str):
        return "not a string"
    if not entry:
        return "empty string"
    return None


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def approve_transactions(
    budget_id: str,
    transaction_ids: Annotated[
        List[str],
        Field(
            description=(
                "List of transaction IDs to mark as approved. Each ID is set to "
                "approved=True via a single bulk PATCH. Re-approving an already "
                "approved transaction is a no-op."
            )
        ),
    ],
) -> str:
    """Approve many transactions in one round-trip via the bulk PATCH endpoint.

    Skips and reports malformed entries (non-string or empty) without aborting
    the rest of the batch. The response table marks each input id as Approved,
    Not found (server didn't acknowledge it), or Invalid (skipped client-side).
    """
    headers = ["Transaction ID", "Result"]
    align = ["left", "left"]

    if not transaction_ids:
        return "# Approve Transactions\n\n_No transaction IDs provided._"

    valid_ids: List[str] = []
    invalid_rows: List[List[str]] = []
    for entry in transaction_ids:
        err = _validate_transaction_id(entry)
        if err is None:
            valid_ids.append(entry)
        else:
            invalid_rows.append(
                [
                    str(entry) if isinstance(entry, str) else "",
                    f"Invalid ({err})",
                ]
            )

    saved_ids: set[str] = set()
    if valid_ids:
        async with await _s.get_ynab_client() as client:
            transactions_api = _s.TransactionsApi(client)
            patch_payload = PatchTransactionsWrapper(
                transactions=[
                    SaveTransactionWithIdOrImportId(id=tid, approved=True) for tid in valid_ids
                ]
            )
            response = transactions_api.update_transactions(budget_id, patch_payload)
            saved_ids = set(response.data.transaction_ids or [])

    rows: List[List[str]] = []
    for tid in valid_ids:
        result = "Approved" if tid in saved_ids else "Not found"
        rows.append([tid, result])
    rows.extend(invalid_rows)

    approved_count = sum(1 for r in rows if r[1] == "Approved")
    markdown = "# Approve Transactions\n\n"
    markdown += f"**{approved_count} of {len(rows)} approved** (budget `{budget_id}`).\n\n"
    markdown += _build_markdown_table(rows, headers, align)
    return markdown


_VALID_FLAG_COLORS = {"red", "orange", "yellow", "green", "blue", "purple"}
_VALID_CLEARED_VALUES = {"cleared", "uncleared", "reconciled"}


@_s.mcp.tool(annotations=_s.MUTATING_TOOL)
async def update_transaction(
    budget_id: str,
    transaction_id: str,
    *,
    memo: Optional[str] = None,
    payee_name: Optional[str] = None,
    amount: Annotated[
        Optional[float],
        Field(description="Amount in dollars; converted to milliunits internally."),
    ] = None,
    txn_date: Annotated[Optional[str], Field(description="ISO date YYYY-MM-DD")] = None,
    flag_color: Annotated[
        Optional[str],
        Field(description="One of: red, orange, yellow, green, blue, purple"),
    ] = None,
    cleared: Annotated[
        Optional[str], Field(description="One of: cleared, uncleared, reconciled")
    ] = None,
    approved: Optional[bool] = None,
    category_id: Optional[str] = None,
) -> str:
    """Partially update a single transaction (PATCH-style).

    Only the fields you supply are sent to YNAB; unspecified fields are left
    untouched. At least one mutable field must be provided.

    Args:
        budget_id: The YNAB budget ID.
        transaction_id: The transaction ID to update.
        memo: New memo text.
        payee_name: New payee name.
        amount: New amount in dollars (outflows are negative). Converted to
            milliunits internally.
        txn_date: New ISO date string (YYYY-MM-DD).
        flag_color: New flag color (red/orange/yellow/green/blue/purple).
        cleared: New cleared status (cleared/uncleared/reconciled).
        approved: New approval state.
        category_id: New category ID.
    """
    supplied: Dict[str, Any] = {}
    if memo is not None:
        supplied["memo"] = memo
    if payee_name is not None:
        supplied["payee_name"] = payee_name
    if amount is not None:
        supplied["amount"] = int(round(amount * 1000))
    if txn_date is not None:
        try:
            supplied["var_date"] = datetime.strptime(txn_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"Invalid txn_date {txn_date!r}; expected ISO YYYY-MM-DD.") from exc
    if flag_color is not None:
        if flag_color.lower() not in _VALID_FLAG_COLORS:
            raise ValueError(
                f"Invalid flag_color {flag_color!r}; must be one of {sorted(_VALID_FLAG_COLORS)}."
            )
        supplied["flag_color"] = flag_color.lower()
    if cleared is not None:
        if cleared.lower() not in _VALID_CLEARED_VALUES:
            raise ValueError(
                f"Invalid cleared value {cleared!r}; must be one of "
                f"{sorted(_VALID_CLEARED_VALUES)}."
            )
        supplied["cleared"] = cleared.lower()
    if approved is not None:
        supplied["approved"] = approved
    if category_id is not None:
        supplied["category_id"] = category_id

    if not supplied:
        raise ValueError(
            "update_transaction requires at least one field to update "
            "(memo, payee_name, amount, txn_date, flag_color, cleared, "
            "approved, or category_id)."
        )

    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)
        wrapper = _s.PutTransactionWrapper(transaction=_s.ExistingTransaction(**supplied))
        transactions_api.update_transaction(
            budget_id=budget_id,
            transaction_id=transaction_id,
            data=wrapper,
        )

    field_labels = {
        "memo": "Memo",
        "payee_name": "Payee",
        "amount": "Amount",
        "var_date": "Date",
        "flag_color": "Flag",
        "cleared": "Cleared",
        "approved": "Approved",
        "category_id": "Category ID",
    }
    rows: List[List[str]] = []
    for key, value in supplied.items():
        label = field_labels.get(key, key)
        if key == "amount":
            display = f"${value / 1000:,.2f}"
        elif key == "var_date":
            display = value.strftime("%Y-%m-%d")
        else:
            display = str(value)
        rows.append([label, display])

    markdown = "# Update Transaction\n\n"
    markdown += f"Updated transaction `{transaction_id}` in budget `{budget_id}`.\n\n"
    markdown += _build_markdown_table(rows, ["Field", "New Value"], ["left", "left"])
    return markdown


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_scheduled_transactions(
    budget_id: str,
    within_days: Annotated[
        int,
        Field(description="Only include scheduled transactions due within this many days."),
    ] = 30,
) -> str:
    """List upcoming scheduled transactions for a YNAB budget.

    Filters server-side results to only those whose `date_next` is on or
    before today + `within_days`.
    """
    cutoff = date.today() + timedelta(days=within_days)

    async with await _s.get_ynab_client() as client:
        scheduled_api = _s.ScheduledTransactionsApi(client)
        response = scheduled_api.get_scheduled_transactions(budget_id)
        scheduled = list(response.data.scheduled_transactions or [])

    upcoming = [
        sched
        for sched in scheduled
        if not getattr(sched, "deleted", False)
        and sched.date_next is not None
        and sched.date_next <= cutoff
    ]
    upcoming.sort(key=lambda s: s.date_next)

    markdown = "# Scheduled Transactions\n\n"
    markdown += f"Showing scheduled transactions due on or before {cutoff.isoformat()}.\n\n"
    if not upcoming:
        return markdown + "_No upcoming scheduled transactions._\n"

    headers = ["Date Next", "Frequency", "Account", "Payee", "Category", "Amount"]
    align = ["left", "left", "left", "left", "left", "right"]
    rows: List[List[str]] = []
    for sched in upcoming:
        amount_dollars = float(sched.amount) / 1000
        amount_str = f"${abs(amount_dollars):,.2f}"
        if amount_dollars < 0:
            amount_str = f"-{amount_str}"
        rows.append(
            [
                sched.date_next.strftime("%Y-%m-%d"),
                str(sched.frequency) if sched.frequency is not None else "N/A",
                sched.account_name or "N/A",
                sched.payee_name or "N/A",
                sched.category_name or "N/A",
                amount_str,
            ]
        )

    markdown += _build_markdown_table(rows, headers, align)
    return markdown


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_transactions_by_category(
    budget_id: str,
    category_id: str,
    since_date: Annotated[
        Optional[str],
        Field(description="ISO date (YYYY-MM-DD) to filter transactions since."),
    ] = None,
) -> str:
    """List transactions assigned to a specific category in a YNAB budget."""
    async with await _s.get_ynab_client() as client:
        categories_api = _s.CategoriesApi(client)
        accounts_api = _s.AccountsApi(client)

        accounts_response = accounts_api.get_accounts(budget_id)
        account_map = {account.id: account.name for account in accounts_response.data.accounts}

        response = categories_api.get_transactions_by_category(
            budget_id, category_id, since_date=since_date
        )
        transactions = list(response.data.transactions or [])

    markdown = f"# Transactions for Category `{category_id}`\n\n"
    if not transactions:
        return markdown + "_No transactions found for this category._\n"

    headers = ["ID", "Date", "Account", "Amount", "Payee", "Status", "Memo"]
    align = ["left", "left", "left", "right", "left", "left", "left"]
    rows = [_get_transaction_row(txn, account_map, "both") for txn in transactions]

    markdown += _build_markdown_table(rows, headers, align)
    return markdown


@_s.mcp.tool(annotations=_s.MUTATING_TOOL)
async def delete_transaction(budget_id: str, transaction_id: str) -> str:
    """Delete a transaction from a YNAB budget.

    Args:
        budget_id: The YNAB budget ID.
        transaction_id: The transaction ID to delete.

    Returns:
        A confirmation string. Raises ApiException on YNAB API errors (e.g.
        404 if the transaction does not exist).
    """
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)
        transactions_api.delete_transaction(budget_id, transaction_id)
    return f"Transaction {transaction_id} deleted from budget {budget_id}."


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def split_transaction(
    budget_id: str,
    transaction_id: str,
    splits: Annotated[
        List[Dict[str, Any]],
        Field(
            description=(
                "List of split entries. Each entry is a dict with keys: "
                "`amount` (float, in dollars; required), `category_id` "
                "(optional str), `payee_name` (optional str), `memo` "
                "(optional str). The sum of `amount` values (in milliunits) "
                "must equal the parent transaction's amount in milliunits."
            )
        ),
    ],
) -> str:
    """Convert a transaction into a split with the provided subtransactions.

    Sums of split amounts (in milliunits) must equal the parent transaction
    amount in milliunits. The parent transaction is patched with the new
    `subtransactions` list, replacing any existing splits.

    Args:
        budget_id: The YNAB budget ID.
        transaction_id: The parent transaction ID to convert into a split.
        splits: List of split dicts; see `splits` parameter description.
    """
    if not splits:
        raise ValueError("split_transaction requires at least one split entry.")

    sub_transactions: List[SaveSubTransaction] = []
    total_milliunits = 0
    for idx, entry in enumerate(splits):
        if not isinstance(entry, dict):
            raise ValueError(f"splits[{idx}] is not a dict.")
        if "amount" not in entry or entry["amount"] is None:
            raise ValueError(f"splits[{idx}] is missing required 'amount' field.")
        try:
            amount_dollars = float(entry["amount"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"splits[{idx}] 'amount' must be a number (dollars); got {entry['amount']!r}."
            ) from exc
        amount_milliunits = int(round(amount_dollars * 1000))
        total_milliunits += amount_milliunits
        sub_transactions.append(
            SaveSubTransaction(
                amount=amount_milliunits,
                category_id=entry.get("category_id"),
                payee_name=entry.get("payee_name"),
                memo=entry.get("memo"),
            )
        )

    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)

        # Fetch parent transaction so we can validate the sum matches.
        try:
            parent_response = transactions_api.get_transaction_by_id(budget_id, transaction_id)
        except ApiException as exc:
            if exc.status == 404:
                raise ValueError(
                    f"Transaction {transaction_id} not found in budget {budget_id}."
                ) from exc
            raise
        parent_amount = parent_response.data.transaction.amount
        if total_milliunits != parent_amount:
            raise ValueError(
                f"Sum of split amounts ({total_milliunits} milliunits) does not equal "
                f"parent transaction amount ({parent_amount} milliunits)."
            )

        wrapper = _s.PutTransactionWrapper(
            transaction=_s.ExistingTransaction(subtransactions=sub_transactions)
        )
        transactions_api.update_transaction(
            budget_id=budget_id,
            transaction_id=transaction_id,
            data=wrapper,
        )

    return (
        f"Transaction {transaction_id} split into {len(sub_transactions)} subtransactions "
        f"in budget {budget_id}."
    )


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def import_transactions(budget_id: str) -> List[str]:
    """Trigger YNAB to import transactions for any linked accounts in a budget.

    Args:
        budget_id: The YNAB budget ID.

    Returns:
        A list of newly imported transaction IDs (may be empty if there is
        nothing new to import).
    """
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)
        response = transactions_api.import_transactions(budget_id)
    return list(response.data.transaction_ids or [])
