from __future__ import annotations
from pathlib import Path

import pytest

import mcp_ynab.server as server


def test_format_accounts_output_groups_and_summary() -> None:
    formatted = server._format_accounts_output(
        [
            {
                "id": "a1",
                "name": "Checking",
                "type": "checking",
                "balance": 250_000,
                "closed": False,
                "deleted": False,
            },
            {
                "id": "a2",
                "name": "Credit",
                "type": "creditCard",
                "balance": -100_000,
                "closed": False,
                "deleted": False,
            },
        ]
    )

    assert formatted["summary"]["total_assets"] == "$250.00"
    assert formatted["summary"]["total_liabilities"] == "$100.00"
    assert formatted["summary"]["net_worth"] == "$150.00"


def test_build_markdown_table_empty_rows() -> None:
    table = server._build_markdown_table([], ["A", "B"])
    assert "| A" in table
    assert "| B" in table


@pytest.mark.asyncio
async def test_tools_register_categorize_transaction_not_private_helper() -> None:
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}

    assert "categorize_transaction" in names
    assert "_find_transaction_by_id" not in names


@pytest.mark.asyncio
async def test_tools_include_annotations_for_read_only_and_mutating() -> None:
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert tools["get_budgets"].annotations.readOnlyHint is True
    assert tools["create_transaction"].annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_get_client_reads_api_key_from_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YNAB_API_KEY", raising=False)

    with pytest.raises(ValueError, match="YNAB_API_KEY"):
        await server._get_client()


@pytest.mark.asyncio
async def test_resource_and_templates_are_exposed() -> None:
    resources = await server.mcp.list_resources()
    templates = await server.mcp.list_resource_templates()

    assert any(str(r.uri) == "ynab://preferences/budget_id" for r in resources)
    assert any(t.uriTemplate == "ynab://categories/{budget_id}" for t in templates)


def test_find_transaction_by_id_variants() -> None:
    class Txn:
        def __init__(self) -> None:
            self.id = "id-1"
            self.import_id = "YNAB:-100:2026-01-01:1"
            self.transfer_transaction_id = "transfer-1"
            self.matched_transaction_id = "matched-1"

    txn = Txn()
    txns = [txn]

    assert server._find_transaction_by_id(txns, "id-1", "id") is txn
    assert server._find_transaction_by_id(txns, "YNAB:-100:2026-01-01:1", "import_id") is txn
    assert server._find_transaction_by_id(txns, "transfer-1", "transfer_transaction_id") is txn
    assert server._find_transaction_by_id(txns, "matched-1", "matched_transaction_id") is txn
    assert server._find_transaction_by_id(txns, "missing", "id") is None


def test_ynab_resources_can_use_custom_config_dir(tmp_path: Path) -> None:
    resources = server.YNABResources(config_dir=tmp_path)
    resources.set_preferred_budget_id("budget-123")

    reloaded = server.YNABResources(config_dir=tmp_path)
    assert reloaded.get_preferred_budget_id() == "budget-123"


@pytest.mark.asyncio
async def test_categorize_transaction_uses_direct_lookup_for_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyTransaction:
        id = "tx-1"
        account_id = "acct-1"
        var_date = "2026-02-01"
        amount = -1000
        payee_id = "payee-1"
        payee_name = "Coffee Shop"
        memo = "memo"
        cleared = "cleared"
        approved = True
        flag_color = "red"
        subtransactions = []

    class DummyApi:
        def __init__(self) -> None:
            self.by_id_called = False
            self.scan_called = False
            self.updated = False

        def get_transaction_by_id(self, budget_id: str, transaction_id: str):
            self.by_id_called = True
            return type(
                "Resp",
                (),
                {"data": type("Data", (), {"transaction": DummyTransaction()})()},
            )()

        def get_transactions(self, budget_id: str, since_date=None):
            self.scan_called = True
            return type("Resp", (), {"data": type("Data", (), {"transactions": []})()})()

        def update_transaction(self, budget_id: str, transaction_id: str, data):
            self.updated = True

    class DummyCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    api = DummyApi()

    async def fake_get_ynab_client():
        return DummyCtx()

    monkeypatch.setattr(server, "get_ynab_client", fake_get_ynab_client)
    monkeypatch.setattr(server, "TransactionsApi", lambda client: api)
    monkeypatch.setattr(server, "ExistingTransaction", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        server, "PutTransactionWrapper", lambda transaction: {"transaction": transaction}
    )

    result = await server.categorize_transaction("budget-1", "tx-1", "cat-1", id_type="id")

    assert "categorized as cat-1" in result
    assert api.by_id_called is True
    assert api.scan_called is False
    assert api.updated is True


@pytest.mark.asyncio
async def test_categorize_transaction_preserves_existing_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyTransaction:
        id = "tx-1"
        account_id = "acct-1"
        var_date = "2026-02-01"
        amount = -1000
        payee_id = "payee-1"
        payee_name = "Coffee Shop"
        memo = "original memo"
        cleared = "reconciled"
        approved = True
        flag_color = "blue"
        subtransactions = [{"amount": -500, "category_id": "cat-old"}]

    class DummyApi:
        def get_transaction_by_id(self, budget_id: str, transaction_id: str):
            return type(
                "Resp",
                (),
                {"data": type("Data", (), {"transaction": DummyTransaction()})()},
            )()

        def update_transaction(self, budget_id: str, transaction_id: str, data):
            self.data = data

    class DummyCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    api = DummyApi()
    captured: dict = {}

    async def fake_get_ynab_client():
        return DummyCtx()

    def fake_existing_transaction(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(server, "get_ynab_client", fake_get_ynab_client)
    monkeypatch.setattr(server, "TransactionsApi", lambda client: api)
    monkeypatch.setattr(server, "ExistingTransaction", fake_existing_transaction)
    monkeypatch.setattr(
        server, "PutTransactionWrapper", lambda transaction: {"transaction": transaction}
    )

    await server.categorize_transaction("budget-1", "tx-1", "cat-new", id_type="id")

    assert captured["account_id"] == "acct-1"
    assert captured["category_id"] == "cat-new"
    assert captured["memo"] == "original memo"
    assert captured["cleared"] == "reconciled"
    assert captured["approved"] is True
    assert captured["flag_color"] == "blue"
