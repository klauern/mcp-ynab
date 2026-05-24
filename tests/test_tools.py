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
from mcp_ynab.state import Preferences


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
    cleared_balance_milliunits: int | None = None,
    uncleared_balance_milliunits: int | None = None,
    on_budget: bool = True,
    closed: bool = False,
    deleted: bool = False,
) -> MagicMock:
    """Build an Account-spec'd mock that survives `isinstance(x, Account)`."""
    account = MagicMock(spec=Account)
    account.id = account_id
    account.name = name
    account.type = account_type
    account.on_budget = on_budget
    account.balance = balance_milliunits
    account.cleared_balance = (
        balance_milliunits if cleared_balance_milliunits is None else cleared_balance_milliunits
    )
    account.uncleared_balance = (
        0 if uncleared_balance_milliunits is None else uncleared_balance_milliunits
    )
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
    cleared: str = "cleared",
    account_id: str = "acct-1",
    transfer_account_id: str | None = None,
    transfer_transaction_id: str | None = None,
    matched_transaction_id: str | None = None,
    import_id: str | None = None,
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
    txn.cleared = cleared
    txn.var_date = var_date or date(2026, 5, 1)
    txn.transfer_account_id = transfer_account_id
    txn.transfer_transaction_id = transfer_transaction_id
    txn.matched_transaction_id = matched_transaction_id
    txn.import_id = import_id
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
async def test_get_account_balance_uses_only_budget_when_no_preference(
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
                cleared="reconciled",
            ),
        ]
    )

    result = await server.get_transactions("b-1", "acct-1")

    assert "# Recent Transactions" in result
    assert "Coffee Shop" in result
    assert "Paycheck" in result
    assert "Cleared" in result
    assert "reconciled" in result
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


@pytest.mark.asyncio
async def test_get_account_reconciliation_profile_returns_structured_totals(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.accounts.get_account_by_id.return_value = _resp(
        account=_account_mock(
            "acct-1",
            "Checking",
            "checking",
            125_000,
            cleared_balance_milliunits=100_000,
            uncleared_balance_milliunits=25_000,
        )
    )
    mock_ynab_apis.transactions.get_transactions_by_account.return_value = _resp(
        transactions=[
            _txn_mock("t-1", amount_milliunits=-10_000, cleared="cleared"),
            _txn_mock("t-2", amount_milliunits=25_000, cleared="uncleared"),
            _txn_mock(
                "t-3",
                amount_milliunits=-5_000,
                cleared="reconciled",
                transfer_account_id="acct-2",
            ),
        ]
    )

    result = await server.get_account_reconciliation_profile(
        "b-1", "acct-1", include_transfers=False, limit=1
    )

    assert result["account"]["balance_milliunits"] == 125_000
    assert result["account"]["cleared_balance"] == 100.0
    assert result["totals"]["count"] == 2
    assert result["totals"]["amount_milliunits"] == 15_000
    assert result["totals"]["by_cleared_milliunits"] == {
        "cleared": -10_000,
        "uncleared": 25_000,
        "reconciled": 0,
    }
    assert result["transactions"] == [
        {
            "id": "t-1",
            "date": "2026-05-01",
            "amount_milliunits": -10_000,
            "amount": -10.0,
            "cleared": "cleared",
            "approved": True,
            "payee_name": "Coffee Shop",
            "category_name": "Food",
            "memo": "",
            "account_id": "acct-1",
            "transfer_account_id": None,
            "transfer_transaction_id": None,
            "matched_transaction_id": None,
            "import_id": None,
        }
    ]
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_find_account_transaction_subset_matches_returns_compact_matches(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions_by_account.return_value = _resp(
        transactions=[
            _txn_mock("t-1", amount_milliunits=-100_000, payee_name="Rent"),
            _txn_mock("t-2", amount_milliunits=-50_000, payee_name="Groceries"),
            _txn_mock("t-3", amount_milliunits=-25_000, payee_name="Fuel"),
        ]
    )

    result = await server.find_account_transaction_subset_matches(
        "b-1", "acct-1", target_amount=-75.0, max_subset_size=2
    )

    assert result["target_milliunits"] == -75_000
    assert result["truncated"] is False
    assert len(result["matches"]) == 1
    match = result["matches"][0]
    assert match["amount_milliunits"] == -75_000
    assert [txn["id"] for txn in match["transactions"]] == ["t-2", "t-3"]


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
    assert "Cleared" in result
    assert "cleared" in result
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic-via-FastMCP rejects out-of-Literal values before the tool runs."""
    from mcp.server.fastmcp.exceptions import ToolError

    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=False)),
    )
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


def test_resource_get_cached_categories_includes_group_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Cached entries with a group should render as 'name [group] (ID: id)'."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.cache_categories(
        "b-1",
        [
            {"id": "c-1", "name": "Groceries", "category_group_name": "Immediate Obligations"},
        ],
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)

    contents = server.get_cached_categories("b-1")

    assert len(contents) == 1
    assert contents[0].type == "text"
    assert contents[0].text == "Groceries [Immediate Obligations] (ID: c-1)"


def test_resource_get_cached_categories_falls_back_when_group_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Legacy cached entries without a group should fall back to the old format."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    # Simulate a legacy cache entry written without the `group` field by
    # poking the internal store directly (cache_categories always writes the
    # field, even if it's None).
    isolated._category_cache["b-1"] = {
        "last_refreshed": None,
        "records": [{"id": "c-1", "name": "Groceries"}],
    }
    monkeypatch.setattr(server, "ynab_resources", isolated)

    contents = server.get_cached_categories("b-1")

    assert len(contents) == 1
    assert contents[0].text == "Groceries (ID: c-1)"


def test_resource_get_cached_categories_falls_back_when_group_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A cached entry with group=None should also use the legacy format."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.cache_categories(
        "b-1",
        [{"id": "c-1", "name": "Groceries"}],  # no category_group_name -> stored as None
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)

    contents = server.get_cached_categories("b-1")

    assert len(contents) == 1
    assert contents[0].text == "Groceries (ID: c-1)"


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
# _find_category_id (cache-first + fuzzy matching helper)
# ---------------------------------------------------------------------------


def _seed_cache(monkeypatch: pytest.MonkeyPatch, tmp_path, budget_id: str, names_with_ids):
    """Helper: build an isolated YNABResources, cache categories, and patch in."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    raw = [
        {"id": cid, "name": name, "category_group_name": group}
        for cid, name, group in names_with_ids
    ]
    isolated.cache_categories(budget_id, raw)
    monkeypatch.setattr(server, "ynab_resources", isolated)
    return isolated


@pytest.mark.asyncio
async def test_find_category_id_cache_hit_exact_skips_api(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(
        monkeypatch,
        tmp_path,
        "b-1",
        [("c-rent", "Rent", "Bills"), ("c-food", "Groceries", "Food")],
    )

    async with await server.get_ynab_client() as client:
        candidates = await server._find_category_id(client, "b-1", "Groceries")

    assert len(candidates) == 1
    assert candidates[0]["id"] == "c-food"
    assert candidates[0]["name"] == "Groceries"
    mock_ynab_apis.categories.get_categories.assert_not_called()


@pytest.mark.asyncio
async def test_find_category_id_cache_hit_case_insensitive(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(monkeypatch, tmp_path, "b-1", [("c-food", "Groceries", "Food")])

    async with await server.get_ynab_client() as client:
        candidates = await server._find_category_id(client, "b-1", "groceries")

    assert len(candidates) == 1
    assert candidates[0]["id"] == "c-food"


@pytest.mark.asyncio
async def test_find_category_id_empty_cache_bootstraps_via_api(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    cat = _category_mock("c-rent", "Rent", 100_000, 50_000)
    cat.to_dict.return_value = {
        "id": "c-rent",
        "name": "Rent",
        "category_group_name": "Bills",
    }
    mock_ynab_apis.categories.get_categories.return_value = _resp(
        category_groups=[_category_group_mock("Bills", [cat])]
    )

    async with await server.get_ynab_client() as client:
        candidates = await server._find_category_id(client, "b-1", "Rent")

    assert len(candidates) == 1
    assert candidates[0]["id"] == "c-rent"
    mock_ynab_apis.categories.get_categories.assert_called_once_with("b-1")
    # Cache should now be populated.
    assert isolated.get_cached_category_records("b-1") == [
        {"id": "c-rent", "name": "Rent", "group": "Bills"}
    ]


@pytest.mark.asyncio
async def test_find_category_id_fuzzy_single_with_emoji(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(
        monkeypatch,
        tmp_path,
        "b-1",
        [("c-food", "Groceries 🛒", "Food"), ("c-rent", "Rent", "Bills")],
    )

    async with await server.get_ynab_client() as client:
        candidates = await server._find_category_id(client, "b-1", "groceries")

    assert len(candidates) == 1
    assert candidates[0]["id"] == "c-food"
    assert candidates[0]["name"] == "Groceries 🛒"
    mock_ynab_apis.categories.get_categories.assert_not_called()


@pytest.mark.asyncio
async def test_find_category_id_substring_multi_candidates(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """bd issue example: 'groceries' should surface both 'Groceries 🛒' and
    'Groceries (& Household)' as candidates so the caller can elicit a choice.
    """
    _seed_cache(
        monkeypatch,
        tmp_path,
        "b-1",
        [
            ("c-food1", "Groceries 🛒", "Food"),
            ("c-food2", "Groceries (& Household)", "Food"),
            ("c-rent", "Rent", "Bills"),
        ],
    )

    async with await server.get_ynab_client() as client:
        candidates = await server._find_category_id(client, "b-1", "groceries")

    ids = {c["id"] for c in candidates}
    assert ids == {"c-food1", "c-food2"}


@pytest.mark.asyncio
async def test_find_category_id_fuzzy_typo_recovery(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Typos like 'grocries' (missing 'e') should still resolve via difflib."""
    _seed_cache(
        monkeypatch,
        tmp_path,
        "b-1",
        [("c-food", "Groceries", "Food"), ("c-rent", "Rent", "Bills")],
    )

    async with await server.get_ynab_client() as client:
        candidates = await server._find_category_id(client, "b-1", "grocries")

    assert len(candidates) >= 1
    assert candidates[0]["id"] == "c-food"


@pytest.mark.asyncio
async def test_find_category_id_no_match_returns_empty_list(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(monkeypatch, tmp_path, "b-1", [("c-rent", "Rent", "Bills")])
    # Stale-cache refresh path: API returns the same data, still no match.
    cat = _category_mock("c-rent", "Rent", 0, 0)
    cat.to_dict.return_value = {
        "id": "c-rent",
        "name": "Rent",
        "category_group_name": "Bills",
    }
    mock_ynab_apis.categories.get_categories.return_value = _resp(
        category_groups=[_category_group_mock("Bills", [cat])]
    )

    async with await server.get_ynab_client() as client:
        candidates = await server._find_category_id(client, "b-1", "Spaceships")

    assert candidates == []


@pytest.mark.asyncio
async def test_find_category_id_stale_cache_refresh_finds_new_category(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(monkeypatch, tmp_path, "b-1", [("c-rent", "Rent", "Bills")])

    fresh_cat = _category_mock("c-new", "Subscriptions", 0, 0)
    fresh_cat.to_dict.return_value = {
        "id": "c-new",
        "name": "Subscriptions",
        "category_group_name": "Bills",
    }
    rent = _category_mock("c-rent", "Rent", 0, 0)
    rent.to_dict.return_value = {
        "id": "c-rent",
        "name": "Rent",
        "category_group_name": "Bills",
    }
    mock_ynab_apis.categories.get_categories.return_value = _resp(
        category_groups=[_category_group_mock("Bills", [rent, fresh_cat])]
    )

    async with await server.get_ynab_client() as client:
        candidates = await server._find_category_id(client, "b-1", "Subscriptions")

    assert len(candidates) == 1
    assert candidates[0]["id"] == "c-new"
    mock_ynab_apis.categories.get_categories.assert_called_once_with("b-1")


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
async def test_create_transaction_uses_only_budget_when_no_preference(
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


@pytest.mark.asyncio
async def test_create_transaction_accepts_payee_id_for_transfers(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    monkeypatch.setattr(server, "ynab_resources", isolated)

    created_txn = MagicMock()
    created_txn.to_dict.return_value = {"id": "new-transfer"}
    mock_ynab_apis.transactions.create_transaction.return_value = _resp(transaction=created_txn)

    result = await server.create_transaction(
        account_id="acct-1",
        amount=-25.0,
        payee_id="transfer-payee-1",
    )

    assert result == {"id": "new-transfer"}
    call = mock_ynab_apis.transactions.create_transaction.call_args
    posted = call.args[1].transaction
    assert posted.payee_id == "transfer-payee-1"
    assert posted.payee_name is None


@pytest.mark.asyncio
async def test_create_transaction_rejects_both_payee_name_and_payee_id(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError, match="payee_name or payee_id, not both"):
        await server.create_transaction(
            account_id="acct-1",
            amount=-25.0,
            payee_name="Transfer : Checking",
            payee_id="transfer-payee-1",
        )

    mock_ynab_apis.transactions.create_transaction.assert_not_called()


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
async def test_update_transaction_accepts_payee_id_for_transfers(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    captured: dict = {}

    def fake_existing(**kwargs: object) -> dict:
        captured.update(kwargs)
        return kwargs  # type: ignore[return-value]

    server.ExistingTransaction = fake_existing  # type: ignore[assignment]
    server.PutTransactionWrapper = lambda transaction: SimpleNamespace(transaction=transaction)  # type: ignore[assignment]

    result = await server.update_transaction("b-1", "t-1", payee_id="transfer-payee-1")

    assert "Updated transaction `t-1`" in result
    assert captured == {"payee_id": "transfer-payee-1"}


@pytest.mark.asyncio
async def test_update_transaction_rejects_both_payee_name_and_payee_id(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError, match="payee_name or payee_id, not both"):
        await server.update_transaction(
            "b-1",
            "t-1",
            payee_name="Transfer : Checking",
            payee_id="transfer-payee-1",
        )

    mock_ynab_apis.transactions.update_transaction.assert_not_called()


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
# delete_transaction (mutating tool)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_transaction_calls_sdk_and_returns_confirmation(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.delete_transaction.return_value = MagicMock()

    result = await server.delete_transaction("b-1", "t-1")

    assert "t-1" in result
    assert "b-1" in result
    assert "deleted" in result.lower()
    mock_ynab_apis.transactions.delete_transaction.assert_called_once_with("b-1", "t-1")


@pytest.mark.asyncio
async def test_delete_transaction_propagates_api_exception(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.delete_transaction.side_effect = ApiException(
        status=404, reason="Not Found"
    )

    with pytest.raises(ApiException):
        await server.delete_transaction("b-1", "t-missing")


@pytest.mark.asyncio
async def test_delete_transaction_elicits_confirmation_and_proceeds_when_confirmed(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    txn_mock = MagicMock()
    txn_mock.amount = -42_500
    txn_mock.payee_name = "Amazon"
    txn_mock.category_name = "Shopping"
    txn_mock.memo = None
    txn_mock.var_date = None
    txn_mock.date = "2026-05-10"
    mock_ynab_apis.transactions.get_transaction_by_id.return_value = _resp(transaction=txn_mock)
    mock_ynab_apis.transactions.delete_transaction.return_value = MagicMock()

    ctx = _FakeContext(_accept_confirm(True))
    result = await server.delete_transaction("b-1", "t-42", ctx=ctx)

    assert "deleted" in result.lower()
    assert len(ctx.calls) == 1
    message, _schema = ctx.calls[0]
    assert "Amazon" in message
    assert "$42.50" in message
    assert "outflow" in message
    mock_ynab_apis.transactions.delete_transaction.assert_called_once_with("b-1", "t-42")


@pytest.mark.asyncio
async def test_delete_transaction_returns_cancelled_when_user_declines(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    txn_mock = MagicMock()
    txn_mock.amount = -10_000
    txn_mock.payee_name = "Netflix"
    txn_mock.category_name = "Subscriptions"
    txn_mock.memo = None
    txn_mock.var_date = None
    txn_mock.date = "2026-05-01"
    mock_ynab_apis.transactions.get_transaction_by_id.return_value = _resp(transaction=txn_mock)

    ctx = _FakeContext(_accept_confirm(False))
    result = await server.delete_transaction("b-1", "t-99", ctx=ctx)

    assert "cancelled" in result.lower()
    mock_ynab_apis.transactions.delete_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_delete_transaction_returns_cancelled_when_user_dismisses(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    txn_mock = MagicMock()
    txn_mock.amount = -5_000
    txn_mock.payee_name = "Gym"
    txn_mock.category_name = None
    txn_mock.memo = None
    txn_mock.var_date = None
    txn_mock.date = "2026-05-15"
    mock_ynab_apis.transactions.get_transaction_by_id.return_value = _resp(transaction=txn_mock)

    ctx = _FakeContext(SimpleNamespace(action="cancel"))
    result = await server.delete_transaction("b-1", "t-88", ctx=ctx)

    assert "cancelled" in result.lower()
    mock_ynab_apis.transactions.delete_transaction.assert_not_called()


# ---------------------------------------------------------------------------
# split_transaction (idempotent mutating tool)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_split_transaction_patches_with_subtransactions_when_sum_matches(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    parent = SimpleNamespace(amount=-30_000)
    mock_ynab_apis.transactions.get_transaction_by_id.return_value = _resp(transaction=parent)
    mock_ynab_apis.transactions.update_transaction.return_value = MagicMock()

    captured: dict = {}

    def fake_existing(**kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    def fake_wrapper(transaction: object) -> object:
        return SimpleNamespace(transaction=transaction)

    server.ExistingTransaction = fake_existing  # type: ignore[assignment]
    server.PutTransactionWrapper = fake_wrapper  # type: ignore[assignment]

    splits = [
        {"amount": -20.0, "category_id": "cat-food", "memo": "Groceries"},
        {"amount": -10.0, "category_id": "cat-fun", "payee_name": "Sub-Payee"},
    ]
    result = await server.split_transaction("b-1", "t-1", splits)

    assert "t-1" in result
    assert "2 subtransactions" in result
    assert "subtransactions" in captured
    subs = captured["subtransactions"]
    assert len(subs) == 2
    assert [s.amount for s in subs] == [-20_000, -10_000]
    assert subs[0].category_id == "cat-food"
    assert subs[0].memo == "Groceries"
    assert subs[1].payee_name == "Sub-Payee"

    mock_ynab_apis.transactions.update_transaction.assert_called_once()
    call = mock_ynab_apis.transactions.update_transaction.call_args
    assert call.kwargs["budget_id"] == "b-1"
    assert call.kwargs["transaction_id"] == "t-1"


@pytest.mark.asyncio
async def test_split_transaction_rejects_when_sum_does_not_match(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    parent = SimpleNamespace(amount=-30_000)
    mock_ynab_apis.transactions.get_transaction_by_id.return_value = _resp(transaction=parent)

    with pytest.raises(ValueError, match="does not equal parent transaction amount"):
        await server.split_transaction(
            "b-1",
            "t-1",
            [
                {"amount": -20.0, "category_id": "cat-1"},
                {"amount": -5.0, "category_id": "cat-2"},
            ],
        )

    mock_ynab_apis.transactions.update_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_split_transaction_rejects_empty_splits(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError, match="at least one split"):
        await server.split_transaction("b-1", "t-1", [])

    mock_ynab_apis.transactions.get_transaction_by_id.assert_not_called()
    mock_ynab_apis.transactions.update_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_split_transaction_rejects_split_missing_amount(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    with pytest.raises(ValueError, match="missing required 'amount'"):
        await server.split_transaction(
            "b-1",
            "t-1",
            [{"category_id": "cat-1"}],  # type: ignore[list-item]
        )

    mock_ynab_apis.transactions.get_transaction_by_id.assert_not_called()


@pytest.mark.asyncio
async def test_split_transaction_raises_when_parent_not_found(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transaction_by_id.side_effect = ApiException(
        status=404, reason="Not Found"
    )

    with pytest.raises(ValueError, match="not found"):
        await server.split_transaction(
            "b-1",
            "t-missing",
            [{"amount": -10.0, "category_id": "c-1"}],
        )

    mock_ynab_apis.transactions.update_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_split_transaction_propagates_non_404_api_exception(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transaction_by_id.side_effect = ApiException(
        status=500, reason="Boom"
    )

    with pytest.raises(ApiException):
        await server.split_transaction(
            "b-1",
            "t-1",
            [{"amount": -10.0, "category_id": "c-1"}],
        )


# ---------------------------------------------------------------------------
# import_transactions (idempotent mutating tool)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_transactions_returns_list_of_imported_ids(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.import_transactions.return_value = _resp(
        transaction_ids=["imp-1", "imp-2"]
    )

    result = await server.import_transactions("b-1")

    assert result == ["imp-1", "imp-2"]
    mock_ynab_apis.transactions.import_transactions.assert_called_once_with("b-1")


@pytest.mark.asyncio
async def test_import_transactions_returns_empty_list_when_nothing_imported(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.import_transactions.return_value = _resp(transaction_ids=[])

    result = await server.import_transactions("b-1")

    assert result == []


@pytest.mark.asyncio
async def test_import_transactions_handles_none_transaction_ids(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    """Defensive: if SDK returns None for transaction_ids, treat as empty list."""
    mock_ynab_apis.transactions.import_transactions.return_value = _resp(transaction_ids=None)

    result = await server.import_transactions("b-1")

    assert result == []


@pytest.mark.asyncio
async def test_import_transactions_propagates_api_exception(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.import_transactions.side_effect = ApiException(
        status=500, reason="Boom"
    )

    with pytest.raises(ApiException):
        await server.import_transactions("b-1")


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
# create_scheduled_transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_scheduled_transaction_basic(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    created = MagicMock()
    created.id = "sched-abc"
    created.payee_name = "Netflix"
    created.account_name = "Checking"
    mock_ynab_apis.scheduled_transactions.create_scheduled_transaction.return_value = _resp(
        scheduled_transaction=created
    )

    result = await server.create_scheduled_transaction(
        "b-1", "acct-1", -15.99, frequency="monthly", payee_name="Netflix"
    )

    assert "sched-abc" in result
    assert "Netflix" in result
    assert "monthly" in result
    assert "$15.99" in result

    call = mock_ynab_apis.scheduled_transactions.create_scheduled_transaction.call_args
    budget_id, wrapper = call.args
    assert budget_id == "b-1"
    txn = wrapper.scheduled_transaction
    assert txn.account_id == "acct-1"
    assert txn.amount == -15990
    assert txn.payee_name == "Netflix"
    assert txn.frequency == "monthly"


@pytest.mark.asyncio
async def test_create_scheduled_transaction_uses_start_date(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    created = MagicMock()
    created.id = "sched-xyz"
    created.payee_name = "Landlord"
    created.account_name = "Checking"
    mock_ynab_apis.scheduled_transactions.create_scheduled_transaction.return_value = _resp(
        scheduled_transaction=created
    )

    await server.create_scheduled_transaction("b-1", "acct-1", -1500.00, start_date="2026-06-01")

    call = mock_ynab_apis.scheduled_transactions.create_scheduled_transaction.call_args
    _, wrapper = call.args
    from datetime import date

    assert wrapper.scheduled_transaction.var_date == date(2026, 6, 1)


@pytest.mark.asyncio
async def test_create_scheduled_transaction_defaults_to_today(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    from datetime import date

    created = MagicMock()
    created.id = "sched-def"
    created.payee_name = "Gym"
    created.account_name = "Checking"
    mock_ynab_apis.scheduled_transactions.create_scheduled_transaction.return_value = _resp(
        scheduled_transaction=created
    )

    await server.create_scheduled_transaction("b-1", "acct-1", -50.00)

    call = mock_ynab_apis.scheduled_transactions.create_scheduled_transaction.call_args
    _, wrapper = call.args
    assert wrapper.scheduled_transaction.var_date == date.today()


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
                cleared="uncleared",
            ),
        ]
    )

    result = await server.get_transactions_by_category("b-1", "cat-1")

    assert "# Transactions for Category `cat-1`" in result
    assert "Cafe" in result
    assert "Bookstore" in result
    assert "Cleared" in result
    assert "uncleared" in result


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


# ---------------------------------------------------------------------------
# spending_by_category / spending_by_payee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spending_by_category_aggregates_outflows_only(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(
        transactions=[
            _txn_mock("t-1", amount_milliunits=-25_000, category_id="cat-A", category_name="Food"),
            _txn_mock("t-2", amount_milliunits=-15_000, category_id="cat-A", category_name="Food"),
            _txn_mock("t-3", amount_milliunits=-5_000, category_id="cat-B", category_name="Gas"),
            _txn_mock("t-4", amount_milliunits=10_000, category_id="cat-A", category_name="Food"),
        ]
    )

    result = await server.spending_by_category("b-1", period="this_month")

    assert "# Spending by Category" in result
    assert "Food" in result
    assert "Gas" in result
    assert "$40.00" in result, "Food: 25 + 15 = 40 (inflow excluded)"
    assert "$5.00" in result, "Gas: 5"


@pytest.mark.asyncio
async def test_spending_by_category_top_n_caps_results(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(
        transactions=[
            _txn_mock(
                f"t-{i}",
                amount_milliunits=-(1000 * (i + 1)),
                category_id=f"cat-{i}",
                category_name=f"Cat{i}",
            )
            for i in range(5)
        ]
    )

    result = await server.spending_by_category("b-1", period="last_30d", top_n=2)

    assert "Cat4" in result
    assert "Cat3" in result
    assert "Cat0" not in result


@pytest.mark.asyncio
async def test_spending_by_category_empty_returns_friendly_message(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(transactions=[])

    result = await server.spending_by_category("b-1", period="ytd")

    assert "_No outflow transactions in the selected period._" in result


@pytest.mark.asyncio
async def test_spending_by_category_passes_since_date(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(transactions=[])

    await server.spending_by_category("b-1", period="this_month")

    call = mock_ynab_apis.transactions.get_transactions.call_args
    expected_since = date.today().replace(day=1)
    assert call.kwargs["since_date"] == expected_since


@pytest.mark.asyncio
async def test_spending_by_category_last_month_enforces_until_date(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - timedelta(days=1)
    in_last_month = last_month_end
    in_this_month = first_of_this_month

    mock_ynab_apis.transactions.get_transactions.return_value = _resp(
        transactions=[
            _txn_mock(
                "t-old",
                amount_milliunits=-10_000,
                category_id="cat-A",
                category_name="OldFood",
                var_date=in_last_month,
            ),
            _txn_mock(
                "t-new",
                amount_milliunits=-99_000,
                category_id="cat-B",
                category_name="NewFood",
                var_date=in_this_month,
            ),
        ]
    )

    result = await server.spending_by_category("b-1", period="last_month")

    assert "OldFood" in result
    assert "NewFood" not in result, "txns dated >= until_date excluded by client filter"


@pytest.mark.asyncio
async def test_spending_by_payee_groups_by_payee(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(
        transactions=[
            SimpleNamespace(
                id="t-1",
                amount=-25_000,
                account_id="acct-1",
                payee_id="p-A",
                payee_name="Cafe",
                var_date=None,
            ),
            SimpleNamespace(
                id="t-2",
                amount=-12_500,
                account_id="acct-1",
                payee_id="p-A",
                payee_name="Cafe",
                var_date=None,
            ),
            SimpleNamespace(
                id="t-3",
                amount=-5_000,
                account_id="acct-1",
                payee_id="p-B",
                payee_name="Gas Station",
                var_date=None,
            ),
        ]
    )

    result = await server.spending_by_payee_tool("b-1", period="last_30d")

    assert "# Spending by Payee" in result
    assert "Cafe" in result
    assert "Gas Station" in result
    assert "$37.50" in result, "Cafe: 25 + 12.50 = 37.50"


@pytest.mark.asyncio
async def test_spending_by_payee_filters_by_account_id(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.transactions.get_transactions.return_value = _resp(
        transactions=[
            SimpleNamespace(
                id="t-1",
                amount=-25_000,
                account_id="acct-1",
                payee_id="p-A",
                payee_name="Cafe",
                var_date=None,
            ),
            SimpleNamespace(
                id="t-2",
                amount=-99_000,
                account_id="acct-2",
                payee_id="p-A",
                payee_name="Cafe",
                var_date=None,
            ),
        ]
    )

    result = await server.spending_by_payee_tool("b-1", period="last_30d", account_id="acct-1")

    assert "$25.00" in result
    assert "$99.00" not in result, "txns on other accounts excluded by account_id filter"


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_returns_user_id_on_success(
    mock_ynab_apis: SimpleNamespace,
) -> None:
    mock_ynab_apis.users.get_user.return_value = _resp(user=SimpleNamespace(id="user-abc-123"))

    result = await server.ping()

    assert result == "ok (user_id=user-abc-123)"


# ---------------------------------------------------------------------------
# _resolve_budget_id (elicitation helper)
# ---------------------------------------------------------------------------


class _FakeContext:
    """Stand-in for FastMCP `Context` whose `elicit` returns a preset result."""

    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[tuple[str, type]] = []

    async def elicit(self, message: str, schema: type) -> object:
        self.calls.append((message, schema))
        return self._result


def _accept(index: int, set_as_preferred: bool = False) -> SimpleNamespace:
    """Mimic an `AcceptedElicitation` with a `.data` Pydantic-model proxy."""
    return SimpleNamespace(
        action="accept",
        data=SimpleNamespace(index=index, set_as_preferred=set_as_preferred),
    )


@pytest.mark.asyncio
async def test_resolve_budget_id_returns_preferred_when_set(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("preferred-b")
    monkeypatch.setattr(server, "ynab_resources", isolated)

    result = await server._resolve_budget_id(client=object(), ctx=None)

    assert result == "preferred-b"
    mock_ynab_apis.budgets.get_budgets.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_budget_id_short_circuits_single_budget(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("only-b", "Only Budget")]
    )

    ctx = _FakeContext(_accept(index=1))
    result = await server._resolve_budget_id(client=object(), ctx=ctx)

    assert result == "only-b"
    assert ctx.calls == [], "Single budget should not trigger elicitation"


@pytest.mark.asyncio
async def test_resolve_budget_id_raises_when_no_budgets(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    monkeypatch.setattr(server, "ynab_resources", YNABResources(config_dir=tmp_path))
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(budgets=[])

    with pytest.raises(ValueError, match="No YNAB budgets"):
        await server._resolve_budget_id(client=object(), ctx=None)


@pytest.mark.asyncio
async def test_resolve_budget_id_raises_when_multiple_and_no_ctx(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """No silent budgets[0] fallback — the foot-gun this helper removes."""
    from mcp_ynab.server import YNABResources

    monkeypatch.setattr(server, "ynab_resources", YNABResources(config_dir=tmp_path))
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("a-b", "A"), _budget_mock("b-b", "B")]
    )

    with pytest.raises(ValueError, match="no preferred budget"):
        await server._resolve_budget_id(client=object(), ctx=None)


@pytest.mark.asyncio
async def test_resolve_budget_id_elicits_and_returns_chosen(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("first-b", "Personal"), _budget_mock("second-b", "Work")]
    )

    ctx = _FakeContext(_accept(index=2))
    result = await server._resolve_budget_id(client=object(), ctx=ctx)

    assert result == "second-b"
    assert isolated.get_preferred_budget_id() is None, "set_as_preferred=False, must not persist"
    assert len(ctx.calls) == 1
    message, schema = ctx.calls[0]
    assert "Personal" in message and "Work" in message


@pytest.mark.asyncio
async def test_resolve_budget_id_persists_preference_when_requested(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("a-b", "A"), _budget_mock("b-b", "B")]
    )

    ctx = _FakeContext(_accept(index=1, set_as_preferred=True))
    result = await server._resolve_budget_id(client=object(), ctx=ctx)

    assert result == "a-b"
    assert isolated.get_preferred_budget_id() == "a-b"


@pytest.mark.asyncio
async def test_resolve_budget_id_raises_on_decline(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    monkeypatch.setattr(server, "ynab_resources", YNABResources(config_dir=tmp_path))
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("a-b", "A"), _budget_mock("b-b", "B")]
    )

    ctx = _FakeContext(SimpleNamespace(action="decline"))
    with pytest.raises(ValueError, match="declined"):
        await server._resolve_budget_id(client=object(), ctx=ctx)


@pytest.mark.asyncio
async def test_resolve_budget_id_raises_on_cancel(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    monkeypatch.setattr(server, "ynab_resources", YNABResources(config_dir=tmp_path))
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("a-b", "A"), _budget_mock("b-b", "B")]
    )

    ctx = _FakeContext(SimpleNamespace(action="cancel"))
    with pytest.raises(ValueError, match="cancelled"):
        await server._resolve_budget_id(client=object(), ctx=ctx)


@pytest.mark.asyncio
async def test_resolve_budget_id_raises_on_out_of_range_index(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    monkeypatch.setattr(server, "ynab_resources", YNABResources(config_dir=tmp_path))
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("a-b", "A"), _budget_mock("b-b", "B")]
    )

    ctx = _FakeContext(_accept(index=99))
    with pytest.raises(ValueError, match="out of range"):
        await server._resolve_budget_id(client=object(), ctx=ctx)


@pytest.mark.asyncio
async def test_create_transaction_elicits_when_multiple_budgets(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """End-to-end: tool routes through helper and uses elicited budget id."""
    from mcp_ynab.server import YNABResources

    monkeypatch.setattr(server, "ynab_resources", YNABResources(config_dir=tmp_path))
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("first-b", "A"), _budget_mock("second-b", "B")]
    )
    created_txn = MagicMock()
    created_txn.to_dict.return_value = {"id": "new-txn"}
    mock_ynab_apis.transactions.create_transaction.return_value = _resp(transaction=created_txn)

    ctx = _FakeContext(_accept(index=2))
    result = await server.create_transaction(
        account_id="acct-1",
        amount=10.0,
        payee_name="Test",
        confirm=False,
        ctx=ctx,
    )

    assert result == {"id": "new-txn"}
    call = mock_ynab_apis.transactions.create_transaction.call_args
    assert call.args[0] == "second-b", "tool must use elicited budget, not first"


@pytest.mark.asyncio
async def test_get_account_balance_elicits_when_multiple_budgets(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    monkeypatch.setattr(server, "ynab_resources", YNABResources(config_dir=tmp_path))
    mock_ynab_apis.budgets.get_budgets.return_value = _resp(
        budgets=[_budget_mock("first-b", "A"), _budget_mock("second-b", "B")]
    )
    mock_ynab_apis.accounts.get_account_by_id.return_value = _resp(
        account=SimpleNamespace(balance=50_000)
    )

    ctx = _FakeContext(_accept(index=1))
    result = await server.get_account_balance("acct-1", ctx=ctx)

    assert result == pytest.approx(50.0)
    mock_ynab_apis.accounts.get_account_by_id.assert_called_once_with("first-b", "acct-1")


# ---------------------------------------------------------------------------
# _resolve_category_id (elicitation helper for create_transaction)
# ---------------------------------------------------------------------------


def _accept_category(index: int) -> SimpleNamespace:
    """Mimic an `AcceptedElicitation` for `_CategoryChoice`."""
    return SimpleNamespace(action="accept", data=SimpleNamespace(index=index))


@pytest.mark.asyncio
async def test_resolve_category_id_exact_match_skips_elicit(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(monkeypatch, tmp_path, "b-1", [("c-food", "Groceries", "Food")])
    ctx = _FakeContext(_accept_category(index=99))  # would fail if elicited

    async with await server.get_ynab_client() as client:
        result = await server._resolve_category_id(client, "b-1", "Groceries", ctx)

    assert result == "c-food"
    assert ctx.calls == []


@pytest.mark.asyncio
async def test_resolve_category_id_no_ctx_returns_none_for_none_name(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(monkeypatch, tmp_path, "b-1", [("c-rent", "Rent", "Bills")])

    async with await server.get_ynab_client() as client:
        result = await server._resolve_category_id(client, "b-1", None, ctx=None)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_category_id_no_ctx_returns_none_when_ambiguous(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(
        monkeypatch,
        tmp_path,
        "b-1",
        [("c-a", "Groceries 🛒", "Food"), ("c-b", "Groceries (Household)", "Bills")],
    )

    async with await server.get_ynab_client() as client:
        result = await server._resolve_category_id(client, "b-1", "groceries", ctx=None)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_category_id_elicits_from_full_list_when_name_none(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(
        monkeypatch,
        tmp_path,
        "b-1",
        [("c-rent", "Rent", "Bills"), ("c-food", "Groceries", "Food")],
    )
    ctx = _FakeContext(_accept_category(index=2))

    async with await server.get_ynab_client() as client:
        result = await server._resolve_category_id(client, "b-1", None, ctx)

    assert result == "c-food"
    assert len(ctx.calls) == 1
    message, schema = ctx.calls[0]
    assert "Rent" in message and "Groceries" in message
    assert schema is server._CategoryChoice


@pytest.mark.asyncio
async def test_resolve_category_id_elicits_from_candidates_when_ambiguous(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(
        monkeypatch,
        tmp_path,
        "b-1",
        [
            ("c-rent", "Rent", "Bills"),
            ("c-a", "Groceries 🛒", "Food"),
            ("c-b", "Groceries (Household)", "Bills"),
        ],
    )
    ctx = _FakeContext(_accept_category(index=2))

    async with await server.get_ynab_client() as client:
        result = await server._resolve_category_id(client, "b-1", "groceries", ctx)

    assert result == "c-b"  # second candidate (Rent excluded — substring match only)
    message, _ = ctx.calls[0]
    assert "Multiple categories match" in message
    assert "Rent" not in message  # only candidates, not full list


@pytest.mark.asyncio
async def test_resolve_category_id_elicits_full_list_on_zero_matches(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(
        monkeypatch,
        tmp_path,
        "b-1",
        [("c-rent", "Rent", "Bills"), ("c-food", "Groceries", "Food")],
    )
    # `_find_category_id` triggers a stale-cache refresh on a 0-match miss,
    # so the API must return the same records to keep the cache populated.
    cat_rent = _category_mock("c-rent", "Rent", 0, 0)
    cat_rent.to_dict.return_value = {
        "id": "c-rent",
        "name": "Rent",
        "category_group_name": "Bills",
    }
    cat_food = _category_mock("c-food", "Groceries", 0, 0)
    cat_food.to_dict.return_value = {
        "id": "c-food",
        "name": "Groceries",
        "category_group_name": "Food",
    }
    mock_ynab_apis.categories.get_categories.return_value = _resp(
        category_groups=[_category_group_mock("Bills", [cat_rent, cat_food])]
    )
    ctx = _FakeContext(_accept_category(index=1))

    async with await server.get_ynab_client() as client:
        result = await server._resolve_category_id(client, "b-1", "wxyz-no-match", ctx)

    assert result == "c-rent"
    message, _ = ctx.calls[0]
    assert "No category matched" in message
    assert "Rent" in message and "Groceries" in message


@pytest.mark.asyncio
async def test_resolve_category_id_index_zero_returns_none(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(monkeypatch, tmp_path, "b-1", [("c-rent", "Rent", "Bills")])
    ctx = _FakeContext(_accept_category(index=0))

    async with await server.get_ynab_client() as client:
        result = await server._resolve_category_id(client, "b-1", None, ctx)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_category_id_decline_returns_none(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(monkeypatch, tmp_path, "b-1", [("c-rent", "Rent", "Bills")])
    ctx = _FakeContext(SimpleNamespace(action="decline"))

    async with await server.get_ynab_client() as client:
        result = await server._resolve_category_id(client, "b-1", None, ctx)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_category_id_out_of_range_raises(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _seed_cache(monkeypatch, tmp_path, "b-1", [("c-rent", "Rent", "Bills")])
    ctx = _FakeContext(_accept_category(index=99))

    async with await server.get_ynab_client() as client:
        with pytest.raises(ValueError, match="out of range"):
            await server._resolve_category_id(client, "b-1", None, ctx)


@pytest.mark.asyncio
async def test_resolve_category_id_returns_none_when_cache_empty_and_no_match(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """When `category_name=None` and no categories exist anywhere, return None."""
    from mcp_ynab.server import YNABResources

    monkeypatch.setattr(server, "ynab_resources", YNABResources(config_dir=tmp_path))
    mock_ynab_apis.categories.get_categories.return_value = _resp(category_groups=[])
    ctx = _FakeContext(_accept_category(index=1))

    async with await server.get_ynab_client() as client:
        result = await server._resolve_category_id(client, "b-1", None, ctx)

    assert result is None
    assert ctx.calls == []  # nothing to elicit


@pytest.mark.asyncio
async def test_create_transaction_elicits_category_when_ambiguous(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """End-to-end: create_transaction triggers category elicitation."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    isolated.cache_categories(
        "b-1",
        [
            {"id": "c-a", "name": "Groceries 🛒", "category_group_name": "Food"},
            {"id": "c-b", "name": "Groceries (Household)", "category_group_name": "Bills"},
        ],
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)
    mock_ynab_apis.transactions.create_transaction.return_value = _resp(
        transaction=SimpleNamespace(to_dict=lambda: {"id": "t-1", "category_id": "c-b"})
    )
    ctx = _FakeContext(_accept_category(index=2))

    result = await server.create_transaction(
        account_id="acct-1",
        amount=12.34,
        payee_name="Trader Joe's",
        category_name="groceries",
        confirm=False,
        ctx=ctx,
    )

    assert result == {"id": "t-1", "category_id": "c-b"}
    # The wrapper passed to YNAB carried our chosen category_id.
    args, _ = mock_ynab_apis.transactions.create_transaction.call_args
    wrapper = args[1]
    assert wrapper.transaction.category_id == "c-b"


# ---------------------------------------------------------------------------
# qlh.3: confirm-before-post elicitation in create_transaction
# ---------------------------------------------------------------------------


class _QueuedFakeContext:
    """Fake ``Context`` that returns elicitation results in order — one per call."""

    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, type]] = []

    async def elicit(self, message: str, schema: type) -> object:
        self.calls.append((message, schema))
        if not self._results:
            raise AssertionError(f"Unexpected extra elicit call: {message!r}")
        return self._results.pop(0)


def _accept_confirm(confirm: bool) -> SimpleNamespace:
    """Mimic an ``AcceptedElicitation`` for ``_PostConfirmation``."""
    return SimpleNamespace(action="accept", data=SimpleNamespace(confirm=confirm))


def test_format_post_confirmation_message_spend_with_category() -> None:
    msg = server._format_post_confirmation_message(
        amount=-12.34,
        payee_name="Trader Joe's",
        txn_date=date(2026, 5, 4),
        category_name="Groceries",
        memo="weekly run",
    )
    assert "Spend" in msg
    assert "$12.34" in msg
    assert "Trader Joe's" in msg
    assert "2026-05-04" in msg
    assert "'Groceries'" in msg
    assert "weekly run" in msg


def test_format_post_confirmation_message_receive_uncategorized_no_memo() -> None:
    msg = server._format_post_confirmation_message(
        amount=50.0,
        payee_name="Refund Co",
        txn_date=date(2026, 5, 4),
        category_name=None,
        memo=None,
    )
    assert msg.startswith("Receive $50.00 to Refund Co")
    assert "(uncategorized)" in msg
    # No memo dash artifact when memo is None.
    assert " — " not in msg


def test_category_display_name_finds_cached_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.cache_categories(
        "b-1",
        [{"id": "c-x", "name": "Dining Out", "category_group_name": "Food"}],
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)

    assert server._category_display_name("b-1", "c-x") == "Dining Out"
    assert server._category_display_name("b-1", "c-missing") is None
    assert server._category_display_name("b-1", None) is None


@pytest.mark.asyncio
async def test_create_transaction_skips_confirmation_when_no_ctx(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Default confirm=True still no-ops when ctx is unavailable — posts directly."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    monkeypatch.setattr(server, "ynab_resources", isolated)
    mock_ynab_apis.transactions.create_transaction.return_value = _resp(
        transaction=SimpleNamespace(to_dict=lambda: {"id": "t-1"})
    )

    result = await server.create_transaction(
        account_id="acct-1",
        amount=5.0,
        payee_name="Cafe",
    )

    assert result == {"id": "t-1"}
    mock_ynab_apis.transactions.create_transaction.assert_called_once()


@pytest.mark.asyncio
async def test_create_transaction_skips_confirmation_when_confirm_false(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """confirm=False bypasses the elicit even when ctx is provided."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    monkeypatch.setattr(server, "ynab_resources", isolated)
    mock_ynab_apis.transactions.create_transaction.return_value = _resp(
        transaction=SimpleNamespace(to_dict=lambda: {"id": "t-1"})
    )
    ctx = _QueuedFakeContext([])  # would raise if any elicit happened

    result = await server.create_transaction(
        account_id="acct-1",
        amount=5.0,
        payee_name="Cafe",
        confirm=False,
        ctx=ctx,
    )

    assert result == {"id": "t-1"}
    assert ctx.calls == []
    mock_ynab_apis.transactions.create_transaction.assert_called_once()


@pytest.mark.asyncio
async def test_create_transaction_posts_after_confirmation_accepted(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """confirm=True with ctx triggers elicit; accept→confirm=True posts."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    isolated.cache_categories(
        "b-1",
        [{"id": "c-food", "name": "Groceries", "category_group_name": "Food"}],
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)
    mock_ynab_apis.transactions.create_transaction.return_value = _resp(
        transaction=SimpleNamespace(to_dict=lambda: {"id": "t-1"})
    )
    ctx = _QueuedFakeContext([_accept_confirm(True)])

    result = await server.create_transaction(
        account_id="acct-1",
        amount=-12.34,
        payee_name="Trader Joe's",
        category_name="Groceries",
        ctx=ctx,
    )

    assert result == {"id": "t-1"}
    mock_ynab_apis.transactions.create_transaction.assert_called_once()
    # Confirmation prompt mentioned the resolved category and amount.
    assert len(ctx.calls) == 1
    confirmation_msg, schema = ctx.calls[0]
    assert schema is server._PostConfirmation
    assert "Groceries" in confirmation_msg
    assert "$12.34" in confirmation_msg


@pytest.mark.asyncio
async def test_create_transaction_returns_cancelled_when_confirm_field_false(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Accept with confirm=False returns cancelled marker; YNAB never called."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    monkeypatch.setattr(server, "ynab_resources", isolated)
    ctx = _QueuedFakeContext([_accept_confirm(False)])

    result = await server.create_transaction(
        account_id="acct-1",
        amount=10.0,
        payee_name="Cafe",
        ctx=ctx,
    )

    assert result == {"cancelled": True, "reason": "user_declined_confirmation"}
    mock_ynab_apis.transactions.create_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_create_transaction_returns_cancelled_on_decline(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Decline action returns cancelled marker; YNAB never called."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    monkeypatch.setattr(server, "ynab_resources", isolated)
    ctx = _QueuedFakeContext([SimpleNamespace(action="decline", data=None)])

    result = await server.create_transaction(
        account_id="acct-1",
        amount=10.0,
        payee_name="Cafe",
        ctx=ctx,
    )

    assert result == {"cancelled": True, "reason": "user_declined_confirmation"}
    mock_ynab_apis.transactions.create_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_create_transaction_returns_cancelled_on_cancel(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Cancel action returns cancelled marker; YNAB never called."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    monkeypatch.setattr(server, "ynab_resources", isolated)
    ctx = _QueuedFakeContext([SimpleNamespace(action="cancel", data=None)])

    result = await server.create_transaction(
        account_id="acct-1",
        amount=10.0,
        payee_name="Cafe",
        ctx=ctx,
    )

    assert result == {"cancelled": True, "reason": "user_declined_confirmation"}
    mock_ynab_apis.transactions.create_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_create_transaction_chains_category_then_confirmation(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """End-to-end: category elicit AND confirmation elicit fire in order, both accepted."""
    from mcp_ynab.server import YNABResources

    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-1")
    isolated.cache_categories(
        "b-1",
        [
            {"id": "c-a", "name": "Groceries 🛒", "category_group_name": "Food"},
            {"id": "c-b", "name": "Groceries (Household)", "category_group_name": "Bills"},
        ],
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)
    mock_ynab_apis.transactions.create_transaction.return_value = _resp(
        transaction=SimpleNamespace(to_dict=lambda: {"id": "t-1", "category_id": "c-b"})
    )
    ctx = _QueuedFakeContext([_accept_category(index=2), _accept_confirm(True)])

    result = await server.create_transaction(
        account_id="acct-1",
        amount=-25.0,
        payee_name="Target",
        category_name="groceries",
        ctx=ctx,
    )

    assert result == {"id": "t-1", "category_id": "c-b"}
    schemas = [c[1] for c in ctx.calls]
    assert schemas == [server._CategoryChoice, server._PostConfirmation]
    # Confirmation message reflects the *chosen* category, not the user's input.
    confirm_msg = ctx.calls[1][0]
    assert "Groceries (Household)" in confirm_msg
