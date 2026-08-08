"""Contract tests for canonical one-to-one YNAB API adapters."""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from ynab.models.put_transaction_wrapper import PutTransactionWrapper
from ynab.models.transaction_detail import TransactionDetail
from ynab.models.transaction_response import TransactionResponse
from ynab.models.transaction_response_data import TransactionResponseData
from ynab.models.transactions_response import TransactionsResponse
from ynab.models.transactions_response_data import TransactionsResponseData
from ynab.rest import ApiException

from mcp_ynab import server
from mcp_ynab.adapters import api_get_transactions, api_update_transaction
from mcp_ynab import adapters
from mcp_ynab.errors import YNABAPIError
from mcp_ynab.state import YNABResources


def _uuid(label: str) -> str:
    """Return a deterministic valid UUID for strict SDK model fields."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, label))


def _transaction(
    transaction_id: str, *, memo: str | None = "coffee", deleted: bool = False
) -> TransactionDetail:
    """Build a complete real SDK transaction model for serialization tests."""
    return TransactionDetail(
        id=transaction_id,
        date=date(2026, 1, 15),
        amount=-1250,
        memo=memo,
        cleared="cleared",
        approved=True,
        account_id=_uuid("account"),
        category_id=_uuid("category"),
        deleted=deleted,
        account_name="Checking",
        payee_name="Coffee Shop",
        category_name="Dining Out",
        subtransactions=[],
    )


def _transactions_response(
    *transactions: TransactionDetail, knowledge: int
) -> TransactionsResponse:
    """Build a real SDK response envelope."""
    return TransactionsResponse(
        data=TransactionsResponseData(transactions=list(transactions), server_knowledge=knowledge)
    )


def _resource(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> YNABResources:
    """Install an isolated state store for one adapter test."""
    resources = YNABResources(tmp_path)
    monkeypatch.setattr(server, "ynab_resources", resources)
    return resources


@pytest.fixture(scope="module", autouse=True)
def _isolate_adapter_registration() -> None:
    """Keep legacy code-mode golden snapshots isolated from this additive module."""
    yield
    for name in {"api_get_transactions", "api_update_transaction"}:
        server.mcp._tool_manager._tools.pop(name, None)


@pytest.mark.asyncio
async def test_get_transactions_full_fetch_has_complete_json_snapshot(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(tmp_path, monkeypatch)
    response = _transactions_response(_transaction("txn-1"), knowledge=17)
    mock_ynab_apis.transactions.get_transactions.return_value = response

    result = await api_get_transactions(plan_id="budget-1")

    assert result == {
        "transactions": [
            {
                "id": "txn-1",
                "date": "2026-01-15",
                "amount": -1250,
                "memo": "coffee",
                "cleared": "cleared",
                "approved": True,
                "account_id": _uuid("account"),
                "category_id": _uuid("category"),
                "deleted": False,
                "account_name": "Checking",
                "payee_name": "Coffee Shop",
                "category_name": "Dining Out",
                "subtransactions": [],
            }
        ],
        "server_knowledge": 17,
    }
    cache_file = tmp_path / adapters.TRANSACTIONS_CACHE_FILENAME
    assert json.loads(cache_file.read_text())["budget-1"] == result["transactions"]


@pytest.mark.asyncio
async def test_get_transactions_delta_merges_records_and_round_trips_knowledge(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = _resource(tmp_path, monkeypatch)
    cache_file = tmp_path / adapters.TRANSACTIONS_CACHE_FILENAME
    old_record = {"id": "txn-old", "memo": "old"}
    deleted_record = {"id": "txn-deleted", "memo": "remove me"}
    cache_file.write_text(json.dumps({"budget-1": [old_record, deleted_record]}))
    resources.set_knowledge("budget-1", "transactions", 10)

    response = _transactions_response(
        _transaction("txn-old", memo="updated"),
        _transaction("txn-deleted", deleted=True),
        _transaction("txn-new", memo="new"),
        knowledge=11,
    )
    mock_ynab_apis.transactions.get_transactions.return_value = response

    result = await api_get_transactions(plan_id="budget-1")

    mock_ynab_apis.transactions.get_transactions.assert_called_once_with(
        plan_id="budget-1",
        since_date=None,
        until_date=None,
        type=None,
        last_knowledge_of_server=10,
    )
    assert result["server_knowledge"] == 11
    cached = json.loads(cache_file.read_text())["budget-1"]
    assert [record["id"] for record in cached] == ["txn-old", "txn-new"]
    assert cached[0]["memo"] == "updated"
    assert resources.get_knowledge("budget-1", "transactions") == 11


@pytest.mark.asyncio
async def test_get_transactions_failure_does_not_advance_knowledge_or_cache(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = _resource(tmp_path, monkeypatch)
    cache_file = tmp_path / adapters.TRANSACTIONS_CACHE_FILENAME
    cache_file.write_text(json.dumps({"budget-1": [{"id": "txn-1", "memo": "safe"}]}))
    resources.set_knowledge("budget-1", "transactions", 10)
    before = cache_file.read_bytes()
    monkeypatch.setattr(adapters, "READ_MAX_ATTEMPTS", 1)
    mock_ynab_apis.transactions.get_transactions.side_effect = ApiException(
        status=500, reason="server error"
    )

    with pytest.raises(YNABAPIError) as raised:
        await api_get_transactions(plan_id="budget-1")

    assert raised.value.retryable is True
    assert resources.get_knowledge("budget-1", "transactions") == 10
    assert cache_file.read_bytes() == before


@pytest.mark.asyncio
async def test_get_transactions_retries_retryable_read(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(tmp_path, monkeypatch)
    monkeypatch.setattr(adapters, "READ_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(adapters, "READ_BASE_DELAY", 0)
    mock_ynab_apis.transactions.get_transactions.side_effect = [
        ApiException(status=429, reason="rate limited"),
        _transactions_response(_transaction("txn-1"), knowledge=4),
    ]

    result = await api_get_transactions(plan_id="budget-1")

    assert result["server_knowledge"] == 4
    assert mock_ynab_apis.transactions.get_transactions.call_count == 2


@pytest.mark.asyncio
async def test_update_transaction_validates_flat_body_and_serializes_response(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(tmp_path, monkeypatch)
    response = TransactionResponse(
        data=TransactionResponseData(
            transaction=_transaction("txn-1", memo="new"), server_knowledge=22
        )
    )
    mock_ynab_apis.transactions.update_transaction.return_value = response

    result = await api_update_transaction(
        plan_id="budget-1",
        transaction_id="txn-1",
        body={"memo": "new", "amount": -3200, "approved": True},
    )

    wrapper = mock_ynab_apis.transactions.update_transaction.call_args.kwargs["data"]
    assert isinstance(wrapper, PutTransactionWrapper)
    assert wrapper.transaction.memo == "new"
    assert wrapper.transaction.amount == -3200
    assert wrapper.transaction.approved is True
    assert result["server_knowledge"] == 22
    assert result["transaction"]["memo"] == "new"


@pytest.mark.asyncio
async def test_update_transaction_normalizes_errors_without_retry(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(tmp_path, monkeypatch)
    mock_ynab_apis.transactions.update_transaction.side_effect = ApiException(
        status=500, reason="server error"
    )

    with pytest.raises(YNABAPIError) as raised:
        await api_update_transaction(
            plan_id="budget-1", transaction_id="txn-1", body={"memo": "new"}
        )

    assert raised.value.retryable is True
    assert mock_ynab_apis.transactions.update_transaction.call_count == 1


@pytest.mark.asyncio
async def test_update_transaction_requires_body_before_client_or_network(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_client = server.get_ynab_client
    called = False

    async def fail_if_client_constructed() -> object:
        nonlocal called
        called = True
        raise AssertionError("client must not be constructed for an absent body")

    monkeypatch.setattr(server, "get_ynab_client", fail_if_client_constructed)

    with pytest.raises(ValueError, match="requires body"):
        await api_update_transaction(plan_id="budget-1", transaction_id="txn-1")
    with pytest.raises(ValueError, match="requires body"):
        await api_update_transaction(plan_id="budget-1", transaction_id="txn-1", body=None)

    assert called is False
    assert mock_ynab_apis.transactions.update_transaction.call_count == 0
    monkeypatch.setattr(server, "get_ynab_client", get_client)


def test_adapter_tools_are_registered_with_canonical_names() -> None:
    tools = server.mcp._tool_manager._tools

    assert tools["api_get_transactions"].annotations.readOnlyHint is True
    assert tools["api_update_transaction"].annotations.readOnlyHint is False
    assert "ctx" not in tools["api_get_transactions"].parameters["properties"]
    assert "ctx" not in tools["api_update_transaction"].parameters["properties"]


def test_body_parameter_name_resolves_real_sdk_write_methods() -> None:
    """The body-kwarg resolver must match the real SDK signatures, not mocks.

    Regression guard: the canonical adapter helper passes the validated write
    body under the SDK method's actual request-body parameter name.  SDK 4.3
    names it ``data`` on transactions writes but ``put_scheduled_transaction_wrapper``
    on scheduled writes; resolving from the real signature prevents a latent
    TypeError that MagicMock-based tests cannot surface.
    """
    from ynab.api.scheduled_transactions_api import ScheduledTransactionsApi
    from ynab.api.transactions_api import TransactionsApi
    from ynab.api_client import ApiClient
    from ynab.configuration import Configuration

    client = ApiClient(Configuration(access_token="test"))
    assert (
        adapters._body_parameter_name(
            TransactionsApi(client).update_transaction,
            {"plan_id": "p", "transaction_id": "t"},
        )
        == "data"
    )
    assert (
        adapters._body_parameter_name(
            ScheduledTransactionsApi(client).update_scheduled_transaction,
            {"plan_id": "p", "scheduled_transaction_id": "s"},
        )
        == "put_scheduled_transaction_wrapper"
    )
