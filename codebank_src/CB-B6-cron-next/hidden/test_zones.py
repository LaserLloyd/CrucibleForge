from datetime import datetime, timezone

from cronplan.cron import next_fire

UTC = timezone.utc


def test_gap_skips_and_fall_back_once():
    nf = lambda e, a, z="Europe/Berlin": next_fire(e, a, z).isoformat()
    # 02:30 does not exist on 2026-03-29 in Berlin: no run that day
    assert nf("30 2 * * *", datetime(2026, 3, 28, 2, 0, tzinfo=UTC)) == "2026-03-30T00:30:00+00:00"
    # 02:30 happens twice on 2026-10-25: only the first
    assert nf("30 2 * * *", datetime(2026, 10, 24, 23, 0, tzinfo=UTC)) == "2026-10-25T00:30:00+00:00"
    assert nf("30 2 * * *", datetime(2026, 10, 25, 0, 30, tzinfo=UTC)) == "2026-10-26T01:30:00+00:00"
    assert nf("*/30 2 * * *", datetime(2026, 10, 25, 0, 45, tzinfo=UTC)) == "2026-10-26T01:00:00+00:00"


def test_strictly_after_and_seconds():
    got = next_fire("0 9 * * *", datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC), "Asia/Tokyo")
    assert got == datetime(2026, 1, 5, 0, 0, tzinfo=UTC) + __import__("datetime").timedelta(days=1)
    got = next_fire("0 9 * * *", datetime(2026, 1, 4, 23, 59, 59, tzinfo=UTC), "Asia/Tokyo")
    assert got == datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def test_never_fires():
    import pytest
    with pytest.raises(ValueError):
        next_fire("0 0 31 2 *", datetime(2026, 1, 1, tzinfo=UTC), "UTC")
