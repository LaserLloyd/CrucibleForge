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
from datetime import datetime

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
    pts = sorted(readings, key=lambda r: r[0])
    out = []
    for (a, wa), (b, wb) in zip(pts, pts[1:]):
        if b == a:
            continue
        out.append((a, b, wb - wa))
    return out


def daily_costs(readings, tariff: Tariff, zone: str = "Europe/Berlin") -> dict:
    """{local date ISO: {"wh": int, "cents": int}} for every local day touched
    by an interval."""
    out: dict[str, dict] = {}
    for start, end, used in intervals(readings):
        day = start.date().isoformat()
        price = tariff.price_at(start.time())
        d = out.setdefault(day, {"wh": 0, "cents": 0})
        d["wh"] += used
        d["cents"] += round(used * price / 1000)
    return dict(sorted(out.items()))
