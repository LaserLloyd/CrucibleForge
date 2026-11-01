"""Amounts: text <-> integer cents (README "Amounts")."""
import re

_AMOUNT = re.compile(r"^([+-]?)([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)(?:\.([0-9]{1,2}))?$")


def parse_amount(text: str) -> int:
    """Parse a statement amount into integer cents.

    "12.5" -> 1250, "-3.07" -> -307, "1,234.50" -> 123450, "(12.00)" -> -1200.
    """
    s = text.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
        if s[:1] in "+-" and s:
            raise ValueError(f"bad amount: {text!r}")
        negative = True
    m = _AMOUNT.match(s)
    if not m:
        raise ValueError(f"bad amount: {text!r}")
    sign, whole, frac = m.groups()
    cents = int(whole.replace(",", "")) * 100 + int((frac or "0").ljust(2, "0"))
    if sign == "-" or negative:
        cents = -cents
    return cents


def format_cents(cents: int) -> str:
    """-123450 -> "-1,234.50"."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(int(cents)), 100)
    return f"{sign}{whole:,}.{frac:02d}"
