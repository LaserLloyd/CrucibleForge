from datetime import datetime, timedelta, timezone

from homehub.config import Config
from homehub.hub import Hub
from homehub.notify import Notifier
from homehub.rules import Engine, Rule
from homehub.scheduler import Scheduler

UTC = timezone.utc


def test_rules_and_notify_unchanged():
    n = Notifier(quiet="22:00-07:00")
    hub = Hub(Config({"device": [{"id": "living", "kind": "temp"}],
                      "rule": [{"name": "cold", "on": "reading", "if": "temp.living < 18",
                                "then": ['notify "cold"']}]}))
    hub.devices["living"].reading(17.0, at=datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
    hub.devices["living"].reading(16.5, at=datetime(2026, 1, 15, 12, 5, tzinfo=UTC))
    assert hub.engine.fired == [("cold", "2026-01-15T12:00:00+00:00")]
    assert [t for _, t in hub.notifier.sent] == ["cold"]
    assert n.notify("r", "d", "x", at=datetime(2026, 1, 15, 22, 30, tzinfo=UTC)) == "held"


def test_scheduler_unchanged():
    s = Scheduler()
    s.add("morning", "07:00")
    got = s.due(datetime(2026, 3, 28, 0, 0, tzinfo=UTC), datetime(2026, 3, 30, 12, 0, tzinfo=UTC))
    assert [at.isoformat() for _, at in got] == ["2026-03-28T06:00:00+00:00", "2026-03-29T05:00:00+00:00",
                                                 "2026-03-30T05:00:00+00:00"]
