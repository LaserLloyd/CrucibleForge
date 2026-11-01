from _e import T, tariff
from homehub.energy import daily_costs


def test_interval_across_peak_start_is_split():
    r = [(T("2026-01-15T15:30"), 0), (T("2026-01-15T16:30"), 1000)]      # 16:30-17:30 Berlin
    assert daily_costs(r, tariff()) == {"2026-01-15": {"wh": 1000, "cents": 30}}


def test_interval_across_local_midnight_is_split():
    r = [(T("2026-01-15T22:30"), 0), (T("2026-01-15T23:30"), 600)]      # 23:30-00:30 Berlin
    assert daily_costs(r, tariff()) == {"2026-01-15": {"wh": 300, "cents": 6},
                                        "2026-01-16": {"wh": 300, "cents": 6}}


def test_window_crossing_midnight():
    r = [(T("2026-01-15T20:00"), 0), (T("2026-01-16T00:00"), 4000)]      # 21:00-01:00 Berlin, peak 22-02
    got = daily_costs(r, tariff(("22:00-02:00",)))
    assert got == {"2026-01-15": {"wh": 3000, "cents": 20 + 40 + 40}, "2026-01-16": {"wh": 1000, "cents": 40}}
