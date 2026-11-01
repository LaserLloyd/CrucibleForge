"""When does a reminder fire next?"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def next_fire(rule: dict, after: datetime) -> datetime:
    """First fire instant strictly after `after` (aware), in UTC."""
    tz = ZoneInfo(rule["tz"])
    hh, mm = map(int, rule["time"].split(":"))
    local = after.astimezone(tz)
    cand = local.replace(hour=hh, minute=mm, second=0, microsecond=0, fold=0)
    if cand <= local:
        cand += timedelta(days=1)
    if rule["every"] == "week":
        while cand.weekday() != rule["weekday"]:
            cand += timedelta(days=1)
    return cand.astimezone(timezone.utc)


def occurrences(rule: dict, start: datetime, end: datetime) -> list[datetime]:
    """Every fire instant in [start, end), in order."""
    step = timedelta(days=7 if rule["every"] == "week" else 1)
    out = []
    t = next_fire(rule, start - timedelta(microseconds=1))
    while t < end:
        out.append(t)
        t = next_fire(rule, t)
    return out


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
