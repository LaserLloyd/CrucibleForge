import pytest

from famcal.rrule import parse_rrule


@pytest.mark.parametrize("bad", ["FREQ=YEARLY", "COUNT=3", "FREQ=DAILY;COUNT=2;UNTIL=20260101T000000Z",
                                 "FREQ=WEEKLY;BYDAY=2TU", "FREQ=MONTHLY;BYDAY=6MO", "FREQ=DAILY;INTERVAL=0",
                                 "FREQ=MONTHLY;BYMONTHDAY=0", "FREQ=DAILY;WKST=SU"])
def test_rejects(bad):
    with pytest.raises(ValueError):
        parse_rrule(bad)


def test_defaults():
    assert parse_rrule("FREQ=DAILY") == {"freq": "DAILY", "interval": 1, "byday": [], "bymonthday": [],
                                         "count": None, "until": None}
