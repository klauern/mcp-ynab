"""Pure formatting helpers for YNAB tool output.

Markdown table builders, account-summary structuring, and dollar-amount
formatting. None of these touch the YNAB API or any module-level state, so
they can be imported freely without circular-import concerns.
"""

from typing import Any, Dict, List, Optional, cast

from ynab.models.category import Category


def _get_empty_table(headers: List[str]) -> str:
    """Create an empty markdown table with just headers."""
    widths = [len(h) + 2 for h in headers]
    header_line = (
        "| " + " | ".join(f"{headers[i]:<{widths[i]}}" for i in range(len(headers))) + " |\n"
    )
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
