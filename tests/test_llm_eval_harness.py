"""Unit tests for the pure helpers in the LLM-eval harness.

These run in the default suite — no API keys, no network. They cover the
schema/parse plumbing that the (gated, costly) integration evals rely on, so a
regression in tool conversion or verdict parsing is caught cheaply.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from tests.integration import _llm_eval_harness as harness
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


# ---------------------------------------------------------------------------
# drive_prompt loop logic — fake Anthropic client + fake MCP session, no keys.
# Verifies tool-call capture, result feedback shape, and termination. Does NOT
# cover real-API acceptance of round-tripped content blocks (SDK-version
# sensitive); that needs a live `task test:eval` run.
# ---------------------------------------------------------------------------


class _Block:
    """Minimal stand-in for an Anthropic content block."""

    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)

    def model_dump(self) -> dict:
        return dict(self.__dict__)


class _Resp:
    def __init__(self, stop_reason: str, content: list) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _FakeMessages:
    def __init__(self, responses: list, calls: list) -> None:
        self._responses = list(responses)
        self._calls = calls

    async def create(self, **kwargs: object):
        self._calls.append(kwargs)
        return self._responses.pop(0)


class _FakeAnthropic:
    def __init__(self, responses: list, calls: list) -> None:
        self.messages = _FakeMessages(responses, calls)


def _make_fake_session_cls(tool_log: list):
    class _FakeSession:
        def __init__(self, _read: object, _write: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a: object) -> bool:
            return False

        async def initialize(self) -> None:
            pass

        async def list_tools(self):
            tool = SimpleNamespace(
                name="execute",
                description="run code",
                inputSchema={"type": "object", "properties": {"code": {"type": "string"}}},
            )
            return SimpleNamespace(tools=[tool])

        async def call_tool(self, name: str, arguments: dict):
            tool_log.append((name, arguments))
            return SimpleNamespace(content=[_Block(type="text", text="budgets: [Personal]")])

    return _FakeSession


def _make_fake_stdio_client():
    @contextlib.asynccontextmanager
    async def _fake(_params: object):
        yield ("read-stream", "write-stream")

    return _fake


@pytest.mark.asyncio
async def test_drive_prompt_runs_tool_loop_and_returns_final_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anthropic
    import mcp
    import mcp.client.stdio

    create_calls: list = []
    responses = [
        _Resp(
            "tool_use",
            [
                _Block(
                    type="tool_use",
                    name="execute",
                    input={"code": "return await ynab.read.get_budgets()"},
                    id="tu1",
                )
            ],
        ),
        _Resp("end_turn", [_Block(type="text", text="You have 1 budget: Personal.")]),
    ]
    tool_log: list = []

    monkeypatch.setattr(
        anthropic, "AsyncAnthropic", lambda **_kw: _FakeAnthropic(responses, create_calls)
    )
    monkeypatch.setattr(mcp, "ClientSession", _make_fake_session_cls(tool_log))
    monkeypatch.setattr(mcp.client.stdio, "stdio_client", _make_fake_stdio_client())

    run = await harness.drive_prompt("How many budgets do I have?", model="test-model")

    # Loop terminated on end_turn, captured the tool call, returned the answer.
    assert run.stopped_early is False
    assert run.tool_names == ["execute"]
    assert run.tool_calls[0].arguments == {"code": "return await ynab.read.get_budgets()"}
    assert run.final_text == "You have 1 budget: Personal."

    # The tool was dispatched to the MCP session with the model's args.
    assert tool_log == [("execute", {"code": "return await ynab.read.get_budgets()"})]

    # First call carried the Code Mode system prompt; the second fed the tool
    # result back as a tool_result tied to the originating tool_use id.
    assert create_calls[0]["system"] == harness.CODE_MODE_SYSTEM
    tool_results = create_calls[1]["messages"][-1]["content"]
    assert tool_results[0]["type"] == "tool_result"
    assert tool_results[0]["tool_use_id"] == "tu1"
    assert "budgets: [Personal]" in tool_results[0]["content"]


@pytest.mark.asyncio
async def test_drive_prompt_marks_stopped_early_when_loop_never_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anthropic
    import mcp
    import mcp.client.stdio

    # Always returns tool_use → loop should hit max_iterations and flag it.
    def _always_tool_use(**_kwargs: object):
        return _Resp(
            "tool_use",
            [_Block(type="tool_use", name="execute", input={"code": "pass"}, id="t")],
        )

    class _Loop:
        async def create(self, **kwargs: object):
            return _always_tool_use(**kwargs)

    monkeypatch.setattr(
        anthropic, "AsyncAnthropic", lambda **_kw: SimpleNamespace(messages=_Loop())
    )
    monkeypatch.setattr(mcp, "ClientSession", _make_fake_session_cls([]))
    monkeypatch.setattr(mcp.client.stdio, "stdio_client", _make_fake_stdio_client())

    run = await harness.drive_prompt("loop forever", model="test-model", max_iterations=3)

    assert run.stopped_early is True
    assert len(run.tool_calls) == 3
