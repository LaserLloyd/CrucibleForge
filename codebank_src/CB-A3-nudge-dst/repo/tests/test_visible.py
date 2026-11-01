from datetime import datetime, timezone

from nudge.rules import parse_rule
from nudge.schedule import next_fire, to_iso

UTC = timezone.utc


def test_parse_daily():
    assert parse_rule("daily 07:00 Asia/Tokyo") == {"every": "day", "time": "07:00", "tz": "Asia/Tokyo"}


def test_next_daily_tokyo():
    r = parse_rule("daily 07:00 Asia/Tokyo")
    assert to_iso(next_fire(r, datetime(2026, 5, 1, 0, 0, tzinfo=UTC))) == "2026-05-01T22:00:00Z"
