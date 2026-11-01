from datetime import datetime, timezone

from homehub.energy import Tariff

UTC = timezone.utc
TARIFF = Tariff(20, 40, [])


def T(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def tariff(peak=("17:00-21:00",)):
    return Tariff.from_config({"base": 20, "peak": 40, "peak_windows": list(peak)})
