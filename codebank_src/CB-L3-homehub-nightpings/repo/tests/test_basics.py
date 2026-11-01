from datetime import datetime, timedelta, timezone

from homehub.config import Config
from homehub.hub import Hub
from homehub.util.timeparse import Window, parse_duration

UTC = timezone.utc


def test_window_and_duration():
    w = Window.parse("22:00-06:00")
    assert w.contains(datetime(2026, 1, 1, 23, 0).time()) and not w.contains(datetime(2026, 1, 1, 12, 0).time())
    assert parse_duration("15m") == timedelta(minutes=15)


def test_hub_wires_devices_and_history():
    cfg = Config({"device": [{"id": "tv", "kind": "plug"}, {"id": "front", "kind": "contact"}]})
    hub = Hub(cfg)
    hub.devices["tv"].meter(100, at=datetime(2026, 3, 2, 8, 0, tzinfo=UTC))
    assert hub.history.last("tv")["data"] == {"wh": 100}
