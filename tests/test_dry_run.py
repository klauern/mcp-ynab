"""Tests for the eval-only mutation intent recorder."""

from __future__ import annotations

import json
import inspect
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from mcp_ynab.code_mode import run_code
from mcp_ynab.dry_run import INTENTS_PATH_ENV, install_dry_run_interceptor


class _CategorizeArgs(BaseModel):
    budget_id: str
    transaction_id: str
    category_id: str


def _dry_run_mcp(original: Any) -> SimpleNamespace:
    tool = SimpleNamespace(
        fn=original,
        fn_metadata=SimpleNamespace(arg_model=_CategorizeArgs),
        context_kwarg=None,
        annotations=SimpleNamespace(readOnlyHint=False),
    )
    return SimpleNamespace(_tool_manager=SimpleNamespace(_tools={"categorize_transaction": tool}))


@pytest.mark.asyncio
async def test_interceptor_records_validated_arguments_without_calling_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    intents_path = tmp_path / "intended_writes.json"
    monkeypatch.setenv(INTENTS_PATH_ENV, str(intents_path))

    async def real_write(budget_id: str, transaction_id: str, category_id: str) -> None:
        raise AssertionError("a real YNAB write must never be dispatched")

    mcp = _dry_run_mcp(real_write)
    install_dry_run_interceptor(mcp)
    assert inspect.signature(
        mcp._tool_manager._tools["categorize_transaction"].fn
    ) == inspect.signature(real_write)

    result = await mcp._tool_manager._tools["categorize_transaction"].fn(
        budget_id="budget-1", transaction_id="transaction-1", category_id="category-1"
    )

    assert result == {
        "dry_run": True,
        "tool": "categorize_transaction",
        "arguments": {
            "budget_id": "budget-1",
            "transaction_id": "transaction-1",
            "category_id": "category-1",
        },
    }
    assert json.loads(intents_path.read_text()) == [
        {"tool": "categorize_transaction", "arguments": result["arguments"]}
    ]


@pytest.mark.asyncio
async def test_interceptor_captures_code_mode_write_without_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    intents_path = tmp_path / "intended_writes.json"
    monkeypatch.setenv(INTENTS_PATH_ENV, str(intents_path))

    async def real_write(**_kwargs: Any) -> None:
        raise AssertionError("a real YNAB write must never be dispatched")

    mcp = _dry_run_mcp(real_write)
    install_dry_run_interceptor(mcp)

    result = await run_code(
        "return await ynab.write.categorize_transaction("
        "budget_id='budget-1', transaction_id='transaction-1', category_id='category-1')",
        mcp=mcp,
        mutations_enabled=True,
    )

    assert result.ok is True
    assert result.result["dry_run"] is True
    assert json.loads(intents_path.read_text())[0]["tool"] == "categorize_transaction"
