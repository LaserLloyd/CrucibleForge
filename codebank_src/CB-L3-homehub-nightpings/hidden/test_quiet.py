from datetime import timedelta

from _night import U, local
from homehub.notify import Notifier


def test_quiet_hours_are_local_winter_and_summer():
    for day, nxt in (((2026, 1, 10), (2026, 1, 11)), ((2026, 7, 10), (2026, 7, 11))):
        n = Notifier(throttle=timedelta(0))
        assert n.notify("r", "d", "a", at=local(*day, 21, 59).astimezone(U)) == "sent"
        assert n.notify("r", "d", "b", at=local(*day, 22, 0).astimezone(U)) == "held"
        assert n.notify("r", "d", "c", at=local(*day, 23, 30).astimezone(U)) == "held"
        assert n.notify("r", "d", "f", urgent=True, at=local(*nxt, 3, 0).astimezone(U)) == "sent"
        assert n.notify("r", "d", "d", at=local(*nxt, 6, 59).astimezone(U)) == "held"
        assert n.notify("r", "d", "e", at=local(*nxt, 7, 0).astimezone(U)) == "sent"


def test_flush_only_after_quiet_hours():
    n = Notifier()
    n.notify("r", "d", "x", at=local(2026, 7, 10, 23, 0).astimezone(U))
    assert not n.flush(local(2026, 7, 11, 6, 30).astimezone(U))
    assert n.flush(local(2026, 7, 11, 7, 0).astimezone(U))
    assert n.sent[-1][1] == "While you slept: x"
