"""A drain during the ladder phase ends it (review, lead 2026-11-15): a hosted run drained by SIGUSR1 in a pack
round finished its in-flight request and then logged "ladder round N ... stop requested, skipping" round after
round (21 by three minutes later) and never exited: the ladder loop checked only the hard stop. A drain now ends
the ladder loop at once (no next model, no escalation, no judge, no next round) and the run exits DRAIN_EXIT;
the Lockout phase and the board do not start either."""
from __future__ import annotations

import argparse

import pytest

from crucibleforge import cli, ladder, report, runner, stopflag


@pytest.fixture(autouse=True)
def _clean():
    def reset():
        runner.reset_drain()
        stopflag.ABORT.clear()
        runner.STOP.clear()
    reset()
    yield
    reset()


def _wire(monkeypatch, calls, drain_on_call=1):
    import crucibleforge.profiles as profiles
    monkeypatch.setattr(profiles, "load_profile", lambda name, cfg=None: {"name": "p", "ladder": {}})
    monkeypatch.setattr(ladder, "discover", lambda prof: [{"name": "s"}])
    monkeypatch.setattr(ladder, "unlock_share", lambda prof: 0.9)
    monkeypatch.setattr(report, "ladder_progress", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, cfg: (cfg, None, None))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, s: [{"name": n} for n in s.split(",")])
    # every round still has units due (nothing was run: the drain skipped them)
    monkeypatch.setattr(cli, "ladder_todo", lambda label, cfg, prof, sets, unlock, tried=None:
                        ([{"id": "PK-1"}], {(None, "PK-1", 1)}, ["s"]))

    class _G:
        def __init__(self, *a, **k):
            pass

        def restore(self):
            pass
    monkeypatch.setattr(cli, "_ProviderGuard", _G)

    def run_models(cfg, entries, cases, **kw):
        calls.append(entries[0]["name"])
        if len(calls) == drain_on_call:
            runner.request_drain(None, why="test SIGUSR1")
        return {"models": {}}
    monkeypatch.setattr(runner, "run_models", run_models)
    monkeypatch.setattr(cli, "_print_run_summary", lambda s: None)
    monkeypatch.setattr(cli, "_run_rc", lambda s, labels: 0)
    escalated, judged = [], []
    monkeypatch.setattr(cli, "_escalate_phase", lambda args, cfg, ran: escalated.append(ran) or 0)
    monkeypatch.setattr(cli, "cmd_judge", lambda args, cfg: judged.append(args.models) or 0)
    return escalated, judged


def _args(**kw):
    base = dict(profile="p", smoke=False, no_tiers=False, no_ladder=False, no_judge=True, force_evict=False)
    return argparse.Namespace(**{**base, **kw})


def test_a_drain_in_a_ladder_round_ends_the_loop_with_the_drain_code(monkeypatch):
    calls = []
    escalated, judged = _wire(monkeypatch, calls)
    rc = cli._ladder_phases(_args(), {"defaults": {}}, ["a", "b"])
    assert rc == cli.DRAIN_EXIT
    assert calls == ["a"]                     # model b and every later round never start
    assert escalated == [] and judged == []


def test_a_drain_already_set_runs_nothing(monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    runner.request_drain(None, why="test")
    assert cli._ladder_phases(_args(), {"defaults": {}}, ["a"]) == cli.DRAIN_EXIT
    assert calls == []


def test_with_the_judge_on_a_drain_still_skips_escalation_and_judge(monkeypatch):
    calls = []
    escalated, judged = _wire(monkeypatch, calls)
    rc = cli._ladder_phases(_args(no_judge=False), {"defaults": {}}, ["a"])
    assert rc == cli.DRAIN_EXIT and calls == ["a"] and escalated == [] and judged == []


def test_cmd_all_exits_with_the_drain_code_after_a_ladder_drain(monkeypatch):
    """No Lockout phase and no board after a drain in the ladder phase."""
    ran = []
    monkeypatch.setattr(cli, "cmd_run", lambda args, cfg: setattr(args, "ran_labels", ["a"]) or 0)
    monkeypatch.setattr(cli, "_escalate_phase", lambda args, cfg, r: 0)
    monkeypatch.setattr(cli, "_stamp_no_judge", lambda labels, flag: None)
    monkeypatch.setattr(cli, "uses_ladder", lambda args, cfg: True)

    def lad(args, cfg, labels):
        runner.request_drain(None, why="test")
        return cli.DRAIN_EXIT
    monkeypatch.setattr(cli, "_ladder_phases", lad)
    monkeypatch.setattr(cli, "_lockout_phase", lambda *a: ran.append("lockout") or 0)
    monkeypatch.setattr(cli, "cmd_report", lambda *a, **k: ran.append("report") or 0)
    rc = cli.cmd_all(_args(escalate=False, timing=False), {"defaults": {}})
    assert rc == cli.DRAIN_EXIT and ran == []
