from datetime import datetime, timezone

from cronplan.cron import next_fire

UTC = timezone.utc


def nf(expr, y, m, d, h=0, mi=0, zone="UTC"):
    return next_fire(expr, datetime(y, m, d, h, mi, tzinfo=UTC), zone).strftime("%Y-%m-%d %H:%M")


def test_last_day_and_nearest_weekday():
    assert nf("0 0 L * *", 2026, 2, 1) == "2026-02-28 00:00"
    assert nf("0 9 1W * *", 2026, 7, 31) == "2026-08-03 09:00"     # Aug 1 2026 is a Saturday
    assert nf("0 9 31W * *", 2026, 5, 1) == "2026-05-29 09:00"     # May 31 2026 is a Sunday
    assert nf("0 9 30W * *", 2026, 2, 1) == "2026-03-30 09:00"     # no 30th in February
    assert nf("0 9 15W * *", 2026, 8, 1) == "2026-08-14 09:00"     # Aug 15 2026 is a Saturday


def test_nth_and_last_weekday():
    assert nf("0 8 ? * 5#2", 2026, 1, 1) == "2026-01-09 08:00"     # second Friday
    assert nf("0 8 * * 1#5", 2026, 1, 1) == "2026-03-30 08:00"     # first month with a 5th Monday
    assert nf("0 20 * * 0L", 2026, 1, 1) == "2026-01-25 20:00"     # last Sunday
    assert nf("0 20 * * SUNL", 2026, 1, 26) == "2026-02-22 20:00"


def test_dom_dow_either_rule():
    # the 13th OR any Friday
    assert nf("0 12 13 * 5", 2026, 2, 7) == "2026-02-13 12:00"
    assert nf("0 12 13 * 5", 2026, 2, 13, 13) == "2026-02-20 12:00"
    assert nf("0 12 13 * *", 2026, 2, 14) == "2026-03-13 12:00"
    assert nf("0 12 ? * 7", 2026, 2, 14) == "2026-02-15 12:00"
