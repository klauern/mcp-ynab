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


@pytest.mark.asyncio
async def test_move_money_rejects_same_source_and_destination(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError, match="must be different"):
        await server.move_money("budget-1", "c-same", "c-same", 50.00, "2026-05-01")

    mock_ynab_apis.categories.get_month_category_by_id.assert_not_called()
    mock_ynab_apis.categories.update_month_category.assert_not_called()


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

    group_id = "22222222-2222-2222-2222-222222222222"
    result = await server.update_category("budget-1", "c-1", category_group_id=group_id)

    assert "moved to group" in result
    args = mock_ynab_apis.categories.update_category.call_args
    _, _, wrapper = args.args
    # SDK parses category_group_id into a uuid.UUID, so compare by string value.
    assert str(wrapper.category.category_group_id) == group_id


@pytest.mark.asyncio
async def test_update_category_sets_goal_target_in_milliunits(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    cat = _category_mock("c-1", "Vacation")
    mock_ynab_apis.categories.update_category.return_value = _resp(category=cat)

    result = await server.update_category("budget-1", "c-1", goal_target=150.0)

    assert "goal target set to $150.00" in result
    _, _, wrapper = mock_ynab_apis.categories.update_category.call_args.args
    # Dollars are converted to YNAB milliunits (x1000).
    assert wrapper.category.goal_target == 150_000


@pytest.mark.asyncio
async def test_update_category_sets_goal_target_date(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    cat = _category_mock("c-1", "Vacation")
    mock_ynab_apis.categories.update_category.return_value = _resp(category=cat)

    result = await server.update_category("budget-1", "c-1", goal_target_date="2026-07-01")

    assert "goal target date set to `2026-07-01`" in result
    _, _, wrapper = mock_ynab_apis.categories.update_category.call_args.args
    # SDK coerces the ISO string into a datetime.date.
    assert wrapper.category.goal_target_date == date(2026, 7, 1)


@pytest.mark.asyncio
async def test_update_category_sets_goal_needs_whole_amount(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    cat = _category_mock("c-1", "Vacation")
    mock_ynab_apis.categories.update_category.return_value = _resp(category=cat)

    result = await server.update_category("budget-1", "c-1", goal_needs_whole_amount=True)

    assert "goal mode set to Set Aside" in result
    _, _, wrapper = mock_ynab_apis.categories.update_category.call_args.args
    assert wrapper.category.goal_needs_whole_amount is True

    # False selects the "Refill" mode.
    result = await server.update_category("budget-1", "c-1", goal_needs_whole_amount=False)
    assert "goal mode set to Refill" in result


@pytest.mark.asyncio
async def test_update_category_omits_unset_fields_from_payload(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    """Only-provided fields reach the wire; omitted fields must not clear data."""
    cat = _category_mock("c-1", "Vacation")
    mock_ynab_apis.categories.update_category.return_value = _resp(category=cat)

    await server.update_category("budget-1", "c-1", goal_target=25.0)

    _, _, wrapper = mock_ynab_apis.categories.update_category.call_args.args
    # ExistingCategory serializes with exclude_none, so name/note/group and the
    # other goal fields are absent from the payload — YNAB leaves them untouched.
    payload = wrapper.category.to_dict()
    assert payload == {"goal_target": 25_000}


@pytest.mark.asyncio
async def test_update_category_requires_at_least_one_field() -> None:
    with pytest.raises(ValueError, match="At least one of"):
        await server.update_category("budget-1", "c-1")


# ---------------------------------------------------------------------------
# move_money elicitation (qlh.5)
# ---------------------------------------------------------------------------


class _ElicitFakeContext:
    """Minimal ctx stub that replays pre-loaded elicitation results."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list = []

    async def elicit(self, *, message: str, schema: object) -> SimpleNamespace:  # type: ignore[override]
        self.calls.append((message, schema))
        if not self._responses:
            raise AssertionError("Unexpected ctx.elicit() call — no more responses queued.")
        return self._responses.pop(0)

    async def request_context(self) -> SimpleNamespace:  # type: ignore[override]
        return SimpleNamespace()


def _accept_category_choice(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        action="accept",
        data=SimpleNamespace(index=index),
    )


@pytest.mark.asyncio
async def test_move_money_elicits_from_and_to_when_missing(
    mock_ynab_apis: SimpleNamespace,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.cache_categories(
        "budget-1",
        [
            {"id": "c-food", "name": "Groceries", "category_group_name": "Food"},
            {"id": "c-fun", "name": "Entertainment", "category_group_name": "Fun"},
        ],
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)

    src = _category_mock("c-food", "Groceries", budgeted=400_000)
    dst = _category_mock("c-fun", "Entertainment", budgeted=50_000)
    mock_ynab_apis.categories.get_month_category_by_id.side_effect = [
        _resp(category=src),
        _resp(category=dst),
    ]
    mock_ynab_apis.categories.update_month_category.return_value = _resp(category=src)

    ctx = _ElicitFakeContext(
        [_accept_category_choice(1), _accept_category_choice(2)]  # FROM=Groceries, TO=Entertainment
    )
    result = await server.move_money("budget-1", amount=25.0, ctx=ctx)

    assert "Moved $25.00 from **Groceries** → **Entertainment**" in result
    assert len(ctx.calls) == 2
    assert "FROM" in ctx.calls[0][0]
    assert "TO" in ctx.calls[1][0]


@pytest.mark.asyncio
async def test_move_money_returns_cancelled_when_from_declined(
    mock_ynab_apis: SimpleNamespace,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.cache_categories(
        "budget-1",
        [{"id": "c-food", "name": "Groceries", "category_group_name": "Food"}],
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)

    ctx = _ElicitFakeContext([SimpleNamespace(action="decline", data=None)])
    result = await server.move_money("budget-1", amount=10.0, ctx=ctx)

    assert "cancelled" in result.lower()
    mock_ynab_apis.categories.update_month_category.assert_not_called()


@pytest.mark.asyncio
async def test_move_money_raises_when_ids_missing_and_no_ctx() -> None:
    with pytest.raises(ValueError, match="requires from_category_id and to_category_id"):
        await server.move_money("budget-1", amount=10.0)


@pytest.mark.asyncio
async def test_move_money_raises_when_amount_missing() -> None:
    with pytest.raises(ValueError, match="requires an amount"):
        await server.move_money("budget-1", from_category_id="c-1", to_category_id="c-2")
