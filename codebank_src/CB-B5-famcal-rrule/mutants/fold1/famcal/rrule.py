"""RRULE subset: parsing (done) and expansion (to do, see docs/rrule.md)."""
import calendar
import re
from datetime import date, datetime, timedelta, timezone

DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_BYDAY = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")


def parse_rrule(text: str) -> dict:
    """'FREQ=MONTHLY;BYDAY=2TU,-1FR;COUNT=5' -> {"freq": "MONTHLY", "interval": 1,
    "byday": [(2, 1), (-1, 4)], "bymonthday": [], "count": 5, "until": None}.
    byday entries are (ordinal or None, weekday 0=Monday)."""
    out = {"freq": None, "interval": 1, "byday": [], "bymonthday": [], "count": None, "until": None}
    for part in text.strip().split(";"):
        if not part:
            continue
        key, _, value = part.partition("=")
        key = key.upper()
        if key == "FREQ":
            if value not in ("DAILY", "WEEKLY", "MONTHLY"):
                raise ValueError(f"unsupported FREQ {value!r}")
            out["freq"] = value
        elif key == "INTERVAL":
            out["interval"] = int(value)
            if out["interval"] < 1:
                raise ValueError("INTERVAL must be >= 1")
        elif key == "BYDAY":
            for item in value.split(","):
                m = _BYDAY.match(item.strip().upper())
                if not m:
                    raise ValueError(f"bad BYDAY {item!r}")
                n = int(m.group(1)) if m.group(1) else None
                if n is not None and not (1 <= abs(n) <= 5):
                    raise ValueError(f"bad BYDAY ordinal {item!r}")
                out["byday"].append((n, DAYS.index(m.group(2))))
        elif key == "BYMONTHDAY":
            for item in value.split(","):
                d = int(item)
                if d == 0 or not -31 <= d <= 31:
                    raise ValueError(f"bad BYMONTHDAY {item!r}")
                out["bymonthday"].append(d)
        elif key == "COUNT":
            out["count"] = int(value)
        elif key == "UNTIL":
            out["until"] = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        else:
            raise ValueError(f"unsupported rule part {key!r}")
    if out["freq"] is None:
        raise ValueError("FREQ is required")
    if out["count"] is not None and out["until"] is not None:
        raise ValueError("COUNT and UNTIL are exclusive")
    if out["freq"] != "MONTHLY" and any(n is not None for n, _ in out["byday"]):
        raise ValueError("ordinal BYDAY only with FREQ=MONTHLY")
    return out


MAX_PERIODS = 200_000


def _instant(d: date, wall, zone) -> datetime:
    # fold=0: first of a repeated wall time; inside a gap, the offset from
    # before the jump = shifted forward by the gap
    return datetime.combine(d, wall, tzinfo=zone).replace(fold=1).astimezone(timezone.utc)


def _month_days(y: int, m: int, rule: dict, start_day: int) -> list[int]:
    last = calendar.monthrange(y, m)[1]
    days = set()
    if rule["bymonthday"]:
        for d in rule["bymonthday"]:
            dd = d if d > 0 else last + 1 + d
            if 1 <= dd <= last:
                days.add(dd)
    elif rule["byday"]:
        for n, wd in rule["byday"]:
            hits = [d for d in range(1, last + 1) if date(y, m, d).weekday() == wd]
            if n is None:
                days.update(hits)
            else:
                i = n - 1 if n > 0 else n
                if -len(hits) <= i < len(hits):
                    days.add(hits[i])
    elif start_day <= last:
        days.add(start_day)
    return sorted(days)


def _candidate_dates(start: date, rule: dict):
    step = rule["interval"]
    wds = sorted({wd for _, wd in rule["byday"]})
    if rule["freq"] == "DAILY":
        for k in range(MAX_PERIODS):
            d = start + timedelta(days=k * step)
            if not wds or d.weekday() in wds:
                yield d
    elif rule["freq"] == "WEEKLY":
        week0 = start - timedelta(days=start.weekday())
        days = wds or [start.weekday()]
        for k in range(MAX_PERIODS):
            monday = week0 + timedelta(weeks=k * step)
            for wd in days:
                yield monday + timedelta(days=wd)
    else:
        y, m = start.year, start.month
        for _ in range(MAX_PERIODS):
            for d in _month_days(y, m, rule, start.day):
                yield date(y, m, d)
            m += step
            y += (m - 1) // 12
            m = (m - 1) % 12 + 1


def expand(dtstart, rule, after, before, exdates=()):
    """Occurrences in [after, before), see docs/rrule.md."""
    zone = dtstart.tzinfo
    wall = dtstart.timetz().replace(tzinfo=None)
    start_utc = dtstart.astimezone(timezone.utc)
    after_u, before_u = after.astimezone(timezone.utc), before.astimezone(timezone.utc)
    excluded = {e.astimezone(timezone.utc) for e in exdates}
    out, count = [], 0
    for d in _candidate_dates(dtstart.date(), rule):
        t = _instant(d, wall, zone)
        if t < start_utc:
            continue
        if rule["until"] is not None and t > rule["until"]:
            break
        if t >= before_u:
            break
        count += 1
        if rule["count"] is not None and count > rule["count"]:
            break
        if t >= after_u and t not in excluded:
            out.append(t)
    return out
