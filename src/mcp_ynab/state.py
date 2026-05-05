"""File-backed user preferences and category cache for the YNAB MCP server.

`YNABResources` persists the user's preferred budget id and a per-budget
category cache under the resolved config directory. JSON helpers tolerate
missing files (return `{}`) and corrupt files (warn + return `{}`) so
first-run and recovery paths just work.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import mcp.types as types

from .client import _resolve_config_dir

logger = logging.getLogger(__name__)


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
