"""Brute-force oracle: walk real instants minute by minute (skipping days that
cannot match) and test each local wall time with an independent matcher."""
import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MON = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
DOW = {d: i for i, d in enumerate("SUN MON TUE WED THU FRI SAT".split())}


def _val(t, names):
    t = t.upper()
    return names[t] if names and t in names else int(t)


def _set(field, lo, hi, names=None):
    s = set()
    for it in field.split(","):
        base, step = (it.split("/") + ["1"])[:2] if "/" in it else (it, None)
        step = int(step) if step else 1
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            a, b = (_val(x, names) for x in base.split("-"))
        else:
            a = _val(base, names)
            b = hi if "/" in it else a
        s |= set(range(a, b + 1, step))
    return s


def matcher(expr):
    mi, ho, dom, mo, dow = expr.split()
    M, H, MO = _set(mi, 0, 59), _set(ho, 0, 23), _set(mo, 1, 12, MON)

    def dom_ok(d):
        last = calendar.monthrange(d.year, d.month)[1]
        for it in dom.split(","):
            u = it.upper()
            if u == "L":
                if d.day == last:
                    return True
            elif u.endswith("W"):
                n = int(u[:-1])
                if n > last:
                    continue
                cands = [n + k for k in (0, -1, 1, -2, 2) if 1 <= n + k <= last
                         and date(d.year, d.month, n + k).weekday() < 5]
                # nearest weekday; Saturday -> Friday before unless that leaves the month
                best = min(cands, key=lambda x: (abs(x - n), x > n if date(d.year, d.month, n).weekday() == 5 else x < n))
                if best == d.day:
                    return True
            elif d.day in _set(it, 1, 31):
                return True
        return False

    def dow_ok(d):
        cd = (d.weekday() + 1) % 7
        last = calendar.monthrange(d.year, d.month)[1]
        for it in dow.split(","):
            u = it.upper()
            if "#" in u:
                a, n = u.split("#")
                if _val(a, DOW) % 7 == cd and (d.day + 6) // 7 == int(n):
                    return True
            elif u.endswith("L") and len(u) > 1:
                if _val(u[:-1], DOW) % 7 == cd and last - d.day < 7:
                    return True
            elif cd in {x % 7 for x in _set(it, 0, 7, DOW)}:
                return True
        return False

    def day_ok(d):
        rd, rw = dom not in ("*", "?"), dow not in ("*", "?")
        if rd and rw:
            return dom_ok(d) or dow_ok(d)
        return (dom_ok(d) if rd else True) and (dow_ok(d) if rw else True)

    return M, H, MO, day_ok


def scan(expr, after, zone, limit_days=400):
    z = ZoneInfo(zone)
    M, H, MO, day_ok = matcher(expr)
    t = after.astimezone(timezone.utc).replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = t + timedelta(days=limit_days)
    while t < end:
        loc = t.astimezone(z)
        if loc.month not in MO or not day_ok(loc.date()):
            nxt = datetime.combine(loc.date() + timedelta(days=1), datetime.min.time(), tzinfo=z)
            t = max(t + timedelta(minutes=1), nxt.astimezone(timezone.utc).replace(second=0))
            continue
        if loc.fold == 0 and loc.hour in H and loc.minute in M:
            return t
        t += timedelta(minutes=1)
    return None
