"""Notifications to the family's phones, throttled.

* The same rule on the same device notifies at most once per `throttle`
  (default 30 min); repeats inside that window are counted, not sent.
* During quiet hours (house-zone window, default 22:00-07:00) non-urgent
  notifications are held; urgent ones go out at once. When quiet hours end,
  one summary message lists the held notifications, oldest first, as
  "<text> (xN)" when a text was held N > 1 times.
* `flush(now)` sends what is due; `sent` keeps everything sent (for tests).
"""
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from .util.timeparse import Window


class Notifier:
    def __init__(self, throttle=timedelta(minutes=30), quiet="22:00-07:00", zone="Europe/Berlin"):
        self.throttle = throttle
        self.quiet = Window.parse(quiet) if quiet else None
        self.zone = ZoneInfo(zone)
        self._last: dict[tuple[str, str], object] = {}
        self.suppressed = 0
        self._held: list[tuple[object, str]] = []
        self.sent: list[tuple[str, str]] = []          # (iso UTC instant, text)

    def _quiet(self, at) -> bool:
        return self.quiet is not None and self.quiet.contains(at.time())

    def notify(self, rule: str, device: str, text: str, urgent: bool = False, at=None) -> str:
        """Returns "sent", "held" or "throttled"."""
        at = at.astimezone(timezone.utc)
        key = (rule, device)
        last = self._last.get(key)
        if last is not None and at - last < self.throttle:
            self.suppressed += 1
            return "throttled"
        self._last[key] = at
        if urgent or not self._quiet(at):
            self.sent.append((at.isoformat(), text))
            return "sent"
        self._held.append((at, text))
        return "held"

    def flush(self, now) -> bool:
        """Send the summary of held notifications once quiet hours are over."""
        now = now.astimezone(timezone.utc)
        if not self._held or self._quiet(now):
            return False
        counts: dict[str, int] = {}
        order: list[str] = []
        for _, text in sorted(self._held, key=lambda h: h[0]):
            if text not in counts:
                order.append(text)
            counts[text] = counts.get(text, 0) + 1
        lines = [t if counts[t] == 1 else f"{t} (x{counts[t]})" for t in order]
        self.sent.append((now.isoformat(), "While you slept: " + "; ".join(lines)))
        self._held.clear()
        return True
