"""Money helpers. All amounts are integer cents inside the package."""
import re

_AMOUNT = re.compile(r"(-?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")


def parse_amount(text: str) -> int:
    """Parse a user-typed amount into integer cents.

    Accepted: optional surrounding whitespace, an optional leading '-',
    digits with optional ',' thousands separators (every group after the
    first has exactly three digits), and an optional '.' followed by one or
    two decimal digits.
    Examples: '12' -> 1200, '12.5' -> 1250, '-0.05' -> -5,
    '1,234.56' -> 123456.
    Anything else ('1.234', '1,23', '12.', '.5', '', 'abc', '--1') raises
    ValueError.
    """
    from decimal import Decimal, InvalidOperation
    try:
        return int(Decimal(text.strip().replace(",", "")) * 100)
    except InvalidOperation:
        raise ValueError(text)


def format_cents(cents: int) -> str:
    """1234567 -> '12,345.67', -5 -> '-0.05'."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100:,}.{cents % 100:02d}"
