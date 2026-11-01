from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homehub.config import Config
from homehub.hub import Hub

Z = ZoneInfo("Europe/Berlin")
U = timezone.utc


def at(h, m, s=0, day=10):
    return datetime(2026, 1, day, h, m, s, tzinfo=Z).astimezone(U)


def hub_with(rule_if, cooldown="0s", on="state"):
    return Hub(Config({"device": [{"id": "front", "kind": "contact"}, {"id": "x", "kind": "temp"}],
                       "rule": [{"name": "r", "on": on, "if": rule_if, "then": ['notify "r" urgent'],
                                 "cooldown": cooldown}],
                       "quiet": ""}))


def fired(hub):
    return [t for _, t in hub.engine.fired]


def tick(hub, a, b):
    hub.tick(a, b)
