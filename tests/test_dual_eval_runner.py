"""Unit tests for pure helpers in evals/run_dual_eval.py.

These run in the default suite (no markers, no API keys needed).
They cover workspace path building, timing aggregation, and run serialization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest  # noqa: F401  # used by tmp_path fixture type hints

# Import from the evals/ package (run_dual_eval module inside evals/ directory).
from evals.run_dual_eval import (
    build_timing_summary,
    eval_output_dir,
    next_iteration_dir,
    run_all,
    run_to_dict,
)
from evals.aggregate_benchmark import build_benchmark, render_benchmark_markdown
from evals.grading import grade_iteration, grade_run
from tests.integration._llm_eval_harness import EvalRun, ToolCall


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


@pytest.mark.asyncio
async def test_run_all_initializes_dry_run_artifacts_for_both_surfaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run_eval_dual(
        _task: dict[str, Any],
        *,
        model: str,
        max_iterations: int,
        intent_paths: dict[str, Path],
    ) -> dict[str, EvalRun]:
        assert model == "test-model"
        assert max_iterations == 3
        assert set(intent_paths) == {"code_mode", "direct_tools"}
        assert all(json.loads(path.read_text()) == [] for path in intent_paths.values())
        return {"code_mode": EvalRun(), "direct_tools": EvalRun()}

    monkeypatch.setattr("evals.run_dual_eval.run_eval_dual", fake_run_eval_dual)
    monkeypatch.setattr("evals.run_dual_eval.write_benchmark", lambda *_args, **_kwargs: None)

    await run_all(
        [{"id": 6, "name": "Dry run", "category": "mutation", "expectations": []}],
        model="test-model",
        max_iterations=3,
        workspace=tmp_path,
    )

    for config_name in ("code_mode", "direct_tools"):
        artifact = tmp_path / "iteration-1" / "6" / config_name / "outputs" / "intended_writes.json"
        assert json.loads(artifact.read_text()) == []


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
# grade_run
# ---------------------------------------------------------------------------


def test_grade_run_records_structural_assertions_for_code_mode() -> None:
    task = {
        "id": 1,
        "category": "read",
        "expected_code_refs": ["get_transactions"],
        "expectations": [
            {
                "text": "The answer identifies a dollar amount.",
                "type": "final_text_regex",
                "pattern": r"\$\d",
            }
        ],
    }
    run = {
        "final_text": "You spent $42.00.",
        "stopped_early": False,
        "tool_calls": [
            {"name": "execute", "arguments": {"code": "return await ynab.read.get_transactions()"}}
        ],
    }

    grading = grade_run(task, "code_mode", run)

    assert grading["expectations"] == [
        {
            "text": "The run completed before the iteration limit.",
            "passed": True,
            "evidence": "completed",
        },
        {
            "text": "No real YNAB write tool was called.",
            "passed": True,
            "evidence": "no write tools called",
        },
        {
            "text": "The run used an expected read operation.",
            "passed": True,
            "evidence": "found get_transactions in executed code",
        },
        {
            "text": "The answer identifies a dollar amount.",
            "passed": True,
            "evidence": "pattern matched: \\$\\d",
        },
    ]
    assert grading["summary"] == {"passed": 4, "failed": 0, "total": 4, "pass_rate": 1.0}
    assert grading["execution_metrics"] == {
        "tool_calls": {"execute": 1},
        "total_tool_calls": 1,
        "errors_encountered": 0,
    }
    assert grading["timing"] == {"total_duration_seconds": 0.0}


def test_grade_run_flags_writes_missing_read_operation_and_failed_expectation() -> None:
    task = {
        "id": 2,
        "category": "mutation",
        "expected_code_refs": ["get_transactions"],
        "expectations": [
            {
                "text": "The answer is a dry run.",
                "type": "final_text_contains",
                "value": "would",
            }
        ],
    }
    run = {
        "final_text": "Done.",
        "stopped_early": True,
        "tool_calls": [{"name": "categorize_transaction", "arguments": {}}],
    }

    grading = grade_run(task, "direct_tools", run)

    assert [entry["passed"] for entry in grading["expectations"]] == [
        False,
        False,
        False,
        False,
        False,
    ]
    assert grading["expectations"][1]["evidence"] == "intended_writes.json missing"
    assert grading["expectations"][2]["evidence"] == "artifact missing"
    assert grading["expectations"][3]["evidence"] == "none of: get_transactions"
    assert grading["expectations"][4]["evidence"] == "missing text: would"
    assert grading["summary"] == {"passed": 0, "failed": 5, "total": 5, "pass_rate": 0.0}


def test_grade_run_validates_captured_mutation_payload() -> None:
    task = {
        "id": 9,
        "category": "mutation",
        "intent_expectation": {
            "tools": ["update_category"],
            "arguments": {
                "budget_id": None,
                "category_id": None,
                "goal_target": 200,
            },
        },
        "expectations": [],
    }
    run = {"final_text": "I would set the $200 goal.", "tool_calls": []}

    grading = grade_run(
        task,
        "code_mode",
        run,
        intended_writes=[
            {
                "tool": "update_category",
                "arguments": {
                    "budget_id": "budget-1",
                    "category_id": "groceries-1",
                    "goal_target": 200,
                },
            }
        ],
    )

    assert [entry["passed"] for entry in grading["expectations"]] == [True, True, True]
    assert grading["expectations"][-1]["evidence"] == (
        "captured update_category with budget_id, category_id, goal_target"
    )


def test_grade_iteration_writes_grading_json_for_each_config(tmp_path: Path) -> None:
    task = {
        "id": 1,
        "expected_code_refs": ["get_transactions"],
        "expectations": [],
    }
    iteration = tmp_path / "iteration-1"
    for config, tool_call in {
        "code_mode": {"name": "execute", "arguments": {"code": "get_transactions"}},
        "direct_tools": {"name": "get_transactions", "arguments": {}},
    }.items():
        output_dir = iteration / "1" / config / "outputs"
        output_dir.mkdir(parents=True)
        (output_dir / "run.json").write_text(
            json.dumps({"final_text": "done", "stopped_early": False, "tool_calls": [tool_call]})
        )

    count = grade_iteration(iteration, [task])

    assert count == 2
    for config in ("code_mode", "direct_tools"):
        grading = json.loads((iteration / "1" / config / "outputs" / "grading.json").read_text())
        assert [entry["passed"] for entry in grading["expectations"]] == [True, True, True]


def test_eval_suite_has_deterministic_expectations() -> None:
    data = json.loads((Path(__file__).parents[1] / "evals" / "evals.json").read_text())

    for task in data["evals"]:
        assert task["expectations"], task["name"]
        assert all(
            expectation["type"] in {"final_text_contains", "final_text_regex"}
            for expectation in task["expectations"]
        )


def test_build_benchmark_leads_with_direct_minus_code_mode_token_delta(tmp_path: Path) -> None:
    iteration = tmp_path / "iteration-1"
    task = {"id": 1, "name": "Cash Balance Summary"}
    for config, tokens, duration_ms in (("code_mode", 100, 1000.0), ("direct_tools", 150, 1500.0)):
        output_dir = iteration / "1" / config / "outputs"
        output_dir.mkdir(parents=True)
        (output_dir / "run.json").write_text(
            json.dumps(
                {
                    "total_tokens": tokens,
                    "duration_ms": duration_ms,
                    "tool_calls": [{"name": "get_accounts", "arguments": {}}],
                }
            )
        )
        (output_dir / "grading.json").write_text(
            json.dumps(
                {
                    "expectations": [],
                    "summary": {"passed": 2, "failed": 0, "total": 2, "pass_rate": 1.0},
                    "execution_metrics": {"total_tool_calls": 1, "errors_encountered": 0},
                }
            )
        )

    benchmark = build_benchmark(
        iteration, [task], model="test-model", timestamp="2026-07-18T00:00:00Z"
    )

    assert benchmark["metadata"]["evals_run"] == [1]
    assert benchmark["run_summary"]["code_mode"]["tokens"]["mean"] == 100.0
    assert benchmark["run_summary"]["direct_tools"]["duration_ms"]["mean"] == 1500.0
    assert benchmark["run_summary"]["delta"] == {
        "pass_rate": "+0.00",
        "duration_ms": "+500.0",
        "time_seconds": "+0.5",
        "tokens": "+50",
    }
    assert "Token delta (direct_tools − code_mode): **+50**" in render_benchmark_markdown(benchmark)
