"""Unit tests for the pure helpers in the LLM-eval harness.

These run in the default suite — no API keys, no network. They cover the
schema/parse plumbing that the (gated, costly) integration evals rely on, so a
regression in tool conversion or verdict parsing is caught cheaply.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.integration._llm_eval_harness import (
    YNAB_WRITE_TOOLS,
    EvalRun,
    ToolCall,
    mcp_tools_to_anthropic,
    parse_verdict,
    tool_result_to_text,
)


def test_mcp_tools_to_anthropic_maps_fields() -> None:
    tools = [
        SimpleNamespace(
            name="get_budgets",
            description="List budgets",
            inputSchema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
    ]
    [schema] = mcp_tools_to_anthropic(tools)
    assert schema["name"] == "get_budgets"
    assert schema["description"] == "List budgets"
    assert schema["input_schema"]["properties"] == {"x": {"type": "string"}}


def test_mcp_tools_to_anthropic_defaults_missing_schema_and_description() -> None:
    tools = [SimpleNamespace(name="ping", description=None, inputSchema=None)]
    [schema] = mcp_tools_to_anthropic(tools)
    assert schema["description"] == ""
    assert schema["input_schema"] == {"type": "object", "properties": {}}


def test_tool_result_to_text_joins_text_blocks() -> None:
    result = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="line one"),
            SimpleNamespace(type="image"),  # no .text → skipped
            SimpleNamespace(type="text", text="line two"),
        ]
    )
    assert tool_result_to_text(result) == "line one\nline two"


def test_tool_result_to_text_handles_empty() -> None:
    assert tool_result_to_text(SimpleNamespace(content=None)) == ""


def test_parse_verdict_reads_tool_use_block() -> None:
    blocks = [
        SimpleNamespace(type="text", text="thinking"),
        SimpleNamespace(
            type="tool_use",
            name="record_verdict",
            input={"passed": True, "reason": "matches expectation"},
        ),
    ]
    passed, reason = parse_verdict(blocks)
    assert passed is True
    assert reason == "matches expectation"


def test_parse_verdict_defaults_to_fail_when_absent() -> None:
    passed, reason = parse_verdict([SimpleNamespace(type="text", text="no verdict here")])
    assert passed is False
    assert "did not return a verdict" in reason


def test_eval_run_tool_names_property() -> None:
    run = EvalRun(
        final_text="done",
        tool_calls=[
            ToolCall("get_categories", {}),
            ToolCall("get_transactions", {"budget_id": "b"}),
        ],
    )
    assert run.tool_names == ["get_categories", "get_transactions"]


def test_write_tools_cover_update_category() -> None:
    # The feature this PR ships is a YNAB write — the dry-run gate must catch it.
    assert "update_category" in YNAB_WRITE_TOOLS
    assert "create_transaction" in YNAB_WRITE_TOOLS
