from datetime import datetime, timezone

from nudgebot.dispatch import tick

CFG = {"tz": "UTC", "quiet": "00:00-00:00", "members": ["Ana", "Ben"],
       "chores": [{"id": "plants", "title": "Water plants", "every": "daily", "at": "09:00",
                   "start": "2026-05-04", "rotate": False}]}


def test_daily_nudge_posted_once():
    state = {}
    assert tick(datetime(2026, 5, 4, 8, 59, tzinfo=timezone.utc), CFG, state) == []
    first = tick(datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc), CFG, state)
    assert [(n.chore_id, n.assignee) for n in first] == [("plants", "everyone")]
    assert tick(datetime(2026, 5, 4, 9, 5, tzinfo=timezone.utc), CFG, state) == []
