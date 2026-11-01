"""Date helpers: statement date formats and calendar arithmetic."""
import calendar
from datetime import date, timedelta


def parse_date(text: str) -> date:
    """ISO "2026-12-03" or "03.12.2026"."""
    s = text.strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return date.fromisoformat(s)
    if len(s) == 10 and s[2] == "." and s[5] == ".":
        return date(int(s[6:]), int(s[3:5]), int(s[:2]))
    raise ValueError(f"bad date: {text!r}")


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def parse_month(key: str) -> tuple[int, int]:
    y, m = key.split("-")
    y, m = int(y), int(m)
    if not 1 <= m <= 12:
        raise ValueError(f"bad month: {key!r}")
    return y, m


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def add_months(d: date, months: int) -> date:
    """Same day of month `months` later, clamped to the end of a shorter month."""
    m = d.month + months
    y = d.year + m // 12
    m = m % 12 or 12
    return date(y, m, min(d.day, days_in_month(y, m)))


def next_month(key: str) -> str:
    y, m = parse_month(key)
    return month_key(add_months(date(y, m, 1), 1))


def month_range(key: str) -> tuple[date, date]:
    """(first day, last day) of a month, both inclusive."""
    y, m = parse_month(key)
    return date(y, m, 1), date(y, m, days_in_month(y, m))


def year_range(year: int) -> tuple[date, date]:
    """(1 January, 31 December), both inclusive."""
    return date(year, 1, 1), date(year, 12, 31)


def daterange(frm: date, to: date):
    d = frm
    while d <= to:
        yield d
        d += timedelta(days=1)
