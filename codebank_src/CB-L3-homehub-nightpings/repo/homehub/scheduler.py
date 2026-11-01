"""Daily triggers at local wall-clock times ("07:00 every day")."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .events import Event
from .util.timeparse import parse_clock


class Scheduler:
    """`add(name, "HH:MM")` registers a daily trigger in the house zone.
    `due(since, until)` returns the (name, instant) pairs that fire in
    (since, until], in time order, each at most once per local day. A wall
    time skipped by a clock change fires at the shifted time; a repeated one
    fires once, at its first occurrence."""

    def __init__(self, zone: str = "Europe/Berlin"):
        self.zone = ZoneInfo(zone)
        self.triggers: dict[str, time] = {}

    def add(self, name: str, clock: str) -> None:
        self.triggers[name] = parse_clock(clock)

    def _instant(self, day, t):
        return datetime.combine(day, t, tzinfo=self.zone).replace(fold=0).astimezone(timezone.utc)

    def due(self, since: datetime, until: datetime) -> list[tuple[str, datetime]]:
        out = []
        day = since.astimezone(self.zone).date() - timedelta(days=1)
        last = until.astimezone(self.zone).date() + timedelta(days=1)
        while day <= last:
            for name, t in self.triggers.items():
                at = self._instant(day, t)
                if since < at <= until:
                    out.append((name, at))
            day += timedelta(days=1)
        return sorted(out, key=lambda x: (x[1], x[0]))

    def tick(self, bus, since: datetime, until: datetime) -> int:
        n = 0
        for name, at in self.due(since, until):
            bus.publish(Event("scheduler", "trigger", at, {"trigger": name}, key=f"{name}@{at.isoformat()}"))
            n += 1
        return n
