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


async def _run(prompt: str, model: str) -> int:
    missing = [k for k in ("ANTHROPIC_API_KEY", "YNAB_API_KEY") if not os.getenv(k)]
    if missing:
        print(
            f"Missing required env var(s): {', '.join(missing)}. "
            "Set them in your shell or a .env file at the repo root.",
            file=sys.stderr,
        )
        return 2

    run = await harness.drive_prompt(prompt, model=model)

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
