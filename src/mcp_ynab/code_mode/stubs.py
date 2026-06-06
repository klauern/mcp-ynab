"""Generate Python type stubs for the Code Mode ``ynab`` namespace."""

from __future__ import annotations

import inspect
import typing
from typing import Any, Union

from pydantic.fields import FieldInfo

from .runner import _is_mutating_tool

_MAX_STUBS_BYTES = 5_000


def _unwrap_annotated(annotation: Any) -> Any:
    """Strip one level of Annotated[T, ...] → T."""
    if typing.get_origin(annotation) is typing.Annotated:
        return typing.get_args(annotation)[0]
    return annotation


def _annotation_name(annotation: Any) -> str:
    """Return a compact, human-readable representation of a type annotation."""
    if annotation is inspect.Signature.empty:
        return "Any"
    annotation = _unwrap_annotated(annotation)
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return f"{_annotation_name(non_none[0])} | None"
        return " | ".join(_annotation_name(a) for a in args)

    if origin is list:
        return f"list[{_annotation_name(args[0])}]" if args else "list"

    if origin is dict:
        if len(args) == 2:
            return f"dict[{_annotation_name(args[0])}, {_annotation_name(args[1])}]"
        return "dict"

    name = getattr(annotation, "__name__", None)
    if name:
        return name
    return str(annotation).replace("typing.", "")


def _field_description(param: inspect.Parameter) -> str | None:
    """Extract FieldInfo.description from Annotated metadata, if present."""
    ann = param.annotation
    if typing.get_origin(ann) is not typing.Annotated:
        return None
    for meta in typing.get_args(ann)[1:]:
        if isinstance(meta, FieldInfo) and meta.description:
            return meta.description
    return None


def _first_sentence(text: str, max_len: int = 100) -> str:
    """Return the first sentence of text, capped at max_len chars."""
    text = text.strip()
    for sep in (".\n", ". ", "\n\n", "\n"):
        idx = text.find(sep)
        if 0 < idx < max_len:
            return text[: idx + 1]
    return text[:max_len]


def _format_param(param: inspect.Parameter) -> str:
    annotation = _annotation_name(param.annotation)
    if param.default is inspect.Parameter.empty:
        return f"{param.name}: {annotation}"
    return f"{param.name}: {annotation} = ..."


def _format_tool_stub(tool: Any) -> list[str]:
    sig = inspect.signature(tool.fn)
    params = [_format_param(p) for p in sig.parameters.values() if p.name != "ctx"]
    method_params = ", ".join(["self", *params])
    return_type = _annotation_name(sig.return_annotation)

    summary = _first_sentence(tool.description or "")

    # Surface Field descriptions only for params that carry them; keep them brief.
    param_descs = []
    for p in sig.parameters.values():
        if p.name == "ctx":
            continue
        desc = _field_description(p)
        if desc:
            param_descs.append((p.name, desc[:80] + "…" if len(desc) > 80 else desc))

    sig_line = f"    async def {tool.name}({method_params}) -> {return_type}:"
    sig_lines = [sig_line]
    if len(sig_line) > 120:
        sig_lines = [f"    async def {tool.name}("]
        sig_lines.extend(f"        {param}," for param in ["self", *params])
        sig_lines.append(f"    ) -> {return_type}:")

    if not summary and not param_descs:
        return [*sig_lines[:-1], f"{sig_lines[-1]} ..."]

    if not param_descs:
        return [*sig_lines, f'        """{summary}"""', "        ..."]

    doc = [*sig_lines, f'        """{summary}', "", "        Args:"]
    for name, desc in param_descs:
        doc.append(f"            {name}: {desc}")
    doc.extend(['        """', "        ..."])
    return doc


def _stub_import_lines(lines: list[str]) -> list[str]:
    stub_text = "\n".join(lines)
    imports = []
    if "date" in stub_text:
        imports.append("from datetime import date")

    typing_names = ["Any"]
    if "Literal" in stub_text:
        typing_names.append("Literal")
    imports.append(f"from typing import {', '.join(typing_names)}")
    return imports


def _iter_mcp_tools(mcp: Any) -> list[tuple[str, Any]]:
    # FastMCP has no stable public tool-enumeration API yet; isolate the
    # private access here so an mcp upgrade only needs one fix point.
    return sorted(mcp._tool_manager._tools.items())


def generate_stubs(mcp: Any, *, mutations_enabled: bool = True) -> str:
    """Return a ``.pyi`` view of registered tools under ``ynab.read``/``ynab.write``."""

    read_lines: list[str] = []
    write_lines: list[str] = []
    for name, tool in _iter_mcp_tools(mcp):
        if name in {"execute", "search"}:
            continue
        target = write_lines if _is_mutating_tool(tool) else read_lines
        target.extend(_format_tool_stub(tool))
        target.append("")

    body_lines = [
        "",
        "class ReadNamespace:",
        *(read_lines or ["    pass"]),
        "class WriteNamespace:",
        *(write_lines if mutations_enabled else ["    pass"]),
        "class YNABNamespace:",
        "    read: ReadNamespace",
        "    write: WriteNamespace",
        "",
        "ynab: YNABNamespace",
        "LIMIT: int",
        "",
    ]
    lines = ["# fmt: off", *_stub_import_lines(body_lines), *body_lines]
    return "\n".join(lines)


def build_spec(mcp: Any, *, mutations_enabled: bool = True) -> list[dict]:
    """Return a structured catalog of available tools for the search sandbox."""
    entries = []
    for name, tool in _iter_mcp_tools(mcp):
        if name in {"execute", "search"}:
            continue
        namespace = "write" if _is_mutating_tool(tool) else "read"
        if not mutations_enabled and namespace == "write":
            continue
        sig = inspect.signature(tool.fn)
        params = [_format_param(p) for p in sig.parameters.values() if p.name != "ctx"]
        entries.append(
            {
                "name": name,
                "namespace": namespace,
                "signature": ", ".join(params),
                "doc": _first_sentence(tool.description or ""),
                "returns": _annotation_name(sig.return_annotation),
            }
        )
    return entries
