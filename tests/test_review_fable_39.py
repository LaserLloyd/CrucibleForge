"""Fable review of 3.9 (2026-10-29): --first keeps the order it was given;
an --until that already passed today is refused instead of running a day."""
from datetime import datetime

import pytest

from crucibleforge import bench_all

from test_bench_all import _cfg, _done, _m, _plan


def test_first_runs_in_the_order_given_across_and_within_groups():
    cfg = _cfg([_m("a", "org/a"), _m("b", "org/b"), _m("q8", "org/q8")])
    same = {"a": _done("C1"), "b": _done("C1"), "q8": _done("C1")}
    p = _plan(cfg, ["org/a", "org/b", "org/q8"], rows=same, first=["q8", "a"])
    assert [g["models"] for g in p["groups"]] == [["q8"], ["a"], ["b"]]
    # different missing sets: still the listed order
    rows = {"a": _done("C1"), "b": _done("C1", "C2")}
    p = _plan(cfg, ["org/a", "org/b", "org/q8"], rows=rows, first=["b", "q8", "a"])
    assert [g["models"] for g in p["groups"]] == [["b"], ["q8"], ["a"]]


def test_an_until_already_past_is_refused_not_run_for_a_day():
    with pytest.raises(SystemExit):
        bench_all.deadlines("16:00", 45, now=datetime(2026, 10, 29, 16, 5))
    # the nightly window across midnight and a day window still work
    _, end = bench_all.deadlines("03:00", 45, now=datetime(2026, 10, 29, 1, 0))
    assert end == datetime(2026, 10, 29, 3, 0)
    _, end = bench_all.deadlines("16:00", 45, now=datetime(2026, 10, 29, 9, 30))
    assert end == datetime(2026, 10, 29, 16, 0)
