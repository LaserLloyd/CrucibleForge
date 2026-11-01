from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homehub.config import Config
from homehub.hub import Hub

Z = ZoneInfo("Europe/Berlin")
U = timezone.utc


def house():
    return Hub(Config({"device": [{"id": "front", "kind": "contact"}, {"id": "living", "kind": "temp"},
                                  {"id": "boiler", "kind": "thermostat"}, {"id": "smoke", "kind": "contact"}],
                       "rule": [{"name": "door-open", "on": "state", "if": "door.front == open",
                                 "then": ['notify "Front door is open"']},
                                {"name": "heat-up", "on": "reading", "if": "temp.living < 18",
                                 "then": ["thermostat.boiler 20", 'notify "Heating on"']},
                                {"name": "smoke", "on": "state", "if": "door.smoke == open",
                                 "then": ['notify "SMOKE ALARM" urgent']}],
                       "triggers": {"morning": "07:00"}}))


def local(*a):
    return datetime(*a, tzinfo=Z)
