from datetime import datetime, timezone

from cronplan.cron import next_fire

UTC = timezone.utc


def test_every_15_minutes():
    got = next_fire("*/15 * * * *", datetime(2026, 1, 5, 10, 7, tzinfo=UTC), "UTC")
    assert got == datetime(2026, 1, 5, 10, 15, tzinfo=UTC)
