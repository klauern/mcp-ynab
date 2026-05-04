"""Unit tests for read-only and idempotent MCP tools.

Each test patches the YNAB API constructors via the `mock_ynab_apis` fixture
(see conftest.py) and calls the tool function directly. Mocks use ``spec=`` for
SDK model classes so the ``isinstance`` filters in server.py treat them as real
SDK objects.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from ynab.models.account import Account
from ynab.models.category import Category
from ynab.models.category_group_with_categories import CategoryGroupWithCategories
from ynab.models.transaction_detail import TransactionDetail
from ynab.rest import ApiException

from mcp_ynab import server


# ---------------------------------------------------------------------------
# Helpers for building API responses
# ---------------------------------------------------------------------------


def _resp(**data_kwargs: object) -> MagicMock:
    """Build a `response` object whose `.data` exposes the given attributes."""
    return MagicMock(data=SimpleNamespace(**data_kwargs))


def _budget_mock(budget_id: str, name: str) -> MagicMock:
    """Build a budget mock whose `.to_dict()` returns id+name."""
    budget = MagicMock()
    budget.id = budget_id
    budget.name = name
    budget.to_dict.return_value = {"id": budget_id, "name": name}
    return budget


def _account_mock(
    account_id: str,
    name: str,
    account_type: str,
    balance_milliunits: int,
    *,
    closed: bool = False,
    deleted: bool = False,
) -> MagicMock:
    """Build an Account-spec'd mock that survives `isinstance(x, Account)`."""
    account = MagicMock(spec=Account)
    account.id = account_id
    account.name = name
    account.closed = closed
    account.deleted = deleted
    account.to_dict.return_value = {
        "id": account_id,
        "name": name,
        "type": account_type,
        "balance": balance_milliunits,
        "closed": closed,
        "deleted": deleted,
    }
    return account


def _txn_mock(
    txn_id: str,
    *,
    amount_milliunits: int = -1500,
    payee_name: str | None = "Coffee Shop",
    category_id: str | None = "cat-1",
    category_name: str | None = "Food",
    memo: str | None = "",
    approved: bool = True,
    account_id: str = "acct-1",
    var_date: date | None = None,
) -> MagicMock:
    """Build a TransactionDetail-spec'd mock for use in API responses."""
    txn = MagicMock(spec=TransactionDetail)
    txn.id = txn_id
    txn.account_id = account_id
    txn.amount = amount_milliunits
    txn.payee_name = payee_name
    txn.category_id = category_id
    txn.category_name = category_name
    txn.memo = memo
    txn.approved = approved
    txn.var_date = var_date or date(2026, 5, 1)
    return txn


def _category_mock(
    cat_id: str, name: str, budgeted_milliunits: int, activity_milliunits: int
) -> MagicMock:
    """Build a Category-spec'd mock that survives `isinstance(x, Category)`."""
    category = MagicMock(spec=Category)
    category.id = cat_id
    category.name = name
    category.budgeted = budgeted_milliunits
    category.activity = activity_milliunits
    return category


def _category_group_mock(name: str, categories: list[MagicMock]) -> MagicMock:
    """Build a CategoryGroupWithCategories-spec'd mock with given categories."""
    group = MagicMock(spec=CategoryGroupWithCategories)
    group.name = name
    group.categories = categories
    return group


# ---------------------------------------------------------------------------
# get_budgets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_budgets_renders_markdown_list(mock_ynab_apis: SimpleNamespace) -> None:
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[
            _budget_mock("b-1", "Personal"),
            _budget_mock("b-2", "Business"),
        ]
    )

    result = await server.get_budgets()

    assert result.startswith("# YNAB Budgets")
    assert "Personal" in result
    assert "b-1" in result
    assert "Business" in result
    assert "b-2" in result


@pytest.mark.asyncio
async def test_get_budgets_handles_empty_budget_list(mock_ynab_apis: SimpleNamespace) -> None:
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(budgets=[])

    result = await server.get_budgets()

    assert "_No budgets found._" in result


@pytest.mark.asyncio
async def test_get_budgets_propagates_api_exception(mock_ynab_apis: SimpleNamespace) -> None:
    mock_ynab_apis.budgets.get_budgets.side_effect = ApiException(status=500, reason="Boom")

    with pytest.raises(ApiException):
        await server.get_budgets()


# ---------------------------------------------------------------------------
# get_account_balance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_account_balance_converts_milliunits_to_dollars(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("b-1", "Personal")]
    )
    account_response = _resp(account=SimpleNamespace(balance=125_000))
    mock_ynab_apis.accounts.get_account_by_id.return_value = account_response

    result = await server.get_account_balance("acct-1")

    assert result == pytest.approx(125.0)
    mock_ynab_apis.accounts.get_account_by_id.assert_called_once_with("b-1", "acct-1")


@pytest.mark.asyncio
async def test_get_account_balance_uses_preferred_budget_id_when_set(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("preferred-b")
    monkeypatch.setattr(server, "ynab_resources", isolated)

    account_response = _resp(account=SimpleNamespace(balance=42_000))
    mock_ynab_apis.accounts.get_account_by_id.return_value = account_response

    result = await server.get_account_balance("acct-1")

    assert result == pytest.approx(42.0)
    # Preferred budget short-circuits the get_budgets fallback
    mock_ynab_apis.budgets.get_budgets.assert_not_called()
    mock_ynab_apis.accounts.get_account_by_id.assert_called_once_with("preferred-b", "acct-1")


@pytest.mark.asyncio
async def test_get_account_balance_falls_back_to_first_budget_when_no_preference(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("first-b", "Default")]
    )
    account_response = _resp(account=SimpleNamespace(balance=1_000))
    mock_ynab_apis.accounts.get_account_by_id.return_value = account_response

    result = await server.get_account_balance("acct-1")

    assert result == pytest.approx(1.0)
    mock_ynab_apis.budgets.get_budgets.assert_called_once()
    mock_ynab_apis.accounts.get_account_by_id.assert_called_once_with("first-b", "acct-1")


# ---------------------------------------------------------------------------
# get_accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_accounts_groups_by_type_and_summarizes(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    accounts = [
        _account_mock("a-1", "Main Checking", "checking", 5_000_000),
        _account_mock("a-2", "Emergency Fund", "savings", 10_000_000),
        _account_mock("a-3", "Visa", "creditCard", -2_500_000),
        _account_mock("a-4", "Closed Acct", "checking", 999_000, closed=True),
    ]
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(accounts=accounts)

    result = await server.get_accounts("b-1")

    assert "# YNAB Account Summary" in result
    assert "Main Checking" in result
    assert "Emergency Fund" in result
    assert "Visa" in result
    assert "Closed Acct" not in result, "closed accounts should be filtered out"
    assert "Total Assets:** $15,000.00" in result
    assert "Total Liabilities:** $2,500.00" in result
    assert "Net Worth:** $12,500.00" in result


@pytest.mark.asyncio
async def test_get_accounts_handles_empty_account_list(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(accounts=[])

    result = await server.get_accounts("b-1")

    assert "# YNAB Account Summary" in result
    assert "Total Assets:** $0.00" in result
    assert "Net Worth:** $0.00" in result


@pytest.mark.asyncio
async def test_get_accounts_propagates_api_exception(mock_ynab_apis: SimpleNamespace) -> None:
    mock_ynab_apis.accounts.get_accounts.side_effect = ApiException(
        status=401, reason="Unauthorized"
    )

    with pytest.raises(ApiException):
        await server.get_accounts("b-1")


# ---------------------------------------------------------------------------
# get_transactions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_transactions_renders_table_for_recent_transactions(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions_by_account.return_value = _resp(
        transactions=[
            _txn_mock("t-1", amount_milliunits=-15_500, payee_name="Coffee Shop"),
            _txn_mock(
                "t-2",
                amount_milliunits=200_000,
                payee_name="Paycheck",
                category_name="Income",
            ),
        ]
    )

    result = await server.get_transactions("b-1", "acct-1")

    assert "# Recent Transactions" in result
    assert "Coffee Shop" in result
    assert "Paycheck" in result
    assert "$-15.50" in result or "-$15.50" in result or "$-15.50" in result
    assert "$200.00" in result


@pytest.mark.asyncio
async def test_get_transactions_defaults_to_start_of_current_month(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    """When no since_date passed, defaults to first of current month."""
    mock_ynab_apis.transactions.get_transactions_by_account.return_value = _resp(transactions=[])

    await server.get_transactions("b-1", "acct-1")

    call = mock_ynab_apis.transactions.get_transactions_by_account.call_args
    expected_since = datetime.now().replace(day=1).date()
    assert call.kwargs["since_date"] == expected_since


@pytest.mark.asyncio
async def test_get_transactions_honors_explicit_since_date(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions_by_account.return_value = _resp(transactions=[])
    explicit = date(2025, 1, 15)

    await server.get_transactions("b-1", "acct-1", since_date=explicit)

    call = mock_ynab_apis.transactions.get_transactions_by_account.call_args
    assert call.kwargs["since_date"] == explicit


@pytest.mark.asyncio
async def test_get_transactions_returns_friendly_message_when_empty(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions_by_account.return_value = _resp(transactions=[])

    result = await server.get_transactions("b-1", "acct-1")

    assert "_No recent transactions found._" in result


@pytest.mark.asyncio
async def test_get_transactions_propagates_api_exception(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions_by_account.side_effect = ApiException(
        status=403, reason="Forbidden"
    )

    with pytest.raises(ApiException):
        await server.get_transactions("b-1", "acct-1")


# ---------------------------------------------------------------------------
# get_transactions_needing_attention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_needs_attention_filters_uncategorized_only(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(
        accounts=[
            SimpleNamespace(id="a-1", name="Checking", closed=False, deleted=False),
        ]
    )
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(
        transactions=[
            _txn_mock("t-1", category_id=None, approved=True, payee_name="Mystery"),
            _txn_mock("t-2", category_id="cat-1", approved=True, payee_name="Coffee"),
            _txn_mock("t-3", category_id="cat-1", approved=False, payee_name="Pending"),
        ]
    )

    result = await server.get_transactions_needing_attention("b-1", filter_type="uncategorized")

    assert "# Transactions Needing Attention" in result
    assert "Mystery" in result
    assert "Coffee" not in result
    assert "Pending" not in result, "unapproved-only txns excluded when filter is uncategorized"


@pytest.mark.asyncio
async def test_needs_attention_filters_unapproved_only(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(
        accounts=[SimpleNamespace(id="a-1", name="Checking", closed=False, deleted=False)]
    )
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(
        transactions=[
            _txn_mock("t-1", category_id=None, approved=True, payee_name="Uncat"),
            _txn_mock("t-3", category_id="cat-1", approved=False, payee_name="Pending"),
        ]
    )

    result = await server.get_transactions_needing_attention("b-1", filter_type="unapproved")

    assert "Pending" in result
    assert "Uncat" not in result, "uncategorized-only txns excluded when filter is unapproved"


@pytest.mark.asyncio
async def test_needs_attention_both_filter_includes_either_problem(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(
        accounts=[SimpleNamespace(id="a-1", name="Checking", closed=False, deleted=False)]
    )
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(
        transactions=[
            _txn_mock("t-1", category_id=None, approved=True, payee_name="Uncat"),
            _txn_mock("t-2", category_id="cat-1", approved=False, payee_name="Pending"),
            _txn_mock("t-3", category_id="cat-1", approved=True, payee_name="Clean"),
        ]
    )

    result = await server.get_transactions_needing_attention("b-1", filter_type="both")

    assert "Uncat" in result
    assert "Pending" in result
    assert "Clean" not in result


@pytest.mark.asyncio
async def test_needs_attention_returns_friendly_message_when_nothing_to_show(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(accounts=[])
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(transactions=[])

    result = await server.get_transactions_needing_attention("b-1")

    assert "_No transactions need attention._" in result


@pytest.mark.asyncio
async def test_needs_attention_rejects_invalid_filter_type_via_mcp_layer(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    """Pydantic-via-FastMCP rejects out-of-Literal values before the tool runs."""
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as exc_info:
        await server.mcp.call_tool(
            "get_transactions_needing_attention",
            {"budget_id": "b-1", "filter_type": "bogus"},
        )

    assert "filter_type" in str(exc_info.value)
    mock_ynab_apis.accounts.get_accounts.assert_not_called()


# ---------------------------------------------------------------------------
# get_categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_categories_renders_grouped_markdown(mock_ynab_apis: SimpleNamespace) -> None:
    groups = [
        _category_group_mock(
            "Monthly Bills",
            [
                _category_mock("c-1", "Rent", 1_500_000, -1_500_000),
                _category_mock("c-2", "Internet", 60_000, -60_000),
            ],
        ),
        _category_group_mock(
            "Fun",
            [_category_mock("c-3", "Dining Out", 200_000, -45_000)],
        ),
        _category_group_mock("Empty Group", []),
    ]
    mock_ynab_apis.categories.get_categories.return_value = _resp(category_groups=groups)

    result = await server.get_categories("b-1")

    assert "# YNAB Categories" in result
    assert "## Monthly Bills" in result
    assert "## Fun" in result
    assert "Empty Group" not in result, "empty groups should be skipped"
    assert "Rent" in result
    assert "$1,500.00" in result
    assert "-$1,500.00" in result


@pytest.mark.asyncio
async def test_get_categories_handles_no_groups(mock_ynab_apis: SimpleNamespace) -> None:
    mock_ynab_apis.categories.get_categories.return_value = _resp(category_groups=[])

    result = await server.get_categories("b-1")

    assert result.strip() == "# YNAB Categories"


@pytest.mark.asyncio
async def test_get_categories_propagates_api_exception(mock_ynab_apis: SimpleNamespace) -> None:
    mock_ynab_apis.categories.get_categories.side_effect = ApiException(status=500, reason="Boom")

    with pytest.raises(ApiException):
        await server.get_categories("b-1")


# ---------------------------------------------------------------------------
# set_preferred_budget_id and resource handlers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_preferred_budget_id_persists_to_resource_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """set_preferred_budget_id writes to the YNABResources file store."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    result = await server.set_preferred_budget_id("b-42")

    assert "b-42" in result
    assert isolated.get_preferred_budget_id() == "b-42"


def test_resource_get_preferred_budget_id_returns_stored_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-99")
    monkeypatch.setattr(server, "ynab_resources", isolated)

    assert server.get_preferred_budget_id() == "b-99"


def test_resource_get_cached_categories_returns_text_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.cache_categories(
        "b-1",
        [
            {"id": "c-1", "name": "Rent", "category_group_name": "Bills"},
            {"id": "c-2", "name": "Coffee", "category_group_name": "Fun"},
        ],
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)

    contents = server.get_cached_categories("b-1")

    assert len(contents) == 2
    assert contents[0].type == "text"
    assert "Rent" in contents[0].text
    assert "c-1" in contents[0].text


def test_resource_get_cached_categories_returns_empty_for_unknown_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    assert server.get_cached_categories("unknown-budget") == []


# ---------------------------------------------------------------------------
# cache_categories tool (idempotent mutating)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_categories_tool_writes_to_resource_store(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    cat = _category_mock("c-1", "Rent", 1_500_000, -1_500_000)
    cat.to_dict.return_value = {
        "id": "c-1",
        "name": "Rent",
        "category_group_name": "Bills",
    }
    group = _category_group_mock("Bills", [cat])
    mock_ynab_apis.categories.get_categories.return_value = _resp(category_groups=[group])

    result = await server.cache_categories("b-1")

    assert "b-1" in result
    cached = isolated.get_cached_categories("b-1")
    assert len(cached) == 1
    assert "Rent" in cached[0].text


# ---------------------------------------------------------------------------
# create_transaction (mutating tool, mocked end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_transaction_uses_preferred_budget_id_when_set(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("preferred-b")
    monkeypatch.setattr(server, "ynab_resources", isolated)

    created_txn = MagicMock()
    created_txn.to_dict.return_value = {"id": "new-txn", "amount": -12_340}
    mock_ynab_apis.transactions.create_transaction.return_value = _resp(transaction=created_txn)

    result = await server.create_transaction(
        account_id="acct-1",
        amount=-12.34,
        payee_name="Test Payee",
    )

    assert result == {"id": "new-txn", "amount": -12_340}
    # Should NOT have called get_budgets — preferred budget short-circuits
    mock_ynab_apis.budgets.get_budgets.assert_not_called()
    call = mock_ynab_apis.transactions.create_transaction.call_args
    assert call.args[0] == "preferred-b"


@pytest.mark.asyncio
async def test_create_transaction_falls_back_to_first_budget_when_no_preference(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("first-b", "Default")]
    )
    created_txn = MagicMock()
    created_txn.to_dict.return_value = {"id": "new-txn"}
    mock_ynab_apis.transactions.create_transaction.return_value = _resp(transaction=created_txn)

    result = await server.create_transaction(
        account_id="acct-1",
        amount=10.0,
        payee_name="Test Payee",
    )

    assert result == {"id": "new-txn"}
    mock_ynab_apis.budgets.get_budgets.assert_called_once()
    call = mock_ynab_apis.transactions.create_transaction.call_args
    assert call.args[0] == "first-b"


@pytest.mark.asyncio
async def test_create_transaction_returns_empty_dict_when_response_missing(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    monkeypatch.setattr(server, "ynab_resources", isolated)

    mock_ynab_apis.transactions.create_transaction.return_value = _resp(transaction=None)

    result = await server.create_transaction(
        account_id="acct-1",
        amount=5.0,
        payee_name="Test",
    )

    assert result == {}


# ---------------------------------------------------------------------------
# bulk_categorize (idempotent mutating tool, mocked end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_categorize_updates_all_when_server_acks_every_id(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.update_transactions.return_value = _resp(
        transaction_ids=["t-1", "t-2", "t-3"]
    )

    assignments = [
        {"transaction_id": "t-1", "category_id": "c-coffee"},
        {"transaction_id": "t-2", "category_id": "c-coffee"},
        {"transaction_id": "t-3", "category_id": "c-rent"},
    ]
    result = await server.bulk_categorize("b-1", assignments)

    assert "# Bulk Categorize" in result
    assert "**3 of 3 updated**" in result
    assert "t-1" in result and "c-coffee" in result and "Updated" in result
    mock_ynab_apis.transactions.update_transactions.assert_called_once()
    call = mock_ynab_apis.transactions.update_transactions.call_args
    assert call.args[0] == "b-1"
    payload = call.args[1]
    assert len(payload.transactions) == 3
    assert {t.id for t in payload.transactions} == {"t-1", "t-2", "t-3"}
    assert {t.category_id for t in payload.transactions} == {"c-coffee", "c-rent"}


@pytest.mark.asyncio
async def test_bulk_categorize_marks_unacked_ids_as_not_found(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.update_transactions.return_value = _resp(transaction_ids=["t-1"])

    assignments = [
        {"transaction_id": "t-1", "category_id": "c-1"},
        {"transaction_id": "t-bad", "category_id": "c-1"},
    ]
    result = await server.bulk_categorize("b-1", assignments)

    assert "**1 of 2 updated**" in result
    assert "t-bad" in result
    assert "Not found" in result


@pytest.mark.asyncio
async def test_bulk_categorize_short_circuits_on_empty_assignments(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    result = await server.bulk_categorize("b-1", [])

    assert "_No assignments provided._" in result
    mock_ynab_apis.transactions.update_transactions.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_categorize_skips_invalid_entries_but_processes_valid_ones(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.update_transactions.return_value = _resp(transaction_ids=["t-good"])

    assignments = [
        {"transaction_id": "t-good", "category_id": "c-1"},
        {"transaction_id": "t-no-category"},
        {"category_id": "c-1"},
    ]
    result = await server.bulk_categorize("b-1", assignments)

    assert "**1 of 3 updated**" in result
    assert "Invalid (missing category_id)" in result
    assert "Invalid (missing transaction_id)" in result
    call = mock_ynab_apis.transactions.update_transactions.call_args
    payload = call.args[1]
    assert len(payload.transactions) == 1
    assert payload.transactions[0].id == "t-good"


@pytest.mark.asyncio
async def test_bulk_categorize_does_not_call_api_when_only_invalid_entries(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    assignments = [{"category_id": "c-1"}, {"transaction_id": "t-1"}]
    result = await server.bulk_categorize("b-1", assignments)

    assert "**0 of 2 updated**" in result
    mock_ynab_apis.transactions.update_transactions.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_categorize_propagates_api_exception(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.update_transactions.side_effect = ApiException(
        status=500, reason="Boom"
    )

    with pytest.raises(ApiException):
        await server.bulk_categorize("b-1", [{"transaction_id": "t-1", "category_id": "c-1"}])


# ---------------------------------------------------------------------------
# approve_transactions (idempotent mutating tool, mocked end-to-end)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_transactions_marks_all_when_server_acks_every_id(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.update_transactions.return_value = _resp(
        transaction_ids=["t-1", "t-2", "t-3"]
    )

    result = await server.approve_transactions("b-1", ["t-1", "t-2", "t-3"])

    assert "# Approve Transactions" in result
    assert "**3 of 3 approved**" in result
    assert "t-1" in result and "Approved" in result
    mock_ynab_apis.transactions.update_transactions.assert_called_once()
    call = mock_ynab_apis.transactions.update_transactions.call_args
    assert call.args[0] == "b-1"
    payload = call.args[1]
    assert len(payload.transactions) == 3
    assert {t.id for t in payload.transactions} == {"t-1", "t-2", "t-3"}
    assert all(t.approved is True for t in payload.transactions)


@pytest.mark.asyncio
async def test_approve_transactions_marks_unacked_ids_as_not_found(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.update_transactions.return_value = _resp(transaction_ids=["t-1"])

    result = await server.approve_transactions("b-1", ["t-1", "t-bad"])

    assert "**1 of 2 approved**" in result
    assert "t-bad" in result
    assert "Not found" in result


@pytest.mark.asyncio
async def test_approve_transactions_short_circuits_on_empty_list(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    result = await server.approve_transactions("b-1", [])

    assert "_No transaction IDs provided._" in result
    mock_ynab_apis.transactions.update_transactions.assert_not_called()


@pytest.mark.asyncio
async def test_approve_transactions_skips_invalid_entries_but_processes_valid_ones(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.update_transactions.return_value = _resp(transaction_ids=["t-good"])

    # Pass non-string and empty string entries; type: ignore to allow
    # exercising the runtime validation path.
    transaction_ids = ["t-good", "", 123]  # type: ignore[list-item]
    result = await server.approve_transactions("b-1", transaction_ids)

    assert "**1 of 3 approved**" in result
    assert "Invalid (empty string)" in result
    assert "Invalid (not a string)" in result
    call = mock_ynab_apis.transactions.update_transactions.call_args
    payload = call.args[1]
    assert len(payload.transactions) == 1
    assert payload.transactions[0].id == "t-good"
    assert payload.transactions[0].approved is True


@pytest.mark.asyncio
async def test_approve_transactions_does_not_call_api_when_only_invalid_entries(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    transaction_ids = ["", 42]  # type: ignore[list-item]
    result = await server.approve_transactions("b-1", transaction_ids)

    assert "**0 of 2 approved**" in result
    mock_ynab_apis.transactions.update_transactions.assert_not_called()


@pytest.mark.asyncio
async def test_approve_transactions_propagates_api_exception(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.update_transactions.side_effect = ApiException(
        status=500, reason="Boom"
    )

    with pytest.raises(ApiException):
        await server.approve_transactions("b-1", ["t-1"])


# ---------------------------------------------------------------------------
# update_transaction (single PATCH any field)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_transaction_sends_only_supplied_fields(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    captured: dict = {}

    def fake_existing(**kwargs: object) -> dict:
        captured.update(kwargs)
        return kwargs  # type: ignore[return-value]

    def fake_wrapper(transaction: object) -> object:
        return SimpleNamespace(transaction=transaction)

    server.ExistingTransaction = fake_existing  # type: ignore[assignment]
    server.PutTransactionWrapper = fake_wrapper  # type: ignore[assignment]

    result = await server.update_transaction("b-1", "t-1", memo="rent")

    assert "Updated transaction `t-1`" in result
    assert "Memo" in result
    assert "rent" in result
    assert captured == {"memo": "rent"}
    mock_ynab_apis.transactions.update_transaction.assert_called_once()


@pytest.mark.asyncio
async def test_update_transaction_converts_amount_dollars_to_milliunits(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    captured: dict = {}

    def fake_existing(**kwargs: object) -> dict:
        captured.update(kwargs)
        return kwargs  # type: ignore[return-value]

    server.ExistingTransaction = fake_existing  # type: ignore[assignment]
    server.PutTransactionWrapper = lambda transaction: SimpleNamespace(transaction=transaction)  # type: ignore[assignment]

    await server.update_transaction("b-1", "t-1", amount=-12.34)

    assert captured == {"amount": -12340}


@pytest.mark.asyncio
async def test_update_transaction_rejects_when_no_fields_supplied(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError, match="at least one field"):
        await server.update_transaction("b-1", "t-1")
    mock_ynab_apis.transactions.update_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_update_transaction_rejects_invalid_flag_color(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError, match="Invalid flag_color"):
        await server.update_transaction("b-1", "t-1", flag_color="chartreuse")


@pytest.mark.asyncio
async def test_update_transaction_rejects_invalid_cleared_value(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError, match="Invalid cleared value"):
        await server.update_transaction("b-1", "t-1", cleared="maybe")


@pytest.mark.asyncio
async def test_update_transaction_rejects_invalid_txn_date(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError, match="Invalid txn_date"):
        await server.update_transaction("b-1", "t-1", txn_date="not-a-date")


# ---------------------------------------------------------------------------
# ynab://budgets resource (list_budgets_resource)
# ---------------------------------------------------------------------------


def _budget_resource_mock(
    budget_id: str,
    name: str,
    *,
    last_modified: datetime | None = None,
    iso_code: str = "USD",
    deleted: bool = False,
    closed: bool = False,
) -> MagicMock:
    """Build a budget mock with the attrs read by list_budgets_resource."""
    budget = MagicMock()
    budget.id = budget_id
    budget.name = name
    budget.last_modified_on = last_modified or datetime(2026, 5, 1, 12, 0, 0)
    budget.currency_format = SimpleNamespace(iso_code=iso_code)
    budget.deleted = deleted
    budget.closed = closed
    return budget


@pytest.mark.asyncio
async def test_list_budgets_resource_renders_markdown_table(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[
            _budget_resource_mock("b-1", "Personal"),
            _budget_resource_mock("b-2", "Business", iso_code="EUR"),
        ]
    )

    result = await server.list_budgets_resource()

    assert len(result) == 1
    text = result[0].text
    assert text.startswith("# YNAB Budgets")
    assert "Personal" in text
    assert "b-1" in text
    assert "Business" in text
    assert "USD" in text
    assert "EUR" in text
    # Markdown table separator
    assert "|---" in text or "|--" in text


@pytest.mark.asyncio
async def test_list_budgets_resource_handles_empty_list(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(budgets=[])

    result = await server.list_budgets_resource()

    assert len(result) == 1
    assert "_No budgets found._" in result[0].text


@pytest.mark.asyncio
async def test_list_budgets_resource_filters_deleted_budgets(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[
            _budget_resource_mock("b-active", "Active"),
            _budget_resource_mock("b-deleted", "Old", deleted=True),
        ]
    )

    result = await server.list_budgets_resource()

    text = result[0].text
    assert "Active" in text
    assert "b-active" in text
    assert "Old" not in text
    assert "b-deleted" not in text


# ---------------------------------------------------------------------------
# ynab://accounts/{budget_id} resource (list_accounts_resource)
# ---------------------------------------------------------------------------


def _account_resource_mock(
    account_id: str,
    name: str,
    account_type: str,
    balance_milliunits: int,
    *,
    closed: bool = False,
    deleted: bool = False,
) -> MagicMock:
    """Build an account mock with the attrs read by list_accounts_resource."""
    account = MagicMock()
    account.id = account_id
    account.name = name
    account.type = account_type
    account.balance = balance_milliunits
    account.closed = closed
    account.deleted = deleted
    return account


@pytest.mark.asyncio
async def test_list_accounts_resource_renders_markdown_with_dollar_balances(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(
        accounts=[
            _account_resource_mock("a-1", "Main Checking", "checking", 5_000_000),
            _account_resource_mock("a-2", "Visa", "creditCard", -2_500_000),
        ]
    )

    result = await server.list_accounts_resource("b-1")

    assert len(result) == 1
    text = result[0].text
    assert text.startswith("# YNAB Accounts (b-1)")
    assert "Main Checking" in text
    assert "checking" in text
    assert "a-1" in text
    assert "Visa" in text
    assert "creditCard" in text
    # Balances rendered in dollars (5_000_000 milliunits -> $5,000.00)
    assert "$5,000.00" in text
    assert "$2,500.00" in text  # negative formatted with parens by formatter
    mock_ynab_apis.accounts.get_accounts.assert_called_once_with("b-1")


@pytest.mark.asyncio
async def test_list_accounts_resource_handles_empty_list(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(accounts=[])

    result = await server.list_accounts_resource("b-1")

    assert len(result) == 1
    assert "_No accounts found._" in result[0].text


@pytest.mark.asyncio
async def test_list_accounts_resource_filters_closed_and_deleted(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(
        accounts=[
            _account_resource_mock("a-open", "Open Acct", "checking", 1_000_000),
            _account_resource_mock("a-closed", "Closed Acct", "checking", 999_000, closed=True),
            _account_resource_mock("a-del", "Deleted Acct", "savings", 1, deleted=True),
        ]
    )

    result = await server.list_accounts_resource("b-1")

    text = result[0].text
    assert "Open Acct" in text
    assert "a-open" in text
    assert "Closed Acct" not in text
    assert "a-closed" not in text
    assert "Deleted Acct" not in text
    assert "a-del" not in text


# ---------------------------------------------------------------------------
# get_scheduled_transactions
# ---------------------------------------------------------------------------


def _scheduled_mock(
    *,
    date_next: date,
    frequency: str = "monthly",
    account_name: str = "Checking",
    payee_name: str = "Landlord",
    category_name: str = "Rent",
    amount_milliunits: int = -1_500_000,
    deleted: bool = False,
) -> MagicMock:
    sched = MagicMock()
    sched.date_next = date_next
    sched.frequency = frequency
    sched.account_name = account_name
    sched.payee_name = payee_name
    sched.category_name = category_name
    sched.amount = amount_milliunits
    sched.deleted = deleted
    return sched


@pytest.mark.asyncio
async def test_get_scheduled_transactions_filters_by_within_days(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    today = date.today()
    mock_ynab_apis.scheduled_transactions.get_scheduled_transactions.return_value = _resp(
        scheduled_transactions=[
            _scheduled_mock(
                date_next=today + timedelta(days=5),
                payee_name="Soon",
            ),
            _scheduled_mock(
                date_next=today + timedelta(days=60),
                payee_name="Later",
            ),
            _scheduled_mock(
                date_next=today + timedelta(days=2),
                payee_name="DeletedSoon",
                deleted=True,
            ),
        ]
    )

    result = await server.get_scheduled_transactions("b-1", within_days=30)

    assert "# Scheduled Transactions" in result
    assert "Soon" in result
    assert "Later" not in result, "scheduled txns past cutoff are excluded"
    assert "DeletedSoon" not in result, "deleted scheduled txns are excluded"


@pytest.mark.asyncio
async def test_get_scheduled_transactions_returns_friendly_message_when_empty(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.scheduled_transactions.get_scheduled_transactions.return_value = _resp(
        scheduled_transactions=[]
    )

    result = await server.get_scheduled_transactions("b-1")

    assert "_No upcoming scheduled transactions._" in result


@pytest.mark.asyncio
async def test_get_scheduled_transactions_renders_amount_columns(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    today = date.today()
    mock_ynab_apis.scheduled_transactions.get_scheduled_transactions.return_value = _resp(
        scheduled_transactions=[
            _scheduled_mock(
                date_next=today + timedelta(days=1),
                amount_milliunits=-1_500_000,
                payee_name="Rent Co",
                category_name="Housing",
                account_name="Main",
                frequency="monthly",
            ),
        ]
    )

    result = await server.get_scheduled_transactions("b-1", within_days=7)

    assert "Rent Co" in result
    assert "Housing" in result
    assert "Main" in result
    assert "monthly" in result
    assert "$1,500.00" in result


# ---------------------------------------------------------------------------
# get_transactions_by_category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_transactions_by_category_renders_table(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(
        accounts=[SimpleNamespace(id="acct-1", name="Checking", closed=False, deleted=False)]
    )
    mock_ynab_apis.categories.get_transactions_by_category.return_value = _resp(
        transactions=[
            _txn_mock(
                "t-1",
                amount_milliunits=-25_000,
                payee_name="Cafe",
                category_id="cat-1",
                approved=True,
            ),
            _txn_mock(
                "t-2",
                amount_milliunits=-15_500,
                payee_name="Bookstore",
                category_id="cat-1",
                approved=True,
            ),
        ]
    )

    result = await server.get_transactions_by_category("b-1", "cat-1")

    assert "# Transactions for Category `cat-1`" in result
    assert "Cafe" in result
    assert "Bookstore" in result


@pytest.mark.asyncio
async def test_get_transactions_by_category_passes_since_date(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(accounts=[])
    mock_ynab_apis.categories.get_transactions_by_category.return_value = _resp(transactions=[])

    await server.get_transactions_by_category("b-1", "cat-1", since_date="2026-01-01")

    call = mock_ynab_apis.categories.get_transactions_by_category.call_args
    assert call.kwargs["since_date"] == "2026-01-01"


@pytest.mark.asyncio
async def test_get_transactions_by_category_returns_friendly_message_when_empty(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_accounts.return_value = _resp(accounts=[])
    mock_ynab_apis.categories.get_transactions_by_category.return_value = _resp(transactions=[])

    result = await server.get_transactions_by_category("b-1", "cat-1")

    assert "_No transactions found for this category._" in result
