from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from famcal.rrule import expand, parse_rrule

BER = ZoneInfo("Europe/Berlin")
UTC = timezone.utc


def test_wall_time_kept_and_gap_shifted():
    s = datetime(2026, 3, 27, 2, 30, tzinfo=BER)
    got = expand(s, parse_rrule("FREQ=DAILY;COUNT=4"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert [t.isoformat() for t in got] == ["2026-03-27T01:30:00+00:00", "2026-03-28T01:30:00+00:00",
                                            "2026-03-29T01:30:00+00:00", "2026-03-30T00:30:00+00:00"]


def test_fall_back_first_occurrence():
    s = datetime(2026, 10, 24, 2, 30, tzinfo=BER)
    got = expand(s, parse_rrule("FREQ=DAILY;COUNT=2"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert [t.isoformat() for t in got] == ["2026-10-24T00:30:00+00:00", "2026-10-25T00:30:00+00:00"]


def test_count_includes_window_start_and_exdates():
    s = datetime(2026, 1, 5, 18, 0, tzinfo=BER)
    r = parse_rrule("FREQ=WEEKLY;COUNT=5")
    ex = [datetime(2026, 1, 19, 17, 0, tzinfo=UTC)]
    got = expand(s, r, datetime(2026, 1, 15, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC), ex)
    assert [t.strftime("%m-%d") for t in got] == ["01-26", "02-02"]


def test_results_are_utc_and_window_half_open():
    s = datetime(2026, 1, 5, 18, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    got = expand(s, parse_rrule("FREQ=DAILY"), datetime(2026, 1, 6, 9, 0, tzinfo=UTC), datetime(2026, 1, 8, 9, 0, tzinfo=UTC))
    assert [t.isoformat() for t in got] == ["2026-01-06T09:00:00+00:00", "2026-01-07T09:00:00+00:00"]
    assert all(t.utcoffset().total_seconds() == 0 for t in got)
