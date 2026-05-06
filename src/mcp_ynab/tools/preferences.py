"""User-preference MCP tools: API key + the typed Preferences model.

Tools here go through `server` module attribute lookup so tests can patch
the underlying helpers via `monkeypatch.setattr(server, "<name>", ...)`.
"""

from .. import server as _s
from ..formatters import _build_markdown_table
from ..state import Preferences, _coerce_field_value


def _format_preferences_markdown(prefs: Preferences) -> str:
    """Render preferences as a 2-column markdown table — matches the resource style."""
    rows = [
        [name, "" if getattr(prefs, name) is None else str(getattr(prefs, name))]
        for name in Preferences.model_fields
    ]
    return "# YNAB MCP Preferences\n\n" + _build_markdown_table(rows, ["Name", "Value"])


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


@_s.mcp.tool(annotations=_s.READ_ONLY_TOOL)
async def get_preferences() -> str:
    """Return the current YNAB MCP preferences as a markdown table.

    Reads the in-memory ``ynab_resources.preferences`` (which already reflects
    the env > preferences.json > defaults source order). The same content is
    available as a resource at ``ynab://preferences`` for clients that prefer
    to read it that way.
    """
    return _format_preferences_markdown(_s.ynab_resources.preferences)


@_s.mcp.tool(annotations=_s.IDEMPOTENT_MUTATING_TOOL)
async def set_preference(name: str, value: str) -> str:
    """Set a single preference and persist it to ``preferences.json``.

    ``name`` must be one of the ``Preferences`` model fields:
    ``default_budget_id``, ``category_cache_ttl_minutes``, or
    ``confirm_before_post``. Bool values accept any of
    ``1/0/true/false/yes/no/on/off`` (case-insensitive). The empty string is
    the documented way to clear ``default_budget_id`` (stored as ``None``);
    on a non-Optional field it surfaces as a coercion error.

    Note: changes to ``default_budget_id`` here are equivalent to calling
    ``set_preferred_budget_id``; both write the same field.
    """
    if name not in Preferences.model_fields:
        valid = ", ".join(sorted(Preferences.model_fields))
        raise ValueError(f"Unknown preference {name!r}. Valid names: {valid}.")
    coerced = _coerce_field_value(name, value)
    updated = _s.ynab_resources.update_preferences(**{name: coerced})
    stored = getattr(updated, name)
    return f"Set {name} = {stored!r}."
