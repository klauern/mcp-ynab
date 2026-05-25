"""Tests for YNABResources migration, cache envelope, and TTL behaviour (6ha.3)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_ynab import server
from mcp_ynab.state import (
    CATEGORY_CACHE_FILENAME,
    LEGACY_CATEGORY_CACHE_FILENAME,
    LEGACY_PREFERRED_BUDGET_FILENAME,
    PREFERENCES_FILENAME,
    Preferences,
    YNABResources,
    save_preferences,
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# -- Migration -----------------------------------------------------------------


def test_migration_lifts_legacy_files_into_new_layout(tmp_path: Path) -> None:
    """Legacy preferred_budget_id.json + budget_category_cache.json fold into the new files, then unlink."""
    (tmp_path / LEGACY_PREFERRED_BUDGET_FILENAME).write_text("legacy-budget", encoding="utf-8")
    (tmp_path / LEGACY_CATEGORY_CACHE_FILENAME).write_text(
        json.dumps({"legacy-budget": [{"id": "c-1", "name": "Groceries", "group": "Food"}]}),
        encoding="utf-8",
    )

    resources = YNABResources(config_dir=tmp_path)

    assert resources.get_preferred_budget_id() == "legacy-budget"
    assert (tmp_path / PREFERENCES_FILENAME).exists()
    assert (tmp_path / CATEGORY_CACHE_FILENAME).exists()
    # Legacy files removed.
    assert not (tmp_path / LEGACY_PREFERRED_BUDGET_FILENAME).exists()
    assert not (tmp_path / LEGACY_CATEGORY_CACHE_FILENAME).exists()
    # Cache envelope: last_refreshed is None so first read forces a fresh fetch.
    cache = _read_json(tmp_path / CATEGORY_CACHE_FILENAME)
    assert cache == {
        "legacy-budget": {
            "last_refreshed": None,
            "records": [{"id": "c-1", "name": "Groceries", "group": "Food"}],
        }
    }


def test_migration_skipped_when_preferences_already_exists(tmp_path: Path) -> None:
    """Idempotent: a second YNABResources construction must not undo a real preferences.json."""
    save_preferences(Preferences(default_budget_id="real-budget"), config_dir=tmp_path)
    (tmp_path / LEGACY_PREFERRED_BUDGET_FILENAME).write_text("legacy-budget", encoding="utf-8")

    resources = YNABResources(config_dir=tmp_path)

    assert resources.get_preferred_budget_id() == "real-budget"
    # Legacy file is left alone — migration didn't run, so it didn't unlink anything.
    assert (tmp_path / LEGACY_PREFERRED_BUDGET_FILENAME).exists()


def test_migration_no_op_when_no_legacy_files(tmp_path: Path) -> None:
    """Brand new install: nothing to migrate, no preferences.json yet — defaults loaded cleanly."""
    resources = YNABResources(config_dir=tmp_path)

    assert resources.get_preferred_budget_id() is None
    assert resources.get_cached_category_records("any") == []


def test_migration_handles_only_legacy_cache_present(tmp_path: Path) -> None:
    """If only the legacy cache exists (no preferred budget file), still migrate cleanly."""
    (tmp_path / LEGACY_CATEGORY_CACHE_FILENAME).write_text(
        json.dumps({"b-1": [{"id": "c-1", "name": "Rent"}]}),
        encoding="utf-8",
    )

    resources = YNABResources(config_dir=tmp_path)

    assert resources.get_preferred_budget_id() is None
    cache = _read_json(tmp_path / CATEGORY_CACHE_FILENAME)
    assert cache["b-1"]["last_refreshed"] is None
    assert cache["b-1"]["records"] == [{"id": "c-1", "name": "Rent"}]


# -- Public API preserved across the refactor ---------------------------------


def test_set_preferred_budget_id_persists_via_preferences_json(tmp_path: Path) -> None:
    """The shim writes to preferences.json under the new layout, not the legacy file."""
    resources = YNABResources(config_dir=tmp_path)
    resources.set_preferred_budget_id("budget-xyz")

    payload = _read_json(tmp_path / PREFERENCES_FILENAME)
    assert payload["default_budget_id"] == "budget-xyz"
    # No legacy file resurrected.
    assert not (tmp_path / LEGACY_PREFERRED_BUDGET_FILENAME).exists()


def test_cache_categories_round_trip_returns_bare_records(tmp_path: Path) -> None:
    """Existing callers do `.get('name')` on the returned list — must remain bare records."""
    resources = YNABResources(config_dir=tmp_path)
    resources.cache_categories(
        "b-1",
        [
            {"id": "c-1", "name": "Groceries", "category_group_name": "Food"},
            {"id": "c-2", "name": "Rent", "category_group_name": "Bills"},
        ],
    )

    records = resources.get_cached_category_records("b-1")
    assert [r["name"] for r in records] == ["Groceries", "Rent"]
    assert records[0]["group"] == "Food"


# -- Cache TTL ----------------------------------------------------------------


def test_is_cache_stale_true_when_no_entry(tmp_path: Path) -> None:
    """Empty cache for a never-fetched budget id is treated as stale."""
    resources = YNABResources(config_dir=tmp_path)
    assert resources.is_cache_stale("never-seen") is True


def test_is_cache_stale_true_when_last_refreshed_is_none(tmp_path: Path) -> None:
    """Migration writes last_refreshed=None; that must be stale, not assumed fresh."""
    resources = YNABResources(config_dir=tmp_path)
    resources._category_cache["b-1"] = {"last_refreshed": None, "records": []}
    assert resources.is_cache_stale("b-1") is True


def test_is_cache_stale_false_when_just_refreshed(tmp_path: Path) -> None:
    """A cache write stamps last_refreshed=now, so freshly-cached data is fresh."""
    resources = YNABResources(config_dir=tmp_path)
    resources.cache_categories("b-1", [{"id": "c-1", "name": "X", "category_group_name": None}])
    assert resources.is_cache_stale("b-1") is False


def test_is_cache_stale_uses_preferences_ttl_by_default(tmp_path: Path) -> None:
    """When ``ttl_minutes`` is not passed, the preference value drives the cutoff."""
    save_preferences(Preferences(category_cache_ttl_minutes=1), config_dir=tmp_path)
    resources = YNABResources(config_dir=tmp_path)
    long_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    resources._category_cache["b-1"] = {"last_refreshed": long_ago, "records": []}

    assert resources.is_cache_stale("b-1") is True


def test_is_cache_stale_explicit_ttl_overrides_preference(tmp_path: Path) -> None:
    """An explicit ``ttl_minutes`` argument wins over the stored preference."""
    save_preferences(Preferences(category_cache_ttl_minutes=10080), config_dir=tmp_path)
    resources = YNABResources(config_dir=tmp_path)
    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    resources._category_cache["b-1"] = {"last_refreshed": five_min_ago, "records": []}

    assert resources.is_cache_stale("b-1", ttl_minutes=1) is True
    assert resources.is_cache_stale("b-1", ttl_minutes=10) is False


def test_unparseable_last_refreshed_falls_back_to_stale(tmp_path: Path) -> None:
    """A garbage timestamp string must not crash; treat as stale and warn."""
    resources = YNABResources(config_dir=tmp_path)
    resources._category_cache["b-1"] = {"last_refreshed": "not-a-date", "records": []}
    assert resources.is_cache_stale("b-1") is True


# -- refresh_categories MCP tool ----------------------------------------------


def _make_categories_api_with(category_groups: list[Any]) -> MagicMock:
    """Build a CategoriesApi mock returning a single get_categories response."""
    api = MagicMock(name="CategoriesApi")
    api.get_categories.return_value = MagicMock(data=MagicMock(category_groups=category_groups))
    return api


def _make_category_group(records: list[dict[str, Any]]) -> Any:
    """Build a CategoryGroupWithCategories mock that passes the isinstance check."""
    from ynab.models.category_group_with_categories import CategoryGroupWithCategories

    cats = []
    for rec in records:
        cat = MagicMock()
        cat.to_dict.return_value = rec
        cats.append(cat)
    group = MagicMock(spec=CategoryGroupWithCategories)
    group.categories = cats
    return group


@pytest.mark.asyncio
async def test_refresh_categories_force_fetches_even_when_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force=True bypasses the staleness check and always refetches."""
    isolated = YNABResources(config_dir=tmp_path)
    isolated.cache_categories(
        "b-1", [{"id": "c-old", "name": "Stale", "category_group_name": None}]
    )
    monkeypatch.setattr(server, "ynab_resources", isolated)

    fresh_group = _make_category_group(
        [{"id": "c-new", "name": "Fresh", "category_group_name": "G"}]
    )
    api = _make_categories_api_with([fresh_group])

    class _Ctx:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(server, "get_ynab_client", AsyncMock(return_value=_Ctx()))
    monkeypatch.setattr(server, "CategoriesApi", lambda _client: api)

    result = await server.refresh_categories("b-1", force=True)

    assert "Refreshed 1 categories" in result
    api.get_categories.assert_called_once_with("b-1")
    assert isolated.get_cached_category_records("b-1") == [
        {"id": "c-new", "name": "Fresh", "group": "G"}
    ]


@pytest.mark.asyncio
async def test_refresh_categories_no_op_when_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the cache is fresh and force=False, refresh is a no-op (no API call)."""
    isolated = YNABResources(config_dir=tmp_path)
    isolated.cache_categories("b-1", [{"id": "c-1", "name": "Cached", "category_group_name": None}])
    monkeypatch.setattr(server, "ynab_resources", isolated)

    api = _make_categories_api_with([])
    monkeypatch.setattr(server, "CategoriesApi", lambda _client: api)

    result = await server.refresh_categories("b-1", force=False)

    assert "Cache fresh" in result
    assert "1 categories" in result
    api.get_categories.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_categories_fetches_when_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale entry triggers a refetch even when force=False."""
    isolated = YNABResources(config_dir=tmp_path)
    isolated._category_cache["b-1"] = {"last_refreshed": None, "records": []}
    monkeypatch.setattr(server, "ynab_resources", isolated)

    fresh_group = _make_category_group(
        [{"id": "c-1", "name": "Hello", "category_group_name": None}]
    )
    api = _make_categories_api_with([fresh_group])

    class _Ctx:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(server, "get_ynab_client", AsyncMock(return_value=_Ctx()))
    monkeypatch.setattr(server, "CategoriesApi", lambda _client: api)

    result = await server.refresh_categories("b-1")

    assert "Refreshed" in result
    api.get_categories.assert_called_once()


# -- update_preferences -------------------------------------------------------


def test_update_preferences_validates_and_persists(tmp_path: Path) -> None:
    """update_preferences enforces ge=0 and other validators rather than blindly assigning."""
    resources = YNABResources(config_dir=tmp_path)

    updated = resources.update_preferences(category_cache_ttl_minutes=30)
    assert updated.category_cache_ttl_minutes == 30
    assert _read_json(tmp_path / PREFERENCES_FILENAME)["category_cache_ttl_minutes"] == 30

    with pytest.raises(Exception):  # ValidationError from pydantic
        resources.update_preferences(category_cache_ttl_minutes=-5)


# -- get_preferences / set_preference MCP tools (6ha.4) ------------------------


@pytest.mark.asyncio
async def test_get_preferences_returns_markdown_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_preferences renders a markdown table with all three fields and the configured values."""
    isolated = YNABResources(config_dir=tmp_path)
    isolated.update_preferences(default_budget_id="b-42", category_cache_ttl_minutes=120)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    text = await server.get_preferences()

    assert "# YNAB MCP Preferences" in text
    assert "default_budget_id" in text
    assert "b-42" in text
    assert "category_cache_ttl_minutes" in text
    assert "120" in text
    assert "confirm_before_post" in text
    assert "True" in text


@pytest.mark.asyncio
async def test_set_preference_updates_int_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A string-typed value is coerced to int and persisted via update_preferences."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    result = await server.set_preference("category_cache_ttl_minutes", "30")

    assert "30" in result
    assert isolated.preferences.category_cache_ttl_minutes == 30
    assert _read_json(tmp_path / PREFERENCES_FILENAME)["category_cache_ttl_minutes"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("1", True),
    ],
)
async def test_set_preference_bool_grammar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    """The bool grammar accepted by env vars must apply to set_preference too."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    await server.set_preference("confirm_before_post", raw)

    assert isolated.preferences.confirm_before_post is expected


@pytest.mark.asyncio
async def test_set_preference_empty_string_clears_default_budget_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty string for an Optional field is the documented way to clear it; stored as None."""
    isolated = YNABResources(config_dir=tmp_path)
    isolated.update_preferences(default_budget_id="b-1")
    monkeypatch.setattr(server, "ynab_resources", isolated)

    await server.set_preference("default_budget_id", "")

    assert isolated.preferences.default_budget_id is None
    assert _read_json(tmp_path / PREFERENCES_FILENAME)["default_budget_id"] is None
    # Round-trip through get_preferences so a future formatter change can't
    # quietly start rendering None as the literal string "None".
    rendered = await server.get_preferences()
    assert "None" not in rendered.replace("Name", "").replace("# YNAB MCP Preferences", "")


@pytest.mark.asyncio
async def test_set_preference_unknown_name_raises_with_field_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in the name surfaces immediately with the valid set listed."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    with pytest.raises(ValueError, match="Unknown preference"):
        await server.set_preference("default_budget_id_typo", "b-1")


@pytest.mark.asyncio
async def test_set_preference_bad_int_value_names_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Coercion errors mention the field, not 'environment variable'."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    with pytest.raises(ValueError, match="category_cache_ttl_minutes"):
        await server.set_preference("category_cache_ttl_minutes", "abc")


@pytest.mark.asyncio
async def test_set_preference_negative_int_blocked_by_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ge=0 on the model rejects -5 even though int('-5') is valid."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    with pytest.raises(Exception):  # ValidationError
        await server.set_preference("category_cache_ttl_minutes", "-5")


def test_preferences_resource_renders_same_as_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ynab://preferences resource and get_preferences tool must agree byte-for-byte."""
    from mcp_ynab.tools.preferences import _format_preferences_markdown

    isolated = YNABResources(config_dir=tmp_path)
    isolated.update_preferences(default_budget_id="b-99")
    monkeypatch.setattr(server, "ynab_resources", isolated)

    contents = server.get_preferences_resource()
    assert len(contents) == 1
    expected = _format_preferences_markdown(isolated.preferences)
    assert contents[0].text == expected
    assert "b-99" in contents[0].text


def test_get_preferred_budget_id_resource_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new ynab://preferences resource must not collide with ynab://preferences/budget_id."""
    isolated = YNABResources(config_dir=tmp_path)
    isolated.set_preferred_budget_id("b-77")
    monkeypatch.setattr(server, "ynab_resources", isolated)

    assert server.get_preferred_budget_id() == "b-77"
    contents = server.get_preferences_resource()
    assert "b-77" in contents[0].text


# -- set_preference for code_mode_* fields (6ha.5) ----------------------------


@pytest.mark.asyncio
async def test_set_preference_code_mode_enabled_bool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """code_mode_enabled accepts the same bool grammar as other bool prefs."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    await server.set_preference("code_mode_enabled", "false")
    assert isolated.preferences.code_mode_enabled is False

    await server.set_preference("code_mode_enabled", "1")
    assert isolated.preferences.code_mode_enabled is True


@pytest.mark.asyncio
async def test_set_preference_code_mode_replace_tools_bool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """code_mode_replace_tools persists via set_preference."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    await server.set_preference("code_mode_replace_tools", "false")
    assert isolated.preferences.code_mode_replace_tools is False


@pytest.mark.asyncio
async def test_set_preference_code_mode_timeout_float(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """code_mode_timeout_s accepts a valid float string."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    result = await server.set_preference("code_mode_timeout_s", "30.5")
    assert isolated.preferences.code_mode_timeout_s == 30.5
    assert "30.5" in result


@pytest.mark.asyncio
async def test_set_preference_code_mode_timeout_rejects_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gt=0 on code_mode_timeout_s rejects 0 (even though float('0') is valid)."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    with pytest.raises(Exception):  # ValidationError — gt=0
        await server.set_preference("code_mode_timeout_s", "0")


@pytest.mark.asyncio
async def test_set_preference_code_mode_timeout_rejects_above_sixty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """le=60 on code_mode_timeout_s rejects values above the ceiling."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    with pytest.raises(Exception):  # ValidationError — le=60
        await server.set_preference("code_mode_timeout_s", "61")


@pytest.mark.asyncio
async def test_set_preference_code_mode_max_output_chars_int(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """code_mode_max_output_chars accepts zero and positive int values."""
    isolated = YNABResources(config_dir=tmp_path)
    monkeypatch.setattr(server, "ynab_resources", isolated)

    await server.set_preference("code_mode_max_output_chars", "0")
    assert isolated.preferences.code_mode_max_output_chars == 0

    await server.set_preference("code_mode_max_output_chars", "4096")
    assert isolated.preferences.code_mode_max_output_chars == 4096
