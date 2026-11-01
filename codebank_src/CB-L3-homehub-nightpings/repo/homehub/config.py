"""Loading homehub.toml."""
import tomllib
from datetime import timedelta
from zoneinfo import ZoneInfo

from .util.timeparse import Window, parse_duration

DEFAULTS = {"zone": "Europe/Berlin", "quiet": "22:00-07:00", "throttle": "30m",
            "tariff": {"base": 28, "peak": 36, "peak_windows": ["17:00-21:00"]}}


class Config:
    def __init__(self, raw: dict):
        self.zone = raw.get("zone", DEFAULTS["zone"])
        ZoneInfo(self.zone)                                  # raises on an unknown zone
        self.quiet = raw.get("quiet", DEFAULTS["quiet"])
        if self.quiet:
            Window.parse(self.quiet)
        self.throttle: timedelta = parse_duration(raw.get("throttle", DEFAULTS["throttle"]))
        self.tariff = {**DEFAULTS["tariff"], **raw.get("tariff", {})}
        for w in self.tariff["peak_windows"]:
            Window.parse(w)
        self.devices: list[dict] = raw.get("device", [])
        self.rules: list[dict] = raw.get("rule", [])
        self.triggers: dict[str, str] = raw.get("triggers", {})


def load(path) -> Config:
    with open(path, "rb") as fh:
        return Config(tomllib.load(fh))
