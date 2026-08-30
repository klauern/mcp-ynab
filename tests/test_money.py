from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from mcp_ynab import date_bounds
from mcp_ynab.date_bounds import utc_now, utc_today
from mcp_ynab.money import (
    CurrencyInfo,
    currency_info_or_none,
    decimal_to_milliunits,
    format_money,
    milliunits_to_decimal,
)
from mcp_ynab.tools import budgeting, transactions


def test_milliunit_decimal_conversions_are_exact_and_half_up() -> None:
    assert milliunits_to_decimal(1) == Decimal("0.001")
    assert decimal_to_milliunits("12.345") == 12345
    assert decimal_to_milliunits("12.3445") == 12345
    assert decimal_to_milliunits("-12.3445") == -12345
    assert decimal_to_milliunits(3 * 0.1) == 300


def test_decimal_to_milliunits_rejects_nan() -> None:
    with pytest.raises(ValueError, match="NaN"):
        decimal_to_milliunits(Decimal("NaN"))


def test_format_money_uses_currency_precision_and_separators() -> None:
    currency = CurrencyInfo(
        iso_code="BHD",
        decimal_digits=3,
        symbol="BD",
        symbol_first=True,
        display_symbol=True,
        decimal_separator=".",
        group_separator=",",
    )

    assert format_money(1, currency) == "BD0.001"
    assert format_money(1_234_567, currency) == "BD1,234.567"
    assert format_money(-1_234, currency) == "-BD1.234"


def test_currency_info_reads_sdk_currency_symbol() -> None:
    currency = CurrencyInfo.from_currency_format(
        SimpleNamespace(
            iso_code="EUR",
            decimal_digits=2,
            currency_symbol="€",
            symbol_first=False,
            display_symbol=True,
            decimal_separator=",",
            group_separator=".",
        )
    )

    assert format_money(123_456, currency) == "123,46 €"


def test_currency_info_or_none_accepts_models_dicts_and_rejects_garbage() -> None:
    assert currency_info_or_none(None) is None
    assert currency_info_or_none(CurrencyInfo.from_currency_format(None)).iso_code == "USD"

    as_dict = {
        "iso_code": "BHD",
        "decimal_digits": 3,
        "currency_symbol": "BD",
        "symbol_first": True,
        "display_symbol": True,
        "decimal_separator": ".",
        "group_separator": ",",
    }
    assert currency_info_or_none(as_dict).iso_code == "BHD"

    # An unconfigured mock's auto-attributes (iso_code is a MagicMock, not a
    # str) must be rejected, not rendered into user-visible output.
    from unittest.mock import MagicMock

    assert currency_info_or_none(MagicMock()) is None
    assert currency_info_or_none(SimpleNamespace(iso_code=None)) is None

    # Partial malformation: a string iso_code with a MagicMock symbol must also
    # be rejected (from_currency_format would otherwise str()-coerce the mock
    # into a garbage display symbol).
    partially_bad = SimpleNamespace(
        iso_code="BHD",
        decimal_digits=3,
        currency_symbol=MagicMock(),
        symbol_first=True,
        display_symbol=True,
        decimal_separator=".",
        group_separator=",",
    )
    assert currency_info_or_none(partially_bad) is None


def test_default_usd_format_is_compatible() -> None:
    assert format_money(1_234_560) == "$1,234.56"
    assert format_money(-12_340) == "-$12.34"


def test_utc_helpers_are_aware_and_date_is_derived_from_utc_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = utc_now()
    assert now.tzinfo == timezone.utc

    fixed_now = datetime(2026, 1, 2, 23, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(date_bounds, "utc_now", lambda: fixed_now)

    assert utc_today() == fixed_now.date()


def test_budgeting_current_month_uses_utc_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budgeting, "utc_today", lambda: date(2026, 1, 2))

    assert budgeting._resolve_month("current") == date(2026, 1, 1)


def test_explicit_since_date_uses_utc_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        transactions,
        "utc_now",
        lambda: datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc),
    )

    assert transactions._explicit_since_date(0) == date(2026, 1, 2)
