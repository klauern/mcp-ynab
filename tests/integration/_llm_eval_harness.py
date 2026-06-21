"""Harness for LLM-driven YNAB evals over the real MCP stdio transport.

The flow per eval:

1. Launch the ``mcp-ynab`` server as a subprocess and connect with the MCP
   stdio client (so the *real* server, tool schemas, and transport are
   exercised — not in-process shims).
2. Convert the server's advertised tools into Anthropic tool schemas and let
   Claude drive a tool-use loop against the live session.
3. Capture the tool calls Claude made and its final natural-language answer.
4. Score the answer against the eval's ``expected_output`` with an LLM judge.

``anthropic`` and ``mcp`` clients are imported lazily inside the async
functions so this module (and its pure helpers) import without API keys — the
unit tests in ``tests/test_llm_eval_harness.py`` exercise the pure helpers with
fakes, while the real run lives behind the ``eval`` marker.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

# Default models are overridable via env so the suite can pin cheaper/newer
# models without code changes.
DEFAULT_EVAL_MODEL = "claude-sonnet-4-6"
DEFAULT_JUDGE_MODEL = "claude-haiku-4-5-20251001"

# Two ways to drive the eval, selected by EVAL_DRIVER:
#   "messages-api" (default) — the anthropic Messages API; bills API tokens.
#   "agent-sdk"             — the Claude Agent SDK (the Claude Code engine),
#                             authenticated by your Claude *subscription* via the
#                             logged-in `claude` CLI (or CLAUDE_CODE_OAUTH_TOKEN).
#                             Draws against your subscription's usage, not an API
#                             balance.
DEFAULT_DRIVER = "messages-api"


def current_driver() -> str:
    """The selected eval driver (``messages-api`` or ``agent-sdk``)."""
    return os.getenv("EVAL_DRIVER", DEFAULT_DRIVER)


# Orient the model to the Code Mode surface so the eval measures whether the
# tools work — not whether the model can rediscover the Code Mode convention
# cold. Mirrors what the ynab://code-mode/examples + stubs resources convey.
CODE_MODE_SYSTEM = (
    "You operate a YNAB budget through a Code Mode interface exposed as two tools:\n"
    "- `search`: find available YNAB operations when you're unsure what exists.\n"
    "- `execute`: run a Python snippet. The snippet is the body of an async function "
    "with a `ynab` object in scope. Call read operations as "
    "`await ynab.read.<operation>(...)` and `return` the value you want back.\n\n"
    "Resolve ids (budget, category, account) by reading them rather than guessing. "
    "The environment is read-only: `ynab.write.*` is unavailable, so for any change "
    "request, gather the relevant data and describe what you would do — do not attempt "
    "to apply it. When done, give the user a clear final answer in plain language."
)

# Orient the model for direct-tools mode: all YNAB tools are visible as flat MCP
# tools. Same read-only + describe-only posture as Code Mode — mutations are
# blocked at the structural-check layer so no real writes can sneak through.
DIRECT_TOOLS_SYSTEM = (
    "You operate a YNAB budget through direct MCP tools. Use the available read tools "
    "to answer questions about the budget. Resolve ids (budget, category, account) by "
    "reading them rather than guessing. The environment is read-only: for any change "
    "request, gather the relevant data and describe what you would do — do not attempt "
    "to apply it. When done, give the user a clear final answer in plain language."
)

# YNAB-mutating tools. Dry-run/read evals must never call these — the structural
# gate in test_llm_evals.py enforces it so a "dry run" prompt can't quietly
# change the live budget. Local-state tools (cache_*, set_preferred_budget_id,
# set_preference) are intentionally excluded; they don't touch YNAB.
YNAB_WRITE_TOOLS = frozenset(
    {
        "create_transaction",
        "update_transaction",
        "delete_transaction",
        "bulk_categorize",
        "categorize_transaction",
        "approve_transactions",
        "import_transactions",
        "create_scheduled_transaction",
        "update_category",
        "assign_money",
        "move_money",
        "merge_payees",
        "rename_payee",
        "set_api_key",
        "clear_api_key",
    }
)


@dataclass
class ToolCall:
    """One tool invocation Claude made during an eval run."""

    name: str
    arguments: dict[str, Any]


@dataclass
class EvalRun:
    """Outcome of driving one eval prompt to completion."""

    final_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stopped_early: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    duration_ms: float = 0.0

    @property
    def tool_names(self) -> list[str]:
        """Names of every tool Claude called, in order."""
        return [tc.name for tc in self.tool_calls]

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


def mcp_tools_to_anthropic(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    """Convert MCP ``Tool`` objects to Anthropic tool-schema dicts."""
    schemas: list[dict[str, Any]] = []
    for tool in mcp_tools:
        schemas.append(
            {
                "name": tool.name,
                "description": (getattr(tool, "description", None) or "")[:1024],
                "input_schema": getattr(tool, "inputSchema", None)
                or {"type": "object", "properties": {}},
            }
        )
    return schemas


def tool_result_to_text(result: Any) -> str:
    """Flatten an MCP ``call_tool`` result's content blocks into text."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


def _verdict_tool_schema() -> dict[str, Any]:
    return {
        "name": "record_verdict",
        "description": "Record whether the assistant's answer satisfies the expected output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "passed": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": ["passed", "reason"],
        },
    }


def parse_verdict(content_blocks: list[Any]) -> tuple[bool, str]:
    """Extract (passed, reason) from a judge response's content blocks."""
    for block in content_blocks:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_verdict":
            data = block.input or {}
            return bool(data.get("passed")), str(data.get("reason", ""))
    return False, "judge did not return a verdict"


def eval_api_key() -> str | None:
    """The Anthropic key to drive evals with.

    Prefers EVAL_ANTHROPIC_API_KEY so the eval can use a real Console API key
    without colliding with an ANTHROPIC_API_KEY that the shell may have set to a
    Claude Code OAuth token (which the Messages API rejects with 401).
    """
    return os.getenv("EVAL_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY")


def _anthropic_client() -> Any:
    import anthropic

    return anthropic.AsyncAnthropic(api_key=eval_api_key())


def _server_params(*, server_env_overrides: dict[str, str] | None = None) -> Any:
    """Build StdioServerParameters for launching the mcp-ynab subprocess.

    Defaults to ``<this interpreter> -m mcp_ynab`` so the eval always exercises
    the *development* server in the current venv (the editable install on the
    checked-out branch) rather than a globally installed mcp-ynab. Override via
    EVAL_MCP_COMMAND / EVAL_MCP_ARGS (JSON list) to point at a different build —
    e.g. EVAL_MCP_COMMAND=mcp-ynab EVAL_MCP_ARGS='[]' for the installed tool.
    ``server_env_overrides`` are layered on top of the current environment so
    callers can inject ``MCP_YNAB_*`` preference vars without a config file.
    """
    from mcp import StdioServerParameters

    command = os.getenv("EVAL_MCP_COMMAND", sys.executable)
    args = json.loads(os.getenv("EVAL_MCP_ARGS", '["-m", "mcp_ynab"]'))
    env = dict(os.environ)
    if server_env_overrides:
        env.update(server_env_overrides)
    return StdioServerParameters(command=command, args=args, env=env)


async def drive_prompt(
    prompt: str,
    *,
    model: str,
    max_iterations: int = 8,
    system_prompt: str | None = None,
    server_env_overrides: dict[str, str] | None = None,
    blocked_tool_names: frozenset[str] | None = None,
) -> EvalRun:
    """Drive ``prompt`` to a final answer using the live mcp-ynab tools.

    Dispatches to the configured backend (EVAL_DRIVER). Both return an EvalRun
    with the same shape so the structural checks and judge work identically.

    ``system_prompt`` overrides the default CODE_MODE_SYSTEM so the dual-config
    runner can orient the model appropriately for each surface.
    ``server_env_overrides`` are forwarded to the subprocess so the server
    launches with a specific ``MCP_YNAB_*`` configuration.
    ``blocked_tool_names`` removes unsafe tools from the model-visible schema and
    fail-closes if a blocked tool is somehow requested.
    """
    driver = current_driver()
    if driver == "agent-sdk":
        return await _drive_via_agent_sdk(prompt, model=model)
    if driver == "messages-api":
        return await _drive_via_messages_api(
            prompt,
            model=model,
            max_iterations=max_iterations,
            system_prompt=system_prompt,
            server_env_overrides=server_env_overrides,
            blocked_tool_names=blocked_tool_names,
        )
    raise ValueError(f"Unknown EVAL_DRIVER {driver!r} (use 'messages-api' or 'agent-sdk').")


async def _drive_via_messages_api(
    prompt: str,
    *,
    model: str,
    max_iterations: int = 8,
    system_prompt: str | None = None,
    server_env_overrides: dict[str, str] | None = None,
    blocked_tool_names: frozenset[str] | None = None,
) -> EvalRun:
    """Drive ``prompt`` with the anthropic Messages API (bills API tokens)."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    client = _anthropic_client()
    run = EvalRun()
    effective_system = system_prompt if system_prompt is not None else CODE_MODE_SYSTEM

    t0 = time.monotonic()
    async with stdio_client(_server_params(server_env_overrides=server_env_overrides)) as (
        read,
        write,
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            if blocked_tool_names:
                tools = [tool for tool in tools if tool.name not in blocked_tool_names]
            anthropic_tools = mcp_tools_to_anthropic(tools)

            messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
            for _ in range(max_iterations):
                response = await client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=effective_system,
                    tools=anthropic_tools,
                    messages=messages,
                )
                if hasattr(response, "usage") and response.usage:
                    run.total_input_tokens += getattr(response.usage, "input_tokens", 0)
                    run.total_output_tokens += getattr(response.usage, "output_tokens", 0)
                if response.stop_reason != "tool_use":
                    run.final_text = "".join(b.text for b in response.content if b.type == "text")
                    run.duration_ms = (time.monotonic() - t0) * 1000
                    return run

                messages.append(
                    {"role": "assistant", "content": [b.model_dump() for b in response.content]}
                )
                tool_results: list[dict[str, Any]] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    run.tool_calls.append(ToolCall(block.name, dict(block.input)))
                    if blocked_tool_names and block.name in blocked_tool_names:
                        content = f"ERROR calling {block.name}: tool is disabled for this eval run"
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": content,
                            }
                        )
                        continue
                    try:
                        result = await session.call_tool(block.name, dict(block.input))
                        content = tool_result_to_text(result) or "(no content)"
                    except Exception as exc:  # surface tool errors back to the model
                        content = f"ERROR calling {block.name}: {exc}"
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": content,
                        }
                    )
                messages.append({"role": "user", "content": tool_results})

    run.stopped_early = True
    run.duration_ms = (time.monotonic() - t0) * 1000
    return run


def _normalize_tool_name(name: str) -> str:
    """Strip the ``mcp__<server>__`` prefix the Agent SDK adds to MCP tools.

    So ``mcp__ynab__execute`` becomes ``execute`` and the structural checks
    (read-tool engagement, write-tool gate) match across both drivers.
    """
    return name.split("__")[-1] if name.startswith("mcp__") else name


def _subscription_env() -> dict[str, str]:
    """Env overrides that force the Agent SDK onto Claude subscription auth.

    Blanks out any API-key / Bedrock vars that would win over the logged-in
    `claude` CLI credentials; honors an explicit EVAL_CLAUDE_CODE_OAUTH_TOKEN
    (from `claude setup-token`) if set, else falls back to the existing login.
    """
    env = {
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "CLAUDE_CODE_USE_BEDROCK": "false",
    }
    token = os.getenv("EVAL_CLAUDE_CODE_OAUTH_TOKEN") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    return env


def agent_sdk_options(model: str) -> Any:
    """Build ClaudeAgentOptions for the subscription-auth Agent SDK driver."""
    from claude_agent_sdk import ClaudeAgentOptions

    command = os.getenv("EVAL_MCP_COMMAND", sys.executable)
    args = json.loads(os.getenv("EVAL_MCP_ARGS", '["-m", "mcp_ynab"]'))

    return ClaudeAgentOptions(
        model=model,
        mcp_servers={
            "ynab": {
                "type": "stdio",
                "command": command,
                "args": args,
                "env": dict(os.environ),
            }
        },
        # Only the Code Mode tools may run; dontAsk denies anything unlisted
        # rather than prompting (this is a non-interactive eval).
        allowed_tools=["mcp__ynab__execute", "mcp__ynab__search"],
        disallowed_tools=["Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch"],
        permission_mode="dontAsk",
        # Isolate from the user's global Claude Code config (other MCP servers,
        # hooks, skills, CLAUDE.md) so the eval is reproducible.
        setting_sources=[],
        strict_mcp_config=True,
        max_turns=int(os.getenv("EVAL_MAX_TURNS", "12")),
        env=_subscription_env(),
    )


async def _drive_via_agent_sdk(prompt: str, *, model: str) -> EvalRun:
    """Drive ``prompt`` with the Claude Agent SDK (subscription auth)."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeSDKClient,
        TextBlock,
        ToolUseBlock,
    )

    run = EvalRun()
    async with ClaudeSDKClient(options=agent_sdk_options(model)) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if not isinstance(message, AssistantMessage):
                continue
            message_text: list[str] = []
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    run.tool_calls.append(
                        ToolCall(_normalize_tool_name(block.name), dict(block.input or {}))
                    )
                elif isinstance(block, TextBlock) and block.text:
                    message_text.append(block.text)
            joined = "".join(message_text).strip()
            if joined:
                # Full text of the latest assistant message wins (don't truncate
                # a multi-block answer to its last fragment).
                run.final_text = joined
    return run


_JUDGE_SYSTEM = (
    "You are a strict but fair evaluator of an AI budgeting assistant. Given the user's "
    "task, a description of the expected output, and the assistant's final answer, decide "
    "whether the answer satisfies the expectation. Judge substance, not exact wording."
)


def _judge_user_prompt(prompt: str, expected_output: str, final_text: str) -> str:
    return (
        f"User task:\n{prompt}\n\n"
        f"Expected output:\n{expected_output}\n\n"
        f"Assistant final answer:\n{final_text or '(empty)'}\n\n"
    )


async def judge_answer(
    prompt: str, expected_output: str, final_text: str, *, model: str
) -> tuple[bool, str]:
    """Use an LLM judge to score ``final_text`` against ``expected_output``.

    Routes through the same driver as the run so an agent-sdk eval judges on the
    subscription too (rather than falling back to the billed Messages API).
    """
    if current_driver() == "agent-sdk":
        return await _judge_via_agent_sdk(prompt, expected_output, final_text, model=model)

    client = _anthropic_client()
    user = _judge_user_prompt(prompt, expected_output, final_text) + (
        "Call record_verdict with whether the answer satisfies the expected output."
    )
    response = await client.messages.create(
        model=model,
        max_tokens=512,
        system=_JUDGE_SYSTEM + " Always respond by calling record_verdict.",
        tools=[_verdict_tool_schema()],
        tool_choice={"type": "tool", "name": "record_verdict"},
        messages=[{"role": "user", "content": user}],
    )
    return parse_verdict(response.content)


async def _judge_via_agent_sdk(
    prompt: str, expected_output: str, final_text: str, *, model: str
) -> tuple[bool, str]:
    """LLM judge over the Agent SDK (subscription auth), no tools.

    Asks for a ``VERDICT: pass|fail`` first line plus a one-line reason, and
    parses that — structured tool-calling isn't needed for a yes/no verdict.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        TextBlock,
    )

    options = ClaudeAgentOptions(
        model=model,
        allowed_tools=[],
        disallowed_tools=["Bash", "Read", "Write", "Edit", "WebFetch", "WebSearch"],
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        max_turns=1,
        env=_subscription_env(),
    )
    instruction = (
        _JUDGE_SYSTEM
        + "\n\n"
        + _judge_user_prompt(prompt, expected_output, final_text)
        + "Reply with a first line exactly 'VERDICT: pass' or 'VERDICT: fail', then a "
        "one-sentence reason on the next line. Do not use any tools."
    )
    text = ""
    async with ClaudeSDKClient(options=options) as client:
        await client.query(instruction)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        text += block.text
    lowered = text.lower()
    passed = "verdict: pass" in lowered or lowered.lstrip().startswith("pass")
    return passed, text.strip()[:500] or "judge returned no text"
