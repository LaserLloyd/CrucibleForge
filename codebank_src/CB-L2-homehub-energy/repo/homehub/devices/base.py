"""Common device behaviour."""
from datetime import datetime, timezone

from ..events import Event

registry: dict[str, type] = {}


class Device:
    kind = "device"

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        registry[cls.kind] = cls

    def __init__(self, device_id: str, name: str, bus=None):
        self.id, self.name, self.bus = device_id, name, bus
        self.state: dict = {}
        self.last_seen: datetime | None = None

    def report(self, kind: str, data: dict, at: datetime | None = None, key: str = "") -> Event:
        at = at or datetime.now(timezone.utc)
        self.last_seen = at
        self.state.update(data)
        ev = Event(self.id, kind, at, dict(data), key)
        if self.bus is not None:
            self.bus.publish(ev)
        return ev

    def snapshot(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "state": dict(self.state),
                "last_seen": self.last_seen.isoformat() if self.last_seen else None}
