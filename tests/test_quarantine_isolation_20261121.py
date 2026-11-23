"""The test suite never reads or writes the live quarantine store, and a closed
model record never re-arms the circuit breaker.

2026-11-21: test runs had left model records named ``bad`` and ``m`` in the
operator's store; with one real early stop they made three in a row, opened a
queue halt, and every later run (and 19 runner tests) skipped its models.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from crucibleforge import quarantine

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

_LIVE = Path.home() / ".local" / "share" / "crucibleforge" / "quarantine"


def _under(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def test_the_store_is_a_per_test_dir_never_the_live_one():
    env = os.environ.get("CRUCIBLEFORGE_QUARANTINE")
    assert env, "CRUCIBLEFORGE_QUARANTINE is not set during the test: store_dir() is the live store"
    d = quarantine.store_dir()
    assert not _under(d, _LIVE) and not _under(_LIVE, d)
    assert not _under(d, Path.home() / ".local" / "share" / "crucibleforge")
    assert not list(d.glob("q-*.yaml")), "the store a test starts with must be empty"


def test_a_record_written_by_one_test_is_not_seen_by_the_next_part_1():
    quarantine.hold(model="leak-probe", stage="run", klass="degenerate_output", reason="probe")
    assert [r["model"] for r in quarantine.records()] == ["leak-probe"]


def test_a_record_written_by_one_test_is_not_seen_by_the_next_part_2():
    assert quarantine.records() == []


def test_the_runner_stop_child_process_gets_the_isolated_store(tmp_path):
    import test_runner_stop_39 as stop
    env = stop._child_env(tmp_path, 30)
    assert env.get("CRUCIBLEFORGE_QUARANTINE") == os.environ["CRUCIBLEFORGE_QUARANTINE"]


# ------------------------------------------------------------ circuit breaker

def _early(model):
    return quarantine.hold(model=model, stage="run", klass="degenerate_output", reason="early stop: blank")


def test_closed_model_records_do_not_count_toward_three_in_a_row():
    a, b = _early("bad"), _early("m")
    quarantine.set_status(a["id"], "rejected")
    quarantine.set_status(b["id"], "fixed")
    _early("real-model")
    assert quarantine.queue_halted() is None


def test_one_closed_record_between_open_ones_does_not_complete_the_three():
    a = _early("a")
    _early("b")
    quarantine.set_status(a["id"], "escalated")
    _early("c")
    assert quarantine.queue_halted() is None
    _early("d")                      # b, c, d open: three in a row
    halt = quarantine.queue_halted()
    assert halt and halt["class"] == "circuit"


def test_three_open_model_records_still_halt():
    for m in ("x", "y", "z"):
        _early(m)
    assert quarantine.queue_halted()
