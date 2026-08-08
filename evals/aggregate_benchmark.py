"""Build viewer-compatible benchmark reports from one dual-eval iteration."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIGS = ("code_mode", "direct_tools")


def calculate_stats(values: Sequence[float]) -> dict[str, float]:
    """Return sample statistics rounded for stable JSON reports."""
    if not values:
        return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1) if len(values) > 1 else 0.0
    return {
        "mean": round(mean, 4),
        "stddev": round(math.sqrt(variance), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def _run_record(iteration_dir: Path, task: Mapping[str, Any], config: str) -> dict[str, Any]:
    output_dir = iteration_dir / str(task["id"]) / config / "outputs"
    run = json.loads((output_dir / "run.json").read_text())
    grading = json.loads((output_dir / "grading.json").read_text())
    metrics = grading.get("execution_metrics", {})
    summary = grading["summary"]
    return {
        "eval_id": task["id"],
        "eval_name": task.get("name", str(task["id"])),
        "configuration": config,
        "run_number": 1,
        "result": {
            "pass_rate": summary["pass_rate"],
            "passed": summary["passed"],
            "failed": summary["failed"],
            "total": summary["total"],
            "time_seconds": round(float(run.get("duration_ms", 0.0)) / 1000, 3),
            "duration_ms": float(run.get("duration_ms", 0.0)),
            "tokens": int(run.get("total_tokens", 0)),
            "tool_calls": int(metrics.get("total_tool_calls", 0)),
            "errors": int(metrics.get("errors_encountered", 0)),
        },
        "expectations": grading.get("expectations", []),
        "notes": [],
    }


def _summary(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    return {
        "pass_rate": calculate_stats([float(record["result"]["pass_rate"]) for record in records]),
        "duration_ms": calculate_stats([float(record["result"]["duration_ms"]) for record in records]),
        "time_seconds": calculate_stats([float(record["result"]["time_seconds"]) for record in records]),
        "tokens": calculate_stats([float(record["result"]["tokens"]) for record in records]),
    }


def build_benchmark(
    iteration_dir: Path,
    tasks: Sequence[Mapping[str, Any]],
    *,
    model: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a benchmark document, using direct-tools minus Code Mode deltas."""
    runs = [_run_record(iteration_dir, task, config) for task in tasks for config in CONFIGS]
    by_config = {config: [run for run in runs if run["configuration"] == config] for config in CONFIGS}
    summaries = {config: _summary(by_config[config]) for config in CONFIGS}
    code_mode, direct_tools = summaries["code_mode"], summaries["direct_tools"]
    summaries["delta"] = {
        "pass_rate": f"{direct_tools['pass_rate']['mean'] - code_mode['pass_rate']['mean']:+.2f}",
        "duration_ms": f"{direct_tools['duration_ms']['mean'] - code_mode['duration_ms']['mean']:+.1f}",
        "time_seconds": f"{direct_tools['time_seconds']['mean'] - code_mode['time_seconds']['mean']:+.1f}",
        "tokens": f"{direct_tools['tokens']['mean'] - code_mode['tokens']['mean']:+.0f}",
    }
    return {
        "metadata": {
            "skill_name": "mcp-ynab",
            "executor_model": model,
            "analyzer_model": "deterministic",
            "timestamp": timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "evals_run": [task["id"] for task in tasks],
            "runs_per_configuration": 1,
        },
        "runs": runs,
        "run_summary": summaries,
        "notes": [],
    }


def render_benchmark_markdown(benchmark: Mapping[str, Any]) -> str:
    """Render the compact, token-delta-first human benchmark report."""
    summary = benchmark["run_summary"]
    code_mode, direct_tools, delta = (summary[name] for name in (*CONFIGS, "delta"))
    return "\n".join(
        [
            "# Code Mode vs Direct Tools Benchmark",
            "",
            f"Token delta (direct_tools − code_mode): **{delta['tokens']}**",
            "",
            "| Metric | Code Mode | Direct Tools | Delta |",
            "| --- | ---: | ---: | ---: |",
            f"| Pass rate | {code_mode['pass_rate']['mean']:.0%} | {direct_tools['pass_rate']['mean']:.0%} | {delta['pass_rate']} |",
            f"| Tokens (mean ± stddev) | {code_mode['tokens']['mean']:.0f} ± {code_mode['tokens']['stddev']:.0f} | {direct_tools['tokens']['mean']:.0f} ± {direct_tools['tokens']['stddev']:.0f} | {delta['tokens']} |",
            f"| Duration (mean ± stddev) | {code_mode['duration_ms']['mean']:.1f} ± {code_mode['duration_ms']['stddev']:.1f} ms | {direct_tools['duration_ms']['mean']:.1f} ± {direct_tools['duration_ms']['stddev']:.1f} ms | {delta['duration_ms']} ms |",
        ]
    ) + "\n"


def write_benchmark(iteration_dir: Path, tasks: Sequence[Mapping[str, Any]], *, model: str) -> None:
    """Write ``benchmark.json`` and ``benchmark.md`` at the iteration root."""
    benchmark = build_benchmark(iteration_dir, tasks, model=model)
    (iteration_dir / "benchmark.json").write_text(json.dumps(benchmark, indent=2) + "\n")
    (iteration_dir / "benchmark.md").write_text(render_benchmark_markdown(benchmark))
