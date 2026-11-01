from _e import T, tariff
from homehub.energy import daily_costs


def test_round_once_per_day_half_up():
    r = [(T("2026-01-15T08:00"), 0), (T("2026-01-15T09:00"), 333), (T("2026-01-15T10:00"), 666),
         (T("2026-01-15T11:00"), 999)]
    assert daily_costs(r, tariff()) == {"2026-01-15": {"wh": 999, "cents": 20}}       # 19.98 -> 20, not 3 x 7
    r = [(T("2026-01-15T08:00"), 0), (T("2026-01-15T09:00"), 25)]
    assert daily_costs(r, tariff())["2026-01-15"]["cents"] == 1                       # 0.5 -> 1 (half up)
    r = [(T("2026-01-15T08:00"), 0), (T("2026-01-15T09:00"), 75), (T("2026-01-15T10:00"), 150)]
    assert daily_costs(r, tariff())["2026-01-15"]["cents"] == 3                       # 1.5 + 1.5 = 3, not 2 + 2
