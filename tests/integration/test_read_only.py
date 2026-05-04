"""Integration tests for read-only MCP tools against the real YNAB API.

Each test calls a tool function from `mcp_ynab.server` directly so the test
exercises the same code path the MCP transport would. Assertions are kept
lenient (shape checks, not exact content) since the tests run against the
user's real account, which the test does not control.
"""

from __future__ import annotations

import pytest

from mcp_ynab import server

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_get_budgets_returns_at_least_one_budget() -> None:
    result = await server.get_budgets()
    assert result.startswith("# YNAB Budgets")
    assert "_No budgets found._" not in result


@pytest.mark.asyncio
async def test_get_accounts_returns_summary_section(
    integration_first_budget_id: str,
) -> None:
    result = await server.get_accounts(integration_first_budget_id)
    assert "# YNAB Account Summary" in result
    assert "Total Assets:**" in result
    assert "Net Worth:**" in result


@pytest.mark.asyncio
async def test_get_account_balance_returns_a_float(
    integration_first_account_id: str,
) -> None:
    balance = await server.get_account_balance(integration_first_account_id)
    assert isinstance(balance, float)


@pytest.mark.asyncio
async def test_get_transactions_returns_markdown(
    integration_first_budget_id: str,
    integration_first_account_id: str,
) -> None:
    result = await server.get_transactions(
        integration_first_budget_id, integration_first_account_id
    )
    assert "# Recent Transactions" in result


@pytest.mark.asyncio
async def test_get_transactions_honors_explicit_since_date(
    integration_first_budget_id: str,
    integration_first_account_id: str,
) -> None:
    from datetime import date, timedelta

    week_ago = date.today() - timedelta(days=7)
    result = await server.get_transactions(
        integration_first_budget_id,
        integration_first_account_id,
        since_date=week_ago,
    )
    assert "# Recent Transactions" in result


@pytest.mark.asyncio
async def test_get_transactions_needing_attention_runs_for_default_filter(
    integration_first_budget_id: str,
) -> None:
    result = await server.get_transactions_needing_attention(integration_first_budget_id)
    assert "# Transactions Needing Attention" in result


@pytest.mark.asyncio
async def test_get_categories_renders_header(
    integration_first_budget_id: str,
) -> None:
    """The tool must always at least render the markdown header.

    Whether category groups appear depends on the user's budget — empty groups
    are intentionally skipped by the tool, so we don't assert any `##` headers.
    """
    result = await server.get_categories(integration_first_budget_id)
    assert "# YNAB Categories" in result
