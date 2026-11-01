from datetime import datetime, timezone

from nudge.rules import parse_rule
from nudge.schedule import occurrences, to_iso

UTC = timezone.utc


def test_daily_keeps_wall_time_across_fall_back():
    r = parse_rule("daily 07:00 Europe/Berlin")
    got = [to_iso(t) for t in occurrences(r, datetime(2026, 10, 23, 5, 0, tzinfo=UTC), datetime(2026, 10, 28, tzinfo=UTC))]
    assert got == ["2026-10-23T05:00:00Z", "2026-10-24T05:00:00Z", "2026-10-25T06:00:00Z",
                   "2026-10-26T06:00:00Z", "2026-10-27T06:00:00Z"]


def test_weekly_across_spring_forward_and_bounds():
    r = parse_rule("weekly sun 07:00 Europe/Berlin")
    got = [to_iso(t) for t in occurrences(r, datetime(2026, 3, 15, 6, 0, tzinfo=UTC), datetime(2026, 4, 5, 5, 0, tzinfo=UTC))]
    assert got == ["2026-03-15T06:00:00Z", "2026-03-22T06:00:00Z", "2026-03-29T05:00:00Z"]


def test_start_included_end_excluded():
    r = parse_rule("daily 07:00 Asia/Tokyo")
    got = occurrences(r, datetime(2026, 5, 1, 22, 0, tzinfo=UTC), datetime(2026, 5, 3, 22, 0, tzinfo=UTC))
    assert [to_iso(t) for t in got] == ["2026-05-01T22:00:00Z", "2026-05-02T22:00:00Z"]
    assert occurrences(r, datetime(2026, 5, 1, 22, 0, tzinfo=UTC), datetime(2026, 5, 1, 22, 0, tzinfo=UTC)) == []


def test_gap_day_inside_a_span():
    r = parse_rule("daily 02:30 Europe/Berlin")
    got = [to_iso(t) for t in occurrences(r, datetime(2026, 3, 28, 0, 0, tzinfo=UTC), datetime(2026, 3, 31, tzinfo=UTC))]
    assert got == ["2026-03-28T01:30:00Z", "2026-03-29T01:30:00Z", "2026-03-30T00:30:00Z"]
