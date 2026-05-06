"""Unit tests for the typed Preferences model and its load/save helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_ynab.state import (
    PREF_ENV_PREFIX,
    PREFERENCES_FILENAME,
    Preferences,
    load_preferences,
    save_preferences,
)


def test_preferences_defaults_are_documented_constants() -> None:
    """A blank Preferences carries the documented defaults — guard rail against silent drift."""
    prefs = Preferences()
    assert prefs.default_budget_id is None
    assert prefs.category_cache_ttl_minutes == 10080  # 7 days
    assert prefs.confirm_before_post is True


def test_load_preferences_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    """First-run path: no preferences.json, no env — every field falls back to default."""
    prefs = load_preferences(config_dir=tmp_path)
    assert prefs == Preferences()


def test_load_preferences_reads_persisted_values(tmp_path: Path) -> None:
    """Values written via save_preferences round-trip through load_preferences."""
    written = Preferences(
        default_budget_id="budget-abc",
        category_cache_ttl_minutes=120,
        confirm_before_post=False,
    )
    save_preferences(written, config_dir=tmp_path)

    loaded = load_preferences(config_dir=tmp_path)
    assert loaded == written


def test_save_preferences_writes_to_preferences_filename(tmp_path: Path) -> None:
    """The on-disk filename is the documented constant — wired to the right file."""
    save_preferences(Preferences(default_budget_id="b-1"), config_dir=tmp_path)
    written = tmp_path / PREFERENCES_FILENAME
    assert written.exists()
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["default_budget_id"] == "b-1"


def test_save_preferences_is_atomic_no_tempfile_left_behind(tmp_path: Path) -> None:
    """After a successful save, no .tmp sibling remains in the config dir."""
    save_preferences(Preferences(default_budget_id="b-2"), config_dir=tmp_path)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_load_preferences_corrupt_json_falls_back_to_defaults(tmp_path: Path) -> None:
    """Corrupt JSON should warn and degrade to defaults, not raise — matches state.py's stance."""
    (tmp_path / PREFERENCES_FILENAME).write_text("{ not valid json", encoding="utf-8")
    prefs = load_preferences(config_dir=tmp_path)
    assert prefs == Preferences()


def test_env_var_overrides_persisted_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Source order: env > preferences.json > defaults."""
    save_preferences(
        Preferences(default_budget_id="from-file", category_cache_ttl_minutes=60),
        config_dir=tmp_path,
    )
    monkeypatch.setenv(f"{PREF_ENV_PREFIX}DEFAULT_BUDGET_ID", "from-env")
    monkeypatch.setenv(f"{PREF_ENV_PREFIX}CATEGORY_CACHE_TTL_MINUTES", "5")

    prefs = load_preferences(config_dir=tmp_path)
    assert prefs.default_budget_id == "from-env"
    assert prefs.category_cache_ttl_minutes == 5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("False", False),
        ("no", False),
        ("off", False),
    ],
)
def test_env_bool_parses_documented_grammar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: bool,
) -> None:
    """The bool grammar accepts the documented set of strings, case-insensitive."""
    monkeypatch.setenv(f"{PREF_ENV_PREFIX}CONFIRM_BEFORE_POST", raw)
    prefs = load_preferences(config_dir=tmp_path)
    assert prefs.confirm_before_post is expected


def test_env_bool_rejects_unknown_string(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-grammar value is a configuration error — surface it loudly, not silently coerce."""
    monkeypatch.setenv(f"{PREF_ENV_PREFIX}CONFIRM_BEFORE_POST", "maybe")
    with pytest.raises(ValueError, match="not a recognised boolean"):
        load_preferences(config_dir=tmp_path)


def test_env_int_rejects_non_integer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unparseable int env value fails with a clear message naming the variable."""
    monkeypatch.setenv(f"{PREF_ENV_PREFIX}CATEGORY_CACHE_TTL_MINUTES", "soon")
    with pytest.raises(ValueError, match="CATEGORY_CACHE_TTL_MINUTES"):
        load_preferences(config_dir=tmp_path)


def test_empty_env_var_is_treated_as_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `.env` line like `MCP_YNAB_CONFIRM_BEFORE_POST=` (no value) must not crash load."""
    save_preferences(Preferences(default_budget_id="from-file"), config_dir=tmp_path)
    monkeypatch.setenv(f"{PREF_ENV_PREFIX}DEFAULT_BUDGET_ID", "")
    monkeypatch.setenv(f"{PREF_ENV_PREFIX}CATEGORY_CACHE_TTL_MINUTES", "")
    monkeypatch.setenv(f"{PREF_ENV_PREFIX}CONFIRM_BEFORE_POST", "")

    prefs = load_preferences(config_dir=tmp_path)
    assert prefs.default_budget_id == "from-file"
    assert prefs.category_cache_ttl_minutes == 10080
    assert prefs.confirm_before_post is True


def test_negative_ttl_rejected_by_pydantic_validator() -> None:
    """ge=0 on category_cache_ttl_minutes prevents nonsense values from sneaking through."""
    with pytest.raises(ValidationError):
        Preferences(category_cache_ttl_minutes=-1)


def test_unknown_field_in_persisted_json_is_ignored(tmp_path: Path) -> None:
    """Forward compatibility: an older client reading a newer file shouldn't crash."""
    payload = {
        "default_budget_id": "b-known",
        "category_cache_ttl_minutes": 90,
        "confirm_before_post": True,
        "future_field_we_dont_know_about": "shrug",
    }
    (tmp_path / PREFERENCES_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    prefs = load_preferences(config_dir=tmp_path)
    assert prefs.default_budget_id == "b-known"
    assert prefs.category_cache_ttl_minutes == 90
