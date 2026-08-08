"""Deterministic grading for persisted dual-eval run artifacts.

The live runner writes one ``run.json`` per task/configuration.  This module
turns the task's declarative structural expectations into a portable
``grading.json`` alongside it, without requiring API credentials or an LLM.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from tests.integration._llm_eval_harness import YNAB_WRITE_TOOLS

GradeEntry = dict[str, str | bool]
GradeResult = dict[str, Any]


def _tool_names(run: Mapping[str, Any]) -> list[str]:
    calls = run.get("tool_calls", [])
    if not isinstance(calls, list):
        return []
    return [str(call.get("name", "")) for call in calls if isinstance(call, Mapping)]


def _executed_code(run: Mapping[str, Any]) -> str:
    snippets: list[str] = []
    for call in run.get("tool_calls", []):
        if not isinstance(call, Mapping):
            continue
        arguments = call.get("arguments", {})
        if isinstance(arguments, Mapping) and arguments.get("code"):
            snippets.append(str(arguments["code"]))
    return "\n".join(snippets)


def _entry(text: str, passed: bool, evidence: str) -> GradeEntry:
    return {"text": text, "passed": passed, "evidence": evidence}


def _grade_expected_read_operation(
    task: Mapping[str, Any], config_name: str, run: Mapping[str, Any]
) -> GradeEntry | None:
    references = [str(ref) for ref in task.get("expected_code_refs", [])]
    if not references:
        return None

    text = "The run used an expected read operation."
    if config_name == "code_mode":
        code = _executed_code(run)
        for reference in references:
            if reference in code:
                return _entry(text, True, f"found {reference} in executed code")
    else:
        names = _tool_names(run)
        for reference in references:
            if reference in names:
                return _entry(text, True, f"called {reference}")
    return _entry(text, False, f"none of: {', '.join(references)}")


def _grade_expectation(expectation: Mapping[str, Any], final_text: str) -> GradeEntry:
    text = str(expectation["text"])
    expectation_type = expectation.get("type")
    if expectation_type == "final_text_contains":
        value = str(expectation["value"])
        passed = value.casefold() in final_text.casefold()
        return _entry(text, passed, f"found text: {value}" if passed else f"missing text: {value}")

    if expectation_type == "final_text_regex":
        pattern = str(expectation["pattern"])
        passed = re.search(pattern, final_text) is not None
        return _entry(
            text,
            passed,
            f"pattern matched: {pattern}" if passed else f"pattern did not match: {pattern}",
        )

    return _entry(text, False, f"unsupported expectation type: {expectation_type!r}")


def _grade_intended_writes(
    task: Mapping[str, Any], intended_writes: Sequence[Mapping[str, Any]] | None
) -> list[GradeEntry]:
    """Grade the persisted dry-run payload rather than treating a write as unsafe."""
    if task.get("category") != "mutation":
        return []

    if intended_writes is None:
        return [_entry("A dry-run intent artifact was persisted.", False, "artifact missing")]
    if not intended_writes:
        return [_entry("A dry-run mutation intent was captured.", False, "no write intent captured")]

    expected = task.get("intent_expectation", {})
    if not isinstance(expected, Mapping):
        return [_entry("The dry-run intent has a valid expectation.", False, "invalid schema")]
    alternatives = expected.get("alternatives", [expected])
    if not isinstance(alternatives, Sequence) or isinstance(alternatives, str):
        return [_entry("The dry-run intent has valid alternatives.", False, "invalid alternatives")]

    expected_tools: set[str] = set()
    details: list[str] = []
    for alternative in alternatives:
        if not isinstance(alternative, Mapping):
            continue
        tools = {str(name) for name in alternative.get("tools", [])}
        expected_tools.update(tools)
        required = {str(name): value for name, value in alternative.get("arguments", {}).items()}
        for intent in intended_writes:
            if str(intent.get("tool", "")) not in tools:
                continue
            arguments = intent.get("arguments", {})
            if not isinstance(arguments, Mapping):
                details.append(f"{intent.get('tool')} has non-object arguments")
                continue
            missing = [name for name in required if name not in arguments]
            incorrect = [
                name
                for name, value in required.items()
                if value is not None and arguments.get(name) != value
            ]
            if not missing and not incorrect:
                return [
                    _entry(
                        "The intended mutation has the required IDs and payload fields.",
                        True,
                        f"captured {intent.get('tool')} with {', '.join(sorted(required))}",
                    )
                ]
            if missing:
                details.append(f"{intent.get('tool')} missing fields: {', '.join(missing)}")
            if incorrect:
                details.append(f"{intent.get('tool')} incorrect fields: {', '.join(incorrect)}")
    if not details:
        details.append(
            f"expected one of {', '.join(sorted(expected_tools))}; captured "
            + ", ".join(str(entry.get("tool", "")) for entry in intended_writes)
        )
    return [
        _entry(
            "The intended mutation has the required IDs and payload fields.",
            False,
            "; ".join(details),
        )
    ]


def grade_run(
    task: Mapping[str, Any],
    config_name: str,
    run: Mapping[str, Any],
    *,
    intended_writes: Sequence[Mapping[str, Any]] | None = None,
) -> GradeResult:
    """Return objective, human-readable grading assertions for one persisted run."""
    stopped_early = bool(run.get("stopped_early", False))
    grading = [
        _entry(
            "The run completed before the iteration limit.",
            not stopped_early,
            "completed" if not stopped_early else "stopped at the iteration limit",
        )
    ]

    writes = [name for name in _tool_names(run) if name in YNAB_WRITE_TOOLS]
    if task.get("category") == "mutation":
        grading.append(
            _entry(
                "Mutation calls were captured by the dry-run recorder.",
                intended_writes is not None,
                "intended_writes.json persisted"
                if intended_writes is not None
                else "intended_writes.json missing",
            )
        )
        grading.extend(_grade_intended_writes(task, intended_writes))
    else:
        grading.append(
            _entry(
                "No real YNAB write tool was called.",
                not writes,
                "no write tools called" if not writes else f"write tools called: {', '.join(writes)}",
            )
        )

    read_operation = _grade_expected_read_operation(task, config_name, run)
    if read_operation is not None:
        grading.append(read_operation)

    final_text = str(run.get("final_text", ""))
    expectations = task.get("expectations", [])
    if isinstance(expectations, Sequence) and not isinstance(expectations, str):
        grading.extend(
            _grade_expectation(expectation, final_text)
            for expectation in expectations
            if isinstance(expectation, Mapping)
        )
    passed = sum(entry["passed"] is True for entry in grading)
    total = len(grading)
    return {
        "expectations": grading,
        "summary": {
            "passed": passed,
            "failed": total - passed,
            "total": total,
            "pass_rate": round(passed / total, 4) if total else 0.0,
        },
        "execution_metrics": {
            "tool_calls": dict(Counter(_tool_names(run))),
            "total_tool_calls": len(_tool_names(run)),
            "errors_encountered": 0,
        },
        "timing": {
            "total_duration_seconds": round(float(run.get("duration_ms", 0.0)) / 1000, 3),
        },
    }


def grade_iteration(iteration_dir: Path, tasks: Sequence[Mapping[str, Any]]) -> int:
    """Write ``grading.json`` for every task/configuration in an iteration.

    A missing run artifact is an error: an aggregate benchmark must never
    mistake a partial iteration for a fully graded one.
    """
    written = 0
    for task in tasks:
        task_id = str(task["id"])
        for config_name in ("code_mode", "direct_tools"):
            output_dir = iteration_dir / task_id / config_name / "outputs"
            run_path = output_dir / "run.json"
            if not run_path.exists():
                raise FileNotFoundError(f"Missing run output: {run_path}")
            run = json.loads(run_path.read_text())
            intents_path = output_dir / "intended_writes.json"
            intended_writes = json.loads(intents_path.read_text()) if intents_path.exists() else None
            if intended_writes is not None and not isinstance(intended_writes, list):
                raise ValueError(f"Expected a JSON list in {intents_path}")
            grading = grade_run(task, config_name, run, intended_writes=intended_writes)
            (output_dir / "grading.json").write_text(json.dumps(grading, indent=2) + "\n")
            written += 1
    return written
