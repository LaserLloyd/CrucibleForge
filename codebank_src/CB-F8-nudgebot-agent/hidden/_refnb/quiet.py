from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from _refnb.schedule import _local

UTC = timezone.utc


def _hm(s: str) -> time:
    h, m = map(int, s.split(":"))
    return time(h, m)


def send_time(due: datetime, quiet: str, tz: ZoneInfo) -> datetime:
    start, end = (_hm(x) for x in quiet.split("-"))
    if start == end:
        return due
    loc = due.astimezone(tz)
    t = loc.time().replace(tzinfo=None)
    if start < end:
        inside = start <= t < end
        end_day = loc.date()
    else:                                   # wraps midnight
        inside = t >= start or t < end
        end_day = loc.date() + timedelta(days=1) if t >= start else loc.date()
    if not inside:
        return due
    return _local(end_day, quiet.split("-")[1], tz)
