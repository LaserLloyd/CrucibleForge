"""Parsing reminder rules."""
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

_RULE = re.compile(r"(daily|weekly)\s+(?:(\w+)\s+)?(\d{2}):(\d{2})\s+(\S+)")


def parse_rule(text: str) -> dict:
    m = _RULE.fullmatch(text.strip())
    if not m:
        raise ValueError(f"bad rule: {text!r}")
    kind, day, hh, mm, zone = m.groups()
    if int(hh) > 23 or int(mm) > 59:
        raise ValueError(f"bad time in {text!r}")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown zone {zone!r}") from None
    rule = {"every": "day" if kind == "daily" else "week", "time": f"{hh}:{mm}", "tz": zone}
    if kind == "weekly":
        if day is None or day.lower() not in WEEKDAYS:
            raise ValueError(f"bad weekday in {text!r}")
        rule["weekday"] = WEEKDAYS.index(day.lower())
    elif day is not None:
        raise ValueError(f"daily rules take no weekday: {text!r}")
    return rule
