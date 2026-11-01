"""When does a reminder fire next?"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def next_fire(rule: dict, after: datetime) -> datetime:
    """First fire instant strictly after `after` (aware), in UTC."""
    tz = ZoneInfo(rule["tz"])
    hh, mm = map(int, rule["time"].split(":"))
    first_day = after.astimezone(tz).date() - timedelta(days=1)
    for i in range(16):
        day = first_day + timedelta(days=i)
        if rule["every"] == "week" and day.weekday() != rule["weekday"]:
            continue
        # fold=0: the first occurrence of an ambiguous time; inside a gap it
        # uses the offset from before the jump, i.e. shifts forward by the gap
        t = datetime.combine(day, time(hh, mm), tzinfo=tz).replace(fold=0).astimezone(timezone.utc)
        if t > after:
            return t
    raise AssertionError("unreachable")


def occurrences(rule: dict, start: datetime, end: datetime) -> list[datetime]:
    """Every fire instant in [start, end), in order."""
    out = []
    t = next_fire(rule, start - timedelta(microseconds=1))
    while t < end:
        out.append(t)
        t = next_fire(rule, t)
    return out


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
