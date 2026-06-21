"""Dual-config eval runner: Code Mode vs direct-tools benchmark.

Runs each eval task under both server configurations in the same pass, captures
outputs and timing, and writes a skill-creator-style workspace layout.

Workspace layout::

    <workspace>/iteration-<N>/
        eval-<id>/
            code_mode/outputs/
                run.json           # tool_calls, final_text, tokens, duration_ms
                executed_code.py   # code snippets from execute calls (code_mode only)
            direct_tools/outputs/
                run.json
        timing.json                # per-eval + total token/time summary

Usage::

    uv run python evals/run_dual_eval.py
    uv run python evals/run_dual_eval.py --task-ids eval-read-01,eval-read-02
    uv run python evals/run_dual_eval.py --workspace /tmp/my-workspace
    uv run python evals/run_dual_eval.py --model claude-sonnet-4-6

Requires:
    YNAB_API_KEY  — live YNAB account key
    EVAL_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY  — Anthropic API key (messages-api driver)

Note on write interception:
    Structured PATCH-payload capture (intended_writes.json) requires a server-side
    dry-run mode that is not yet implemented. The executed_code.py files capture the
    Python snippets that would have been run in code_mode, which is the closest
    proxy available without server changes. Tracked in mcp-ynab-g57.3.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# Allow running from the repo root without installing.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.integration._llm_eval_harness import (  # noqa: E402
    CODE_MODE_SYSTEM,
    DEFAULT_EVAL_MODEL,
    DIRECT_TOOLS_SYSTEM,
    EvalRun,
    current_driver,
    drive_prompt,
    YNAB_WRITE_TOOLS,
)

EVALS_PATH = Path(__file__).resolve().parent / "evals.json"
DEFAULT_WORKSPACE = Path(__file__).resolve().parent / "workspace"

# Tools only present (and functional) when code mode is enabled; block them
# from the direct-tools surface so the model doesn't waste iterations on errors.
YNAB_CODE_MODE_TOOLS = frozenset({"execute", "search"})

# Config A — Code Mode with tool replacement, mutations blocked (read-only).
CODE_MODE_ENV: dict[str, str] = {
    "MCP_YNAB_CODE_MODE_ENABLED": "true",
    "MCP_YNAB_CODE_MODE_REPLACE_TOOLS": "true",
    "MCP_YNAB_CODE_MODE_MUTATIONS_ENABLED": "false",
}

# Config B — Direct tools: read-only YNAB tool set visible, code mode off.
DIRECT_TOOLS_ENV: dict[str, str] = {
    "MCP_YNAB_CODE_MODE_ENABLED": "false",
    "MCP_YNAB_CODE_MODE_REPLACE_TOOLS": "false",
}

CONFIGS: list[dict[str, Any]] = [
    {
        "name": "code_mode",
        "env": CODE_MODE_ENV,
        "system_prompt": CODE_MODE_SYSTEM,
        "blocked_tool_names": frozenset(),
    },
    {
        "name": "direct_tools",
        "env": DIRECT_TOOLS_ENV,
        "system_prompt": DIRECT_TOOLS_SYSTEM,
        "blocked_tool_names": YNAB_WRITE_TOOLS | YNAB_CODE_MODE_TOOLS,
    },
]


# ---------------------------------------------------------------------------
# Pure workspace helpers (unit-testable — no I/O)
# ---------------------------------------------------------------------------


def next_iteration_dir(workspace: Path) -> Path:
    """Return the path for the next iteration directory (does not create it)."""
    existing = (
        sorted(
            int(p.name.split("-")[1])
            for p in workspace.iterdir()
            if p.is_dir() and p.name.startswith("iteration-") and p.name.split("-")[1].isdigit()
        )
        if workspace.exists()
        else []
    )
    n = (existing[-1] + 1) if existing else 1
    return workspace / f"iteration-{n}"


def eval_output_dir(iteration_dir: Path, eval_id: str, config_name: str) -> Path:
    """Return the outputs directory for a single eval/config pair."""
    return iteration_dir / eval_id / config_name / "outputs"


def run_to_dict(run: EvalRun) -> dict[str, Any]:
    """Serialize an EvalRun to a JSON-compatible dict."""
    return {
        "final_text": run.final_text,
        "stopped_early": run.stopped_early,
        "total_input_tokens": run.total_input_tokens,
        "total_output_tokens": run.total_output_tokens,
        "total_tokens": run.total_tokens,
        "duration_ms": round(run.duration_ms, 2),
        "tool_calls": [{"name": tc.name, "arguments": tc.arguments} for tc in run.tool_calls],
    }


def build_timing_summary(
    results: dict[str, dict[str, EvalRun]],
) -> dict[str, Any]:
    """Build the timing.json payload from a {eval_id: {config_name: EvalRun}} map."""
    evals_summary: dict[str, Any] = {}
    grand_total_tokens = 0
    grand_total_ms = 0.0

    for eval_id, config_runs in results.items():
        eval_entry: dict[str, Any] = {}
        for config_name, run in config_runs.items():
            eval_entry[config_name] = {
                "total_tokens": run.total_tokens,
                "total_input_tokens": run.total_input_tokens,
                "total_output_tokens": run.total_output_tokens,
                "duration_ms": round(run.duration_ms, 2),
            }
            grand_total_tokens += run.total_tokens
            grand_total_ms += run.duration_ms
        evals_summary[eval_id] = eval_entry

    return {
        "total_tokens": grand_total_tokens,
        "duration_ms": round(grand_total_ms, 2),
        "total_duration_seconds": round(grand_total_ms / 1000, 3),
        "evals": evals_summary,
    }


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def load_evals(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    return data["evals"]


def write_run_outputs(output_dir: Path, config_name: str, run: EvalRun) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run.json").write_text(json.dumps(run_to_dict(run), indent=2))

    if config_name == "code_mode":
        snippets = [
            str(tc.arguments.get("code", ""))
            for tc in run.tool_calls
            if tc.name == "execute" and tc.arguments.get("code")
        ]
        if snippets:
            header = "# Executed code snippets from code_mode run\n"
            separator = "\n\n# --- next snippet ---\n\n"
            (output_dir / "executed_code.py").write_text(header + separator.join(snippets) + "\n")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_eval_dual(
    task: dict[str, Any],
    *,
    model: str,
    max_iterations: int,
) -> dict[str, EvalRun]:
    """Run a single eval task under both configs concurrently."""

    async def _run_config(cfg: dict[str, Any]) -> EvalRun:
        return await drive_prompt(
            task["prompt"],
            model=model,
            max_iterations=max_iterations,
            system_prompt=cfg["system_prompt"],
            server_env_overrides=cfg["env"],
            blocked_tool_names=cfg["blocked_tool_names"],
        )

    runs = await asyncio.gather(*[_run_config(cfg) for cfg in CONFIGS])
    return {cfg["name"]: run for cfg, run in zip(CONFIGS, runs)}


async def run_all(
    tasks: list[dict[str, Any]],
    *,
    model: str,
    max_iterations: int,
    workspace: Path,
) -> None:
    iteration_dir = next_iteration_dir(workspace)
    print(f"Writing to: {iteration_dir}")

    results: dict[str, dict[str, EvalRun]] = {}

    for task in tasks:
        task_id = str(task["id"])
        print(f"  [{task_id}] {task['name']} ...", end=" ", flush=True)
        config_runs = await run_eval_dual(task, model=model, max_iterations=max_iterations)
        results[task_id] = config_runs

        for config_name, run in config_runs.items():
            out_dir = eval_output_dir(iteration_dir, task_id, config_name)
            write_run_outputs(out_dir, config_name, run)

        tokens_summary = " | ".join(
            f"{cfg}: {config_runs[cfg].total_tokens}t {config_runs[cfg].duration_ms:.0f}ms"
            for cfg in ("code_mode", "direct_tools")
        )
        print(f"done  ({tokens_summary})")

    timing = build_timing_summary(results)
    (iteration_dir / "timing.json").write_text(json.dumps(timing, indent=2))
    print(
        f"\nTotal: {timing['total_tokens']} tokens, "
        f"{timing['total_duration_seconds']:.1f}s  →  {iteration_dir / 'timing.json'}"
    )


def _require_keys() -> None:
    missing = []
    if not os.getenv("YNAB_API_KEY"):
        missing.append("YNAB_API_KEY")
    if not (os.getenv("EVAL_ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
        missing.append("EVAL_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY)")
    if missing:
        print(f"ERROR: missing required env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--task-ids",
        help="Comma-separated eval task IDs to run (default: all)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=DEFAULT_WORKSPACE,
        help=f"Workspace root (default: {DEFAULT_WORKSPACE})",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("EVAL_MODEL", DEFAULT_EVAL_MODEL),
        help="Model to use for eval runs (default: %(default)s)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=int(os.getenv("EVAL_MAX_ITERATIONS", "8")),
        help="Max tool-use iterations per run (default: %(default)s)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    _require_keys()

    if current_driver() == "agent-sdk":
        print(
            "ERROR: EVAL_DRIVER=agent-sdk is not supported by the dual-config runner. "
            "The agent-sdk path drops system_prompt, server_env_overrides, blocked_tool_names, "
            "and max_iterations, so both configs would run identically in code-mode. "
            "Unset EVAL_DRIVER or set EVAL_DRIVER=messages-api.",
            file=sys.stderr,
        )
        sys.exit(1)

    tasks = load_evals(EVALS_PATH)
    if args.task_ids:
        wanted = {s.strip() for s in args.task_ids.split(",")}
        tasks = [t for t in tasks if str(t["id"]).strip() in wanted]
        if not tasks:
            print(f"ERROR: no tasks matched --task-ids {args.task_ids!r}", file=sys.stderr)
            sys.exit(1)

    print(f"Running {len(tasks)} eval task(s) under {len(CONFIGS)} config(s) each")
    print(f"Model: {args.model}  |  Max iterations: {args.max_iterations}")
    asyncio.run(
        run_all(
            tasks,
            model=args.model,
            max_iterations=args.max_iterations,
            workspace=args.workspace,
        )
    )


if __name__ == "__main__":
    main()
