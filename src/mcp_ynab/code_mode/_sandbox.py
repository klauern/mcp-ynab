"""Pure sandbox primitives shared by the parent runner and the subprocess worker.

This module is deliberately leaf-level: it imports only the standard library and
``pydantic_core``. It MUST NOT import :mod:`mcp_ynab.server` (or anything that
transitively does), because the Code Mode worker loads it by file path inside a
fresh, side-effect-free subprocess. Pulling in the server would build the FastMCP
app and demand a YNAB API key in the child — exactly what subprocess isolation
exists to avoid.
"""

from __future__ import annotations

import asyncio
import io
import json
import textwrap
from typing import Any

from pydantic_core import to_jsonable_python


def encode_frame(obj: Any) -> bytes:
    """Encode a JSON object as a length-prefixed frame: ``<byte-length>\\n<payload>``.

    Length prefixing (rather than newline-delimited JSON) lets the reader pull
    the exact payload with ``readexactly``, which is not bounded by asyncio's
    64KB line-buffer limit. A single tool result can exceed that easily.
    """
    payload = json.dumps(obj, ensure_ascii=True, default=str).encode()
    return str(len(payload)).encode() + b"\n" + payload


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one length-prefixed frame, or ``None`` at clean EOF."""
    header = await reader.readline()
    if not header:
        return None
    try:
        length = int(header)
    except ValueError as exc:
        raise RuntimeError(f"bad_frame_header: {header!r}") from exc
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise RuntimeError("incomplete_frame") from exc
    return json.loads(payload)


# Builtins exposed to user snippets. References to the real builtin callables so
# the child does not need to reconstruct them.
SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "False": False,
    "float": float,
    "hasattr": hasattr,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "None": None,
    "print": print,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "True": True,
    "tuple": tuple,
    "zip": zip,
}


def wrap_code(code: str) -> str:
    """Wrap a snippet as the body of an async ``__main__`` coroutine."""
    indented = textwrap.indent(code.strip() or "return None", "    ")
    return f"async def __main__():\n{indented}\n"


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 0 or len(text) <= max_chars:
        return text, False
    suffix = "\n[... truncated]"
    keep = max(0, max_chars - len(suffix))
    return (text[:keep] + suffix)[:max_chars], True


def serialize_result(result: Any) -> str:
    try:
        jsonable = to_jsonable_python(result)
    except Exception:
        jsonable = repr(result)

    try:
        return json.dumps(jsonable, ensure_ascii=True, sort_keys=True, default=str)
    except TypeError:
        return repr(result)


def truncate_result(result: Any, max_chars: int) -> tuple[Any, bool]:
    serialized = serialize_result(result)
    try:
        json_safe = json.loads(serialized)
    except json.JSONDecodeError:
        json_safe = serialized

    if max_chars < 0:
        return json_safe, False
    if len(serialized) <= max_chars:
        return json_safe, False

    preview, _ = truncate(serialized, max_chars)
    return (
        {
            "truncated": True,
            "message": f"result exceeded {max_chars} characters and was truncated",
            "preview": preview,
        },
        True,
    )


class BoundedStringIO(io.TextIOBase):
    """TextIO that stops accepting writes once ``max_chars`` is reached.

    Keeps memory usage bounded during execution rather than truncating the full
    buffer after the fact.
    """

    def __init__(self, max_chars: int) -> None:
        self._buf = io.StringIO()
        # Negative means unlimited; use a large sentinel so write() logic is unchanged.
        self._remaining = max_chars if max_chars >= 0 else 10**18
        self.truncated = False

    def write(self, s: str) -> int:
        if self._remaining <= 0:
            self.truncated = True
            return len(s)
        if len(s) > self._remaining:
            self._buf.write(s[: self._remaining])
            self._remaining = 0
            self.truncated = True
        else:
            self._buf.write(s)
            self._remaining -= len(s)
        return len(s)

    def getvalue(self) -> str:
        val = self._buf.getvalue()
        return val + "\n[... truncated]" if self.truncated else val
