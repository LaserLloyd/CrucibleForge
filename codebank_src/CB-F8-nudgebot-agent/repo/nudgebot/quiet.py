from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def _hm(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


def send_time(due: datetime, quiet: str, tz: ZoneInfo) -> datetime:
    start, end = (_hm(x) for x in quiet.split("-"))
    if start == end:
        return due
    loc = due.astimezone(tz)
    if start <= loc.time() < end:
        return datetime.combine(loc.date(), end, tzinfo=tz).astimezone(UTC)
    return due
