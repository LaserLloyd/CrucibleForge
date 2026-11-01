from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from famcal.rrule import expand, parse_rrule

BER = ZoneInfo("Europe/Berlin")
UTC = timezone.utc


def test_biweekly_byday_counted_from_dtstart_week():
    s = datetime(2026, 1, 8, 7, 30, tzinfo=BER)                        # Thursday
    got = expand(s, parse_rrule("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH"), datetime(2026, 1, 1, tzinfo=UTC),
                 datetime(2026, 2, 3, tzinfo=UTC))
    assert [t.astimezone(BER).strftime("%m-%d %a") for t in got] == [
        "01-08 Thu", "01-19 Mon", "01-22 Thu", "02-02 Mon"]


def test_daily_with_byday_filter_and_until():
    s = datetime(2026, 3, 2, 6, 45, tzinfo=BER)                        # Monday
    r = parse_rrule("FREQ=DAILY;BYDAY=MO,WE,FR;UNTIL=20260311T054500Z")
    got = expand(s, r, datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert [t.astimezone(BER).strftime("%d") for t in got] == ["02", "04", "06", "09", "11"]


def test_dtstart_not_matching_rule_is_not_an_occurrence():
    s = datetime(2026, 1, 7, 10, 0, tzinfo=BER)                        # Wednesday
    got = expand(s, parse_rrule("FREQ=WEEKLY;BYDAY=TU;COUNT=2"), datetime(2026, 1, 1, tzinfo=UTC),
                 datetime(2026, 3, 1, tzinfo=UTC))
    assert [t.astimezone(BER).strftime("%m-%d") for t in got] == ["01-13", "01-20"]
