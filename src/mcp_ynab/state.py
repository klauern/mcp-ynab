"""File-backed user preferences and category cache for the YNAB MCP server.

`YNABResources` persists the user's preferred budget id and a per-budget
category cache under the resolved config directory. `Preferences` is the
typed Pydantic model for the unified preferences.json file (added in the
6ha epic; YNABResources will migrate onto it in 6ha.3). JSON helpers
tolerate missing files (return `{}`) and corrupt files (warn + return
`{}`) so first-run and recovery paths just work.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import mcp.types as types
from pydantic import BaseModel, Field

from .client import _resolve_config_dir

logger = logging.getLogger(__name__)

PREFERENCES_FILENAME = "preferences.json"
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
    """Write JSON to ``filename`` atomically via a sibling tempfile + os.replace."""
    tmp = filename.with_suffix(filename.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, filename)


def _parse_env_bool(raw: str, name: str) -> bool:
    """Parse a bool from an env string. Accepts 1/0, true/false, yes/no, on/off (any case)."""
    lowered = raw.strip().lower()
    if lowered in _TRUTHY_STR:
        return True
    if lowered in _FALSY_STR:
        return False
    raise ValueError(
        f"Environment variable {name}={raw!r} is not a recognised boolean. "
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
        annotation = Preferences.model_fields[field_name].annotation
        if annotation is bool or annotation == Optional[bool]:
            merged[field_name] = _parse_env_bool(raw, env_name)
        elif annotation is int or annotation == Optional[int]:
            try:
                merged[field_name] = int(raw)
            except ValueError as exc:
                raise ValueError(
                    f"Environment variable {env_name}={raw!r} is not an integer."
                ) from exc
        else:
            merged[field_name] = raw
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


class YNABResources:
    """File-backed store for user preferences and the budget category cache."""

    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize and load any persisted state from `config_dir`."""
        self._config_dir = _resolve_config_dir(config_dir)
        self._preferred_budget_id_file = self._config_dir / "preferred_budget_id.json"
        self._budget_category_cache_file = self._config_dir / "budget_category_cache.json"
        self._preferred_budget_id: Optional[str] = None
        self._category_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._load_data()

    def _load_data(self) -> None:
        """Load data from files."""
        try:
            with open(self._preferred_budget_id_file, "r", encoding="utf-8") as f:
                self._preferred_budget_id = f.read().strip() or None
        except FileNotFoundError:
            self._preferred_budget_id = None

        self._category_cache = _load_json_file(self._budget_category_cache_file)

    def get_preferred_budget_id(self) -> Optional[str]:
        """Get the preferred budget ID."""
        return self._preferred_budget_id

    def set_preferred_budget_id(self, budget_id: str) -> None:
        """Set the preferred budget ID."""
        self._preferred_budget_id = budget_id
        with open(self._preferred_budget_id_file, "w", encoding="utf-8") as f:
            f.write(budget_id)

    def get_cached_category_records(self, budget_id: str) -> List[Dict[str, Any]]:
        """Return raw cached category records ({id, name, group}) for a budget."""
        return list(self._category_cache.get(budget_id, []))

    def get_cached_categories(self, budget_id: str) -> list[types.TextContent]:
        """Get categories from the cache formatted for MCP resources."""
        cached_categories = self._category_cache.get(budget_id, [])
        contents: list[types.TextContent] = []
        for cat in cached_categories:
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
        """Cache categories for a budget ID."""
        self._category_cache[budget_id] = [
            {
                "id": cat.get("id"),
                "name": cat.get("name"),
                "group": cat.get("category_group_name"),
            }
            for cat in categories
        ]
        _save_json_file(self._budget_category_cache_file, self._category_cache)
