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


@mcp.resource("ynab://budgets")
async def get_budgets() -> List[Dict[str, Any]]:
    """List all YNAB budgets."""
    with _get_client() as client:
        budgets_api = ynab.BudgetsApi(client)
        budgets_response = budgets_api.get_budgets()
        return [budget.to_dict() for budget in budgets_response.data.budgets]


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


@mcp.resource("ynab://accounts")
async def get_accounts() -> Dict[str, Any]:
    """List all YNAB accounts across all budgets."""
    with _get_client() as client:
        budgets_api = ynab.BudgetsApi(client)
        accounts_api = ynab.AccountsApi(client)
        all_accounts = []

        budgets_response = budgets_api.get_budgets()
        for budget in budgets_response.data.budgets:
            try:
                response = accounts_api.get_accounts(budget.id)
                all_accounts.extend(account.to_dict() for account in response.data.accounts)
            except ynab.ApiException:
                continue

        return _format_accounts_output(all_accounts)


@mcp.resource("ynab://transactions/{account_id}")
async def get_transactions(account_id: str) -> List[Dict[str, Any]]:
    """Get recent transactions for a specific account."""
    with _get_client() as client:
        transactions_api = ynab.TransactionsApi(client)
        budgets_api = ynab.BudgetsApi(client)
        all_transactions = []

        # Find which budget contains this account
        budgets_response = budgets_api.get_budgets()
        for budget in budgets_response.data.budgets:
            try:
                # Example: get transactions since the start of the month
                since_date = datetime.now().replace(day=1).strftime("%Y-%m-%d")
                response = transactions_api.get_transactions_by_account(
                    budget.id, account_id, since_date=since_date
                )
                all_transactions.extend(txn.to_dict() for txn in response.data.transactions)
                # If we found transactions, we found the right budget
                if all_transactions:
                    break
            except ynab.ApiException:
                # Account not found in this budget, try the next one
                continue

        return all_transactions
