"""Unit tests for pure helpers in evals/run_dual_eval.py.

These run in the default suite (no markers, no API keys needed).
They cover workspace path building, timing aggregation, and run serialization.
"""

from __future__ import annotations

from pathlib import Path

import pytest  # noqa: F401  # used by tmp_path fixture type hints

# Import from the evals/ package (run_dual_eval module inside evals/ directory).
from evals.run_dual_eval import (
    CONFIGS,
    YNAB_CODE_MODE_TOOLS,
    build_timing_summary,
    eval_output_dir,
    next_iteration_dir,
    run_to_dict,
)
from tests.integration._llm_eval_harness import EvalRun, ToolCall, YNAB_WRITE_TOOLS


# ---------------------------------------------------------------------------
# next_iteration_dir
# ---------------------------------------------------------------------------


def test_next_iteration_dir_empty_workspace(tmp_path: Path) -> None:
    """When the workspace does not exist yet, iteration-1 is returned."""
    ws = tmp_path / "workspace"
    result = next_iteration_dir(ws)
    assert result == ws / "iteration-1"


def test_next_iteration_dir_existing_workspace(tmp_path: Path) -> None:
    """When iteration-1 and iteration-2 exist, iteration-3 is returned."""
    ws = tmp_path / "workspace"
    (ws / "iteration-1").mkdir(parents=True)
    (ws / "iteration-2").mkdir(parents=True)
    result = next_iteration_dir(ws)
    assert result == ws / "iteration-3"


def test_next_iteration_dir_non_iteration_dirs_ignored(tmp_path: Path) -> None:
    """Non-iteration-N directories in the workspace are ignored."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "README.md").write_text("")
    (ws / "some-dir").mkdir()
    result = next_iteration_dir(ws)
    assert result == ws / "iteration-1"


def test_next_iteration_dir_gap_handled(tmp_path: Path) -> None:
    """The highest existing N drives the next number, even with gaps."""
    ws = tmp_path / "workspace"
    (ws / "iteration-1").mkdir(parents=True)
    (ws / "iteration-5").mkdir(parents=True)
    result = next_iteration_dir(ws)
    assert result == ws / "iteration-6"


# ---------------------------------------------------------------------------
# eval_output_dir
# ---------------------------------------------------------------------------


def test_eval_output_dir() -> None:
    iteration = Path("/workspace/iteration-3")
    result = eval_output_dir(iteration, "eval-read-01", "code_mode")
    assert result == Path("/workspace/iteration-3/eval-read-01/code_mode/outputs")


def test_eval_output_dir_direct_tools() -> None:
    iteration = Path("/workspace/iteration-1")
    result = eval_output_dir(iteration, "eval-dry-run-02", "direct_tools")
    assert result == Path("/workspace/iteration-1/eval-dry-run-02/direct_tools/outputs")


# ---------------------------------------------------------------------------
# run_to_dict
# ---------------------------------------------------------------------------


def test_run_to_dict_empty() -> None:
    run = EvalRun()
    d = run_to_dict(run)
    assert d["final_text"] == ""
    assert d["stopped_early"] is False
    assert d["total_input_tokens"] == 0
    assert d["total_output_tokens"] == 0
    assert d["total_tokens"] == 0
    assert d["duration_ms"] == 0.0
    assert d["tool_calls"] == []


def test_run_to_dict_with_tool_calls() -> None:
    run = EvalRun(
        final_text="Budget balance is $500.",
        total_input_tokens=100,
        total_output_tokens=50,
        duration_ms=1234.567,
        tool_calls=[ToolCall("execute", {"code": "return await ynab.read.get_budgets()"})],
    )
    d = run_to_dict(run)
    assert d["final_text"] == "Budget balance is $500."
    assert d["total_tokens"] == 150
    assert d["duration_ms"] == 1234.57  # rounded to 2 dp
    assert len(d["tool_calls"]) == 1
    assert d["tool_calls"][0]["name"] == "execute"


# ---------------------------------------------------------------------------
# build_timing_summary
# ---------------------------------------------------------------------------


def test_build_timing_summary_single() -> None:
    results = {
        "eval-read-01": {
            "code_mode": EvalRun(total_input_tokens=100, total_output_tokens=40, duration_ms=800.0),
            "direct_tools": EvalRun(
                total_input_tokens=200, total_output_tokens=60, duration_ms=1200.0
            ),
        }
    }
    summary = build_timing_summary(results)
    assert summary["total_tokens"] == 400
    assert summary["duration_ms"] == 2000.0
    assert summary["total_duration_seconds"] == 2.0

    cm = summary["evals"]["eval-read-01"]["code_mode"]
    assert cm["total_tokens"] == 140
    assert cm["duration_ms"] == 800.0

    dt = summary["evals"]["eval-read-01"]["direct_tools"]
    assert dt["total_tokens"] == 260


def test_build_timing_summary_multiple() -> None:
    results = {
        "eval-a": {
            "code_mode": EvalRun(total_input_tokens=100, total_output_tokens=50, duration_ms=500.0),
            "direct_tools": EvalRun(
                total_input_tokens=150, total_output_tokens=75, duration_ms=700.0
            ),
        },
        "eval-b": {
            "code_mode": EvalRun(
                total_input_tokens=200, total_output_tokens=100, duration_ms=600.0
            ),
            "direct_tools": EvalRun(
                total_input_tokens=180, total_output_tokens=90, duration_ms=650.0
            ),
        },
    }
    summary = build_timing_summary(results)
    # grand total tokens: 150 + 225 + 300 + 270 = 945
    assert summary["total_tokens"] == 945
    assert "eval-a" in summary["evals"]
    assert "eval-b" in summary["evals"]


def test_build_timing_summary_empty() -> None:
    summary = build_timing_summary({})
    assert summary["total_tokens"] == 0
    assert summary["duration_ms"] == 0.0
    assert summary["total_duration_seconds"] == 0.0
    assert summary["evals"] == {}


# ---------------------------------------------------------------------------
# CONFIGS: tool blocking
# ---------------------------------------------------------------------------


def test_direct_tools_config_blocks_write_tools() -> None:
    """direct_tools config must block all YNAB write tools."""
    cfg = next(c for c in CONFIGS if c["name"] == "direct_tools")
    assert YNAB_WRITE_TOOLS <= cfg["blocked_tool_names"]


def test_direct_tools_config_blocks_code_mode_tools() -> None:
    """direct_tools config must block code-mode tools (execute/search) to avoid wasted iterations."""
    cfg = next(c for c in CONFIGS if c["name"] == "direct_tools")
    assert YNAB_CODE_MODE_TOOLS <= cfg["blocked_tool_names"]


def test_code_mode_config_does_not_block_code_mode_tools() -> None:
    """code_mode config must NOT block execute/search."""
    cfg = next(c for c in CONFIGS if c["name"] == "code_mode")
    assert not (YNAB_CODE_MODE_TOOLS & cfg["blocked_tool_names"])


# ---------------------------------------------------------------------------
# --task-ids: integer/string normalization
# ---------------------------------------------------------------------------


def test_task_id_filter_matches_integer_ids() -> None:
    """Filtering by string '1' must match a task with integer id=1."""
    tasks = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
    wanted = {"1"}
    filtered = [t for t in tasks if str(t["id"]).strip() in wanted]
    assert len(filtered) == 1
    assert filtered[0]["id"] == 1


def test_task_id_filter_trims_whitespace() -> None:
    """Whitespace around task IDs in --task-ids is stripped."""
    tasks = [{"id": 3, "name": "c"}]
    wanted = {s.strip() for s in " 3 , 4 ".split(",")}
    filtered = [t for t in tasks if str(t["id"]).strip() in wanted]
    assert len(filtered) == 1
