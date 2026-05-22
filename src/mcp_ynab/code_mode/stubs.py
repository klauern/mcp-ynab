"""Generate Python type stubs for the Code Mode ``ynab`` namespace."""

from __future__ import annotations

import inspect
from typing import Any

from .runner import _is_mutating_tool


def _annotation_name(annotation: Any) -> str:
    if annotation is inspect.Signature.empty:
        return "Any"
    text = getattr(annotation, "__name__", None)
    if text:
        return text
    return str(annotation).replace("typing.", "")


def _format_param(param: inspect.Parameter) -> str:
    annotation = _annotation_name(param.annotation)
    if param.default is inspect.Parameter.empty:
        return f"{param.name}: {annotation}"
    return f"{param.name}: {annotation} = ..."


def _format_tool_stub(tool: Any) -> list[str]:
    sig = inspect.signature(tool.fn)
    params = [_format_param(param) for param in sig.parameters.values() if param.name != "ctx"]
    return_type = _annotation_name(sig.return_annotation)
    description = (tool.description or "").replace('"""', '\\"\\"\\"').strip()
    if description:
        return [
            f"    async def {tool.name}({', '.join(params)}) -> {return_type}:",
            f'        """{description}"""',
            "        ...",
        ]
    return [f"    async def {tool.name}({', '.join(params)}) -> {return_type}: ..."]


def generate_stubs(mcp: Any, *, mutations_enabled: bool = True) -> str:
    """Return a ``.pyi`` view of registered tools under ``ynab.read``/``ynab.write``."""

    read_lines: list[str] = []
    write_lines: list[str] = []
    for name, tool in sorted(mcp._tool_manager._tools.items()):
        if name == "ynab_code_execute":
            continue
        target = write_lines if _is_mutating_tool(tool) else read_lines
        target.extend(_format_tool_stub(tool))
        target.append("")

    lines = [
        "from typing import Any",
        "",
        "class ReadNamespace:",
        *(read_lines or ["    pass"]),
        "class WriteNamespace:",
        *(write_lines if mutations_enabled else ["    pass"]),
        "class YNABNamespace:",
        "    read: ReadNamespace",
        "    write: WriteNamespace",
        "ynab: YNABNamespace",
        "LIMIT: int",
        "",
    ]
    return "\n".join(lines)
