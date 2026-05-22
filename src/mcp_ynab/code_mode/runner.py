"""In-process Code Mode runner.

This runner is an opt-in convenience layer, not a Python security boundary.
It rejects common escape hatches before running snippets under a small
builtins allow-list and a gated ``ynab.read`` / ``ynab.write`` proxy.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import inspect
import io
import json
import textwrap
import traceback as traceback_module
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

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

SAFE_BUILTINS = {
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "False": False,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "None": None,
    "print": print,
    "range": range,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "True": True,
    "tuple": tuple,
    "zip": zip,
}


class CodeModeResult(BaseModel):
    """Structured result returned by ``ynab_code_execute``."""

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


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 0 or len(text) <= max_chars:
        return text, False
    suffix = "\n[... truncated]"
    keep = max(0, max_chars - len(suffix))
    return (text[:keep] + suffix)[:max_chars], True


def _serialize_result(result: Any) -> str:
    try:
        jsonable = to_jsonable_python(result)
    except Exception:
        jsonable = repr(result)

    try:
        return json.dumps(jsonable, ensure_ascii=True, sort_keys=True, default=str)
    except TypeError:
        return repr(result)


def _truncate_result(result: Any, max_chars: int) -> tuple[Any, bool]:
    if max_chars < 0:
        return result, False

    serialized = _serialize_result(result)
    if len(serialized) <= max_chars:
        return result, False

    preview, _ = _truncate(serialized, max_chars)
    return (
        {
            "truncated": True,
            "message": f"result exceeded {max_chars} characters and was truncated",
            "preview": preview,
        },
        True,
    )


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


def _bind_tool(tool: Any, ctx: Any):
    async def _bound(**kwargs: Any) -> Any:
        return await _call_tool(tool, ctx, kwargs)

    _bound.__name__ = tool.name
    _bound.__doc__ = tool.description
    return _bound


def _build_ynab_proxy(mcp: Any, ctx: Any, *, mutations_enabled: bool) -> SimpleNamespace:
    read = SimpleNamespace()
    write = SimpleNamespace()
    for name, tool in mcp._tool_manager._tools.items():
        if name == "ynab_code_execute":
            continue
        target = write if _is_mutating_tool(tool) else read
        setattr(target, name, _bind_tool(tool, ctx))

    if not mutations_enabled:
        write = _DisabledWriteNamespace()
    return SimpleNamespace(read=read, write=write)


class _DisabledWriteNamespace:
    def __getattr__(self, name: str) -> Any:
        raise PermissionError(f"mutations_disabled: ynab.write.{name} is not available")


def _wrap_code(code: str) -> str:
    indented = textwrap.indent(code.strip() or "return None", "    ")
    return f"async def __main__():\n{indented}\n"


async def run_code(
    code: str,
    *,
    mcp: Any,
    ctx: Any = None,
    mutations_enabled: bool = False,
    timeout_s: float = 10.0,
    max_output_chars: int = 8192,
) -> CodeModeResult:
    """Audit and execute ``code`` as the body of an async ``__main__`` function."""

    logs_buffer = io.StringIO()
    try:
        _audit_code(code, mutations_enabled=mutations_enabled)
        sandbox_globals = {
            "__builtins__": SAFE_BUILTINS,
            "LIMIT": 100,
            "ynab": _build_ynab_proxy(mcp, ctx, mutations_enabled=mutations_enabled),
        }
        compiled = compile(_wrap_code(code), "<ynab-code-mode>", "exec")
        exec(compiled, sandbox_globals, sandbox_globals)
        main = sandbox_globals["__main__"]
        with contextlib.redirect_stdout(logs_buffer):
            result = await asyncio.wait_for(main(), timeout=timeout_s)
        logs, logs_truncated = _truncate(logs_buffer.getvalue(), max_output_chars)
        result, result_truncated = _truncate_result(result, max_output_chars)
        return CodeModeResult(
            ok=True,
            result=result,
            logs=logs,
            truncated=logs_truncated or result_truncated,
        )
    except asyncio.TimeoutError:
        logs, truncated = _truncate(logs_buffer.getvalue(), max_output_chars)
        return CodeModeResult(ok=False, logs=logs, error="timeout", truncated=truncated)
    except CodeModeAuditError as exc:
        return CodeModeResult(ok=False, error=str(exc))
    except Exception as exc:
        logs, truncated = _truncate(logs_buffer.getvalue(), max_output_chars)
        return CodeModeResult(
            ok=False,
            logs=logs,
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback_module.format_exc(),
            truncated=truncated,
        )
