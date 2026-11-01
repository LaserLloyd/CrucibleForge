from _nb import CHI, UTC, chi
from nudgebot.quiet import send_time


def st(dt, q="22:00-07:00"):
    return send_time(dt.astimezone(UTC), q, CHI).astimezone(CHI)


def test_wrapping_quiet_hours():
    assert st(chi(2026, 3, 10, 6, 0)) == chi(2026, 3, 10, 7, 0)
    assert st(chi(2026, 3, 10, 23, 30)) == chi(2026, 3, 11, 7, 0)
    assert st(chi(2026, 3, 10, 22, 0)) == chi(2026, 3, 11, 7, 0)      # start is inside
    assert st(chi(2026, 3, 10, 7, 0)) == chi(2026, 3, 10, 7, 0)       # end is not
    assert st(chi(2026, 3, 10, 12, 0)) == chi(2026, 3, 10, 12, 0)


def test_plain_and_empty_quiet_hours():
    assert st(chi(2026, 3, 10, 13, 15), "13:00-14:30") == chi(2026, 3, 10, 14, 30)
    assert st(chi(2026, 3, 10, 12, 59), "13:00-14:30") == chi(2026, 3, 10, 12, 59)
    assert st(chi(2026, 3, 10, 3, 0), "07:00-07:00") == chi(2026, 3, 10, 3, 0)


def test_quiet_end_in_a_dst_gap():
    # quiet hours end at 02:30, which does not exist on 2026-03-08
    assert st(chi(2026, 3, 8, 1, 0), "23:00-02:30") == chi(2026, 3, 8, 3, 0)
    assert send_time(chi(2026, 3, 7, 23, 30).astimezone(UTC), "23:00-02:30", CHI) == chi(2026, 3, 8, 3, 0).astimezone(UTC)
