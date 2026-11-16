"""A regrade never restamps an attempt that a newer attempt of the same instance supersedes (lead 2026-11-15).

A regrade sets ``ts`` to now on every row it writes, and the board reads the newest graded row of each
(unit, rung, attempt) by (ts, file order) (chains.results_from_rows). Restamping an older run's attempt
beside a newer run's attempt that stays untouched (current) made the OLD attempt the newest one. The
older attempt is now ``superseded``: nothing is written for it."""
from __future__ import annotations

import copy

from crucibleforge import chains, regrade

from test_regrade_20261114 import NOW, _fixture, _reset_switches  # noqa: F401  (autouse fixture)

OLD_TS, NEW_TS = "2026-11-14T10:00:00+00:00", "2026-11-15T01:00:00+00:00"


def _two_runs(monkeypatch, old_stale: bool, new_stale: bool, old_last_in_file: bool = False):
    rows, specs, rules = _fixture(monkeypatch, "implied_stale", stale=False)   # every row current here
    old, new = copy.deepcopy(rows), copy.deepcopy(rows)
    for r in old:
        r["bench_run_id"], r["ts"] = "old", OLD_TS
        if old_stale:
            r["chain_revision"] = "stale000"
    for r in new:
        r["bench_run_id"], r["ts"] = "new", NEW_TS
        if new_stale:
            r["chain_revision"] = "stale111"
    both = (new + old) if old_last_in_file else (old + new)
    return both, specs, rules


def _board_runs(rows):
    """Which run the board reads for every instance (the newest graded row wins)."""
    seen = {}
    for i, r in enumerate(rows):
        ch = r.get("chain")
        if ch and ch.get("passed") is not None:
            k = (ch["id"], ch["rung"], ch["attempt"])
            if k not in seen or (str(r["ts"]), i) >= seen[k][0]:
                seen[k] = ((str(r["ts"]), i), r["bench_run_id"])
    return {k: v[1] for k, v in seen.items()}


def _apply(rows, recs):
    return rows + [row for rec in recs if rec["status"] == "regrade" for row in rec["rows"]]


def test_an_older_attempt_beside_a_current_newer_one_is_superseded_not_restamped(monkeypatch):
    rows, specs, rules = _two_runs(monkeypatch, old_stale=True, new_stale=False)
    assert set(_board_runs(rows).values()) == {"new"}
    recs = regrade.plan(rows, specs, "test", rules=rules, now=NOW, commit="abc1234")
    by_run = {}
    for r in recs:
        by_run.setdefault(r["bench_run_id"], set()).add(r["status"])
    assert by_run == {"old": {"superseded"}, "new": {"current"}}
    assert all("run new" in r["why"] for r in recs if r["status"] == "superseded")
    after = _apply(rows, recs)
    assert after == rows                                        # nothing written
    assert set(_board_runs(after).values()) == {"new"}          # the board still reads the newer attempt
    assert sum(s["superseded"] for s in regrade.summary(recs)) == len(by_run and [r for r in recs if r["bench_run_id"] == "old"])
    assert "superseded" in regrade.render(recs)


def test_both_runs_stale_only_the_newer_one_is_regraded(monkeypatch):
    rows, specs, rules = _two_runs(monkeypatch, old_stale=True, new_stale=True)
    recs = regrade.plan(rows, specs, "test", rules=rules, now=NOW, commit="abc1234")
    assert {r["status"] for r in recs if r["bench_run_id"] == "old"} == {"superseded"}
    assert {r["status"] for r in recs if r["bench_run_id"] == "new"} == {"regrade"}
    after = _apply(rows, recs)
    assert set(_board_runs(after).values()) == {"new"}
    newest = [r for r in after if r["bench_run_id"] == "new" and r.get("regraded_by")]
    assert newest and all(r["chain_revision"] == chains.revision(r["chain"]) for r in newest)


def test_the_newer_attempt_wins_by_time_not_by_file_position(monkeypatch):
    # the older run sits LATER in the file: the newer ts still decides which attempt is current
    rows, specs, rules = _two_runs(monkeypatch, old_stale=True, new_stale=False, old_last_in_file=True)
    recs = regrade.plan(rows, specs, "test", rules=rules, now=NOW, commit="abc1234")
    assert {r["status"] for r in recs if r["bench_run_id"] == "old"} == {"superseded"}
    assert set(_board_runs(_apply(rows, recs)).values()) == {"new"}


def test_a_single_run_is_unchanged(monkeypatch):
    rows, specs, rules = _fixture(monkeypatch, "implied_stale")
    recs = regrade.plan(rows, specs, "test", rules=rules, now=NOW, commit="abc1234")
    assert {r["status"] for r in recs} == {"regrade"}


def test_stamp_pass_skips_a_superseded_attempt_too():
    winners = regrade.superseded_runs([
        {"bench_run_id": "a", "ts": OLD_TS, "repeat": 1, "chain": {"id": "U", "rung": 1, "attempt": 1, "passed": True}},
        {"bench_run_id": "b", "ts": NEW_TS, "repeat": 1, "chain": {"id": "U", "rung": 1, "attempt": 1, "passed": False}}])
    assert winners == {("U", 1, 1, 1): "b"}
    grp = [{"bench_run_id": "a", "repeat": 1, "chain": {"id": "U", "rung": 1, "attempt": 1}}]
    assert regrade._superseded_by(grp, winners) == "b"
    assert regrade._superseded_by([dict(grp[0], bench_run_id="b")], winners) is None
