from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from famcal.rrule import expand, parse_rrule

BER = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
A, B = datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)


def iso_days(ts):
    return [t.astimezone(BER).strftime("%m-%d") for t in ts]


def test_ordinal_weekdays():
    s = datetime(2026, 1, 13, 19, 0, tzinfo=BER)                       # 2nd Tuesday of January
    got = expand(s, parse_rrule("FREQ=MONTHLY;BYDAY=2TU;COUNT=4"), A, B)
    assert iso_days(got) == ["01-13", "02-10", "03-10", "04-14"]
    got = expand(datetime(2026, 1, 1, 9, 0, tzinfo=BER), parse_rrule("FREQ=MONTHLY;BYDAY=-1FR;COUNT=3"), A, B)
    assert iso_days(got) == ["01-30", "02-27", "03-27"]


def test_fifth_weekday_and_31st_are_skipped_not_moved():
    got = expand(datetime(2026, 1, 1, 9, 0, tzinfo=BER), parse_rrule("FREQ=MONTHLY;BYDAY=5MO"), A,
                 datetime(2026, 9, 1, tzinfo=UTC))
    assert iso_days(got) == ["03-30", "06-29", "08-31"]
    got = expand(datetime(2026, 1, 31, 9, 0, tzinfo=BER), parse_rrule("FREQ=MONTHLY;COUNT=4"), A, B)
    assert iso_days(got) == ["01-31", "03-31", "05-31", "07-31"]


def test_negative_monthday_and_interval():
    got = expand(datetime(2026, 1, 1, 9, 0, tzinfo=BER), parse_rrule("FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=1,-1"), A,
                 datetime(2026, 7, 1, tzinfo=UTC))
    assert iso_days(got) == ["01-01", "01-31", "03-01", "03-31", "05-01", "05-31"]
