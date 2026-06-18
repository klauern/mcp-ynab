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
from dataclasses import dataclass, field
from typing import Any

# Default models are overridable via env so the suite can pin cheaper/newer
# models without code changes.
DEFAULT_EVAL_MODEL = "claude-sonnet-4-6"
DEFAULT_JUDGE_MODEL = "claude-haiku-4-5-20251001"

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

    @property
    def tool_names(self) -> list[str]:
        """Names of every tool Claude called, in order."""
        return [tc.name for tc in self.tool_calls]


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


def _server_params() -> Any:
    """Build StdioServerParameters for launching the mcp-ynab subprocess.

    Overridable via EVAL_MCP_COMMAND / EVAL_MCP_ARGS (JSON list) for unusual
    environments; defaults to ``uv run mcp-ynab``.
    """
    from mcp import StdioServerParameters

    command = os.getenv("EVAL_MCP_COMMAND", "uv")
    args = json.loads(os.getenv("EVAL_MCP_ARGS", '["run", "mcp-ynab"]'))
    return StdioServerParameters(command=command, args=args, env=dict(os.environ))


async def drive_prompt(prompt: str, *, model: str, max_iterations: int = 8) -> EvalRun:
    """Drive ``prompt`` to a final answer using the live mcp-ynab tools."""
    import anthropic
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    client = anthropic.AsyncAnthropic()
    run = EvalRun()

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            anthropic_tools = mcp_tools_to_anthropic(tools)

            messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
            for _ in range(max_iterations):
                response = await client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=CODE_MODE_SYSTEM,
                    tools=anthropic_tools,
                    messages=messages,
                )
                if response.stop_reason != "tool_use":
                    run.final_text = "".join(b.text for b in response.content if b.type == "text")
                    return run

                messages.append(
                    {"role": "assistant", "content": [b.model_dump() for b in response.content]}
                )
                tool_results: list[dict[str, Any]] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    run.tool_calls.append(ToolCall(block.name, dict(block.input)))
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
    return run


async def judge_answer(
    prompt: str, expected_output: str, final_text: str, *, model: str
) -> tuple[bool, str]:
    """Use an LLM judge to score ``final_text`` against ``expected_output``."""
    import anthropic

    client = anthropic.AsyncAnthropic()
    system = (
        "You are a strict but fair evaluator of an AI budgeting assistant. Given the user's "
        "task, a description of the expected output, and the assistant's final answer, decide "
        "whether the answer satisfies the expectation. Judge substance, not exact wording. "
        "Always respond by calling record_verdict."
    )
    user = (
        f"User task:\n{prompt}\n\n"
        f"Expected output:\n{expected_output}\n\n"
        f"Assistant final answer:\n{final_text or '(empty)'}\n\n"
        "Call record_verdict with whether the answer satisfies the expected output."
    )
    response = await client.messages.create(
        model=model,
        max_tokens=512,
        system=system,
        tools=[_verdict_tool_schema()],
        tool_choice={"type": "tool", "name": "record_verdict"},
        messages=[{"role": "user", "content": user}],
    )
    return parse_verdict(response.content)
