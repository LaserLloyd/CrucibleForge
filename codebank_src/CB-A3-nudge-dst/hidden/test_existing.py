from datetime import datetime, timedelta, timezone

from nudge.rules import parse_rule
from nudge.schedule import next_fire, to_iso

UTC = timezone.utc


def test_tokyo_daily_and_iso():
    r = parse_rule("daily 07:00 Asia/Tokyo")
    t = next_fire(r, datetime(2026, 5, 1, 22, 0, tzinfo=UTC))
    assert to_iso(t) == "2026-05-02T22:00:00Z"
    assert to_iso(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=9)))) == "2026-01-01T18:04:05Z"


def test_spring_forward_gap_shifts_forward():
    r = parse_rule("daily 02:30 Europe/Berlin")
    assert to_iso(next_fire(r, datetime(2026, 3, 28, 23, 0, tzinfo=UTC))) == "2026-03-29T01:30:00Z"
    assert to_iso(next_fire(r, datetime(2026, 3, 29, 1, 30, tzinfo=UTC))) == "2026-03-30T00:30:00Z"


def test_non_utc_after_and_errors():
    r = parse_rule("daily 07:00 Asia/Tokyo")
    after = datetime(2026, 5, 2, 6, 59, tzinfo=timezone(timedelta(hours=9)))
    assert to_iso(next_fire(r, after)) == "2026-05-01T22:00:00Z"
    import pytest
    with pytest.raises(ValueError):
        parse_rule("daily 7:00 Asia/Tokyo")
