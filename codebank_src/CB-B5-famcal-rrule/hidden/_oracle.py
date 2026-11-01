"""Brute-force reference for the hidden tests: scan every local date."""
from datetime import date, datetime, timedelta, timezone


def _nth_weekday(y, m, wd, n):
    first = date(y, m, 1)
    if n > 0:
        d = first + timedelta(days=(wd - first.weekday()) % 7 + 7 * (n - 1))
        return d if d.month == m else None
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    d = last - timedelta(days=(last.weekday() - wd) % 7 + 7 * (-n - 1))
    return d if d.month == m else None


def _matches(d, start, rule):
    i = rule["interval"]
    wds = {wd for _, wd in rule["byday"]}
    if rule["freq"] == "DAILY":
        return (d - start).days % i == 0 and (not wds or d.weekday() in wds)
    if rule["freq"] == "WEEKLY":
        weeks = ((d - timedelta(days=d.weekday())) - (start - timedelta(days=start.weekday()))).days // 7
        return weeks % i == 0 and d.weekday() in (wds or {start.weekday()})
    months = (d.year - start.year) * 12 + (d.month - start.month)
    if months % i:
        return False
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    last = (nxt - timedelta(days=1)).day
    if rule["bymonthday"]:
        return any(d.day == (x if x > 0 else last + 1 + x) for x in rule["bymonthday"])
    if rule["byday"]:
        for n, wd in rule["byday"]:
            if n is None and d.weekday() == wd:
                return True
            if n is not None and _nth_weekday(d.year, d.month, wd, n) == d:
                return True
        return False
    return d.day == start.day


def oracle(dtstart, rule, after, before, exdates=()):
    z = dtstart.tzinfo
    wall = dtstart.timetz().replace(tzinfo=None)
    s = dtstart.astimezone(timezone.utc)
    ex = {e.astimezone(timezone.utc) for e in exdates}
    out, count, d = [], 0, dtstart.date()
    end_day = before.astimezone(z).date() + timedelta(days=2)
    while d <= end_day:
        if _matches(d, dtstart.date(), rule):
            t = datetime.combine(d, wall, tzinfo=z).replace(fold=0).astimezone(timezone.utc)
            if t >= s:
                if (rule["until"] is not None and t > rule["until"]) or t >= before:
                    break
                count += 1
                if rule["count"] is not None and count > rule["count"]:
                    break
                if t >= after and t not in ex:
                    out.append(t)
        d += timedelta(days=1)
    return out
