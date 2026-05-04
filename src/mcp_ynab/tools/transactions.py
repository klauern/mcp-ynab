"""Transaction-domain MCP tools.

`create_transaction`, `get_transactions`, `get_transactions_needing_attention`,
and `categorize_transaction`. SDK API classes (`TransactionsApi`,
`BudgetsApi`, `AccountsApi`, `CategoriesApi`), `ExistingTransaction`, and
`ynab_resources` are looked up via the `server` module so test
monkeypatches propagate.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Any, Dict, List, Optional

from pydantic import Field
from ynab.api_client import ApiClient
from ynab.models.new_transaction import NewTransaction
from ynab.models.post_transactions_wrapper import PostTransactionsWrapper
from ynab.models.transaction_detail import TransactionDetail
from ynab.rest import ApiException

from .. import server as _s
from ..formatters import _build_markdown_table


async def _find_category_id(client: ApiClient, budget_id: str, category_name: str) -> Optional[str]:
    """Find a category ID by name."""
    categories_api = _s.CategoriesApi(client)
    categories_response = categories_api.get_categories(budget_id)
    categories = categories_response.data.category_groups
    for group in categories:
        for cat in group.categories:
            if cat.name.lower() == category_name.lower():
                return cat.id
    return None


@_s.mcp.tool(annotations=_s.MUTATING_TOOL)
async def create_transaction(
    account_id: str,
    amount: Annotated[float, Field(description="Amount in dollars")],
    payee_name: str,
    category_name: Optional[str] = None,
    memo: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new transaction in YNAB."""
    async with await _s.get_ynab_client() as client:
        transactions_api = _s.TransactionsApi(client)
        budgets_api = _s.BudgetsApi(client)

        amount_milliunits = int(amount * 1000)

        # Use preferred budget ID if available, otherwise fetch a list of budgets
        budget_id = _s.ynab_resources.get_preferred_budget_id()
        if not budget_id:
            budgets_response = budgets_api.get_budgets()
            budget_id = budgets_response.data.budgets[0].id

        category_id = None
        if category_name:
            category_id = await _find_category_id(client, budget_id, category_name)

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
        str,
        Field(
            description="Type of transactions to show. One of: 'uncategorized', 'unapproved', 'both'"
        ),
    ] = "both",
    days_back: Annotated[
        Optional[int], Field(description="Number of days to look back (default 30, None for all)")
    ] = 30,
) -> str:
    """List transactions that need attention based on specified filter type in a YNAB budget."""
    filter_type = filter_type.lower()
    if filter_type not in ["uncategorized", "unapproved", "both"]:
        return "Error: Invalid filter_type. Must be 'uncategorized', 'unapproved', or 'both'"

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

        # Get since_date for import_id type
        since_date = None
        if id_type == "import_id" and ":" in transaction_id:
            try:
                since_date = datetime.strptime(transaction_id.split(":")[2], "%Y-%m-%d").date()
            except (ValueError, IndexError):
                pass

        target_transaction: Optional[TransactionDetail] = None
        if id_type == "id":
            try:
                single_response = transactions_api.get_transaction_by_id(
                    budget_id=budget_id, transaction_id=transaction_id
                )
                target_transaction = single_response.data.transaction
            except ApiException as exc:
                if exc.status == 404:
                    target_transaction = None
                else:
                    raise
        else:
            response = transactions_api.get_transactions(budget_id, since_date=since_date)
            target_transaction = _find_transaction_by_id(
                response.data.transactions, transaction_id, id_type
            )

        if target_transaction:
            wrapper = _s.PutTransactionWrapper(
                transaction=_s.ExistingTransaction(
                    account_id=target_transaction.account_id,
                    var_date=target_transaction.var_date,
                    amount=target_transaction.amount,
                    payee_id=target_transaction.payee_id,
                    payee_name=target_transaction.payee_name,
                    category_id=category_id,
                    memo=target_transaction.memo,
                    cleared=target_transaction.cleared,
                    approved=target_transaction.approved,
                    flag_color=target_transaction.flag_color,
                    subtransactions=target_transaction.subtransactions,
                )
            )
            transactions_api.update_transaction(
                budget_id=budget_id,
                transaction_id=target_transaction.id,
                data=wrapper,
            )
            return f"Transaction {transaction_id} (type: {id_type}) categorized as {category_id}."

        return f"Transaction {transaction_id} (type: {id_type}) not found."
