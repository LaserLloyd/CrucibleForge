"""Retry fairness (attempts45): the scored attempt of a unit is its first reply; a later attempt replaces an earlier
one only after an infrastructure failure. Row selection at report time; no grader, no stamp."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from crucibleforge import attempts45 as A
from crucibleforge import fixrows45, report

ROOT = Path(__file__).resolve().parent.parent
CASE = "PATCHFIX-core-7-1"


def _r(run, ts, *, bench="b1", case=CASE, repeat=1, finish="stop", text="reply", grade=None, **kw):
    row = {"case_id": case, "repeat": repeat, "category": "coding", "profile": "bench45", "run_id": run,
           "bench_run_id": bench, "ts": ts, "turn": None, "finish_reason": finish, "response": text,
           "fixcheck": {"score": kw.pop("score", 0.0), "grade": "fail", "checker": "x", "stamp": fixrows45.checker_stamp()}}
    if grade:
        row["grade"] = grade
    row.update(kw)
    return row


def _err(run, ts, **kw):
    return {**_r(run, ts, finish="error", text="", **kw), "grade": "error", "error": "HTTP 503", "error_kind": "transport",
            "fixcheck": None}


def _scored(rows):
    return [r for r in report._board_rows(rows) if r["case_id"] == CASE]


def test_cut_then_good_the_cut_one_scores():
    rows = [_r("a1", "2026-11-19T10:00:00+00:00", finish="length", score=0.0),
            _r("a2", "2026-11-19T11:00:00+00:00", score=1.0)]
    out = _scored(rows)
    assert [r["run_id"] for r in out] == ["a1"]
    assert out[0]["attempts45"] == {"ignored": 1, "replaced_infra": 0, "kept": "a1"}


def test_infra_failure_then_good_the_good_one_scores():
    rows = [_err("a1", "2026-11-19T10:00:00+00:00"), _r("a2", "2026-11-19T11:00:00+00:00", score=1.0)]
    out = _scored(rows)
    assert [r["run_id"] for r in out] == ["a2"]
    assert out[0]["attempts45"]["replaced_infra"] == 1 and out[0]["attempts45"]["ignored"] == 0


def test_good_then_good_the_first_scores_and_the_count_is_kept():
    rows = [_r("a1", "2026-11-19T10:00:00+00:00", score=0.0), _r("a2", "2026-11-19T11:00:00+00:00", score=1.0),
            _r("a3", "2026-11-19T12:00:00+00:00", score=1.0)]
    out = _scored(rows)
    assert [r["run_id"] for r in out] == ["a1"] and out[0]["attempts45"]["ignored"] == 2
    s = A.summary(report._board_rows(rows))
    assert s["jobs"] == 1 and s["ignored"] == 2 and s["by_job"][f"{CASE}#r1"]["kept"] == "a1"


def test_a_dropped_stream_and_a_timeout_are_infrastructure_but_a_stop_with_text_is_a_reply():
    dropped = _r("a1", "2026-11-19T10:00:00+00:00", finish="", text="")
    wall = _r("a1", "2026-11-19T10:00:00+00:00", finish="wall_timeout", text="", error_kind="wall")
    assert A.is_infra(dropped) and A.is_infra(wall)
    assert not A.is_infra(_r("a1", "2026-11-19T10:00:00+00:00", finish="length"))
    assert not A.is_infra(_r("a1", "2026-11-19T10:00:00+00:00", finish="stop", text="x"))


def test_all_infrastructure_keeps_the_last_attempt_as_the_missing_job():
    rows = [_err("a1", "2026-11-19T10:00:00+00:00"), _err("a2", "2026-11-19T11:00:00+00:00")]
    out = _scored(rows)
    assert [r["run_id"] for r in out] == ["a2"] and out[0]["attempts45"]["replaced_infra"] == 1


def test_a_whole_job_redraw_by_the_operator_keeps_the_rerun_mark_and_the_newest_run():
    rows = [_r("a1", "2026-11-19T10:00:00+00:00", bench="b1", score=0.0), _r("a2", "2026-11-19T12:00:00+00:00", bench="b2", score=1.0)]
    out = _scored(rows)
    assert [r["run_id"] for r in out] == ["a2"] and out[0]["rerun"]["run_ids"] == ["b1", "b2"]


def test_an_errored_run_then_a_good_run_is_not_a_rerun():
    rows = [_err("a1", "2026-11-19T10:00:00+00:00", bench="b1"), _r("a2", "2026-11-19T12:00:00+00:00", bench="b2", score=1.0)]
    out = _scored(rows)
    assert [r["run_id"] for r in out] == ["a2"] and "rerun" not in out[0]


def test_a_later_infrastructure_failure_never_replaces_a_reply_even_in_a_new_run():
    rows = [_r("a1", "2026-11-19T10:00:00+00:00", bench="b1", score=0.5), _err("a2", "2026-11-19T12:00:00+00:00", bench="b2")]
    out = _scored(rows)
    assert [r["run_id"] for r in out] == ["a1"] and "rerun" not in out[0]


def test_numbered_multi_turn_sessions_are_not_split():
    rows = [{**_r(f"t{i}", f"2026-11-19T10:0{i}:00+00:00"), "turn": i} for i in (1, 2, 3)]
    kept, mark = A.pick(rows)
    assert kept == rows and mark is None


def test_the_rule_is_off_for_other_profiles_and_the_input_rows_are_never_written():
    rows = [_r("a1", "2026-11-19T10:00:00+00:00", finish="length"), _r("a2", "2026-11-19T11:00:00+00:00")]
    before = copy.deepcopy(rows)
    A.select(rows)
    assert rows == before
    old = [{**r, "profile": "no-such-profile"} for r in rows]
    assert len(report._board_rows(old)) == 2          # a profile without the rule keeps both rows, as before


def test_fix_scores_use_the_first_reply():
    rows = [_r("a1", "2026-11-19T10:00:00+00:00", finish="length", score=0.0), _r("a2", "2026-11-19T11:00:00+00:00", score=1.0)]
    assert fixrows45.case_scores(rows, [CASE])["cases"][CASE]["scores"] == [0.0]
    rows = [_err("a1", "2026-11-19T10:00:00+00:00"), _r("a2", "2026-11-19T11:00:00+00:00", score=1.0)]
    assert fixrows45.case_scores(rows, [CASE])["cases"][CASE]["scores"] == [1.0]
    only_err = [_err("a1", "2026-11-19T10:00:00+00:00")]
    cs = fixrows45.case_scores(only_err, [CASE])["cases"][CASE]
    assert cs["scores"] == [] and cs["old_grader"] == 0 and cs["reason"].startswith("pending")


def test_resume_presents_only_units_without_a_reply(monkeypatch):
    from crucibleforge import cli
    rows = [_r("a1", "2026-11-19T10:00:00+00:00", finish="length"), _err("e1", "2026-11-19T10:00:00+00:00", repeat=2)]
    monkeypatch.setattr(report, "board_filter", lambda *a, **k: rows)
    cases = [{"id": CASE, "category": "coding"}]
    cfg = {"defaults": {"repeats": {"coding": 2}}}
    assert cli._first_reply_jobs("m", cfg, cases) == {(None, CASE, 2)}


def test_recover_is_refused_under_the_rule(monkeypatch, capsys):
    from crucibleforge import cli
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda a, c: (c, [], {"attempt_rule": "first_reply"}))
    assert cli.cmd_recover(type("A", (), {"profile": None, "models": "m"})(), {}) != 0     # a refusal is not success
    assert "refused" in capsys.readouterr().out


def test_the_profile_turns_the_rule_on():
    import yaml
    assert yaml.safe_load((ROOT / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))["attempt_rule"] == "first_reply"
