import pytest

from _nb import UTC, cfg, chi, chore, utc
from nudgebot.dispatch import tick
from nudgebot.state import load_state, save_state


def test_round_trip_exact(tmp_path):
    p = tmp_path / "state.json"
    s = {"chores": {"x": {"last": utc(2026, 3, 9, 12), "who": "Ana", "names": ["Ana", "Ben"]}},
         "when": chi(2026, 11, 1, 3, 30), "n": 3}
    save_state(p, s)
    back = load_state(p)
    assert back == s
    assert back["chores"]["x"]["last"].utcoffset() is not None
    assert load_state(tmp_path / "missing.json") == {}


def test_restart_does_not_repeat(tmp_path):
    p = tmp_path / "state.json"
    c = cfg(chore("plants", at="09:00", start="2026-03-08"),
            chore("dishes", at="19:00", start="2026-03-02", rotate=True))
    st = {}
    first = tick(chi(2026, 3, 9, 20, 0).astimezone(UTC), c, st)
    assert len(first) == 2
    save_state(p, st)
    st2 = load_state(p)
    assert tick(chi(2026, 3, 9, 20, 5).astimezone(UTC), c, st2) == []
    nxt = tick(chi(2026, 3, 10, 19, 0).astimezone(UTC), c, st2)
    assert [(n.chore_id, n.assignee) for n in nxt] == [("plants", "everyone"), ("dishes", "Ben")]


class Boom:
    def __str__(self):
        raise RuntimeError("disk full")

    __repr__ = __str__


def test_failed_save_keeps_previous_file(tmp_path):
    p = tmp_path / "state.json"
    good = {"chores": {"x": {"last": utc(2026, 3, 9, 12)}}, "a": 1}
    save_state(p, good)
    with pytest.raises(Exception):
        save_state(p, {"a": 1, "z": Boom()})
    assert load_state(p) == good
