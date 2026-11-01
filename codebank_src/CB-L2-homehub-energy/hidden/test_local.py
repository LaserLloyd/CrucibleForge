from _e import T, tariff
from homehub.energy import daily_costs


def test_summer_time_peak():
    r = [(T("2026-07-01T15:00"), 0), (T("2026-07-01T16:00"), 1000)]      # 17:00-18:00 CEST
    assert daily_costs(r, tariff()) == {"2026-07-01": {"wh": 1000, "cents": 40}}


def test_winter_and_other_zone():
    r = [(T("2026-01-15T16:00"), 0), (T("2026-01-15T17:00"), 1000)]      # 17:00-18:00 CET
    assert daily_costs(r, tariff()) == {"2026-01-15": {"wh": 1000, "cents": 40}}
    assert daily_costs(r, tariff(), "Asia/Tokyo") == {"2026-01-16": {"wh": 1000, "cents": 20}}


def test_day_of_a_late_evening_interval():
    r = [(T("2026-03-02T23:10"), 0), (T("2026-03-02T23:40"), 100)]      # 00:10-00:40 on the 3rd, Berlin
    assert list(daily_costs(r, tariff())) == ["2026-03-03"]
