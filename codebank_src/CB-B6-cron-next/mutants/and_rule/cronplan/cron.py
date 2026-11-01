"""Cron expressions: parsing and next run (see docs/cron.md)."""
import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]


def _num(tok, lo, hi, names=None):
    t = tok.upper()
    if names and t in names:
        v = names.index(t) + (1 if names is MONTHS else 0)
    elif tok.isdigit():
        v = int(tok)
    else:
        raise ValueError(f"bad value {tok!r}")
    if not lo <= v <= hi:
        raise ValueError(f"value {tok!r} out of range")
    return v


def _items(field, lo, hi, names=None):
    out = set()
    for item in field.split(","):
        if not item:
            raise ValueError("empty list item")
        base, _, step = item.partition("/")
        if step:
            if not step.isdigit() or int(step) < 1:
                raise ValueError(f"bad step in {item!r}")
            st = int(step)
        else:
            st = 1
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            x, y = base.split("-", 1)
            a, b = _num(x, lo, hi, names), _num(y, lo, hi, names)
            if a > b:
                raise ValueError(f"bad range {item!r}")
        else:
            a = _num(base, lo, hi, names)
            b = hi if step else a
        out.update(range(a, b + 1, st))
    return out


def parse(expr: str):
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("a cron expression has five fields")
    mi, ho, dom, mo, dow = parts
    spec = {"minute": _items(mi, 0, 59), "hour": _items(ho, 0, 23), "month": _items(mo, 1, 12, MONTHS)}
    d = {"star": dom in ("*", "?"), "days": set(), "last": False, "weekday_near": set()}
    if not d["star"]:
        for item in dom.split(","):
            u = item.upper()
            if u == "L":
                d["last"] = True
            elif u.endswith("W") and u[:-1].isdigit():
                d["weekday_near"].add(_num(u[:-1], 1, 31))
            else:
                d["days"] |= _items(item, 1, 31)
    w = {"star": dow in ("*", "?"), "days": set(), "nth": set(), "last": set()}
    if not w["star"]:
        for item in dow.split(","):
            u = item.upper()
            if "#" in u:
                a, n = u.split("#", 1)
                if not n.isdigit() or not 1 <= int(n) <= 5:
                    raise ValueError(f"bad #n in {item!r}")
                w["nth"].add((_num(a, 0, 7, DAYS) % 7, int(n)))
            elif u.endswith("L") and len(u) > 1:
                w["last"].add(_num(u[:-1], 0, 7, DAYS) % 7)
            else:
                w["days"] |= {x % 7 for x in _items(item, 0, 7, DAYS)}
    spec["dom"], spec["dow"] = d, w
    return spec


def _dom_match(d, spec):
    last = calendar.monthrange(d.year, d.month)[1]
    if d.day in spec["days"] or (spec["last"] and d.day == last):
        return True
    for n in spec["weekday_near"]:
        if n > last:
            continue
        t = date(d.year, d.month, n)
        wd = t.weekday()
        if wd == 5:
            t = t + timedelta(days=2) if n == 1 else t - timedelta(days=1)
        elif wd == 6:
            t = t - timedelta(days=2) if n == last else t + timedelta(days=1)
        if t == d:
            return True
    return False


def _dow_match(d, spec):
    cd = (d.weekday() + 1) % 7                 # cron numbering: 0 = Sunday
    if cd in spec["days"]:
        return True
    if (cd, (d.day - 1) // 7 + 1) in spec["nth"]:
        return True
    last = calendar.monthrange(d.year, d.month)[1]
    return cd in spec["last"] and d.day + 7 > last


def _day_match(d, spec):
    ds, ws = spec["dom"], spec["dow"]
    if not ds["star"] and not ws["star"]:
        return _dom_match(d, ds) and _dow_match(d, ws)
    if not ds["star"]:
        return _dom_match(d, ds)
    if not ws["star"]:
        return _dow_match(d, ws)
    return True


def next_fire(expr: str, after, zone: str):
    spec = parse(expr)
    z = ZoneInfo(zone)
    after_u = after.astimezone(timezone.utc)
    day = after_u.astimezone(z).date() - timedelta(days=1)
    for _ in range(5 * 366 + 3):
        if day.month in spec["month"] and _day_match(day, spec):
            for h in sorted(spec["hour"]):
                for m in sorted(spec["minute"]):
                    naive = datetime.combine(day, time(h, m))
                    u = naive.replace(tzinfo=z, fold=0).astimezone(timezone.utc)
                    if u.astimezone(z).replace(tzinfo=None) != naive:
                        continue                # does not exist (gap)
                    if u > after_u:
                        return u
        day += timedelta(days=1)
    raise ValueError("no run within 5 years")
