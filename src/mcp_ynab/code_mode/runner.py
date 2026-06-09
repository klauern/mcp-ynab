"""Subprocess-isolated Code Mode runner.

User snippets execute in a fresh child process (:mod:`mcp_ynab.code_mode._worker`)
spawned per call, never in the parent. The parent:

* audits the snippet (AST allow-list) *before* spawning,
* holds the live MCP tool registry and the request ``ctx`` (neither crosses the
  process boundary), and
* answers ``ynab.read`` / ``ynab.write`` calls over a stdio JSON-RPC bridge.

This gives a real OS process boundary: a hard ``kill()`` on timeout interrupts
synchronous blocking user code (e.g. ``time.sleep``, CPU-bound loops) that the
old in-process ``asyncio.wait_for`` could never stop (mcp-ynab-fkv). It is still
not a complete security sandbox -- OS resource limits (RLIMIT) and syscall
filtering (seccomp) are tracked separately (mcp-ynab-fsv.1b) -- but user code no
longer runs in, or shares the address space of, the server process.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import sys
from typing import Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from ._sandbox import encode_frame, read_frame, wrap_code

_WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_worker.py")

FORBIDDEN_NAMES = {
    "__import__",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "locals",
    "open",
    "setattr",
    "vars",
}


class CodeModeResult(BaseModel):
    """Structured result returned by ``execute``."""

    ok: bool
    result: Any = None
    logs: str = ""
    error: str | None = None
    traceback: str | None = None
    truncated: bool = False


class CodeModeAuditError(ValueError):
    """Raised when a snippet contains forbidden syntax or names."""


def _is_mutating_tool(tool: Any) -> bool:
    annotations = getattr(tool, "annotations", None)
    return not bool(getattr(annotations, "readOnlyHint", False))


def _audit_code(code: str, *, mutations_enabled: bool) -> ast.Module:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise CodeModeAuditError(f"syntax_error: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise CodeModeAuditError("forbidden: imports are disabled")
        if isinstance(node, ast.With):
            raise CodeModeAuditError("forbidden: with blocks are disabled")
        if isinstance(node, ast.AsyncWith):
            raise CodeModeAuditError("forbidden: async with blocks are disabled")
        if isinstance(node, ast.JoinedStr):
            raise CodeModeAuditError("forbidden: f-strings are disabled")
        if isinstance(node, ast.Attribute) and "__" in node.attr:
            raise CodeModeAuditError(f"forbidden: dunder attribute {node.attr!r}")
        if isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or "__" in node.id):
            raise CodeModeAuditError(f"forbidden: name {node.id!r}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            raise CodeModeAuditError("forbidden: dunder string literal")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            and _is_ynab_write_attribute(node.func)
            and not mutations_enabled
        ):
            raise CodeModeAuditError("mutations_disabled")

    return tree


def _is_ynab_write_attribute(attr: ast.Attribute) -> bool:
    current: ast.AST = attr
    parts: list[str] = []
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return list(reversed(parts))[:2] == ["ynab", "write"]


async def _call_tool(tool: Any, ctx: Any, kwargs: dict[str, Any]) -> Any:
    model = tool.fn_metadata.arg_model.model_validate(kwargs)
    validated = model.model_dump(exclude_none=False)
    if tool.context_kwarg:
        validated["ctx"] = ctx
    result = tool.fn(**validated)
    if inspect.isawaitable(result):
        return await result
    return result


# --- Parent-side RPC dispatch -------------------------------------------------


def _build_dispatch(mcp: Any, *, mutations_enabled: bool) -> dict[tuple[str, str], Any]:
    """Map ``(namespace, method)`` to the live tool the child may invoke.

    Write tools are omitted entirely when mutations are disabled, so a stray
    ``ynab.write.*`` RPC fails closed even if it slipped past the AST audit.
    """
    dispatch: dict[tuple[str, str], Any] = {}
    for name, tool in mcp._tool_manager._tools.items():
        if name in {"execute", "search"}:
            continue
        if _is_mutating_tool(tool):
            if mutations_enabled:
                dispatch[("write", name)] = tool
        else:
            dispatch[("read", name)] = tool
    return dispatch


async def _handle_rpc(
    frame: dict[str, Any],
    dispatch: dict[tuple[str, str], Any],
    ctx: Any,
) -> dict[str, Any]:
    call_id = frame.get("id")
    namespace = frame.get("namespace", "")
    method = frame.get("method", "")
    kwargs = frame.get("kwargs", {})

    tool = dispatch.get((namespace, method))
    if tool is None:
        return {
            "type": "rpc_result",
            "id": call_id,
            "ok": False,
            "error": f"unknown_tool: ynab.{namespace}.{method}",
        }
    try:
        result = await _call_tool(tool, ctx, kwargs)
        return {
            "type": "rpc_result",
            "id": call_id,
            "ok": True,
            "result": to_jsonable_python(result),
        }
    except Exception as exc:  # surface tool errors to the snippet, don't crash the bridge
        return {
            "type": "rpc_result",
            "id": call_id,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _serve(
    proc: asyncio.subprocess.Process,
    dispatch: dict[tuple[str, str], Any],
    ctx: Any,
) -> dict[str, Any]:
    """Pump frames from the child until it emits its final result.

    Dispatches RPC requests concurrently with reading, so the child never blocks
    on an unanswered call (the deadlock the advisor warned about).
    """
    assert proc.stdout is not None and proc.stdin is not None
    while True:
        try:
            frame = await read_frame(proc.stdout)
        except (RuntimeError, ValueError):
            # Bad header or malformed JSON payload: treat as a dead worker and
            # fail closed rather than letting the exception escape run_code.
            frame = None
        if frame is None:
            stderr = b""
            if proc.stderr is not None:
                stderr = await proc.stderr.read()
            detail = stderr.decode(errors="replace").strip()[-2000:] or "no output"
            return {
                "ok": False,
                "result": None,
                "logs": "",
                "error": f"worker_failed: {detail}",
                "traceback": None,
                "truncated": False,
            }
        if frame.get("type") == "rpc":
            response = await _handle_rpc(frame, dispatch, ctx)
            proc.stdin.write(encode_frame(response))
            await proc.stdin.drain()
        elif frame.get("type") == "result":
            return frame


_RESULT_FIELDS = ("ok", "result", "logs", "error", "traceback", "truncated")


async def _run_in_subprocess(
    startup: dict[str, Any],
    *,
    dispatch: dict[tuple[str, str], Any],
    ctx: Any,
    timeout_s: float,
) -> CodeModeResult:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        _WORKER_PATH,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None
    proc.stdin.write(encode_frame(startup))
    await proc.stdin.drain()

    try:
        frame = await asyncio.wait_for(_serve(proc, dispatch, ctx), timeout=timeout_s)
        # The child emits its result frame as its last act, then exits. Close its
        # stdin and give it a brief grace period to wind down on its own.
        if proc.stdin is not None and not proc.stdin.is_closing():
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
    except asyncio.TimeoutError:
        # _serve exceeded the wall clock: hard-kill non-cooperative user code.
        return CodeModeResult(ok=False, error="timeout")
    finally:
        # Backstop: never leave a child running (covers timeout, cancellation,
        # and a child that ignored the stdin EOF).
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

    return CodeModeResult(**{key: frame.get(key) for key in _RESULT_FIELDS})


async def run_code(
    code: str,
    *,
    mcp: Any,
    ctx: Any = None,
    mutations_enabled: bool = False,
    timeout_s: float = 10.0,
    max_output_chars: int = 8192,
) -> CodeModeResult:
    """Audit and execute ``code`` as the body of an async ``__main__`` in a child process."""
    try:
        _audit_code(wrap_code(code), mutations_enabled=mutations_enabled)
    except CodeModeAuditError as exc:
        return CodeModeResult(ok=False, error=str(exc))

    dispatch = _build_dispatch(mcp, mutations_enabled=mutations_enabled)
    startup = {
        "mode": "code",
        "code": code,
        "mutations_enabled": mutations_enabled,
        "max_output_chars": max_output_chars,
        "read": [method for (ns, method) in dispatch if ns == "read"],
        "write": [method for (ns, method) in dispatch if ns == "write"],
        "filename": "<ynab-code-mode>",
    }
    return await _run_in_subprocess(startup, dispatch=dispatch, ctx=ctx, timeout_s=timeout_s)


async def run_search(
    code: str,
    *,
    spec: list[dict],
    timeout_s: float = 10.0,
    max_output_chars: int = 8192,
) -> CodeModeResult:
    """Audit and execute ``code`` against a spec catalog (no live YNAB API access)."""
    try:
        # No ynab namespace here, so the mutation check is irrelevant.
        _audit_code(wrap_code(code), mutations_enabled=True)
    except CodeModeAuditError as exc:
        return CodeModeResult(ok=False, error=str(exc))

    startup = {
        "mode": "search",
        "code": code,
        "mutations_enabled": True,
        "max_output_chars": max_output_chars,
        "spec": spec,
        "filename": "<ynab-search>",
    }
    return await _run_in_subprocess(startup, dispatch={}, ctx=None, timeout_s=timeout_s)
