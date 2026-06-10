"""Code Mode subprocess worker (the isolated child process).

The parent (:mod:`mcp_ynab.code_mode.runner`) spawns this script by **file
path** — never as ``python -m mcp_ynab.code_mode._worker`` — so that importing
the ``mcp_ynab`` package (which pulls in the FastMCP server and a YNAB API key)
never happens in the child. The only project code this module touches is the
leaf ``_sandbox`` module, loaded directly by path below.

Protocol (newline-delimited JSON, one frame per line):

  parent -> child stdin:
    startup:      {"mode":"code"|"search","code":..,"mutations_enabled":bool,
                   "max_output_chars":int,"read":[names],"write":[names],"spec":[...]}
    rpc response: {"type":"rpc_result","id":N,"ok":bool,"result":..,"error":..}

  child -> parent stdout (these frames ONLY — user print() is captured separately):
    rpc request:  {"type":"rpc","id":N,"namespace":"read"|"write","method":..,"kwargs":{}}
    final:        {"type":"result","ok":..,"result":..,"logs":..,"error":..,
                   "traceback":..,"truncated":..}
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import os
import sys
import traceback as traceback_module
from types import SimpleNamespace
from typing import Any

# --- Load the leaf sandbox module by path (no mcp_ynab package import) --------
_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("_cm_sandbox", os.path.join(_HERE, "_sandbox.py"))
assert _spec is not None and _spec.loader is not None
_sandbox = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sandbox)

SAFE_BUILTINS = _sandbox.SAFE_BUILTINS
BoundedStringIO = _sandbox.BoundedStringIO
truncate_result = _sandbox.truncate_result
wrap_code = _sandbox.wrap_code
encode_frame = _sandbox.encode_frame
read_frame = _sandbox.read_frame

# Real stdout buffer, captured before user code can redirect sys.stdout. ALL
# protocol frames go here as raw bytes; user print() is redirected to an
# in-memory buffer instead, so it can never corrupt the framing.
_OUT = sys.stdout.buffer


def _send(frame: dict[str, Any]) -> None:
    _OUT.write(encode_frame(frame))
    _OUT.flush()


class _RpcBridge:
    """Serializes tool calls into request/response round-trips with the parent."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader
        self._lock = asyncio.Lock()
        self._next_id = 0

    async def call(self, namespace: str, method: str, kwargs: dict[str, Any]) -> Any:
        async with self._lock:
            self._next_id += 1
            call_id = self._next_id
            _send(
                {
                    "type": "rpc",
                    "id": call_id,
                    "namespace": namespace,
                    "method": method,
                    "kwargs": kwargs,
                }
            )
            response = await read_frame(self._reader)
            if response is None:
                raise RuntimeError("rpc_channel_closed: parent did not respond")
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "rpc_error")
        return response.get("result")


class _DisabledWriteNamespace:
    def __getattr__(self, name: str) -> Any:
        raise PermissionError(f"mutations_disabled: ynab.write.{name} is not available")


def _make_stub(bridge: _RpcBridge, namespace: str, method: str):
    async def _stub(**kwargs: Any) -> Any:
        return await bridge.call(namespace, method, kwargs)

    _stub.__name__ = method
    return _stub


def _build_ynab_proxy(
    bridge: _RpcBridge,
    read_names: list[str],
    write_names: list[str],
    *,
    mutations_enabled: bool,
) -> SimpleNamespace:
    read = SimpleNamespace()
    for name in read_names:
        setattr(read, name, _make_stub(bridge, "read", name))

    if mutations_enabled:
        write: Any = SimpleNamespace()
        for name in write_names:
            setattr(write, name, _make_stub(bridge, "write", name))
    else:
        write = _DisabledWriteNamespace()

    return SimpleNamespace(read=read, write=write)


async def _run(startup: dict[str, Any], reader: asyncio.StreamReader) -> dict[str, Any]:
    code = startup["code"]
    max_output_chars = startup["max_output_chars"]
    logs_buffer = BoundedStringIO(max_output_chars)

    extra_globals: dict[str, Any] = {}
    if startup["mode"] == "search":
        extra_globals["spec"] = startup.get("spec", [])
    else:
        bridge = _RpcBridge(reader)
        extra_globals["ynab"] = _build_ynab_proxy(
            bridge,
            startup.get("read", []),
            startup.get("write", []),
            mutations_enabled=startup["mutations_enabled"],
        )

    try:
        wrapped_code = wrap_code(code)
        sandbox_globals = {"__builtins__": SAFE_BUILTINS, "LIMIT": 100, **extra_globals}
        compiled = compile(wrapped_code, startup.get("filename", "<ynab-code-mode>"), "exec")
        exec(compiled, sandbox_globals, sandbox_globals)  # noqa: S102
        main = sandbox_globals["__main__"]
        with contextlib.redirect_stdout(logs_buffer):
            result = await main()
        logs = logs_buffer.getvalue()
        logs_truncated = logs_buffer.truncated
        result, result_truncated = truncate_result(result, max_output_chars)
        return {
            "type": "result",
            "ok": True,
            "result": result,
            "logs": logs,
            "error": None,
            "traceback": None,
            "truncated": logs_truncated or result_truncated,
        }
    except Exception as exc:  # noqa: BLE001 — mirror parent's catch-all boundary
        logs = logs_buffer.getvalue()
        return {
            "type": "result",
            "ok": False,
            "result": None,
            "logs": logs,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback_module.format_exc(),
            "truncated": logs_buffer.truncated,
        }


async def _main() -> None:
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    startup = await read_frame(reader)
    if startup is None:
        return
    frame = await _run(startup, reader)
    _send(frame)


if __name__ == "__main__":
    asyncio.run(_main())
