"""Unit tests for the Budgeting Core epic (mcp-ynab-jwa).

Covers: get_month, get_category_for_month, assign_money, move_money, and the
two month resources. Patches MonthsApi / CategoriesApi via the
`mock_ynab_apis` fixture and asserts on rendered markdown plus PATCH bodies.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from ynab.rest import ApiException

from mcp_ynab import server


def _resp(**data_kwargs: object) -> MagicMock:
    """Build a `response` whose `.data` exposes the given attributes."""
    return MagicMock(data=SimpleNamespace(**data_kwargs))


def _category_mock(
    cat_id: str,
    name: str,
    *,
    budgeted: int = 0,
    activity: int = 0,
    balance: int = 0,
    category_group_name: str = "Daily",
    goal_type: str | None = None,
    goal_target: int | None = None,
    goal_percentage_complete: int | None = None,
    note: str | None = None,
    hidden: bool = False,
    deleted: bool = False,
) -> MagicMock:
    cat = MagicMock()
    cat.id = cat_id
    cat.name = name
    cat.budgeted = budgeted
    cat.activity = activity
    cat.balance = balance
    cat.category_group_name = category_group_name
    cat.goal_type = goal_type
    cat.goal_target = goal_target
    cat.goal_percentage_complete = goal_percentage_complete
    cat.note = note
    cat.hidden = hidden
    cat.deleted = deleted
    return cat


def _month_detail_mock(
    *,
    month_iso: str = "2026-05-01",
    to_be_budgeted: int = 250_000,
    age_of_money: int | None = 47,
    income: int = 4_500_000,
    budgeted: int = 4_250_000,
    activity: int = -1_800_000,
    categories: list[MagicMock] | None = None,
) -> MagicMock:
    md = MagicMock()
    md.month = date.fromisoformat(month_iso)
    md.to_be_budgeted = to_be_budgeted
    md.age_of_money = age_of_money
    md.income = income
    md.budgeted = budgeted
    md.activity = activity
    md.categories = categories or []
    return md


# ---------------------------------------------------------------------------
# get_month
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_month_renders_summary_and_groups(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    cats = [
        _category_mock(
            "c-1",
            "Groceries",
            budgeted=400_000,
            activity=-150_000,
            balance=250_000,
            category_group_name="Daily",
        ),
        _category_mock(
            "c-2",
            "Rent",
            budgeted=1_500_000,
            activity=-1_500_000,
            balance=0,
            category_group_name="Bills",
        ),
    ]
    mock_ynab_apis.months.get_budget_month.return_value = _resp(
        month=_month_detail_mock(categories=cats)
    )

    result = await server.get_month("budget-1")

    assert "# YNAB Month: 2026-05-01" in result
    assert "Ready to Assign" in result and "$250.00" in result
    assert "Age of Money:** 47 days" in result
    assert "## Bills" in result and "## Daily" in result
    assert "Groceries" in result and "Rent" in result
    # Verify SDK call shape
    args = mock_ynab_apis.months.get_budget_month.call_args
    assert args.args[0] == "budget-1"
    assert args.args[1] == date.today().replace(day=1)


@pytest.mark.asyncio
async def test_get_month_accepts_explicit_iso_date(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.months.get_budget_month.return_value = _resp(
        month=_month_detail_mock(month_iso="2026-04-01")
    )

    result = await server.get_month("budget-1", "2026-04-01")

    assert "2026-04-01" in result
    args = mock_ynab_apis.months.get_budget_month.call_args
    assert args.args[1] == date(2026, 4, 1)


@pytest.mark.asyncio
async def test_get_month_handles_missing_age_of_money(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.months.get_budget_month.return_value = _resp(
        month=_month_detail_mock(age_of_money=None)
    )

    result = await server.get_month("budget-1")

    assert "Age of Money:** N/A days" in result


# ---------------------------------------------------------------------------
# get_category_for_month
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_category_for_month_renders_detail(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    cat = _category_mock(
        "c-1",
        "Groceries",
        budgeted=400_000,
        activity=-150_000,
        balance=250_000,
        goal_type="TB",
        goal_target=500_000,
        goal_percentage_complete=80,
        note="Aldi only",
    )
    mock_ynab_apis.categories.get_month_category_by_id.return_value = _resp(category=cat)

    result = await server.get_category_for_month("budget-1", "c-1", "2026-05-01")

    assert "# Groceries" in result
    assert "Budgeted:** $400.00" in result
    assert "Activity:** -$150.00" in result
    assert "Balance:** $250.00" in result
    assert "TB" in result and "$500.00" in result and "80%" in result
    assert "Aldi only" in result

    args = mock_ynab_apis.categories.get_month_category_by_id.call_args
    assert args.args == ("budget-1", date(2026, 5, 1), "c-1")


# ---------------------------------------------------------------------------
# assign_money
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_money_patches_with_milliunits(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    cat = _category_mock("c-1", "Groceries", budgeted=400_000)
    mock_ynab_apis.categories.update_month_category.return_value = _resp(category=cat)

    result = await server.assign_money("budget-1", "c-1", 400.00, "2026-05-01")

    assert "Assigned $400.00 to **Groceries**" in result
    args = mock_ynab_apis.categories.update_month_category.call_args
    budget_id, month_arg, category_id, body = args.args
    assert (budget_id, category_id) == ("budget-1", "c-1")
    assert month_arg == date(2026, 5, 1)
    assert body.category.budgeted == 400_000


@pytest.mark.asyncio
async def test_assign_money_default_month_is_current(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.categories.update_month_category.return_value = _resp(
        category=_category_mock("c-1", "Groceries")
    )

    await server.assign_money("budget-1", "c-1", 50.00)

    args = mock_ynab_apis.categories.update_month_category.call_args
    assert args.args[1] == date.today().replace(day=1)


# ---------------------------------------------------------------------------
# move_money
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_money_issues_two_gets_and_two_patches(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    src = _category_mock("c-src", "Groceries", budgeted=400_000)
    dst = _category_mock("c-dst", "Dining", budgeted=100_000)
    mock_ynab_apis.categories.get_month_category_by_id.side_effect = [
        _resp(category=src),
        _resp(category=dst),
    ]
    mock_ynab_apis.categories.update_month_category.return_value = _resp(category=src)

    result = await server.move_money("budget-1", "c-src", "c-dst", 50.00, "2026-05-01")

    assert "Moved $50.00 from **Groceries** → **Dining**" in result
    assert mock_ynab_apis.categories.get_month_category_by_id.call_count == 2
    assert mock_ynab_apis.categories.update_month_category.call_count == 2

    debit_call, credit_call = mock_ynab_apis.categories.update_month_category.call_args_list
    assert debit_call.args[2] == "c-src"
    assert debit_call.args[3].category.budgeted == 350_000  # 400k - 50k
    assert credit_call.args[2] == "c-dst"
    assert credit_call.args[3].category.budgeted == 150_000  # 100k + 50k


@pytest.mark.asyncio
async def test_move_money_partial_failure_includes_recovery_state(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    src = _category_mock("c-src", "Groceries", budgeted=400_000)
    dst = _category_mock("c-dst", "Dining", budgeted=100_000)
    mock_ynab_apis.categories.get_month_category_by_id.side_effect = [
        _resp(category=src),
        _resp(category=dst),
    ]
    mock_ynab_apis.categories.update_month_category.side_effect = [
        _resp(category=src),  # debit succeeds
        ApiException(status=500, reason="boom"),  # credit fails
    ]

    with pytest.raises(RuntimeError) as exc_info:
        await server.move_money("budget-1", "c-src", "c-dst", 50.00, "2026-05-01")

    msg = str(exc_info.value)
    assert "c-src" in msg and "c-dst" in msg
    assert "350.00" in msg  # post-debit source balance
    assert "150.00" in msg  # intended post-credit dest balance
    assert "manually" in msg.lower() or "reverse" in msg.lower()


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_month_resource_returns_text_content(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.months.get_budget_month.return_value = _resp(
        month=_month_detail_mock(month_iso="2026-05-01")
    )

    result = await server.get_current_month_resource("budget-1")

    assert isinstance(result, list) and len(result) == 1
    assert result[0].type == "text"
    assert "# YNAB Month: 2026-05-01" in result[0].text
    args = mock_ynab_apis.months.get_budget_month.call_args
    assert args.args[1] == date.today().replace(day=1)


@pytest.mark.asyncio
async def test_arbitrary_month_resource_uses_iso_date(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.months.get_budget_month.return_value = _resp(
        month=_month_detail_mock(month_iso="2026-03-01")
    )

    result = await server.get_month_resource("budget-1", "2026-03-01")

    assert isinstance(result, list) and len(result) == 1
    assert "2026-03-01" in result[0].text
    args = mock_ynab_apis.months.get_budget_month.call_args
    assert args.args[1] == date(2026, 3, 1)


# ---------------------------------------------------------------------------
# update_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_category_rename(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    cat = _category_mock("c-1", "Financial Planning (Future)")
    mock_ynab_apis.categories.update_category.return_value = _resp(category=cat)

    result = await server.update_category("budget-1", "c-1", name="Financial Planning (Future)")

    assert "renamed to **Financial Planning (Future)**" in result
    args = mock_ynab_apis.categories.update_category.call_args
    budget_id, category_id, wrapper = args.args
    assert budget_id == "budget-1"
    assert category_id == "c-1"
    assert wrapper.category.name == "Financial Planning (Future)"
    assert wrapper.category.note is None
    assert wrapper.category.category_group_id is None


@pytest.mark.asyncio
async def test_update_category_note(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    cat = _category_mock("c-1", "Groceries", note="Aldi only")
    mock_ynab_apis.categories.update_category.return_value = _resp(category=cat)

    result = await server.update_category("budget-1", "c-1", note="Aldi only")

    assert "note set to" in result
    args = mock_ynab_apis.categories.update_category.call_args
    _, _, wrapper = args.args
    assert wrapper.category.note == "Aldi only"
    assert wrapper.category.name is None


@pytest.mark.asyncio
async def test_update_category_move_group(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    cat = _category_mock("c-1", "Groceries")
    mock_ynab_apis.categories.update_category.return_value = _resp(category=cat)

    result = await server.update_category("budget-1", "c-1", category_group_id="group-2")

    assert "moved to group" in result
    args = mock_ynab_apis.categories.update_category.call_args
    _, _, wrapper = args.args
    assert wrapper.category.category_group_id == "group-2"


@pytest.mark.asyncio
async def test_update_category_requires_at_least_one_field() -> None:
    with pytest.raises(ValueError, match="At least one of"):
        await server.update_category("budget-1", "c-1")
