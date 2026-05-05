"""User-preference MCP tools (API key, future: settings get/set).

Tools here go through `server` module attribute lookup so tests can patch
the underlying helpers via `monkeypatch.setattr(server, "<name>", ...)`.
"""

from .. import server as _s


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def set_api_key(api_key: str) -> str:
    """Store a YNAB personal access token in the OS keychain.

    The token is persisted in the platform keychain (macOS Keychain,
    Windows Credential Locker, or Linux Secret Service) under
    service=``mcp-ynab`` user=``YNAB_API_KEY``. After this returns, future
    requests in this process and future runs will pick the key up via
    ``_resolve_api_key`` — though the ``YNAB_API_KEY`` env var still wins
    if it is set.
    """
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API key must be a non-empty string.")
    _s._store_api_key(api_key)
    return "YNAB API key stored in OS keychain."


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def clear_api_key() -> str:
    """Remove the stored YNAB API key from the OS keychain (env var unaffected)."""
    removed = _s._delete_stored_api_key()
    if removed:
        return "YNAB API key removed from OS keychain."
    return "No YNAB API key was stored in the OS keychain."
