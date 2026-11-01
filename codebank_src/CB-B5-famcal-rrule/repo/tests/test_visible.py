from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from famcal.rrule import expand, parse_rrule

BER = ZoneInfo("Europe/Berlin")
UTC = timezone.utc


def test_parse():
    assert parse_rrule("FREQ=MONTHLY;BYDAY=2TU,-1FR;COUNT=5")["byday"] == [(2, 1), (-1, 4)]


def test_weekly_basic():
    start = datetime(2026, 1, 5, 18, 0, tzinfo=BER)          # a Monday
    got = expand(start, parse_rrule("FREQ=WEEKLY;COUNT=3"), datetime(2026, 1, 1, tzinfo=UTC),
                 datetime(2026, 3, 1, tzinfo=UTC))
    assert [t.isoformat() for t in got] == ["2026-01-05T17:00:00+00:00", "2026-01-12T17:00:00+00:00",
                                            "2026-01-19T17:00:00+00:00"]
