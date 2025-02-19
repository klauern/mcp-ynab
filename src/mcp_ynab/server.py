import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, cast

import mcp.types as types  # Import MCP types
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from xdg import XDG_CONFIG_HOME
from ynab.api.accounts_api import AccountsApi
from ynab.api.budgets_api import BudgetsApi
from ynab.api.categories_api import CategoriesApi
from ynab.api.transactions_api import TransactionsApi
from ynab.api_client import ApiClient
from ynab.configuration import Configuration
from ynab.models.account import Account
from ynab.models.category import Category
from ynab.models.category_group_with_categories import CategoryGroupWithCategories
from ynab.models.existing_transaction import ExistingTransaction
from ynab.models.new_transaction import NewTransaction
from ynab.models.post_transactions_wrapper import PostTransactionsWrapper
from ynab.models.put_transaction_wrapper import PutTransactionWrapper
from ynab.models.transaction_detail import TransactionDetail

# 1. Load environment variables
load_dotenv(verbose=True)

# 2. Globals / configuration
ynab_api_key = os.environ.get("YNAB_API_KEY")

# Set up XDG config directory
CONFIG_DIR = Path(XDG_CONFIG_HOME) / "mcp-ynab"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

PREFERRED_BUDGET_ID_FILE = CONFIG_DIR / "preferred_budget_id.json"
BUDGET_CATEGORY_CACHE_FILE = CONFIG_DIR / "budget_category_cache.json"

# 3. Private helper functions


async def _get_client() -> ApiClient:
    """Get a configured YNAB API client. Reads API key from environment variables."""
    if not ynab_api_key:
        raise ValueError("YNAB_API_KEY not found in environment variables")
    configuration = Configuration(access_token=ynab_api_key)
    return ApiClient(configuration)


class AsyncYNABClient:
    """Async context manager for YNAB API client."""

    def __init__(self):
        self.client: Optional[ApiClient] = None

    async def __aenter__(self) -> ApiClient:
        self.client = await _get_client()
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            # ApiClient doesn't have a close method, but we'll keep the context manager pattern
            pass


async def get_ynab_client() -> AsyncYNABClient:
    """Get an async YNAB client context manager."""
    return AsyncYNABClient()


def _get_empty_table(headers: List[str]) -> str:
    """Create an empty markdown table with just headers."""
    widths = [len(h) + 2 for h in headers]
    header_line = "| " + " | ".join(f"{headers[i]:<{widths[i]}}" for i in range(len(headers))) + " |\n"
    sep_line = "|" + "|".join("-" * (widths[i] + 2) for i in range(len(headers))) + "|\n"
    return header_line + sep_line + "\n"


def _get_column_widths(headers: List[str], rows: List[List[str]], col_count: int) -> List[int]:
    """Calculate column widths based on content."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(col_count):
            widths[i] = max(widths[i], len(row[i]))
    return [w + 2 for w in widths]


def _format_table_line(items: List[str], widths: List[int], alignments: List[str]) -> str:
    """Format a single line of the markdown table."""
    line = "| "
    for i, item in enumerate(items):
        if alignments[i] == "right":
            line += f"{item:>{widths[i]}} | "
        else:
            line += f"{item:<{widths[i]}} | "
    return line.rstrip() + "\n"


def _build_markdown_table(
    rows: List[List[str]], headers: List[str], alignments: Optional[List[str]] = None
) -> str:
    """Build a markdown table from rows and headers."""
    if not rows:
        return _get_empty_table(headers)

    alignments = alignments if alignments is not None else ["left"] * len(headers)
    col_count = len(headers)
    widths = _get_column_widths(headers, rows, col_count)

    header_line = _format_table_line(headers, widths, alignments)
    sep_line = "|" + "|".join("-" * (w + 1) for w in widths) + "|\n"

    row_lines = "".join(_format_table_line(row, widths, alignments) for row in rows)
    return header_line + sep_line + row_lines


def _format_accounts_output(accounts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Format account data into a user-friendly structure."""
    account_groups: Dict[str, List[Dict[str, Any]]] = {}
    type_order = [
        "checking",
        "savings",
        "creditCard",
        "mortgage",
        "autoLoan",
        "studentLoan",
        "otherAsset",
        "otherLiability",
    ]

    type_display_names = {
        "checking": "Checking Accounts",
        "savings": "Savings Accounts",
        "creditCard": "Credit Cards",
        "mortgage": "Mortgages",
        "autoLoan": "Auto Loans",
        "studentLoan": "Student Loans",
        "otherAsset": "Other Assets",
        "otherLiability": "Other Liabilities",
    }

    for account in accounts:
        if account.get("closed", False) or account.get("deleted", False):
            continue

        acct_type = account["type"]
        if acct_type not in account_groups:
            account_groups[acct_type] = []

        balance = float(account["balance"]) / 1000
        account_groups[acct_type].append(
            {
                "name": account["name"],
                "balance": f"${balance:,.2f}",
                "balance_raw": balance,
                "id": account["id"],
            }
        )

    for group in account_groups.values():
        group.sort(key=lambda x: abs(x["balance_raw"]), reverse=True)

    output: Dict[str, Any] = {
        "accounts": [],
        "summary": {
            "total_assets": 0.0,
            "total_liabilities": 0.0,
            "net_worth": 0.0,
        },
    }

    for acct_type in type_order:
        if acct_type in account_groups and account_groups[acct_type]:
            group_data = {
                "type": type_display_names.get(acct_type, acct_type),
                "accounts": account_groups[acct_type],
            }
            group_total = sum(acct["balance_raw"] for acct in account_groups[acct_type])
            group_data["total"] = f"${group_total:,.2f}"

            if acct_type in ["checking", "savings", "otherAsset"]:
                output["summary"]["total_assets"] += group_total
            elif acct_type in [
                "creditCard",
                "mortgage",
                "autoLoan",
                "studentLoan",
                "otherLiability",
            ]:
                output["summary"]["total_liabilities"] += abs(group_total)

            output["accounts"].append(group_data)

    output["summary"]["net_worth_raw"] = (
        output["summary"]["total_assets"] - output["summary"]["total_liabilities"]
    )
    output["summary"]["total_assets"] = f"${output['summary']['total_assets']:,.2f}"
    output["summary"]["total_liabilities"] = f"${output['summary']['total_liabilities']:,.2f}"
    output["summary"]["net_worth"] = f"${output['summary']['net_worth_raw']:,.2f}"

    return output


def _load_json_file(filename: str | Path) -> Dict[str, Any]:
    """Load JSON data from a file."""
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_json_file(filename: str | Path, data: Dict[str, Any]) -> None:
    """Save JSON data to a file."""
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)


# 4. Create the MCP server instance
mcp = FastMCP("YNAB")


# Define resources
class YNABResources:
    def __init__(self):
        self._preferred_budget_id: Optional[str] = None
        self._category_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._load_data()

    def _load_data(self) -> None:
        """Load data from files."""
        try:
            with open(PREFERRED_BUDGET_ID_FILE, "r") as f:
                self._preferred_budget_id = f.read().strip() or None
        except FileNotFoundError:
            self._preferred_budget_id = None

        try:
            self._category_cache = _load_json_file(BUDGET_CATEGORY_CACHE_FILE)
        except FileNotFoundError:
            self._category_cache = {}

    def get_preferred_budget_id(self) -> Optional[str]:
        """Get the preferred budget ID."""
        return self._preferred_budget_id

    def set_preferred_budget_id(self, budget_id: str) -> None:
        """Set the preferred budget ID."""
        self._preferred_budget_id = budget_id
        with open(PREFERRED_BUDGET_ID_FILE, "w") as f:
            f.write(budget_id)

    def get_cached_categories(self, budget_id: str) -> list[types.TextContent]:
        """Get categories from the cache formatted for MCP resources."""
        cached_categories = self._category_cache.get(budget_id, [])
        return [
            types.TextContent(
                type="text", text=f"{cat.get('name', 'Unnamed')} (ID: {cat.get('id', 'N/A')})"
            )
            for cat in cached_categories
        ]

    def cache_categories(self, budget_id: str, categories: List[Dict[str, Any]]) -> None:
        """Cache categories for a budget ID."""
        self._category_cache[budget_id] = [
            {
                "id": cat.get("id"),
                "name": cat.get("name"),
                "group": cat.get("category_group_name"),
            }
            for cat in categories
        ]
        _save_json_file(BUDGET_CATEGORY_CACHE_FILE, self._category_cache)


# Instantiate the resources
ynab_resources = YNABResources()


# Define resources using decorators
@mcp.resource("ynab://preferences/budget_id")
def get_preferred_budget_id() -> Optional[str]:
    """Get the preferred YNAB budget ID."""
    return ynab_resources.get_preferred_budget_id()


@mcp.resource("ynab://categories/{budget_id}")
def get_cached_categories(budget_id: str) -> list[types.TextContent]:
    """Get cached categories for a budget ID."""
    return ynab_resources.get_cached_categories(budget_id)


# 5. Public tool functions


async def _find_category_id(client: ApiClient, budget_id: str, category_name: str) -> Optional[str]:
    """Find a category ID by name."""
    categories_api = CategoriesApi(client)
    categories_response = categories_api.get_categories(budget_id)
    categories = categories_response.data.category_groups
    for group in categories:
        for cat in group.categories:
            if cat.name.lower() == category_name.lower():
                return cat.id
    return None


@mcp.tool()
async def create_transaction(
    account_id: str,
    amount: Annotated[float, Field(description="Amount in dollars")],
    payee_name: str,
    category_name: Optional[str] = None,
    memo: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new transaction in YNAB."""
    async with await get_ynab_client() as client:
        transactions_api = TransactionsApi(client)
        budgets_api = BudgetsApi(client)

        amount_milliunits = int(amount * 1000)

        # Use preferred budget ID if available, otherwise fetch a list of budgets
        budget_id = ynab_resources.get_preferred_budget_id()
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


@mcp.tool()
async def get_account_balance(account_id: str) -> float:
    """Get the current balance of a YNAB account (in dollars)."""
    async with await get_ynab_client() as client:
        accounts_api = AccountsApi(client)
        budgets_api = BudgetsApi(client)
        budgets_response = budgets_api.get_budgets()
        budget_id = budgets_response.data.budgets[0].id

        response = accounts_api.get_account_by_id(budget_id, account_id)
        return float(response.data.account.balance) / 1000


@mcp.tool()
async def get_budgets() -> str:
    """List all YNAB budgets in Markdown format."""
    async with await get_ynab_client() as client:
        budgets_api = BudgetsApi(client)
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


@mcp.tool()
async def get_accounts(budget_id: str) -> str:
    """List all YNAB accounts in a specific budget in Markdown format."""
    async with await get_ynab_client() as client:
        accounts_api = AccountsApi(client)
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


@mcp.tool()
async def get_transactions(budget_id: str, account_id: str) -> str:
    """Get recent transactions for a specific account in a specific budget."""
    async with await get_ynab_client() as client:
        transactions_api = TransactionsApi(client)
        all_transactions: List[TransactionDetail] = []
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


@mcp.tool()
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

    async with await get_ynab_client() as client:
        transactions_api = TransactionsApi(client)
        accounts_api = AccountsApi(client)

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


@mcp.tool()
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
    async with await get_ynab_client() as client:
        transactions_api = TransactionsApi(client)

        # Get since_date for import_id type
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

        if target_transaction:
            wrapper = PutTransactionWrapper(
                transaction=ExistingTransaction(
                    account_id=target_transaction.account_id,
                    amount=target_transaction.amount,
                    category_id=category_id,
                )
            )
            transactions_api.update_transaction(
                budget_id=budget_id,
                transaction_id=target_transaction.id,
                data=wrapper,
            )
            return f"Transaction {transaction_id} (type: {id_type}) categorized as {category_id}."

        return f"Transaction {transaction_id} (type: {id_type}) not found."


def _process_category_data(category: Category | Dict[str, Any]) -> tuple[str, str, float, float]:
    """Process category data and return tuple of (id, name, budgeted, activity)."""
    if isinstance(category, Category):
        return category.id, category.name, category.budgeted, category.activity
    cat_dict = cast(Dict[str, Any], category)
    return cat_dict["id"], cat_dict["name"], cat_dict["budgeted"], cat_dict["activity"]


def _format_dollar_amount(amount: float) -> str:
    """Format a dollar amount with proper sign and formatting."""
    amount_str = f"${abs(amount):,.2f}"
    return f"-{amount_str}" if amount < 0 else amount_str


@mcp.tool()
async def get_categories(budget_id: str) -> str:
    """List all transaction categories for a given YNAB budget in Markdown format."""
    async with await get_ynab_client() as client:
        categories_api = CategoriesApi(client)
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


@mcp.tool()
async def set_preferred_budget_id(budget_id: str) -> str:
    """Set the preferred YNAB budget ID."""
    ynab_resources.set_preferred_budget_id(budget_id)
    return f"Preferred budget ID set to {budget_id}"


@mcp.tool()
async def cache_categories(budget_id: str) -> str:
    """Cache all categories for a given YNAB budget ID."""
    async with await get_ynab_client() as client:
        categories_api = CategoriesApi(client)
        response = categories_api.get_categories(budget_id)
        groups = response.data.category_groups
        categories = []
        for group in groups:
            if isinstance(group, CategoryGroupWithCategories):
                categories.extend(group.categories)

        ynab_resources.cache_categories(budget_id, [cat.to_dict() for cat in categories])
        return f"Categories cached for budget ID {budget_id}"
