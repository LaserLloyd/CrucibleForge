"""Time spent waiting for a refused GPU lease is booked as "waiting", never as "generation" minutes; the run's
wall-clock total keeps it (the shakedown's "ladder round 1 generation 83.9 min" held a 5-minute wait)."""
import json

from crucibleforge import report, studioforge, timing


def test_clock_add_split_books_the_wait_apart():
    c = timing.Clock()
    c.add_split("ladder round 1 generation", 83.9 * 60, ["m"], waited_s=300, record_meta=False)
    by = {n: s for n, s, _ in c.rows}
    assert abs(by["ladder round 1 generation"] - 78.9 * 60) < 1e-6
    assert abs(by["ladder round 1 waiting"] - 300) < 1e-6
    c2 = timing.Clock()
    c2.add_split("generation", 600, ["m"], waited_s=0, record_meta=False)
    assert [n for n, _, _ in c2.rows] == ["generation"]          # no wait, no extra row


def test_a_wait_never_exceeds_the_phase():
    c = timing.Clock()
    c.add_split("generation", 60, ["m"], waited_s=500, record_meta=False)
    by = {n: s for n, s, _ in c.rows}
    assert by["generation"] == 0 and by["waiting for the rig"] == 60


def test_a_refused_lease_wait_is_counted(monkeypatch):
    before = timing.waited_total()
    timing.add_wait(7.5)
    assert timing.waited_total() - before == 7.5


def test_acquire_lease_books_its_refusal_waits(monkeypatch):
    replies = [(503, {"error": "priority_hold", "_retry_after_s": 4}), (200, {"id": "L1", "devices": [0]})]
    clock = {"t": 0.0}
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(studioforge.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(studioforge, "_sleep", lambda s: clock.__setitem__("t", clock["t"] + 4.0))
    before = timing.waited_total()
    studioforge.acquire_lease("http://x", "k", {}, [0], model_ids=["m"], holder="h", wait_busy_s=60)
    assert timing.waited_total() - before == 4.0


def test_run_minutes_keep_the_wall_clock_and_generation_excludes_the_wait():
    meta = {"bench_run_id": "r", "timing": [
        {"phase": "ladder round 1 generation", "s": 78.9 * 60},
        {"phase": "ladder round 1 waiting", "s": 300.0},
        {"phase": "generation", "s": 600.0}, {"phase": "waiting for the rig", "s": 120.0},
        {"phase": "judge", "s": 60.0}]}
    assert report.timing_minutes45(meta) == 13.0                  # generation 10 + waiting 2 + judge 1
    assert report.timing_pack_minutes45(meta) == 83.9             # the pack rounds' wall time, wait included
    gen = sum(t["s"] for t in meta["timing"] if t["phase"].startswith("generation"))
    assert gen == 600.0
