"""Code Mode MCP tool."""

from typing import Optional

from mcp.server.fastmcp import Context

from .. import server as _s
from ..code_mode import run_code


@_s.mcp.tool(annotations=_s.MUTATING_TOOL)
async def ynab_code_execute(
    code: str,
    timeout: Optional[float] = None,
    ctx: Optional[Context] = None,
) -> dict:
    """Execute a Python snippet against the gated ``ynab.read``/``ynab.write`` Code Mode API.

    Code Mode is disabled by default. Enable read-only calls with the
    ``code_mode_enabled`` preference. Mutating calls require the separate
    ``code_mode_mutations_enabled`` preference and must use ``ynab.write.*``.
    The snippet is treated as the body of an async function, so use
    ``await ynab.read.get_budgets()`` and ``return`` directly.
    """
    prefs = _s.ynab_resources.preferences
    if not prefs.code_mode_enabled:
        return {
            "ok": False,
            "error": "code_mode_disabled",
            "logs": "",
            "result": None,
            "traceback": None,
            "truncated": False,
        }

    timeout_s = prefs.code_mode_timeout_s
    if timeout is not None:
        timeout_s = min(max(timeout, 0.1), prefs.code_mode_timeout_s)

    result = await run_code(
        code,
        mcp=_s.mcp,
        ctx=ctx,
        mutations_enabled=prefs.code_mode_mutations_enabled,
        timeout_s=timeout_s,
        max_output_chars=prefs.code_mode_max_output_chars,
    )
    return result.model_dump(mode="json")
