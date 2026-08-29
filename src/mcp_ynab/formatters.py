"""Pure formatting helpers for YNAB tool output.

Markdown table builders, account-summary structuring, and dollar-amount
formatting. None of these touch the YNAB API or any module-level state, so
they can be imported freely without circular-import concerns.
"""

from typing import Any, Dict, List, Optional, cast

from ynab.models.category import Category

from .money import CurrencyInfo, decimal_to_milliunits, format_money, milliunits_to_decimal


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

    # Cells are display data — coerce to str so non-str values (e.g. uuid.UUID
    # ids from ynab >=2.x response models) don't break len()/format below.
    rows = [[str(cell) for cell in row] for row in rows]
    alignments = alignments if alignments is not None else ["left"] * len(headers)
    col_count = len(headers)
    widths = _get_column_widths(headers, rows, col_count)

    header_line = _format_table_line(headers, widths, alignments)
    sep_line = "|" + "|".join("-" * (w + 1) for w in widths) + "|\n"

    row_lines = "".join(_format_table_line(row, widths, alignments) for row in rows)
    return header_line + sep_line + row_lines


def _format_accounts_output(
    accounts: List[Dict[str, Any]], currency: Optional[CurrencyInfo] = None
) -> Dict[str, Any]:
    """Format account data into a user-friendly structure.

    All 13 official YNAB ``AccountType`` values are grouped and classified
    into asset/liability totals.  Unknown future types are preserved in their
    own group rather than silently dropped, so a new YNAB account type can
    never disappear from an MCP response.
    """
    account_groups: Dict[str, List[Dict[str, Any]]] = {}

    # Every official AccountType (OpenAPI 1.86).  Order determines display
    # order; an account of any other type is appended at the end so it stays
    # visible even though we do not yet know how to classify it.
    type_order = [
        "checking",
        "savings",
        "cash",
        "creditCard",
        "lineOfCredit",
        "mortgage",
        "autoLoan",
        "studentLoan",
        "personalLoan",
        "medicalDebt",
        "otherAsset",
        "otherLiability",
        "otherDebt",
    ]

    type_display_names = {
        "checking": "Checking Accounts",
        "savings": "Savings Accounts",
        "cash": "Cash Accounts",
        "creditCard": "Credit Cards",
        "lineOfCredit": "Lines of Credit",
        "mortgage": "Mortgages",
        "autoLoan": "Auto Loans",
        "studentLoan": "Student Loans",
        "personalLoan": "Personal Loans",
        "medicalDebt": "Medical Debt",
        "otherAsset": "Other Assets",
        "otherLiability": "Other Liabilities",
        "otherDebt": "Other Debt",
    }

    # Asset types carry a positive balance; liability types are reported as
    # positive totals.  Anything unknown is left unclassified (not counted in
    # either total) but still rendered.
    asset_types = frozenset({"checking", "savings", "cash", "otherAsset"})
    liability_types = frozenset(
        {
            "creditCard",
            "lineOfCredit",
            "mortgage",
            "autoLoan",
            "studentLoan",
            "personalLoan",
            "medicalDebt",
            "otherLiability",
            "otherDebt",
        }
    )

    for account in accounts:
        if account.get("closed", False) or account.get("deleted", False):
            continue

        acct_type = account["type"]
        if acct_type not in account_groups:
            account_groups[acct_type] = []

        balance_milliunits = int(account["balance"])
        balance = float(milliunits_to_decimal(balance_milliunits))
        # Prefer the SDK's own currency-formatted balance when the API supplied
        # it (official formatted field, always right for the plan's currency);
        # fall back to our currency-aware renderer for mocks/legacy payloads.
        # Str-typed only: an unconfigured mock's auto-attribute must not leak
        # into display output.
        balance_display = account.get("balance_formatted")
        if not isinstance(balance_display, str):
            balance_display = format_money(balance_milliunits, currency)
        account_groups[acct_type].append(
            {
                "name": account["name"],
                "balance": balance_display,
                "balance_raw": balance,
                "balance_milliunits": balance_milliunits,
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

    def _append_group(acct_type: str) -> None:
        group_data = {
            "type": type_display_names.get(acct_type, acct_type),
            "accounts": account_groups[acct_type],
        }
        group_total_milliunits = sum(
            acct["balance_milliunits"] for acct in account_groups[acct_type]
        )
        group_total = float(milliunits_to_decimal(group_total_milliunits))
        group_data["total"] = format_money(group_total_milliunits, currency)

        if acct_type in asset_types:
            output["summary"]["total_assets"] += group_total
        elif acct_type in liability_types:
            output["summary"]["total_liabilities"] += sum(
                -min(acct["balance_raw"], 0.0) for acct in account_groups[acct_type]
            )

        if acct_type in asset_types or acct_type in liability_types:
            output["summary"]["net_worth"] += group_total

        output["accounts"].append(group_data)

    for acct_type in type_order:
        if account_groups.get(acct_type):
            _append_group(acct_type)

    # Unknown future account types stay visible instead of being dropped.
    for acct_type in account_groups:
        if acct_type not in type_order and account_groups[acct_type]:
            _append_group(acct_type)

    output["summary"]["net_worth_raw"] = output["summary"]["net_worth"]
    output["summary"]["total_assets"] = format_money(
        decimal_to_milliunits(output["summary"]["total_assets"]), currency
    )
    output["summary"]["total_liabilities"] = format_money(
        decimal_to_milliunits(output["summary"]["total_liabilities"]), currency
    )
    output["summary"]["net_worth"] = format_money(
        decimal_to_milliunits(output["summary"]["net_worth_raw"]), currency
    )

    for group_data in output["accounts"]:
        for account in group_data["accounts"]:
            account.pop("balance_milliunits", None)

    return output


def _process_category_data(category: Category | Dict[str, Any]) -> tuple[str, str, float, float]:
    """Process category data and return tuple of (id, name, budgeted, activity)."""
    if isinstance(category, Category):
        return str(category.id), category.name, category.budgeted, category.activity
    cat_dict = cast(Dict[str, Any], category)
    return cat_dict["id"], cat_dict["name"], cat_dict["budgeted"], cat_dict["activity"]


def _format_dollar_amount(amount: float) -> str:
    """Backward-compatible USD formatter for existing callers.

    Intentional policy change: money values now round half-up via
    :func:`mcp_ynab.money.decimal_to_milliunits` instead of Python's binary
    float formatting, so edge values like ``2.675`` render ``$2.68`` rather
    than the old float artifact ``$2.67``.  Ordinary USD output (two decimal
    places, thousands separators, leading minus) is unchanged.
    """
    return format_money(decimal_to_milliunits(amount))


def _render_month_markdown(month_detail: Any) -> str:
    """Render a YNAB MonthDetail as markdown: header, totals, per-group table."""
    month_value = getattr(month_detail, "month", None)
    isoformat = getattr(month_value, "isoformat", None)
    month_label = str(isoformat()) if callable(isoformat) else str(month_value)

    rta = float(getattr(month_detail, "to_be_budgeted", 0) or 0) / 1000
    income = float(getattr(month_detail, "income", 0) or 0) / 1000
    budgeted = float(getattr(month_detail, "budgeted", 0) or 0) / 1000
    activity = float(getattr(month_detail, "activity", 0) or 0) / 1000
    age_of_money = getattr(month_detail, "age_of_money", None)

    md = f"# YNAB Month: {month_label}\n\n"
    md += "## Summary\n"
    md += f"- **Ready to Assign:** {_format_dollar_amount(rta)}\n"
    md += f"- **Age of Money:** {age_of_money if age_of_money is not None else 'N/A'} days\n"
    md += f"- **Income:** {_format_dollar_amount(income)}\n"
    md += f"- **Budgeted:** {_format_dollar_amount(budgeted)}\n"
    md += f"- **Activity:** {_format_dollar_amount(activity)}\n\n"

    categories: List[Any] = list(getattr(month_detail, "categories", []) or [])
    grouped: Dict[str, List[Any]] = {}
    for cat in categories:
        if getattr(cat, "hidden", False) or getattr(cat, "deleted", False):
            continue
        group_name = getattr(cat, "category_group_name", None) or "Uncategorized"
        grouped.setdefault(group_name, []).append(cat)

    headers = ["Category ID", "Category Name", "Budgeted", "Activity", "Balance"]
    align = ["left", "left", "right", "right", "right"]
    for group_name in sorted(grouped):
        md += f"## {group_name}\n\n"
        rows: List[List[str]] = []
        for cat in grouped[group_name]:
            cat_id = getattr(cat, "id", "")
            name = getattr(cat, "name", "")
            b = float(getattr(cat, "budgeted", 0) or 0) / 1000
            a = float(getattr(cat, "activity", 0) or 0) / 1000
            bal = float(getattr(cat, "balance", 0) or 0) / 1000
            rows.append(
                [
                    str(cat_id),
                    str(name),
                    _format_dollar_amount(b),
                    _format_dollar_amount(a),
                    _format_dollar_amount(bal),
                ]
            )
        md += _build_markdown_table(rows, headers, align) + "\n"

    return md


def _render_month_category_markdown(category: Any) -> str:
    """Render a single Category's month detail as markdown."""
    name = getattr(category, "name", "Unknown")
    cat_id = getattr(category, "id", "")
    budgeted = float(getattr(category, "budgeted", 0) or 0) / 1000
    activity = float(getattr(category, "activity", 0) or 0) / 1000
    balance = float(getattr(category, "balance", 0) or 0) / 1000
    goal_type = getattr(category, "goal_type", None)
    goal_target = getattr(category, "goal_target", None)
    goal_pct = getattr(category, "goal_percentage_complete", None)
    note = getattr(category, "note", None)

    md = f"# {name}\n\n"
    md += f"- **ID:** {cat_id}\n"
    md += f"- **Budgeted:** {_format_dollar_amount(budgeted)}\n"
    md += f"- **Activity:** {_format_dollar_amount(activity)}\n"
    md += f"- **Balance:** {_format_dollar_amount(balance)}\n"
    if goal_type:
        target = float(goal_target or 0) / 1000
        md += f"- **Goal:** {goal_type} (target {_format_dollar_amount(target)}"
        if goal_pct is not None:
            md += f", {goal_pct}% complete"
        md += ")\n"
    if note:
        md += f"\n**Note:** {note}\n"
    return md
