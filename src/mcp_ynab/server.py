import os
from datetime import datetime
from typing import Annotated, Any, Dict, List, Optional

import mcp.types as types  # Import MCP types
import ynab
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# 1. Load environment variables
load_dotenv(verbose=True)

# 2. Globals / configuration
ynab_api_key = os.environ.get("YNAB_API_KEY")
PREFERRED_BUDGET_ID_FILE = "preferred_budget_id.json"  # File to store preferred budget ID
# CATEGORY_CACHE: Dict[str, List[Dict[str, Any]]] = {}  # Category cache - No longer needed here

# 3. Private helper functions


def _get_client() -> ynab.ApiClient:
    """Get a configured YNAB API client. Reads API key from environment variables."""
    if not ynab_api_key:
        raise ValueError("YNAB_API_KEY not found in environment variables")
    configuration = ynab.Configuration(access_token=ynab_api_key)
    return ynab.ApiClient(configuration)


def _build_markdown_table(
    rows: List[List[str]], headers: List[str], alignments: Optional[List[str]] = None
) -> str:
    """Build a markdown table from rows and headers."""
    if not rows:
        widths = [len(h) + 2 for h in headers]
        header_line = (
            "| " + " | ".join(f"{headers[i]:<{widths[i]}}" for i in range(len(headers))) + " |\n"
        )
        sep_line = "|" + "|".join("-" * (widths[i] + 2) for i in range(len(headers))) + "|\n"
        return header_line + sep_line + "\n"

    if alignments is None:
        alignments = ["left"] * len(headers)

    col_count = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(col_count):
            widths[i] = max(widths[i], len(row[i]))
    widths = [w + 2 for w in widths]

    header_line = "| "
    sep_line = "|"
    for i in range(col_count):
        if alignments[i] == "right":
            header_line += f"{headers[i]:>{widths[i]}} | "
        else:
            header_line += f"{headers[i]:<{widths[i]}} | "
        sep_line += "-" * (widths[i] + 1) + "|"
    header_line = header_line.rstrip() + "\n"
    sep_line += "\n"

    row_lines = ""
    for row in rows:
        line = "| "
        for i in range(col_count):
            if alignments[i] == "right":
                line += f"{row[i]:>{widths[i]}} | "
            else:
                line += f"{row[i]:<{widths[i]}} | "
        row_lines += line.rstrip() + "\n"

    return header_line + sep_line + row_lines


def _format_accounts_output(accounts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Format account data into a user-friendly structure."""
    account_groups = {}
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

    output = {
        "accounts": [],
        "summary": {
            "total_assets": 0,
            "total_liabilities": 0,
            "net_worth": 0,
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

    output["summary"]["net_worth"] = (
        output["summary"]["total_assets"] - output["summary"]["total_liabilities"]
    )
    output["summary"]["total_assets"] = f"${output['summary']['total_assets']:,.2f}"
    output["summary"]["total_liabilities"] = f"${output['summary']['total_liabilities']:,.2f}"
    output["summary"]["net_worth"] = f"${output['summary']['net_worth']:,.2f}"

    return output


def _load_preferred_budget_id() -> Optional[str]:
    """Load the preferred budget ID from file."""
    try:
        with open(PREFERRED_BUDGET_ID_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def _save_preferred_budget_id(budget_id: str) -> None:
    """Save the preferred budget ID to file."""
    with open(PREFERRED_BUDGET_ID_FILE, "w") as f:
        f.write(budget_id)


# def _get_cached_categories(budget_id: str) -> Optional[List[Dict[str, Any]]]:
#     """Get categories from the cache."""
#     return CATEGORY_CACHE.get(budget_id)


# def _cache_categories(budget_id: str, categories: List[Dict[str, Any]]) -> None:
#     """Cache categories for a budget ID."""
#     CATEGORY_CACHE[budget_id] = categories


# 4. Create the MCP server instance
mcp = FastMCP("YNAB")


# Define resources
class YNABResources:
    def __init__(self):
        self._preferred_budget_id = _load_preferred_budget_id()
        self._category_cache: Dict[str, List[Dict[str, Any]]] = {}

    def get_preferred_budget_id(self) -> Optional[str]:
        """Get the preferred budget ID."""
        return self._preferred_budget_id

    def set_preferred_budget_id(self, budget_id: str) -> None:
        """Set the preferred budget ID."""
        self._preferred_budget_id = budget_id
        _save_preferred_budget_id(budget_id)

    # def get_cached_categories(self, budget_id: str) -> Optional[List[Dict[str, Any]]]:
    #     return self._category_cache.get(budget_id)

    def get_cached_categories(self, budget_id: str) -> list[types.TextContent]:
        """Get categories from the cache formatted for MCP resources."""
        cached_categories = self._category_cache.get(budget_id)
        if not cached_categories:
            return []

        return [
            types.TextContent(
                type="text", text=f"{cat.get('name', 'Unnamed')} (ID: {cat.get('id', 'N/A')})"
            )
            for cat in cached_categories
        ]

    def cache_categories(self, budget_id: str, categories: List[Dict[str, Any]]) -> None:
        """Cache categories for a budget ID."""
        self._category_cache[budget_id] = categories


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


@mcp.tool()
async def create_transaction(
    account_id: str,
    amount: Annotated[float, Field(description="Amount in dollars")],
    payee_name: str,
    category_name: Optional[str] = None,
    memo: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new transaction in YNAB."""
    with _get_client() as client:
        transactions_api = ynab.TransactionsApi(client)
        budgets_api = ynab.BudgetsApi(client)

        amount_milliunits = int(amount * 1000)

        # Use preferred budget ID if available, otherwise fetch a list of budgets
        budget_id = ynab_resources.preferred_budget_id
        if not budget_id:
            budgets_response = budgets_api.get_budgets()
            budget_id = budgets_response.data.budgets[0].id

        category_id = None
        if category_name:
            # Use cached categories if available
            # cached_categories = ynab_resources.get_cached_categories(budget_id)
            # if cached_categories:
            #     for cat in cached_categories:
            #         if cat.get("name", "").lower() == category_name.lower():
            #             category_id = cat.get("id")
            #             break
            # else:
            # Fetch categories from API if not cached
            categories_api = ynab.CategoriesApi(client)
            categories_response = categories_api.get_categories(budget_id)
            categories = categories_response.data.category_groups
            for group in categories:
                for cat in group.categories:
                    if cat.name.lower() == category_name.lower():
                        category_id = cat.id
                        break
                if category_id:
                    break

        transaction = {
            "account_id": account_id,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "amount": amount_milliunits,
            "payee_name": payee_name,
            "memo": memo,
            "category_id": category_id,
        }

        response = transactions_api.create_transaction(budget_id, {"transaction": transaction})
        return response.data.transaction.to_dict()


@mcp.tool()
async def get_account_balance(account_id: str) -> float:
    """Get the current balance of a YNAB account (in dollars)."""
    with _get_client() as client:
        accounts_api = ynab.AccountsApi(client)
        budgets_api = ynab.BudgetsApi(client)
        budgets_response = budgets_api.get_budgets()
        budget_id = budgets_response.data.budgets[0].id

        response = accounts_api.get_account_by_id(budget_id, account_id)
        return float(response.data.account.balance) / 1000


@mcp.tool()
async def get_budgets() -> str:
    """List all YNAB budgets in Markdown format."""
    with _get_client() as client:
        budgets_api = ynab.BudgetsApi(client)
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
    with _get_client() as client:
        accounts_api = ynab.AccountsApi(client)
        all_accounts = []
        response = accounts_api.get_accounts(budget_id)
        all_accounts.extend(account.to_dict() for account in response.data.accounts)

        formatted = _format_accounts_output(all_accounts)

        markdown = "# YNAB Account Summary\n\n"
        markdown += "## Summary\n"
        markdown += f"- **Total Assets:** {formatted['summary']['total_assets']}\n"
        markdown += f"- **Total Liabilities:** {formatted['summary']['total_liabilities']}\n"
        markdown += f"- **Net Worth:** {formatted['summary']['net_worth']}\n\n"

        headers = ["Account Name", "Balance", "ID"]
        align = ["left", "right", "left"]

        for group in formatted["accounts"]:
            markdown += f"## {group['type']}\n"
            markdown += f"**Group Total:** {group['total']}\n\n"

            rows = []
            for acct in group["accounts"]:
                rows.append([acct["name"], acct["balance"], acct["id"]])

            markdown += _build_markdown_table(rows, headers, align)
            markdown += "\n"

        return markdown


@mcp.tool()
async def get_transactions(budget_id: str, account_id: str) -> str:
    """Get recent transactions for a specific account in a specific budget."""
    with _get_client() as client:
        transactions_api = ynab.TransactionsApi(client)
        all_transactions = []
        since_date = datetime.now().replace(day=1).date()
        response = transactions_api.get_transactions_by_account(
            budget_id, account_id, since_date=since_date
        )
        all_transactions.extend(txn.to_dict() for txn in response.data.transactions)

        markdown = "# Recent Transactions\n\n"
        if not all_transactions:
            return markdown + "_No recent transactions found._\n"

        headers = ["Date", "Amount", "Payee Name", "Category Name", "Memo"]
        align = ["left", "right", "left", "left", "left"]
        rows = []

        for txn in all_transactions:
            date_str = txn.get("date", "N/A")
            amount_str = f"${txn.get('amount', 0) / 1000:,.2f}"
            payee_str = txn.get("payee_name", "N/A")
            category_str = txn.get("category_name", "N/A")
            memo_str = txn.get("memo", "N/A") or ""
            rows.append(
                [
                    date_str,
                    amount_str,
                    payee_str,
                    category_str,
                    memo_str,
                ]
            )

        markdown += _build_markdown_table(rows, headers, align)
        return markdown


@mcp.tool()
async def get_uncategorized_transactions(budget_id: str) -> str:
    """List all uncategorized transactions for a given YNAB budget in Markdown format."""
    with _get_client() as client:
        accounts_api = ynab.AccountsApi(client)
        transactions_api = ynab.TransactionsApi(client)
        accounts_response = accounts_api.get_accounts(budget_id)
        active_accounts = []
        for account in accounts_response.data.accounts:
            account_dict = account.to_dict()
            if account_dict.get("closed") or account_dict.get("deleted"):
                continue
            active_accounts.append(account_dict)

        since_date = datetime.now().replace(day=1).date()
        all_transactions = []
        for account in active_accounts:
            resp = transactions_api.get_transactions_by_account(
                budget_id, account["id"], since_date=since_date
            )
            for txn in resp.data.transactions:
                txn_dict = txn.to_dict()
                txn_dict["account_name"] = account["name"]
                all_transactions.append(txn_dict)

        uncategorized = [txn for txn in all_transactions if txn.get("category_id") in (None, "")]

        markdown = "# Uncategorized Transactions\n\n"
        if not uncategorized:
            return markdown + "_No uncategorized transactions found._"

        headers = ["Date", "Account", "Amount", "Payee", "Memo"]
        align = ["left", "left", "right", "left", "left"]
        rows = []

        for txn in uncategorized:
            date_str = str(txn.get("date", "N/A"))
            account_name = txn.get("account_name", "N/A")
            amount_dollars = float(txn.get("amount", 0)) / 1000
            amount_str = f"${abs(amount_dollars):,.2f}"
            if amount_dollars < 0:
                amount_str = f"-{amount_str}"
            payee_name = txn.get("payee_name", "N/A")
            memo = txn.get("memo", "") or ""
            rows.append(
                [
                    date_str,
                    account_name,
                    amount_str,
                    payee_name,
                    memo,
                ]
            )

        markdown += _build_markdown_table(rows, headers, align)
        return markdown


@mcp.tool()
async def categorize_transactions(budget_id: str, category_id: str) -> str:
    """Categorize all uncategorized transactions for a given YNAB budget with the provided category ID."""
    with _get_client() as client:
        accounts_api = ynab.AccountsApi(client)
        transactions_api = ynab.TransactionsApi(client)
        accounts_response = accounts_api.get_accounts(budget_id)
        active_accounts = []
        for account in accounts_response.data.accounts:
            account_dict = account.to_dict()
            if account_dict.get("closed") or account_dict.get("deleted"):
                continue
            active_accounts.append(account_dict)

        since_date = datetime.now().replace(day=1).date()
        updated_txns = []
        for account in active_accounts:
            resp = transactions_api.get_transactions_by_account(
                budget_id, account["id"], since_date=since_date
            )
            for txn in resp.data.transactions:
                txn_dict = txn.to_dict()
                if txn_dict.get("category_id") in (None, ""):
                    update_payload = {"transaction": {"category_id": category_id}}
                    transactions_api.update_transaction(budget_id, txn_dict["id"], update_payload)
                    updated_txns.append(txn_dict["id"])

        return f"Updated {len(updated_txns)} transactions with category ID {category_id}."


@mcp.tool()
async def get_categories(budget_id: str) -> str:
    """List all transaction categories for a given YNAB budget in Markdown format."""
    with _get_client() as client:
        categories_api = ynab.CategoriesApi(client)
        response = categories_api.get_categories(budget_id)
        groups = response.data.category_groups

        markdown = "# YNAB Categories\n\n"
        for group in groups:
            group_dict = group.to_dict() if hasattr(group, "to_dict") else group
            categories_list = group_dict.get("categories", [])
            if not categories_list:
                continue

            markdown += f"## {group_dict.get('name', 'Unnamed Group')}\n\n"

            headers = ["Category ID", "Category Name", "Budgeted", "Activity"]
            align = ["left", "left", "right", "right"]
            rows = []

            for category in categories_list:
                cat = category.to_dict() if hasattr(category, "to_dict") else category
                cat_id = cat.get("id", "N/A")
                name = cat.get("name", "N/A")
                budgeted = cat.get("budgeted", 0)
                activity = cat.get("activity", 0)
                budgeted_dollars = (
                    float(budgeted) / 1000 if isinstance(budgeted, (int, float)) else 0
                )
                activity_dollars = (
                    float(activity) / 1000 if isinstance(activity, (int, float)) else 0
                )
                budget_str = f"${abs(budgeted_dollars):,.2f}"
                if budgeted_dollars < 0:
                    budget_str = f"-{budget_str}"
                activity_str = f"${abs(activity_dollars):,.2f}"
                if activity_dollars < 0:
                    activity_str = f"-{activity_str}"

                rows.append(
                    [
                        cat_id,
                        name,
                        budget_str,
                        activity_str,
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
    with _get_client() as client:
        categories_api = ynab.CategoriesApi(client)
        response = categories_api.get_categories(budget_id)
        groups = response.data.category_groups
        categories = []
        for group in groups:
            group_dict = group.to_dict() if hasattr(group, "to_dict") else group
            categories_list = group_dict.get("categories", [])
            categories.extend(categories_list)

        ynab_resources.cache_categories(budget_id, categories)
        return f"Categories cached for budget ID {budget_id}"
