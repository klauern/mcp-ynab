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
