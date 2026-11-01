from _nb import UTC, chi, chore, cfg
from nudgebot.dispatch import tick
from nudgebot.model import Nudge
from nudgebot.schedule import period_days


def test_basic_daily_flow_and_shape():
    c = cfg(chore("plants", at="09:00", start="2026-05-04"))
    st = {}
    assert tick(chi(2026, 5, 4, 8, 59).astimezone(UTC), c, st) == []
    out = tick(chi(2026, 5, 4, 9, 0).astimezone(UTC), c, st)
    assert len(out) == 1 and isinstance(out[0], Nudge)
    assert (out[0].chore_id, out[0].title, out[0].assignee) == ("plants", "plants", "everyone")
    assert tick(chi(2026, 5, 4, 9, 5).astimezone(UTC), c, st) == []


def test_period_days():
    assert [period_days(x) for x in ("daily", "weekly", "5d")] == [1, 7, 5]
