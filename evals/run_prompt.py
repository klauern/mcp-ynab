#!/usr/bin/env python
"""Run one ad-hoc prompt against the development mcp-ynab server.

Drives Claude over the real MCP stdio transport against the *dev* server in the
current venv (Code Mode, read-only), then prints the Code Mode tool calls
(including the Python sent to ``execute``) and the final answer. Useful for
poking at the server during development without writing a permanent eval.

Read-only by construction: the server's ``code_mode_mutations_enabled`` defaults
to False, so ``ynab.write.*`` is blocked and no change can reach YNAB.

Usage (from the repo root, with ANTHROPIC_API_KEY + YNAB_API_KEY in env or .env)::

    uv run python evals/run_prompt.py "How much did I spend on dining last month?"
    uv run python evals/run_prompt.py --model claude-opus-4-8 "Which categories are over budget?"

To point at a different build instead of the dev tree, set EVAL_MCP_COMMAND /
EVAL_MCP_ARGS (e.g. EVAL_MCP_COMMAND=mcp-ynab EVAL_MCP_ARGS='[]' for the
installed tool).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "tests" / "integration"))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except Exception:  # dotenv is optional for this script
    pass

import _llm_eval_harness as harness  # noqa: E402


def _is_auth_error(exc: BaseException) -> bool:
    """True if exc is (or a group containing) an Anthropic auth error.

    The error surfaces wrapped in anyio ExceptionGroups from the MCP task group,
    so unwrap groups recursively.
    """
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_auth_error(sub) for sub in exc.exceptions)
    return False


_AUTH_HELP = (
    "Anthropic auth failed (401): the key in use is not a valid Console API key.\n"
    "If your shell's ANTHROPIC_API_KEY is a Claude Code OAuth token (sk-ant-oat...),\n"
    "it won't work against the Messages API. Set a real Console key for the eval:\n"
    "    export EVAL_ANTHROPIC_API_KEY=sk-ant-api...\n"
    "(EVAL_ANTHROPIC_API_KEY takes precedence and leaves your Claude Code setup alone.)"
)


async def _run(prompt: str, model: str) -> int:
    problems = []
    if not os.getenv("YNAB_API_KEY"):
        problems.append("YNAB_API_KEY")
    # agent-sdk authenticates via the Claude subscription (claude CLI login),
    # so it needs no Anthropic API key; messages-api does.
    if harness.current_driver() == "messages-api" and not harness.eval_api_key():
        problems.append("ANTHROPIC_API_KEY (or EVAL_ANTHROPIC_API_KEY)")
    if problems:
        print(
            f"Missing required credential(s): {', '.join(problems)}. "
            "Set them in your shell or a .env file at the repo root.",
            file=sys.stderr,
        )
        return 2

    try:
        run = await harness.drive_prompt(prompt, model=model)
    except BaseException as exc:  # noqa: BLE001 - re-raised unless it's an auth error
        if _is_auth_error(exc):
            print(_AUTH_HELP, file=sys.stderr)
            return 2
        raise

    print(f"=== model: {model} | tool calls: {len(run.tool_calls)} ===")
    for i, call in enumerate(run.tool_calls, start=1):
        code = call.arguments.get("code")
        print(f"\n[{i}] {call.name}")
        if code:
            print("    " + str(code).replace("\n", "\n    "))
        else:
            print(f"    args: {call.arguments}")
    if run.stopped_early:
        print("\n(!) stopped early — hit the max-iteration cap before a final answer")
    print("\n=== final answer ===")
    print(run.final_text or "(no final answer produced)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one prompt against the dev mcp-ynab server (read-only Code Mode)."
    )
    parser.add_argument("prompt", help="The natural-language prompt to send to Claude.")
    parser.add_argument(
        "--model",
        default=os.getenv("EVAL_MODEL", harness.DEFAULT_EVAL_MODEL),
        help="Anthropic model id (default: %(default)s).",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.prompt, args.model)))


if __name__ == "__main__":
    main()
