"""Energy accounting for smart plugs.

A plug reports a cumulative meter reading (Wh since the plug's last reset).
Consumption between two consecutive readings is the difference; if a reading
is LOWER than the previous one the plug was reset (power cut, firmware
update) and the new reading itself is the consumption since the reset.

Cost uses a time-of-use tariff: `peak` window(s) in the house's local wall
time with their own price, everything else at the base price. The energy of
an interval is spread evenly over the interval's duration, so an interval
that crosses a window boundary (or local midnight, for daily totals) is split
in proportion to the time on each side. Daily totals belong to the LOCAL
calendar day. Money: a day's cost is computed exactly (Fractions) and rounded
half-up to whole cents once per day; never round per interval.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

from .util.timeparse import Window


@dataclass
class Tariff:
    base_cents_per_kwh: int
    peak_cents_per_kwh: int
    peak_windows: list            # [Window]

    @classmethod
    def from_config(cls, cfg: dict) -> "Tariff":
        return cls(int(cfg["base"]), int(cfg["peak"]), [Window.parse(w) for w in cfg.get("peak_windows", [])])

    def price_at(self, local_time) -> int:
        return self.peak_cents_per_kwh if any(w.contains(local_time) for w in self.peak_windows) \
            else self.base_cents_per_kwh


def intervals(readings):
    """[(start, end, wh)] from [(at, cumulative_wh)] sorted by time. Readings at
    the same instant: the later in the list wins."""
    latest = {}
    for at, wh in readings:              # same instant: the later in the list wins
        latest[at] = wh
    pts = sorted(latest.items())
    out = []
    for (a, wa), (b, wb) in zip(pts, pts[1:]):
        used = wb - wa if wb >= wa else wb
        out.append((a, b, used))
    return out


def _boundaries(start: datetime, end: datetime, zone, tariff: Tariff):
    """Instants in (start, end) where the price or the local date can change:
    window edges and local midnights, as UTC instants."""
    cuts = set()
    day = start.astimezone(zone).date() - timedelta(days=1)
    last = end.astimezone(zone).date() + timedelta(days=1)
    clocks = {datetime.min.time()}
    for w in tariff.peak_windows:
        clocks.add(w.start)
        clocks.add(w.end)
    while day <= last:
        for c in clocks:
            t = datetime.combine(day, c, tzinfo=zone).astimezone(timezone.utc)
            if start < t < end:
                cuts.add(t)
        day += timedelta(days=1)
    return sorted(cuts)


def daily_costs(readings, tariff: Tariff, zone: str = "Europe/Berlin") -> dict:
    """{local date ISO: {"wh": Fraction-rounded int, "cents": int}} for every
    local day touched by an interval."""
    z = ZoneInfo(zone)
    wh_by_day: dict[str, Fraction] = {}
    cost_by_day: dict[str, Fraction] = {}
    for start, end, used in intervals(readings):
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
        total = Fraction((end - start).total_seconds())
        cuts = [start, end]
        for a, b in zip(cuts, cuts[1:]):
            part = Fraction(used) * Fraction((b - a).total_seconds()) / total
            local = a.astimezone(z)
            day = local.date().isoformat()
            price = tariff.price_at(local.time())
            wh_by_day[day] = wh_by_day.get(day, Fraction(0)) + part
            cost_by_day[day] = cost_by_day.get(day, Fraction(0)) + part * price / 1000
    out = {}
    for day in sorted(wh_by_day):
        out[day] = {"wh": _half_up(wh_by_day[day]), "cents": _half_up(cost_by_day[day])}
    return out


def _half_up(x: Fraction) -> int:
    return int((x * 2 + 1) // 2) if x >= 0 else -int((-x * 2 + 1) // 2)
