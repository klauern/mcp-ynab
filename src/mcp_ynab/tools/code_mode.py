"""Code Mode MCP tool."""

from typing import Optional

from mcp.server.fastmcp import Context

from .. import server as _s
from ..code_mode import build_spec, run_code, run_search


@_s.mcp.tool(annotations=_s.MUTATING_TOOL)
async def execute(
    code: str,
    timeout: Optional[float] = None,
    ctx: Optional[Context] = None,
) -> dict:
    """Execute a Python snippet against the gated ``ynab.read``/``ynab.write`` Code Mode API.

    Enabled by default. Mutating calls require ``code_mode_mutations_enabled`` preference
    and must use ``ynab.write.*``. The snippet is treated as the body of an async function:
    ``await ynab.read.get_budgets()``, ``return`` directly.
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


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def search(
    code: str,
    ctx: Optional[Context] = None,
) -> dict:
    """Discover available YNAB tools by running a Python snippet against the tool catalog.

    The snippet runs in a sandboxed environment with a ``spec`` variable — a list of dicts,
    each with keys ``name``, ``namespace`` (``"read"`` or ``"write"``), ``signature``,
    ``doc``, and ``returns``. Filter or map ``spec`` and return the subset you need.

    Example: ``return [t for t in spec if "transaction" in t["name"]]``

    No live YNAB API access. Returns the same CodeModeResult shape as execute.
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

    spec = build_spec(_s.mcp, mutations_enabled=prefs.code_mode_mutations_enabled)
    result = await run_search(
        code,
        spec=spec,
        timeout_s=prefs.code_mode_timeout_s,
        max_output_chars=prefs.code_mode_max_output_chars,
    )
    return result.model_dump(mode="json")
