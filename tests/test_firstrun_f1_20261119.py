"""F1 of the first fresh reference run (2026-11-19): ``crucibleforge all --models X --yes`` did not place the
role-play sessions (``ladder-judge`` had to be run by hand). `all` now runs that pass for the models it ran whenever
the profile judges its session part on the ladder; ``--no-judge`` (the one opt-out of every judge step) skips it."""
from __future__ import annotations

import argparse

import pytest

from crucibleforge import cli, profiles

SESSION_PROFILE = {"name": "p", "chains": {"chat/j_rpsession": {"judge_mode": {"kind": "ladder", "windows": [1, 3]}}}}


def _args(**kw):
    base = dict(models="a,b", cmd="all", profile="p", smoke=False, no_judge=False, judge=None)
    return argparse.Namespace(**{**base, **kw})


@pytest.fixture
def phases(monkeypatch):
    calls = {"order": [], "lj": []}
    monkeypatch.setattr(cli, "cmd_run", lambda args, cfg: setattr(args, "ran_labels", ["a", "b"]) or 0)
    monkeypatch.setattr(cli, "cmd_judge", lambda args, cfg: calls["order"].append("judge") or 0)
    monkeypatch.setattr(cli, "_tier_phases", lambda args, cfg, labels: 0)
    monkeypatch.setattr(cli, "_ladder_phases", lambda args, cfg, labels: calls["order"].append("ladder") or 0)
    monkeypatch.setattr(cli, "_lockout_phase", lambda args, cfg, labels: 0)
    monkeypatch.setattr(cli, "cmd_report", lambda args, cfg, full_board=False: calls["order"].append("report") or 0)
    monkeypatch.setattr(cli, "uses_ladder", lambda args, cfg: True)

    def fake_lj(args, cfg):
        calls["order"].append("session-judge")
        calls["lj"].append((args.models, args.profile, args.plan, args.windows, args.force))
        return calls.get("lj_rc", 0)
    monkeypatch.setattr(cli, "cmd_ladder_judge", fake_lj)
    monkeypatch.setattr(profiles, "load_profile", lambda name, cfg=None: calls.get("prof", SESSION_PROFILE))
    return calls


def test_all_places_the_sessions_of_the_models_it_ran_after_the_judge(phases):
    args = _args()
    assert cli.cmd_all(args, {}) == 0
    assert phases["lj"] == [("a,b", "p", False, None, False)]
    assert phases["order"] == ["judge", "session-judge", "ladder", "report"]
    assert args.models == "a,b"                          # the caller's args are untouched


def test_a_profile_without_a_judged_session_part_runs_no_session_judge(phases):
    phases["prof"] = {"name": "p", "chains": {"work/c1_implied": {}}}
    assert cli.cmd_all(_args(), {}) == 0
    assert phases["lj"] == []


def test_no_judge_skips_it(phases):
    phases["order"].clear()
    assert cli.cmd_all(_args(no_judge=True), {}) == 0
    assert phases["lj"] == []


def test_a_smoke_run_skips_it(phases):
    assert cli.cmd_all(_args(smoke=True), {}) == 0
    assert phases["lj"] == []


def test_a_failed_session_judge_is_reported_and_the_board_is_still_built(phases):
    phases["lj_rc"] = 1
    assert cli.cmd_all(_args(), {}) == 1
    assert phases["order"][-1] == "report"
