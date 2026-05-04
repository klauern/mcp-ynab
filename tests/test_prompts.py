"""Unit tests for the @mcp.prompt definitions in mcp_ynab.prompts."""

import pytest

from mcp_ynab.server import (
    categorize_recent,
    fund_sinking_categories,
    mcp,
    monthly_budget_check,
    move_money_interactive,
    spending_by_payee,
    weekly_review,
)


@pytest.mark.asyncio
async def test_all_six_prompts_register_with_fastmcp():
    """The server should expose exactly the 6 budgeting-workflow prompts."""
    prompts = await mcp.list_prompts()
    assert {p.name for p in prompts} == {
        "categorize_recent",
        "fund_sinking_categories",
        "monthly_budget_check",
        "move_money_interactive",
        "spending_by_payee",
        "weekly_review",
    }


@pytest.mark.asyncio
async def test_weekly_review_directs_through_attention_then_bulk_workflow():
    text = await weekly_review(budget_id="b1", days_back=10)
    assert "get_transactions_needing_attention" in text
    assert "days_back=10" in text
    assert "bulk_categorize" in text
    assert "approve_transactions" in text
    assert "b1" in text


@pytest.mark.asyncio
async def test_weekly_review_falls_back_to_preferences_resource_when_no_budget():
    text = await weekly_review()
    assert "ynab://preferences/budget_id" in text
    assert "days_back=7" in text


@pytest.mark.asyncio
async def test_monthly_budget_check_drives_get_month_then_move_money():
    text = await monthly_budget_check(budget_id="b1", month="2026-05-01")
    assert "get_month" in text
    assert "move_money" in text
    assert "Age of Money" in text
    assert "Ready to Assign" in text
    assert "2026-05-01" in text


@pytest.mark.asyncio
async def test_move_money_interactive_walks_source_destination_amount():
    text = await move_money_interactive(budget_id="b1")
    assert "source" in text.lower()
    assert "destination" in text.lower()
    assert "amount" in text.lower()
    assert "move_money" in text
    assert "ynab://categories/{budget_id}" in text


@pytest.mark.asyncio
async def test_fund_sinking_categories_targets_rule_2_and_assign_money():
    text = await fund_sinking_categories(budget_id="b1")
    assert "Rule 2" in text
    assert "assign_money" in text
    assert "get_month" in text
    assert "Ready to Assign" in text
    # Negative-RTA guardrail surfaces the cross-prompt reference
    assert "monthly_budget_check" in text


@pytest.mark.asyncio
async def test_categorize_recent_respects_auto_apply_flag():
    auto_text = await categorize_recent(budget_id="b1", auto_apply=True)
    assert "auto-apply" in auto_text
    assert "WAIT" not in auto_text

    manual_text = await categorize_recent(budget_id="b1", auto_apply=False)
    assert "WAIT" in manual_text
    assert "Do not auto-apply" in manual_text


@pytest.mark.asyncio
async def test_categorize_recent_scopes_by_account_when_provided():
    with_account = await categorize_recent(budget_id="b1", account_id="a1")
    assert "account_id='a1'" in with_account
    assert "account `a1`" in with_account

    without_account = await categorize_recent(budget_id="b1")
    assert "all accounts in the budget" in without_account
    assert "account_id=" not in without_account


@pytest.mark.asyncio
async def test_spending_by_payee_uses_period_and_calls_named_tool():
    text = await spending_by_payee(budget_id="b1", period="last_90d")
    assert "spending_by_payee" in text
    assert "period='last_90d'" in text
    assert "period-over-period" in text
