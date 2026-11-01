import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from _oracle import oracle
from famcal.rrule import expand, parse_rrule

DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _rule(rnd):
    freq = rnd.choice(["DAILY", "WEEKLY", "MONTHLY"])
    parts = [f"FREQ={freq}"]
    if rnd.random() < 0.6:
        parts.append(f"INTERVAL={rnd.randint(1, 4)}")
    if freq == "MONTHLY":
        c = rnd.random()
        if c < 0.35:
            parts.append("BYMONTHDAY=" + ",".join(str(rnd.choice([1, 15, 28, 29, 30, 31, -1, -2, -7])) for _ in range(rnd.randint(1, 2))))
        elif c < 0.75:
            parts.append("BYDAY=" + ",".join(rnd.choice(["", "1", "2", "3", "4", "5", "-1", "-2"]) + rnd.choice(DAYS)
                                             for _ in range(rnd.randint(1, 2))))
    elif rnd.random() < 0.6:
        parts.append("BYDAY=" + ",".join(rnd.sample(DAYS, rnd.randint(1, 3))))
    if rnd.random() < 0.4:
        parts.append(f"COUNT={rnd.randint(1, 20)}")
    elif rnd.random() < 0.4:
        parts.append("UNTIL=2026-12-15T12:00:00Z".replace("-", "").replace(":", ""))
    return ";".join(parts)


def test_random_rules_match_brute_force():
    rnd = random.Random(1029)
    for zone in ("Europe/Berlin", "America/New_York", "Asia/Tokyo"):
        z = ZoneInfo(zone)
        for _ in range(120):
            text = _rule(rnd)
            s = datetime(2026, rnd.randint(1, 12), rnd.randint(1, 28), rnd.choice([0, 2, 7, 23]),
                         rnd.choice([0, 30]), tzinfo=z)
            a = s + timedelta(days=rnd.randint(-20, 60))
            b = a + timedelta(days=rnd.randint(1, 200))
            ex = [x for x in oracle(s, parse_rrule(text), a.astimezone(timezone.utc), b.astimezone(timezone.utc))[:3]
                  if rnd.random() < 0.5]
            want = oracle(s, parse_rrule(text), a.astimezone(timezone.utc), b.astimezone(timezone.utc), ex)
            assert expand(s, parse_rrule(text), a, b, ex) == want, (zone, text, s, a, b)
