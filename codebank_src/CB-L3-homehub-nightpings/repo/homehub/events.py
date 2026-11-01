"""The event bus.

Devices and the scheduler publish `Event`s; subscribers (the rule engine,
storage, the notifier) receive them in publish order. Devices on flaky Wi-Fi
resend the same report, so the bus drops an event whose (source, kind, key)
was already seen within `dedupe_window` of the earlier one's time.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable


@dataclass(frozen=True)
class Event:
    source: str                 # device id or "scheduler"
    kind: str                   # "reading", "state", "trigger", ...
    at: datetime                # aware
    data: dict = field(default_factory=dict, compare=False, hash=False)
    key: str = ""               # what makes two events "the same report"

    def __post_init__(self):
        if self.at.tzinfo is None:
            raise ValueError("Event.at must be timezone-aware")


class Bus:
    def __init__(self, dedupe_window: timedelta = timedelta(seconds=30)):
        self.dedupe_window = dedupe_window
        self._subs: list[tuple[str | None, Callable[[Event], None]]] = []
        self._seen: dict[tuple[str, str, str], datetime] = {}
        self.dropped = 0

    def subscribe(self, fn: Callable[[Event], None], kind: str | None = None) -> None:
        self._subs.append((kind, fn))

    def publish(self, ev: Event) -> bool:
        """Deliver `ev`; returns False when it was dropped as a duplicate."""
        k = (ev.source, ev.kind, ev.key)
        at = ev.at.astimezone(timezone.utc)
        last = self._seen.get(k)
        if ev.key and last is not None and abs(at - last) < self.dedupe_window:
            self.dropped += 1
            return False
        self._seen[k] = at
        for kind, fn in list(self._subs):
            if kind is None or kind == ev.kind:
                fn(ev)
        return True
