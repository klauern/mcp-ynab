"""LLM-driven evals: Claude uses the live mcp-ynab tools to satisfy prompts.

Marked ``integration`` + ``eval`` (excluded from the default run). Always needs
``YNAB_API_KEY``; the ``messages-api`` driver additionally needs an Anthropic
key (``EVAL_ANTHROPIC_API_KEY`` or ``ANTHROPIC_API_KEY``) and costs API tokens,
while the ``agent-sdk`` driver authenticates via your Claude subscription. Runs
against whatever budget the YNAB key points at. Scope is read-only + dry-run —
the structural gate below fails the test if any eval performs a real YNAB
write, so it is safe to run against a live budget.

Run with::

    YNAB_API_KEY=... ANTHROPIC_API_KEY=... uv run pytest -m eval
    YNAB_API_KEY=... EVAL_DRIVER=agent-sdk uv run pytest -m eval   # subscription
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.integration._llm_eval_harness import (
    DEFAULT_EVAL_MODEL,
    DEFAULT_JUDGE_MODEL,
    YNAB_WRITE_TOOLS,
    current_driver,
    drive_prompt,
    eval_api_key,
    judge_answer,
)

pytestmark = [pytest.mark.integration, pytest.mark.eval]

_EVALS_PATH = Path(__file__).resolve().parents[2] / "evals" / "evals.json"


def _load_eval_cases() -> list[dict]:
    if not _EVALS_PATH.exists():
        return []
    return json.loads(_EVALS_PATH.read_text())["evals"]


_EVAL_CASES = _load_eval_cases()


def _require_eval_keys() -> None:
    missing = []
    if not os.getenv("YNAB_API_KEY"):
        missing.append("YNAB_API_KEY")
    # The messages-api driver needs an Anthropic API key; the agent-sdk driver
    # authenticates via the Claude subscription (logged-in `claude` CLI or
    # CLAUDE_CODE_OAUTH_TOKEN), which can't be cheaply pre-checked here.
    if current_driver() == "messages-api" and not eval_api_key():
        missing.append("ANTHROPIC_API_KEY (or EVAL_ANTHROPIC_API_KEY)")
    if missing:
        pytest.skip(f"LLM eval requires {', '.join(missing)} in the environment")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    _EVAL_CASES,
    ids=[f"{c['id']}-{c['name']}" for c in _EVAL_CASES],
)
async def test_eval_case(case: dict) -> None:
    _require_eval_keys()
    model = os.getenv("EVAL_MODEL", DEFAULT_EVAL_MODEL)
    judge_model = os.getenv("EVAL_JUDGE_MODEL", DEFAULT_JUDGE_MODEL)

    run = await drive_prompt(case["prompt"], model=model)

    assert not run.stopped_early, (
        f"tool loop hit max iterations without finishing; tools called={run.tool_names}"
    )

    # The server runs in Code Mode (its default and target surface): the public
    # tools are `search` + `execute`, and the YNAB helpers are reached as Python
    # `ynab.read.*` / `ynab.write.*` inside `execute`. The model must actually
    # engage that surface rather than answer from prior knowledge.
    assert run.tool_names, f"eval engaged no tools; answer={run.final_text!r}"

    # Read-only + dry-run scope. Two layers keep this safe against a live budget:
    #   1. Server-enforced (primary): code_mode_mutations_enabled defaults to
    #      False, so `ynab.write.*` is blocked by the runner (AST audit + dispatch
    #      fail-closed) and hidden from the Code Mode spec.
    #   2. Defensive (this assert): if someone disabled Code Mode tool replacement
    #      (the escape hatch), the legacy direct write tools become callable — none
    #      should ever be invoked under the eval config.
    illegal_writes = [t for t in run.tool_names if t in YNAB_WRITE_TOOLS]
    assert not illegal_writes, f"eval performed disallowed YNAB write(s): {illegal_writes}"

    # Structural wrong-tool-path check (read tasks only). In Code Mode the YNAB
    # call lives in the Python passed to `execute`, so inspect that code rather
    # than the tool name. Any one of the listed read ops is acceptable. Mutation
    # tasks can't be checked this way — the write ops are hidden in read-only
    # Code Mode — so the LLM judge carries those.
    expected_refs = case.get("expected_code_refs") or []
    if expected_refs:
        executed_code = "\n".join(str(tc.arguments.get("code", "")) for tc in run.tool_calls)
        assert any(ref in executed_code for ref in expected_refs), (
            f"expected the executed code to call one of {expected_refs}; "
            f"executed code was:\n{executed_code[:800]}"
        )

    passed, reason = await judge_answer(
        case["prompt"], case["expected_output"], run.final_text, model=judge_model
    )
    assert passed, (
        f"judge rejected the answer for eval {case['id']} ({case['name']}): {reason}\n"
        f"--- tools called ---\n{run.tool_names}\n"
        f"--- final answer ---\n{run.final_text}"
    )
