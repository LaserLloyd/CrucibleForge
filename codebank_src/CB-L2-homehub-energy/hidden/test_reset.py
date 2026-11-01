from _e import T
from homehub.energy import intervals


def test_meter_reset_counts_from_zero():
    r = [(T("2026-01-15T10:00"), 1000), (T("2026-01-15T11:00"), 1500), (T("2026-01-15T12:00"), 200),
         (T("2026-01-15T13:00"), 300)]
    assert [w for _, _, w in intervals(r)] == [500, 200, 100]


def test_unsorted_and_same_instant():
    r = [(T("2026-01-15T12:00"), 900), (T("2026-01-15T10:00"), 100), (T("2026-01-15T11:00"), 400),
         (T("2026-01-15T11:00"), 500)]
    assert [w for _, _, w in intervals(r)] == [400, 400]
