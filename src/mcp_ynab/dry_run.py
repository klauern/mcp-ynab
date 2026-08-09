"""Eval-only interception of YNAB mutation tools.

When ``MCP_YNAB_EVAL_DRY_RUN_INTENTS_PATH`` is set, registered write tools do
not execute.  Instead their validated arguments are appended to that JSON
file.  This is deliberately an environment opt-in rather than a preference:
normal server operation, including the Code Mode mutation gate, is unchanged.
"""

from __future__ import annotations

import json
import os
from functools import wraps
from pathlib import Path
from typing import Any

from pydantic_core import to_jsonable_python

INTENTS_PATH_ENV = "MCP_YNAB_EVAL_DRY_RUN_INTENTS_PATH"

_EXCLUDED_TOOLS = frozenset({"execute"})


def intents_path() -> Path | None:
    """Return the configured intent artifact path, if dry-run mode is enabled."""
    raw_path = os.getenv(INTENTS_PATH_ENV)
    return Path(raw_path) if raw_path else None


def dry_run_enabled() -> bool:
    """Whether this server process is an eval dry-run server."""
    return intents_path() is not None


def _write_intent(path: Path, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Append one intent atomically and return the synthetic tool response."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(path.read_text()) if path.exists() else []
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid dry-run intents file: {path}") from exc
    if not isinstance(existing, list):
        raise RuntimeError(f"Dry-run intents file must contain a JSON list: {path}")

    payload = to_jsonable_python(arguments)
    existing.append({"tool": tool_name, "arguments": payload})
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(existing, indent=2, default=str) + "\n")
    os.replace(temporary, path)
    return {"dry_run": True, "tool": tool_name, "arguments": payload}


def install_dry_run_interceptor(mcp: Any) -> None:
    """Replace registered YNAB write handlers with no-dispatch recorders.

    FastMCP validates arguments before calling ``tool.fn``. Replacing every
    mutating handler (except the Code Mode dispatcher itself) therefore records
    the same validated payload for direct MCP calls and Code Mode's internal
    RPC dispatch, while making both YNAB and local-state mutations unreachable.
    """
    path = intents_path()
    if path is None:
        return

    tools = mcp._tool_manager._tools
    for tool_name, tool in tools.items():
        annotations = getattr(tool, "annotations", None)
        if tool_name in _EXCLUDED_TOOLS or bool(getattr(annotations, "readOnlyHint", False)):
            continue
        if getattr(tool, "_dry_run_intercepted", False):
            continue

        original = tool.fn

        @wraps(original)
        async def record(*args: Any, _tool_name: str = tool_name, **kwargs: Any) -> dict[str, Any]:
            # FastMCP / Code Mode may inject Context. It is transport state,
            # not part of the intended YNAB request, and is not JSON serializable.
            kwargs.pop("ctx", None)
            return _write_intent(path, _tool_name, kwargs)

        tool.fn = record
        tool._dry_run_intercepted = True
