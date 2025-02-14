"""
MCP server implementation for YNAB API integration.
"""

import os
from datetime import datetime
from typing import Annotated, Any, Dict, List, Optional

import ynab
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# Load environment variables at module level
load_dotenv(verbose=True)

ynab_api_key = os.environ.get("YNAB_API_KEY")


def _get_client() -> ynab.ApiClient:
    """Get a configured YNAB API client. Reads API key from environment variables."""
    api_key = ynab_api_key
    if not api_key:
        raise ValueError("YNAB_API_KEY not found in environment variables")

    configuration = ynab.Configuration(access_token=api_key)
    return ynab.ApiClient(configuration)


# Create the MCP server instance
mcp = FastMCP("YNAB")


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

        # Convert dollars to milliunits (YNAB uses milliunits internally)
        amount_milliunits = int(amount * 1000)

        # Get the default budget ID
        budgets_response = budgets_api.get_budgets()
        budget_id = budgets_response.data.budgets[0].id

        # Look up category_id if category_name was provided
        category_id = None
        if category_name:
            categories_api = ynab.CategoriesApi(client)
            categories = categories_api.get_categories(budget_id).data.category_groups
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

        # Build Markdown output for budgets.
        markdown = "# YNAB Budgets\n\n"
        if not budgets_list:
            markdown += "_No budgets found._"
        else:
            for budget in budgets_list:
                b = budget.to_dict()
                markdown += f"- **{b.get('name', 'Unnamed Budget')}** (ID: {b.get('id')})\n"
        return markdown


def _format_accounts_output(accounts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Format account data into a user-friendly structure."""
    # Group accounts by type
    account_groups = {}

    # Define display order for account types
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

    # Filter and group active accounts
    for account in accounts:
        if account.get("closed", False) or account.get("deleted", False):
            continue

        acct_type = account["type"]
        if acct_type not in account_groups:
            account_groups[acct_type] = []

        # Convert balance from milliunits to dollars
        balance = float(account["balance"]) / 1000

        formatted_account = {
            "name": account["name"],
            "balance": f"${balance:,.2f}",
            "balance_raw": balance,
            "id": account["id"],
        }

        account_groups[acct_type].append(formatted_account)

    # Sort accounts within each group by balance
    for group in account_groups.values():
        group.sort(key=lambda x: abs(x["balance_raw"]), reverse=True)

    # Create final output structure
    output = {
        "accounts": [],
        "summary": {
            "total_assets": 0,
            "total_liabilities": 0,
            "net_worth": 0,
        },
    }

    # Add accounts in the defined order
    for acct_type in type_order:
        if acct_type in account_groups and account_groups[acct_type]:
            group_data = {
                "type": type_display_names.get(acct_type, acct_type),
                "accounts": account_groups[acct_type],
            }

            # Calculate group total
            group_total = sum(acct["balance_raw"] for acct in account_groups[acct_type])
            group_data["total"] = f"${group_total:,.2f}"

            # Update summary totals
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

    # Calculate net worth and format summary totals
    output["summary"]["net_worth"] = (
        output["summary"]["total_assets"] - output["summary"]["total_liabilities"]
    )
    output["summary"]["total_assets"] = f"${output['summary']['total_assets']:,.2f}"
    output["summary"]["total_liabilities"] = f"${output['summary']['total_liabilities']:,.2f}"
    output["summary"]["net_worth"] = f"${output['summary']['net_worth']:,.2f}"

    return output


@mcp.tool()
async def get_accounts(budget_id: str) -> str:
    """List all YNAB accounts in a specific budget in Markdown format."""
    with _get_client() as client:
        accounts_api = ynab.AccountsApi(client)
        all_accounts = []

        response = accounts_api.get_accounts(budget_id)
        all_accounts.extend(account.to_dict() for account in response.data.accounts)

        formatted = _format_accounts_output(all_accounts)

        # Build Markdown output for accounts.
        markdown = "# YNAB Account Summary\n\n"
        markdown += "## Summary\n"
        markdown += f"- **Total Assets:** {formatted['summary']['total_assets']}\n"
        markdown += f"- **Total Liabilities:** {formatted['summary']['total_liabilities']}\n"
        markdown += f"- **Net Worth:** {formatted['summary']['net_worth']}\n\n"

        for group in formatted["accounts"]:
            markdown += f"## {group['type']}\n"
            markdown += f"**Group Total:** {group['total']}\n\n"

            # Calculate column widths
            headers = ["Account Name", "Balance", "ID"]
            name_width = len(headers[0])
            balance_width = len(headers[1])
            id_width = len(headers[2])

            # First pass to determine column widths
            for acct in group["accounts"]:
                name_width = max(name_width, len(acct["name"]))
                balance_width = max(balance_width, len(acct["balance"]))
                id_width = max(id_width, len(acct["id"]))

            # Add padding
            name_width += 2
            balance_width += 2
            id_width += 2

            # Build header and separator
            header = f"| {headers[0]:<{name_width}} | {headers[1]:>{balance_width}} | {headers[2]:<{id_width}} |\n"
            separator = f"|{'-' * name_width}|{'-' * balance_width}|{'-' * id_width}|\n"

            markdown += header
            markdown += separator

            # Second pass: output data
            for acct in group["accounts"]:
                markdown += f"| {acct['name']:<{name_width}} | {acct['balance']:>{balance_width}} | {acct['id']:<{id_width}} |\n"

            markdown += "\n"

        return markdown


@mcp.tool()
async def get_transactions(budget_id: str, account_id: str) -> str:
    """Get recent transactions for a specific account in a specific budget."""
    with _get_client() as client:
        transactions_api = ynab.TransactionsApi(client)
        all_transactions = []

        # Example: get transactions since the start of the month
        since_date = datetime.now().replace(day=1).date()
        response = transactions_api.get_transactions_by_account(
            budget_id, account_id, since_date=since_date
        )
        all_transactions.extend(txn.to_dict() for txn in response.data.transactions)

        # Build Markdown output for transactions
        markdown = "# Recent Transactions\n\n"

        # Calculate column widths
        headers = ["Date", "Amount", "Payee Name", "Category Name", "Memo"]
        date_width = len(headers[0])
        amount_width = len(headers[1])
        payee_width = len(headers[2])
        category_width = len(headers[3])
        memo_width = len(headers[4])

        # First pass to determine column widths
        for txn in all_transactions:
            date_str = txn.get("date", "N/A")
            amount_str = f"${txn.get('amount', 0) / 1000:,.2f}"
            payee_str = txn.get("payee_name", "N/A")
            category_str = txn.get("category_name", "N/A")
            memo_str = txn.get("memo", "N/A")

            date_width = max(date_width, len(date_str))
            amount_width = max(amount_width, len(amount_str))
            payee_width = max(payee_width, len(payee_str))
            category_width = max(category_width, len(category_str))
            memo_width = max(memo_width, len(memo_str))

        # Add padding
        date_width += 2
        amount_width += 2
        payee_width += 2
        category_width += 2
        memo_width += 2

        # Build header and separator
        header = (
            f"| {headers[0]:<{date_width}} "
            f"| {headers[1]:>{amount_width}} "
            f"| {headers[2]:<{payee_width}} "
            f"| {headers[3]:<{category_width}} "
            f"| {headers[4]:<{memo_width}} |\n"
        )
        separator = f"|{'-' * date_width}|{'-' * amount_width}|{'-' * payee_width}|{'-' * category_width}|{'-' * memo_width}|\n"

        markdown += header
        markdown += separator

        # Second pass: output data
        for txn in all_transactions:
            date_str = txn.get("date", "N/A")
            amount_str = f"${txn.get('amount', 0) / 1000:,.2f}"
            payee_str = txn.get("payee_name", "N/A")
            category_str = txn.get("category_name", "N/A")
            memo_str = txn.get("memo", "N/A")

            markdown += (
                f"| {date_str:<{date_width}} "
                f"| {amount_str:>{amount_width}} "
                f"| {payee_str:<{payee_width}} "
                f"| {category_str:<{category_width}} "
                f"| {memo_str:<{memo_width}} |\n"
            )

        return markdown


@mcp.tool()
async def get_uncategorized_transactions(budget_id: str) -> str:
    """List all uncategorized transactions for a given YNAB budget in Markdown format."""
    with _get_client() as client:
        accounts_api = ynab.AccountsApi(client)
        transactions_api = ynab.TransactionsApi(client)

        # Retrieve all active accounts in the budget
        accounts_response = accounts_api.get_accounts(budget_id)
        active_accounts = []
        for account in accounts_response.data.accounts:
            account_dict = account.to_dict()
            if account_dict.get("closed") or account_dict.get("deleted"):
                continue
            active_accounts.append(account_dict)

        # Define since_date as the first day of the current month (as a date object)
        since_date = datetime.now().replace(day=1).date()

        all_transactions = []
        # Collect transactions from each active account
        for account in active_accounts:
            resp = transactions_api.get_transactions_by_account(
                budget_id, account["id"], since_date=since_date
            )
            for txn in resp.data.transactions:
                txn_dict = txn.to_dict()
                txn_dict["account_name"] = account["name"]
                all_transactions.append(txn_dict)

        # Filter out only uncategorized transactions (where category_id is None or empty string)
        uncategorized = [txn for txn in all_transactions if txn.get("category_id") in (None, "")]

        markdown = "# Uncategorized Transactions\n\n"
        if not uncategorized:
            markdown += "_No uncategorized transactions found._"
        else:
            # Calculate column widths
            headers = ["Date", "Account", "Amount", "Payee", "Memo"]
            date_width = len(headers[0])
            account_width = len(headers[1])
            amount_width = len(headers[2])
            payee_width = len(headers[3])
            memo_width = len(headers[4])

            # First pass to determine column widths
            for txn in uncategorized:
                date_str = str(txn.get("date", "N/A"))
                account_name = txn.get("account_name", "N/A")
                amount_dollars = float(txn.get("amount", 0)) / 1000
                amount_str = f"${abs(amount_dollars):,.2f}"
                if amount_dollars < 0:
                    amount_str = f"-{amount_str}"
                payee_name = txn.get("payee_name", "N/A")
                memo = txn.get("memo", "N/A")

                date_width = max(date_width, len(date_str))
                account_width = max(account_width, len(account_name))
                amount_width = max(amount_width, len(amount_str))
                payee_width = max(payee_width, len(payee_name))
                memo_width = max(memo_width, len(memo))

            # Add padding
            date_width += 2
            account_width += 2
            amount_width += 2
            payee_width += 2
            memo_width += 2

            # Build header and separator
            header = (
                f"| {headers[0]:<{date_width}} "
                f"| {headers[1]:<{account_width}} "
                f"| {headers[2]:>{amount_width}} "
                f"| {headers[3]:<{payee_width}} "
                f"| {headers[4]:<{memo_width}} |\n"
            )
            separator = (
                f"|{'-' * date_width}|{'-' * account_width}|{'-' * amount_width}|"
                f"{'-' * payee_width}|{'-' * memo_width}|\n"
            )

            markdown += header
            markdown += separator

            # Second pass: output data
            for txn in uncategorized:
                date_str = str(txn.get("date", "N/A"))
                account_name = txn.get("account_name", "N/A")
                amount_dollars = float(txn.get("amount", 0)) / 1000
                amount_str = f"${abs(amount_dollars):,.2f}"
                if amount_dollars < 0:
                    amount_str = f"-{amount_str}"
                payee_name = txn.get("payee_name", "N/A")
                memo = txn.get("memo", "N/A")

                markdown += (
                    f"| {date_str:<{date_width}} "
                    f"| {account_name:<{account_width}} "
                    f"| {amount_str:>{amount_width}} "
                    f"| {payee_name:<{payee_width}} "
                    f"| {memo:<{memo_width}} |\n"
                )

        return markdown


@mcp.tool()
async def categorize_transactions(budget_id: str, category_id: str) -> str:
    """Categorize all uncategorized transactions for a given YNAB budget with the provided category ID."""
    with _get_client() as client:
        accounts_api = ynab.AccountsApi(client)
        transactions_api = ynab.TransactionsApi(client)

        # Retrieve all active accounts in the budget
        accounts_response = accounts_api.get_accounts(budget_id)
        active_accounts = []
        for account in accounts_response.data.accounts:
            account_dict = account.to_dict()
            if account_dict.get("closed") or account_dict.get("deleted"):
                continue
            active_accounts.append(account_dict)

        # Define since_date as the first day of the current month (as a date object)
        since_date = datetime.now().replace(day=1).date()
        updated_txns = []

        # Loop over each account and update uncategorized transactions
        for account in active_accounts:
            resp = transactions_api.get_transactions_by_account(
                budget_id, account["id"], since_date=since_date
            )
            for txn in resp.data.transactions:
                txn_dict = txn.to_dict()
                if txn_dict.get("category_id") in (None, ""):
                    update_payload = {"transaction": {"category_id": category_id}}
                    update_resp = transactions_api.update_transaction(
                        budget_id, txn_dict["id"], update_payload
                    )
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

            # First pass: calculate maximum widths
            id_width = len("Category ID")
            name_width = len("Category Name")
            budget_width = len("Budgeted")
            activity_width = len("Activity")

            # Examine all categories in this group to find maximum widths
            for category in group_dict.get("categories", []):
                cat = category.to_dict() if hasattr(category, "to_dict") else category
                cat_id = cat.get("id", "N/A")
                name = cat.get("name", "N/A")
                budgeted = cat.get("budgeted", 0)
                activity = cat.get("activity", 0)

                # Calculate widths needed for this row
                id_width = max(id_width, len(cat_id))
                name_width = max(name_width, len(name))

                # Convert milliunits to dollars for width calculation
                budgeted_dollars = (
                    float(budgeted) / 1000 if isinstance(budgeted, (int, float)) else 0
                )
                activity_dollars = (
                    float(activity) / 1000 if isinstance(activity, (int, float)) else 0
                )

                budget_str = f"${abs(budgeted_dollars):,.2f}"
                activity_str = f"${abs(activity_dollars):,.2f}"
                if budgeted_dollars < 0:
                    budget_str = f"-{budget_str}"
                if activity_dollars < 0:
                    activity_str = f"-{activity_str}"

                budget_width = max(budget_width, len(budget_str))
                activity_width = max(activity_width, len(activity_str))

            # Add some padding between columns
            id_width += 2
            name_width += 2
            budget_width += 2
            activity_width += 2

            # Create the header with calculated widths
            header = f"| {'Category ID':<{id_width}} | {'Category Name':<{name_width}} | {'Budgeted':>{budget_width}} | {'Activity':>{activity_width}} |\n"
            separator = f"|{'-' * (id_width + 2)}|{'-' * (name_width + 2)}|{'-' * (budget_width + 2)}|{'-' * (activity_width + 2)}|\n"

            markdown += f"## {group_dict.get('name', 'Unnamed Group')}\n\n"
            markdown += header
            markdown += separator

            # Second pass: output the data using calculated widths
            for category in group_dict.get("categories", []):
                cat = category.to_dict() if hasattr(category, "to_dict") else category
                cat_id = cat.get("id", "N/A")
                name = cat.get("name", "N/A")
                budgeted = cat.get("budgeted", 0)
                activity = cat.get("activity", 0)

                # Convert milliunits to dollars
                budgeted_dollars = (
                    float(budgeted) / 1000 if isinstance(budgeted, (int, float)) else 0
                )
                activity_dollars = (
                    float(activity) / 1000 if isinstance(activity, (int, float)) else 0
                )

                # Format with consistent sign placement
                budget_str = f"${abs(budgeted_dollars):,.2f}"
                if budgeted_dollars < 0:
                    budget_str = f"-{budget_str}"

                activity_str = f"${abs(activity_dollars):,.2f}"
                if activity_dollars < 0:
                    activity_str = f"-{activity_str}"

                markdown += f"| {cat_id:<{id_width}} | {name:<{name_width}} | {budget_str:>{budget_width}} | {activity_str:>{activity_width}} |\n"

            markdown += "\n"
        return markdown
