import random
from datetime import datetime, timedelta, timezone

from _scan import scan
from cronplan.cron import next_fire


def _field(rnd, lo, hi, names=None):
    c = rnd.random()
    if c < 0.3:
        return "*"
    if c < 0.5:
        return f"*/{rnd.randint(2, max(2, (hi - lo) // 2))}"
    if c < 0.7:
        a = rnd.randint(lo, hi - 1)
        return f"{a}-{rnd.randint(a, hi)}"
    return ",".join(str(rnd.randint(lo, hi)) for _ in range(rnd.randint(1, 3)))


def _expr(rnd):
    mi = rnd.choice(["0", "30", "*/20", "15,45", _field(rnd, 0, 59)])
    ho = rnd.choice(["*", "2", "0-3", "*/6", "1,2,3", _field(rnd, 0, 23)])
    dom = rnd.choice(["*", "*", "?", "L", f"{rnd.randint(1, 31)}W", _field(rnd, 1, 31), "1,15"])
    mo = rnd.choice(["*", "*", "*/2", "3,10", _field(rnd, 1, 12)])
    dow = rnd.choice(["*", "*", "?", f"{rnd.randint(0, 7)}#{rnd.randint(1, 5)}", f"{rnd.randint(0, 6)}L",
                      "MON-FRI", _field(rnd, 0, 7)])
    return " ".join([mi, ho, dom, mo, dow])


def test_random_expressions_match_brute_force():
    rnd = random.Random(31)
    checked = 0
    for zone in ("Europe/Berlin", "America/New_York", "UTC"):
        for _ in range(70):
            e = _expr(rnd)
            a = datetime(2026, rnd.randint(1, 12), rnd.randint(1, 28), rnd.randint(0, 23), rnd.randint(0, 59),
                         tzinfo=timezone.utc)
            want = scan(e, a, zone)
            if want is None:
                continue
            assert next_fire(e, a, zone) == want, (e, a, zone)
            checked += 1
    assert checked > 150
