from _nb import CHI, UTC, chi, chore, utc
from nudgebot.schedule import last_due, next_due


def test_weekly_keeps_local_time_across_dst():
    c = chore(every="weekly", at="07:00", start="2026-03-02")
    assert next_due(c, CHI, chi(2026, 3, 2, 7)) == chi(2026, 3, 9, 7).astimezone(UTC)
    assert next_due(c, CHI, chi(2026, 3, 2, 7)) == utc(2026, 3, 9, 12)
    assert next_due(c, CHI, chi(2026, 10, 26, 7)) == chi(2026, 11, 2, 7).astimezone(UTC)
    assert last_due(c, CHI, utc(2026, 3, 9, 12, 30)) == utc(2026, 3, 9, 12)
    assert last_due(c, CHI, utc(2026, 3, 9, 11, 59)) == utc(2026, 3, 2, 13)


def test_n_days_counted_in_calendar_days():
    c = chore(every="3d", at="19:00", start="2026-03-05")
    assert next_due(c, CHI, utc(2026, 3, 6, 0)) == utc(2026, 3, 6, 1)          # 19:00 CST on 03-05
    assert next_due(c, CHI, utc(2026, 3, 6, 1)) == utc(2026, 3, 9, 0)          # 19:00 CDT on 03-08
    assert next_due(c, CHI, utc(2026, 3, 9, 0)) == utc(2026, 3, 12, 0)


def test_gap_moves_to_first_minute_after():
    c = chore(every="daily", at="02:30", start="2026-03-07")
    assert next_due(c, CHI, chi(2026, 3, 7, 2, 30)) == chi(2026, 3, 8, 3, 0).astimezone(UTC)
    assert next_due(c, CHI, chi(2026, 3, 8, 3, 0)) == chi(2026, 3, 9, 2, 30).astimezone(UTC)


def test_repeated_hour_fires_once_first():
    c = chore(every="daily", at="01:30", start="2026-10-31")
    first = next_due(c, CHI, chi(2026, 10, 31, 1, 30))
    assert first == chi(2026, 11, 1, 1, 30, fold=0).astimezone(UTC) == utc(2026, 11, 1, 6, 30)
    assert next_due(c, CHI, first) == chi(2026, 11, 2, 1, 30).astimezone(UTC)
    assert last_due(c, CHI, utc(2026, 11, 1, 7, 45)) == first


def test_strictly_after_and_not_started():
    c = chore(every="daily", at="07:00", start="2026-04-01")
    assert next_due(c, CHI, chi(2026, 4, 1, 7)) == chi(2026, 4, 2, 7).astimezone(UTC)
    assert next_due(c, CHI, utc(2026, 1, 1, 0)) == chi(2026, 4, 1, 7).astimezone(UTC)
    assert last_due(c, CHI, chi(2026, 4, 1, 6, 59)) is None
    assert last_due(c, CHI, chi(2026, 4, 1, 7)) == chi(2026, 4, 1, 7).astimezone(UTC)
