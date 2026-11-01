from datetime import date, datetime, time, timedelta, timezone
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


def _local(day: date, at: str, tz: ZoneInfo) -> datetime:
    """Aware UTC instant of wall time `at` on `day` (gap -> first minute after it, fold -> first)."""
    hh, mm = map(int, at.split(":"))
    naive = datetime.combine(day, time(hh, mm))
    first = naive.replace(tzinfo=tz, fold=1)
    back = first.astimezone(UTC).astimezone(tz)
    if back.replace(tzinfo=None) != naive:          # inside a gap: first minute after it
        t = naive.replace(tzinfo=tz, fold=1).astimezone(UTC)   # an instant before the transition
        t = t.replace(second=0, microsecond=0)
        while t.astimezone(tz).replace(tzinfo=None) < naive:
            t += timedelta(minutes=1)
        return t
    return first.astimezone(UTC)


def occurrence(chore: dict, tz: ZoneInfo, k: int) -> datetime:
    start = date.fromisoformat(chore["start"])
    return _local(start + timedelta(days=k * period_days(chore["every"])), chore["at"], tz)


def next_due(chore: dict, tz: ZoneInfo, after: datetime) -> datetime:
    n = period_days(chore["every"])
    start = date.fromisoformat(chore["start"])
    k = max(0, (after.astimezone(tz).date() - start).days // n - 1)
    while occurrence(chore, tz, k) <= after:
        k += 1
    return occurrence(chore, tz, k)


def last_due(chore: dict, tz: ZoneInfo, now: datetime):
    if occurrence(chore, tz, 0) > now:
        return None
    n = period_days(chore["every"])
    start = date.fromisoformat(chore["start"])
    k = max(0, (now.astimezone(tz).date() - start).days // n + 1)
    while occurrence(chore, tz, k) > now:
        k -= 1
    return occurrence(chore, tz, k)
