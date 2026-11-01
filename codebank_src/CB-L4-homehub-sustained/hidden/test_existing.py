from datetime import datetime, timezone

from homehub.config import Config
from homehub.energy import Tariff, daily_costs
from homehub.hub import Hub
from homehub.util.timeparse import Window

UTC = timezone.utc


def test_plain_rules_and_summary():
    hub = Hub(Config({"device": [{"id": "living", "kind": "temp"}],
                      "rule": [{"name": "cold", "on": "reading", "if": "temp.living < 18", "then": ['notify "cold"']}]}))
    hub.devices["living"].reading(17.0, at=datetime(2026, 1, 15, 22, 30, tzinfo=UTC))
    hub.devices["living"].reading(16.0, at=datetime(2026, 1, 15, 22, 35, tzinfo=UTC))
    assert len(hub.engine.fired) == 1
    hub.tick(datetime(2026, 1, 16, 5, 59, tzinfo=UTC), datetime(2026, 1, 16, 6, 0, tzinfo=UTC))
    assert [t for _, t in hub.notifier.sent] == ["While you slept: cold"]


def test_energy():
    T = lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC)
    r = [(T("2026-01-15T15:30"), 0), (T("2026-01-15T16:30"), 1000)]
    assert daily_costs(r, Tariff(20, 40, [Window.parse("17:00-21:00")])) == {"2026-01-15": {"wh": 1000, "cents": 30}}
