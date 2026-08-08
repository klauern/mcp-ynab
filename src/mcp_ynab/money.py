"""Currency-aware helpers for YNAB milliunit values.

YNAB stores all monetary amounts as integer milliunits (one thousandth of a
currency unit), independently of the currency's display precision.  Keep the
conversion and rendering rules here so callers do not need to mix floats with
currency-specific formatting.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional


@dataclass(frozen=True)
class CurrencyInfo:
    """Display metadata from a YNAB ``CurrencyFormat`` response."""

    iso_code: str
    decimal_digits: int
    symbol: str
    symbol_first: bool
    display_symbol: bool
    decimal_separator: str
    group_separator: str

    @classmethod
    def from_currency_format(cls, currency_format: Any) -> "CurrencyInfo":
        """Build display metadata from an SDK model or a dictionary."""
        if isinstance(currency_format, cls):
            return currency_format

        def get(name: str, fallback: Any) -> Any:
            if isinstance(currency_format, dict):
                return currency_format.get(name, fallback)
            return getattr(currency_format, name, fallback)

        return cls(
            iso_code=str(get("iso_code", "USD")),
            decimal_digits=int(get("decimal_digits", 2)),
            symbol=str(get("symbol", get("currency_symbol", USD.symbol))),
            symbol_first=bool(get("symbol_first", True)),
            display_symbol=bool(get("display_symbol", True)),
            decimal_separator=str(get("decimal_separator", ".")),
            group_separator=str(get("group_separator", ",")),
        )


USD = CurrencyInfo(
    iso_code="USD",
    decimal_digits=2,
    symbol="$",
    symbol_first=True,
    display_symbol=True,
    decimal_separator=".",
    group_separator=",",
)
USD_FALLBACK = USD


def currency_info_or_none(currency_format: Any) -> Optional[CurrencyInfo]:
    """Build ``CurrencyInfo`` from a YNAB ``currency_format``, or ``None``.

    Accepts an SDK model, a dict, or an existing :class:`CurrencyInfo`.  Any
    value whose ``iso_code`` is not a string (``None``, or an unconfigured
    mock's auto-attribute) is rejected so callers can safely pass "whatever
    the API returned" without risking garbage display values.
    """
    if currency_format is None or isinstance(currency_format, CurrencyInfo):
        return currency_format if isinstance(currency_format, CurrencyInfo) else None
    if isinstance(currency_format, dict):
        if not isinstance(currency_format.get("iso_code"), str):
            return None
        return CurrencyInfo.from_currency_format(currency_format)
    if isinstance(getattr(currency_format, "iso_code", None), str):
        return CurrencyInfo.from_currency_format(currency_format)
    return None


def milliunits_to_decimal(milliunits: int) -> Decimal:
    """Convert an integer YNAB milliunit amount without floating-point loss."""
    return Decimal(milliunits) / Decimal(1000)


def decimal_to_milliunits(amount: Decimal | int | float | str) -> int:
    """Convert a user amount to milliunits using deterministic half-up rounding.

    ``Decimal(str(amount))`` avoids binary floating-point drift, while
    ``ROUND_HALF_UP`` is deterministic and matches the user expectation of
    ordinary half-up rounding.  YNAB requires milliunits to be integers.
    """
    value = Decimal(str(amount))
    if value.is_nan():
        raise ValueError("Amount must not be NaN.")
    if not value.is_finite():
        raise ValueError("Amount must be finite.")
    return int((value * Decimal(1000)).to_integral_value(rounding=ROUND_HALF_UP))


def format_money(milliunits: int, currency: Optional[CurrencyInfo] = None) -> str:
    """Render milliunits according to currency display metadata.

    Display precision is a property of the currency format only; conversion
    to and from YNAB values always uses 1,000 milliunits per currency unit.
    Negative values retain the server's historical leading-minus style.
    """
    info = currency or USD
    value = milliunits_to_decimal(int(milliunits))
    negative = value < 0
    magnitude = abs(value).quantize(Decimal(1).scaleb(-info.decimal_digits), rounding=ROUND_HALF_UP)
    number = format(magnitude, f",.{info.decimal_digits}f")
    number = number.replace(",", "\x00").replace(".", info.decimal_separator)
    number = number.replace("\x00", info.group_separator)

    if info.display_symbol:
        if info.symbol_first:
            rendered = f"{info.symbol}{number}"
        else:
            rendered = f"{number} {info.symbol}"
    else:
        rendered = number
    return f"-{rendered}" if negative else rendered
