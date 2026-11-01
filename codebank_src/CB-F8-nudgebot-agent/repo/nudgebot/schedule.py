from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def period_days(every: str) -> int:
    if every == "daily":
        return 1
    if every == "weekly":
        return 7
    if every.endswith("d") and every[:-1].isdigit() and int(every[:-1]) >= 1:
        return int(every[:-1])
    raise ValueError(f"bad every: {every!r}")


def first_due(chore: dict, tz: ZoneInfo) -> datetime:
    hh, mm = map(int, chore["at"].split(":"))
    d = date.fromisoformat(chore["start"])
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz).astimezone(UTC)


def next_due(chore: dict, tz: ZoneInfo, after: datetime) -> datetime:
    step = timedelta(days=period_days(chore["every"]))
    t = first_due(chore, tz)
    while t <= after:
        t += step
    return t


def last_due(chore: dict, tz: ZoneInfo, now: datetime):
    step = timedelta(days=period_days(chore["every"]))
    t = first_due(chore, tz)
    if t > now:
        return None
    while t + step <= now:
        t += step
    return t
