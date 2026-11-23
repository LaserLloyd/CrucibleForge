"""S6 (fix round 2026-11-18, AUDIT-HARNESS F2): the board kept the rows of the NEWEST run per (case, repeat), so an
aborted resume that wrote one turn of a finished session replaced the whole session (State went pending, Chat fell
from 5.7 to 4.2), and a complete second draw replaced the first with no mark anywhere. Now the newest COMPLETE job
wins (its final turn graded, or an error row); a partial newer run never replaces a finished job; a complete re-draw
does, and its rows carry a ``rerun`` mark naming both run ids. The audit's two synthetic cases are locked here."""
from __future__ import annotations

from crucibleforge import report


def _trace(rid, ts, passed, score):
    # one-call chain unit (Trace): one graded row
    return {"case_id": f"FLAT-rc2_trace4_L4-00{score}-r1-a1", "repeat": 1, "turn": None, "bench_run_id": rid,
            "ts": ts, "category": "coding",
            "chain": {"id": "U", "rung": 1, "attempt": 1, "passed": passed, "score": score}}


def _state(rid, ts, n, final=True):
    rows = []
    for t in range(1, n + 1):
        ch = {"id": "S", "rung": 1, "attempt": 1}
        r = {"case_id": "FLAT-rc2_state_L10-001-r1-a1", "repeat": 1, "turn": t, "bench_run_id": rid,
             "ts": f"{ts}{t:02d}", "category": "rpdeep", "response": f"t{t}", "chain": ch}
        if final and t == n:
            ch.update(passed=True, score=1.0)
            r["conversation"] = [{"role": "assistant", "content": f"t{i}"} for i in range(1, n + 1)]
        rows.append(r)
    return rows


def test_an_aborted_resume_never_replaces_a_finished_session():
    old = _state("aaaa", "2026-11-14T10:00:", 5)
    stray = _state("bbbb", "2026-11-18T01:35:", 1, final=False)       # syn/state_aborted2: one turn-1 row
    got = report._board_rows(old + stray)
    assert [r["bench_run_id"] for r in got] == ["aaaa"] * 5
    assert not any(r.get("rerun") for r in got)


def test_a_complete_redraw_replaces_and_is_marked_with_both_run_ids():
    old = [{**_trace("aaaa", "2026-11-14T10:00:00", False, 0)}]
    old[0]["case_id"] = "FLAT-rc2_trace4_L4-001-r1-a1"
    new = [{**_trace("ffff", "2026-11-18T00:00:00", True, 1)}]          # syn/trace_rerun_perfect
    new[0]["case_id"] = "FLAT-rc2_trace4_L4-001-r1-a1"
    got = report._board_rows(old + new)
    assert len(got) == 1 and got[0]["bench_run_id"] == "ffff" and got[0]["chain"]["passed"] is True
    assert got[0]["rerun"] == {"run_ids": ["aaaa", "ffff"], "kept": "ffff"}
    # the input rows are not written to
    assert "rerun" not in new[0]


def test_a_complete_redraw_of_a_session_replaces_the_old_session():
    got = report._board_rows(_state("aaaa", "2026-11-14T10:00:", 4) + _state("cccc", "2026-11-18T09:00:", 4))
    assert {r["bench_run_id"] for r in got} == {"cccc"} and len(got) == 4
    assert all(r["rerun"]["run_ids"] == ["aaaa", "cccc"] for r in got)


def test_an_error_row_completes_a_job_and_a_lone_run_is_kept_as_before(monkeypatch):
    # the legacy rule (newest complete run wins, an error row is a finished job to re-run); under the first-reply rule
    # (profile attempt_rule, tests/test_attempts45_20261119.py) a later infrastructure failure never replaces a reply
    from crucibleforge import attempts45
    monkeypatch.setattr(attempts45, "rule_on", lambda row: False)
    old = _state("aaaa", "2026-11-14T10:00:", 3)
    err = [{"case_id": "FLAT-rc2_state_L10-001-r1-a1", "repeat": 1, "turn": 1, "bench_run_id": "eeee",
            "ts": "2026-11-18T00:00:00", "grade": "error", "error": "HTTP 503"}]
    got = report._board_rows(old + err)
    assert [r["bench_run_id"] for r in got] == ["eeee"]                # an error is a job to re-run (pending)
    # only a partial run exists: it is still the board's (the part reads pending, as before)
    part = _state("bbbb", "2026-11-18T01:35:", 2, final=False)
    assert report._board_rows(part) == part


def test_single_rows_and_ties_behave_as_before():
    a = {"case_id": "CB-C5", "repeat": 1, "turn": None, "bench_run_id": "r1", "ts": "2026-11-14T10:00:00"}
    b = {**a, "bench_run_id": "r2"}                                    # same second: the later line wins
    got = report._board_rows([a, b])
    assert [r["bench_run_id"] for r in got] == ["r2"] and got[0]["rerun"]["run_ids"] == ["r1", "r2"]
    # the same run's re-judged copies are one job, never a re-draw
    c = {**a, "judge": {"x": 1}}
    assert not any(r.get("rerun") for r in report._board_rows([a, c]))
