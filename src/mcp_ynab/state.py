"""File-backed user preferences and category cache for the YNAB MCP server.

`YNABResources` persists the user's preferred budget id and a per-budget
category cache under the resolved config directory. `Preferences` is the
typed Pydantic model for ``preferences.json`` (the 3 user-configurable
fields). The category cache lives in a separate ``category_cache.json``
under the same dir, keyed by budget id with `{last_refreshed, records}`
envelopes so we can answer freshness questions without refetching. JSON
helpers tolerate missing files (return `{}`) and corrupt files (warn +
return `{}`) so first-run and recovery paths just work.

A one-shot migration runs on `YNABResources.__init__` if the legacy
`preferred_budget_id.json` + `budget_category_cache.json` pair exists
and `preferences.json` does not — fold both into the new layout, then
unlink the old files. Migrated cache entries get `last_refreshed=None`
so :meth:`YNABResources.is_cache_stale` returns True on first read,
forcing a fresh fetch instead of trusting an unknown-age timestamp.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import mcp.types as types
from pydantic import BaseModel, Field

from .client import _resolve_config_dir

logger = logging.getLogger(__name__)

PREFERENCES_FILENAME = "preferences.json"
CATEGORY_CACHE_FILENAME = "category_cache.json"
PAYEES_CACHE_FILENAME = "payees_cache.json"
LEGACY_PREFERRED_BUDGET_FILENAME = "preferred_budget_id.json"
LEGACY_CATEGORY_CACHE_FILENAME = "budget_category_cache.json"
PREF_ENV_PREFIX = "MCP_YNAB_"

_TRUTHY_STR = frozenset({"1", "true", "yes", "on"})
_FALSY_STR = frozenset({"0", "false", "no", "off"})


def _load_json_file(filename: str | Path) -> Dict[str, Any]:
    """Load JSON data from a file."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        logger.warning("Failed to decode JSON from %s: %s", filename, exc)
        return {}


def _save_json_file(filename: str | Path, data: Dict[str, Any]) -> None:
    """Save JSON data to a file."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _atomic_write_json(filename: Path, data: Dict[str, Any]) -> None:
    """Write JSON to ``filename`` atomically via a sibling tempfile + os.replace.

    ``default=str`` coerces non-JSON-native values (notably ``uuid.UUID`` ids
    from ynab >=2.x ``Category.to_dict()``, which ``json.dump`` would otherwise
    reject) to their string form — cache records are str-compared on read.
    """
    tmp = filename.with_suffix(filename.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, filename)


def _parse_bool_value(raw: str, name: str) -> bool:
    """Parse a bool from a string. Accepts 1/0, true/false, yes/no, on/off (any case)."""
    lowered = raw.strip().lower()
    if lowered in _TRUTHY_STR:
        return True
    if lowered in _FALSY_STR:
        return False
    raise ValueError(
        f"Value {raw!r} for {name} is not a recognised boolean. "
        f"Use one of: {sorted(_TRUTHY_STR | _FALSY_STR)}."
    )


class Preferences(BaseModel):
    """User preferences persisted to ``preferences.json`` under the config dir.

    Three fields, deliberately flat — this is not pydantic-settings; env
    overlay is handled explicitly in :func:`load_preferences` so we can keep
    grammar tight and error messages domain-specific without a new dep.
    """

    default_budget_id: Optional[str] = Field(
        default=None,
        description="Preferred YNAB budget id used when a tool does not specify one.",
    )
    category_cache_ttl_minutes: int = Field(
        default=10080,  # 7 days
        ge=0,
        description="How long the per-budget category cache stays fresh, in minutes.",
    )
    confirm_before_post: bool = Field(
        default=True,
        description="Whether mutating tools must elicit a confirmation before posting.",
    )
    code_mode_enabled: bool = Field(
        default=True,
        description="Whether the Code Mode search and execute tools are enabled.",
    )
    code_mode_mutations_enabled: bool = Field(
        default=False,
        description="Whether Code Mode may call mutating YNAB helpers.",
    )
    code_mode_replace_tools: bool = Field(
        default=True,
        description=(
            "When True (default), only search and execute are exposed as tools. "
            "Set False to restore the full direct-tool surface (escape hatch)."
        ),
    )
    code_mode_timeout_s: float = Field(
        default=10.0,
        gt=0,
        le=60.0,
        description="Maximum Code Mode execution timeout, in seconds.",
    )
    code_mode_max_output_chars: int = Field(
        default=8192,
        ge=0,
        description="Maximum captured stdout characters returned from Code Mode.",
    )


def _is_optional_field(field_name: str) -> bool:
    """Return True if ``field_name``'s annotation includes None (i.e. ``Optional[...]``)."""
    annotation = Preferences.model_fields[field_name].annotation
    return type(None) in getattr(annotation, "__args__", ())


def _coerce_field_value(field_name: str, raw: str) -> Any:
    """Coerce ``raw`` to ``field_name``'s declared type. Raises ValueError on bad input.

    Empty strings are treated as ``None`` for Optional fields (the documented
    way to clear ``default_budget_id``); empty strings on a required field are
    a coercion error and surface as such. Used by both the env overlay and the
    ``set_preference`` MCP tool — keep the bool/int branch logic in one place.
    """
    if raw == "" and _is_optional_field(field_name):
        return None

    annotation = Preferences.model_fields[field_name].annotation
    if annotation is bool or annotation == Optional[bool]:
        return _parse_bool_value(raw, field_name)
    if annotation is int or annotation == Optional[int]:
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"Value {raw!r} for {field_name} is not an integer.") from exc
    if annotation is float or annotation == Optional[float]:
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError(f"Value {raw!r} for {field_name} is not a number.") from exc
    return raw


def _apply_env_overlay(data: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay ``MCP_YNAB_*`` env vars on top of ``data`` (env wins). Returns new dict."""
    merged: Dict[str, Any] = dict(data)
    for field_name in Preferences.model_fields:
        env_name = PREF_ENV_PREFIX + field_name.upper()
        raw = os.getenv(env_name)
        if raw is None or raw == "":
            # Treat an unset *or* empty env var the same. A .env file with
            # `MCP_YNAB_CONFIRM_BEFORE_POST=` (no value) should not crash the
            # bool parser, and it should not coerce default_budget_id to "".
            continue
        try:
            merged[field_name] = _coerce_field_value(field_name, raw)
        except ValueError as exc:
            # Re-raise with the env var name so users see WHERE the bad value came from.
            raise ValueError(f"Environment variable {env_name}: {exc}") from exc
    return merged


def load_preferences(config_dir: Optional[Path] = None) -> Preferences:
    """Load preferences with source order: env > preferences.json > defaults.

    ``config_dir`` resolves through :func:`_resolve_config_dir` so callers
    in tests can pass a ``tmp_path`` and avoid touching the user's real
    XDG config. Pydantic validation errors propagate; corrupt JSON degrades
    to "as if the file were missing" (warn + defaults), matching the
    forgiving stance of the existing JSON helpers.
    """
    resolved = _resolve_config_dir(config_dir)
    path = resolved / PREFERENCES_FILENAME
    on_disk = _load_json_file(path)
    merged = _apply_env_overlay(on_disk)
    return Preferences.model_validate(merged)


def save_preferences(prefs: Preferences, config_dir: Optional[Path] = None) -> None:
    """Atomically persist ``prefs`` to ``preferences.json`` under the config dir."""
    resolved = _resolve_config_dir(config_dir)
    path = resolved / PREFERENCES_FILENAME
    _atomic_write_json(path, prefs.model_dump(mode="json"))


def _utcnow_iso() -> str:
    """Timezone-aware UTC ISO-8601 timestamp; one place so tests can monkeypatch it."""
    return datetime.now(timezone.utc).isoformat()


class YNABResources:
    """File-backed store for user preferences and the budget category cache.

    Public API (signatures preserved across the 6ha refactor):
        - get_preferred_budget_id / set_preferred_budget_id
        - get_cached_category_records / get_cached_categories / cache_categories
        - is_cache_stale (new in 6ha.3)
        - preferences (read/write the typed model)
    """

    def __init__(self, config_dir: Optional[Path] = None):
        """Resolve ``config_dir``, run one-shot legacy migration, then load state."""
        self._config_dir = _resolve_config_dir(config_dir)
        self._preferences_file = self._config_dir / PREFERENCES_FILENAME
        self._category_cache_file = self._config_dir / CATEGORY_CACHE_FILENAME
        self._payees_cache_file = self._config_dir / PAYEES_CACHE_FILENAME
        self._migrate_legacy_files_if_needed()
        self._preferences: Preferences = load_preferences(self._config_dir)
        # On-disk shape: {budget_id: {"last_refreshed": iso8601 | None, "records": [...]}}.
        self._category_cache: Dict[str, Dict[str, Any]] = _load_json_file(self._category_cache_file)
        self._payees_cache: Dict[str, Dict[str, Any]] = _load_json_file(self._payees_cache_file)

    def _migrate_legacy_files_if_needed(self) -> None:
        """One-shot lift of legacy files into the new layout. Idempotent.

        Skipped entirely when ``preferences.json`` already exists — that's the
        signal that migration has already run (or never had legacy data to
        begin with). When migrating: legacy ``preferred_budget_id.json``
        becomes ``preferences.json[default_budget_id]``; each budget's bare
        records list becomes ``{last_refreshed: None, records: [...]}`` so
        :meth:`is_cache_stale` flags it stale on next read instead of
        trusting an unknown-age timestamp.
        """
        if self._preferences_file.exists():
            return
        legacy_prefs = self._config_dir / LEGACY_PREFERRED_BUDGET_FILENAME
        legacy_cache = self._config_dir / LEGACY_CATEGORY_CACHE_FILENAME
        if not legacy_prefs.exists() and not legacy_cache.exists():
            return

        migrated_budget_id: Optional[str] = None
        if legacy_prefs.exists():
            try:
                migrated_budget_id = legacy_prefs.read_text(encoding="utf-8").strip() or None
            except OSError as exc:
                logger.warning("Failed to read legacy preferred_budget_id: %s", exc)

        save_preferences(
            Preferences(default_budget_id=migrated_budget_id),
            config_dir=self._config_dir,
        )

        if legacy_cache.exists():
            legacy_records = _load_json_file(legacy_cache)
            wrapped: Dict[str, Dict[str, Any]] = {
                budget_id: {"last_refreshed": None, "records": list(records)}
                for budget_id, records in legacy_records.items()
            }
            _atomic_write_json(self._category_cache_file, wrapped)

        for path in (legacy_prefs, legacy_cache):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("Failed to remove legacy file %s: %s", path, exc)

        logger.info("Migrated legacy YNAB resource files to %s", self._preferences_file.name)

    @property
    def preferences(self) -> Preferences:
        """Return the in-memory typed preferences (read-only view; use ``save_preferences`` to persist)."""
        return self._preferences

    def reload_preferences(self) -> Preferences:
        """Re-read preferences.json from disk. For tests that mutate the file directly."""
        self._preferences = load_preferences(self._config_dir)
        return self._preferences

    def update_preferences(self, **fields: Any) -> Preferences:
        """Validate ``fields``, persist a new Preferences, and return it. Raises on validation error."""
        merged = self._preferences.model_copy(update=fields)
        # Re-validate via model_validate to surface ge=0 / type errors that
        # model_copy alone would silently accept.
        validated = Preferences.model_validate(merged.model_dump())
        save_preferences(validated, config_dir=self._config_dir)
        self._preferences = validated
        return validated

    def get_preferred_budget_id(self) -> Optional[str]:
        """Get the preferred budget ID (alias for ``preferences.default_budget_id``)."""
        return self._preferences.default_budget_id

    def set_preferred_budget_id(self, budget_id: str) -> None:
        """Set the preferred budget ID and persist preferences.json."""
        self.update_preferences(default_budget_id=budget_id)

    def _records_for(self, budget_id: str) -> List[Dict[str, Any]]:
        """Return the bare records list for ``budget_id`` (envelope-aware)."""
        envelope = self._category_cache.get(budget_id)
        if not envelope:
            return []
        return list(envelope.get("records", []))

    def get_cached_category_records(self, budget_id: str) -> List[Dict[str, Any]]:
        """Return raw cached category records ({id, name, group}) for a budget."""
        return self._records_for(budget_id)

    def get_cached_categories(self, budget_id: str) -> list[types.TextContent]:
        """Get categories from the cache formatted for MCP resources."""
        contents: list[types.TextContent] = []
        for cat in self._records_for(budget_id):
            name = cat.get("name", "Unnamed")
            cat_id = cat.get("id", "N/A")
            group = cat.get("group")
            if group:
                text = f"{name} [{group}] (ID: {cat_id})"
            else:
                text = f"{name} (ID: {cat_id})"
            contents.append(types.TextContent(type="text", text=text))
        return contents

    def cache_categories(self, budget_id: str, categories: List[Dict[str, Any]]) -> None:
        """Cache categories for a budget ID, stamping ``last_refreshed`` to now."""
        self._category_cache[budget_id] = {
            "last_refreshed": _utcnow_iso(),
            "records": [
                {
                    "id": cat.get("id"),
                    "name": cat.get("name"),
                    "group": cat.get("category_group_name"),
                }
                for cat in categories
            ],
        }
        _atomic_write_json(self._category_cache_file, self._category_cache)

    def get_cached_payee_records(self, budget_id: str) -> List[Dict[str, Any]]:
        """Return raw cached payee records ({id, name, transfer_account_id}) for a budget."""
        envelope = self._payees_cache.get(budget_id)
        if not envelope:
            return []
        return list(envelope.get("records", []))

    def cache_payees(self, budget_id: str, payees: List[Dict[str, Any]]) -> None:
        """Cache payees for a budget ID, stamping ``last_refreshed`` to now."""
        self._payees_cache[budget_id] = {
            "last_refreshed": _utcnow_iso(),
            "records": [
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "transfer_account_id": p.get("transfer_account_id"),
                }
                for p in payees
            ],
        }
        _atomic_write_json(self._payees_cache_file, self._payees_cache)

    def get_last_refreshed(self, budget_id: str) -> Optional[datetime]:
        """Return when ``budget_id`` was last refreshed, or None if never (or no entry)."""
        envelope = self._category_cache.get(budget_id)
        if not envelope:
            return None
        raw = envelope.get("last_refreshed")
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            logger.warning("Unparseable last_refreshed timestamp for %s: %r", budget_id, raw)
            return None

    def is_cache_stale(self, budget_id: str, ttl_minutes: Optional[int] = None) -> bool:
        """Return True when the cache for ``budget_id`` is missing or older than ``ttl_minutes``.

        ``ttl_minutes`` defaults to ``preferences.category_cache_ttl_minutes``.
        Missing entry, ``last_refreshed=None``, or any unparseable timestamp
        all evaluate as stale — caller should refresh.
        """
        if ttl_minutes is None:
            ttl_minutes = self._preferences.category_cache_ttl_minutes
        last = self.get_last_refreshed(budget_id)
        if last is None:
            return True
        # `datetime.fromisoformat` returns aware-or-naive matching its input;
        # we always write tz-aware via _utcnow_iso, so compare against aware now.
        now = datetime.now(timezone.utc)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_minutes = (now - last).total_seconds() / 60
        return age_minutes > ttl_minutes
