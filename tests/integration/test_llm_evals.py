"""LLM-driven evals: Claude uses the live mcp-ynab tools to satisfy prompts.

Marked ``integration`` + ``eval`` (excluded from the default run). Requires
``ANTHROPIC_API_KEY`` and ``YNAB_API_KEY``; these cost API tokens and run
against whatever budget the YNAB key points at. Scope is read-only + dry-run —
the structural gate below fails the test if any eval performs a real YNAB
write, so it is safe to run against a live budget.

Run with::

    YNAB_API_KEY=... ANTHROPIC_API_KEY=... uv run pytest -m eval
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
    drive_prompt,
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
    missing = [k for k in ("ANTHROPIC_API_KEY", "YNAB_API_KEY") if not os.getenv(k)]
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

    # Read-only + dry-run scope: no eval may perform a real YNAB write.
    illegal_writes = [t for t in run.tool_names if t in YNAB_WRITE_TOOLS]
    assert not illegal_writes, f"eval performed disallowed YNAB write(s): {illegal_writes}"

    if case.get("category") == "read":
        # A read task must actually consult YNAB rather than answer from prior knowledge.
        assert run.tool_names, f"read eval made no tool calls; answer={run.final_text!r}"
    elif case.get("category") == "mutation":
        # Dry-run prompts must not reach a write via the code-mode `execute` sandbox,
        # which would bypass the write-tool gate above.
        assert "execute" not in run.tool_names, (
            "dry-run eval used the code-mode `execute` tool, which can perform live "
            f"mutations and bypasses the write gate; tools={run.tool_names}"
        )

    passed, reason = await judge_answer(
        case["prompt"], case["expected_output"], run.final_text, model=judge_model
    )
    assert passed, (
        f"judge rejected the answer for eval {case['id']} ({case['name']}): {reason}\n"
        f"--- tools called ---\n{run.tool_names}\n"
        f"--- final answer ---\n{run.final_text}"
    )
