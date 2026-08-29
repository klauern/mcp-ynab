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

from mcp_ynab import adapters, server
from mcp_ynab.adapters import api_get_transactions, api_update_transaction
from mcp_ynab.code_mode import build_spec, generate_stubs
from mcp_ynab.errors import YNABAPIError
from mcp_ynab.state import Preferences, YNABResources


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"bogus": 1},  # unknown key must not be silently ignored by Pydantic
        {},  # nothing to update
        {"id": "txn-1"},  # selector keys alone are not an update
        {"transaction": {"bogus": 1}},  # nested unknown keys rejected too
    ],
)
async def test_update_transaction_rejects_invalid_bodies_before_network(
    mock_ynab_apis: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, body: dict
) -> None:
    called = False

    async def fail_if_client_constructed() -> object:
        nonlocal called
        called = True
        raise AssertionError("client must not be constructed for an invalid body")

    monkeypatch.setattr(server, "get_ynab_client", fail_if_client_constructed)

    with pytest.raises(ValueError):
        await api_update_transaction(plan_id="budget-1", transaction_id="txn-1", body=body)

    assert called is False
    assert mock_ynab_apis.transactions.update_transaction.call_count == 0


@pytest.mark.asyncio
async def test_get_transactions_delta_without_cache_baseline_forces_full_fetch(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Knowledge without a cache baseline must NOT advance from a delta-only fetch."""
    resources = _resource(tmp_path, monkeypatch)
    resources.set_knowledge("budget-1", "transactions", 10)
    # Note: transactions_delta.json deliberately missing.

    response = _transactions_response(_transaction("txn-1"), knowledge=11)
    mock_ynab_apis.transactions.get_transactions.return_value = response

    result = await api_get_transactions(plan_id="budget-1")

    # No baseline -> full fetch (no knowledge token), so the cache is REPLACED
    # with the complete records instead of a delta merge that would lose data.
    mock_ynab_apis.transactions.get_transactions.assert_called_once_with(
        plan_id="budget-1",
        since_date=None,
        until_date=None,
        type=None,
        last_knowledge_of_server=None,
    )
    assert result["server_knowledge"] == 11
    cache_file = tmp_path / adapters.TRANSACTIONS_CACHE_FILENAME
    assert json.loads(cache_file.read_text())["budget-1"] == result["transactions"]


@pytest.mark.asyncio
async def test_get_transactions_corrupt_cache_forces_full_fetch(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = _resource(tmp_path, monkeypatch)
    resources.set_knowledge("budget-1", "transactions", 10)
    cache_file = tmp_path / adapters.TRANSACTIONS_CACHE_FILENAME
    cache_file.write_text("not json at all")

    mock_ynab_apis.transactions.get_transactions.return_value = _transactions_response(
        _transaction("txn-1"), knowledge=12
    )

    await api_get_transactions(plan_id="budget-1")

    mock_ynab_apis.transactions.get_transactions.assert_called_once_with(
        plan_id="budget-1",
        since_date=None,
        until_date=None,
        type=None,
        last_knowledge_of_server=None,
    )


@pytest.mark.asyncio
async def test_get_transactions_explicit_token_without_baseline_forces_full_fetch(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicitly supplied knowledge token is only honored with a baseline."""
    _resource(tmp_path, monkeypatch)
    # No cache file at all.

    mock_ynab_apis.transactions.get_transactions.return_value = _transactions_response(
        _transaction("txn-1"), knowledge=11
    )

    await api_get_transactions(plan_id="budget-1", last_knowledge_of_server=7)

    mock_ynab_apis.transactions.get_transactions.assert_called_once_with(
        plan_id="budget-1",
        since_date=None,
        until_date=None,
        type=None,
        last_knowledge_of_server=None,
    )


@pytest.mark.asyncio
async def test_get_transactions_malformed_cache_records_forces_full_fetch(
    mock_ynab_apis: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A baseline containing non-dict records is not a safe merge target."""
    resources = _resource(tmp_path, monkeypatch)
    resources.set_knowledge("budget-1", "transactions", 10)
    cache_file = tmp_path / adapters.TRANSACTIONS_CACHE_FILENAME
    cache_file.write_text(json.dumps({"budget-1": [{"id": "txn-1"}, "garbage"]}))

    mock_ynab_apis.transactions.get_transactions.return_value = _transactions_response(
        _transaction("txn-1"), knowledge=13
    )

    await api_get_transactions(plan_id="budget-1")

    mock_ynab_apis.transactions.get_transactions.assert_called_once_with(
        plan_id="budget-1",
        since_date=None,
        until_date=None,
        type=None,
        last_knowledge_of_server=None,
    )


def test_adapter_tools_are_registered_with_canonical_names() -> None:
    tools = server.mcp._tool_manager._tools

    assert tools["api_get_transactions"].annotations.readOnlyHint is True
    assert tools["api_update_transaction"].annotations.readOnlyHint is False
    assert "ctx" not in tools["api_get_transactions"].parameters["properties"]
    assert "ctx" not in tools["api_update_transaction"].parameters["properties"]


@pytest.mark.asyncio
async def test_implemented_canonical_adapters_share_one_registered_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the manifest, Code Mode, and direct-tool escape hatch aligned."""
    manifest_path = Path(__file__).parents[1] / "docs" / "api-parity-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {
        operation["canonical_tool"]: operation["classification"]
        for operation in manifest["operations"]
        if operation["implementation_status"] == "implemented"
    }

    registered = {name for name in server.mcp._tool_manager._tools if name.startswith("api_")}
    assert registered == set(expected)

    catalog = {
        entry["name"]: entry["namespace"]
        for entry in build_spec(server.mcp, mutations_enabled=True)
        if entry["name"].startswith("api_")
    }
    assert catalog == expected

    read_only_catalog = {
        entry["name"]
        for entry in build_spec(server.mcp, mutations_enabled=False)
        if entry["name"].startswith("api_")
    }
    assert read_only_catalog == {name for name, kind in expected.items() if kind == "read"}

    stubs = generate_stubs(server.mcp, mutations_enabled=True)
    read_stubs, write_stubs = stubs.split("class WriteNamespace:", maxsplit=1)
    for name, namespace in expected.items():
        expected_stubs = read_stubs if namespace == "read" else write_stubs
        other_stubs = write_stubs if namespace == "read" else read_stubs
        assert f"async def {name}(" in expected_stubs
        assert f"async def {name}(" not in other_stubs

    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=False)),
    )
    direct_names = {tool.name for tool in await server.mcp.list_tools()}
    assert direct_names & registered == registered

    monkeypatch.setattr(
        server,
        "ynab_resources",
        SimpleNamespace(preferences=Preferences(code_mode_replace_tools=True)),
    )
    compressed_names = {tool.name for tool in await server.mcp.list_tools()}
    assert compressed_names.isdisjoint(registered)


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
