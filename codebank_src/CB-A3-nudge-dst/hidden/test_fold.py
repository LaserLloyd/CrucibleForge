from datetime import datetime, timedelta, timezone

from nudge.rules import parse_rule
from nudge.schedule import next_fire, to_iso

UTC = timezone.utc


def _at(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_fall_back_fires_once_at_first_occurrence():
    r = parse_rule("daily 02:30 Europe/Berlin")
    assert to_iso(next_fire(r, _at("2026-10-24T23:00"))) == "2026-10-25T00:30:00Z"
    # after the first 02:30 (CEST) has fired, the second 02:30 (CET) must not fire again
    for after in ("2026-10-25T00:30", "2026-10-25T00:45", "2026-10-25T01:15", "2026-10-25T01:29"):
        assert to_iso(next_fire(r, _at(after))) == "2026-10-26T01:30:00Z", after


def test_never_goes_backwards_through_the_night():
    r = parse_rule("daily 02:15 Europe/Berlin")
    t = _at("2026-10-24T20:00")
    end = _at("2026-10-26T06:00")
    seen = []
    while t < end:
        nxt = next_fire(r, t)
        assert nxt > t and nxt.utcoffset() == timedelta(0)
        seen.append(nxt)
        t = t + timedelta(minutes=7)
    assert sorted(set(to_iso(x) for x in seen)) == ["2026-10-25T00:15:00Z", "2026-10-26T01:15:00Z", "2026-10-27T01:15:00Z"]


def test_fall_back_us_zone():
    r = parse_rule("daily 01:30 America/New_York")
    assert to_iso(next_fire(r, _at("2026-11-01T05:31"))) == "2026-11-02T06:30:00Z"
    assert to_iso(next_fire(r, _at("2026-11-01T04:00"))) == "2026-11-01T05:30:00Z"
