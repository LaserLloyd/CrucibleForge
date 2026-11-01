import pytest

from nudge.rules import parse_rule


@pytest.mark.parametrize("day,idx", [("mon", 0), ("Tue", 1), ("WED", 2), ("thu", 3), ("fri", 4), ("sat", 5), ("sun", 6)])
def test_weekday_numbers(day, idx):
    assert parse_rule(f"weekly {day} 18:30 Europe/Berlin") == {
        "every": "week", "time": "18:30", "weekday": idx, "tz": "Europe/Berlin"}


@pytest.mark.parametrize("text", ["weekly 18:30 Europe/Berlin", "weekly funday 18:30 Europe/Berlin",
                                  "daily mon 07:00 Asia/Tokyo", "daily 24:00 Asia/Tokyo",
                                  "daily 07:00 Mars/Olympus", "hourly 07:00 Asia/Tokyo"])
def test_rejects(text):
    with pytest.raises(ValueError):
        parse_rule(text)


def test_weekly_next_fire_lands_on_the_named_day():
    from datetime import datetime, timezone
    from nudge.schedule import next_fire, to_iso
    r = parse_rule("weekly mon 09:00 Asia/Tokyo")
    # 2026-10-25 is a Sunday
    assert to_iso(next_fire(r, datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc))) == "2026-10-26T00:00:00Z"
