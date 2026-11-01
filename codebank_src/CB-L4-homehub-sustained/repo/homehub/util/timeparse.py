"""Clock times, daily windows and durations."""
import re
from datetime import time, timedelta

_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_DURATION = re.compile(r"^(\d+)\s*(s|m|h|d)$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_clock(text: str) -> time:
    """'7:05' / '07:05' -> time(7, 5). ValueError otherwise."""
    m = _CLOCK.match(text.strip())
    if not m:
        raise ValueError(f"bad clock time {text!r}")
    return time(int(m.group(1)), int(m.group(2)))


def parse_duration(text: str) -> timedelta:
    """'90s', '15m', '2h', '1d' -> timedelta. ValueError otherwise."""
    m = _DURATION.match(text.strip().lower())
    if not m:
        raise ValueError(f"bad duration {text!r}")
    return timedelta(seconds=int(m.group(1)) * _UNIT_SECONDS[m.group(2)])


class Window:
    """A daily wall-clock window [start, end). If end <= start the window
    crosses midnight ("22:00-06:00"). "00:00-00:00" is the whole day."""

    def __init__(self, start: time, end: time):
        self.start, self.end = start, end

    @classmethod
    def parse(cls, text: str) -> "Window":
        try:
            a, b = text.split("-")
        except ValueError:
            raise ValueError(f"bad window {text!r}") from None
        return cls(parse_clock(a), parse_clock(b))

    def contains(self, t: time) -> bool:
        if self.start == self.end:
            return True
        if self.start < self.end:
            return self.start <= t < self.end
        return t >= self.start or t < self.end

    def __repr__(self):
        return f"Window({self.start:%H:%M}-{self.end:%H:%M})"
