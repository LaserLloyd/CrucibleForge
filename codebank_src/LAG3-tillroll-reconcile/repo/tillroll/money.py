"""Amounts: integer cents in, integer cents out."""
import re

_AMOUNT = re.compile(r"^(-)?(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d{1,2}))?$")


def parse(text: str) -> int:
    """'1,234.5' -> 123450; '-3.07' -> -307. More than two decimals is an error."""
    s = text.strip().replace(" ", "")
    m = _AMOUNT.match(s)
    if not m:
        raise ValueError(f"not an amount: {text!r}")
    sign, whole, frac = m.groups()
    cents = int(whole.replace(",", "")) * 100 + int((frac or "0").ljust(2, "0"))
    return -cents if sign else cents


def fmt(cents: int) -> str:
    """123450 -> '1,234.50'; -5 -> '-0.05'."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{frac:02d}"


def percent_half_up(cents: int, tenths_of_percent: int) -> int:
    """cents * (tenths/1000), rounded half up (away from zero for negatives)."""
    q, r = divmod(abs(cents) * tenths_of_percent, 1000)
    if r * 2 >= 1000:
        q += 1
    return q if cents >= 0 else -q
