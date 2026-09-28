"""`all` judges the models that ran even when another model of the batch failed
(2026-09-28: one unservable model in a 31-model backfill returned from cmd_all
before the judge, leaving 25 good transcripts unjudged).

The per-model failure stays visible: its meta says FAILED (written by
_fail_fast / run_models, unchanged here) and the batch exit code is non-zero.
"""
from __future__ import annotations

import argparse

import pytest

from crucibleforge import cli


def _args(models="a,b,c"):
    return argparse.Namespace(models=models, cmd="all")


@pytest.fixture
def phases(monkeypatch):
    calls = {"judge": [], "report": 0}

    def fake_judge(args, cfg):
        calls["judge"].append(args.models)
        return calls.get("judge_rc", 0)

    def fake_report(args, cfg, full_board=False):
        calls["report"] += 1
        assert full_board
        return 0

    monkeypatch.setattr(cli, "cmd_judge", fake_judge)
    monkeypatch.setattr(cli, "cmd_report", fake_report)
    return calls


def _run_stub(monkeypatch, rc, ran):
    def fake_run(args, cfg):
        args.ran_labels = ran
        return rc
    monkeypatch.setattr(cli, "cmd_run", fake_run)


def test_a_failed_model_does_not_stop_the_judge_for_the_others(monkeypatch, phases):
    # c never ran (not served); a and b generated rows
    _run_stub(monkeypatch, 1, ["a", "b"])
    args = _args()
    rc = cli.cmd_all(args, {})
    assert phases["judge"] == ["a,b"]          # survivors only
    assert phases["report"] == 1               # the board is rebuilt
    assert rc == 1                             # the batch is still not a success
    assert args.models == "a,b,c"              # the caller's args are untouched


def test_a_model_that_failed_mid_run_is_still_judged(monkeypatch, phases):
    # b hit a transport abort after some rows: it is in the ran list (the
    # judge no-ops a label with nothing pending)
    _run_stub(monkeypatch, 1, ["a", "b"])
    cli.cmd_all(_args("a,b"), {})
    assert phases["judge"] == ["a,b"]


def test_a_run_phase_that_never_started_skips_judge_and_report(monkeypatch, phases):
    _run_stub(monkeypatch, 1, None)            # nothing runnable / LM Studio busy
    assert cli.cmd_all(_args(), {}) == 1
    assert phases["judge"] == [] and phases["report"] == 0


def test_all_clean_run_judges_the_requested_models(monkeypatch, phases):
    _run_stub(monkeypatch, 0, ["a", "b", "c"])
    assert cli.cmd_all(_args(), {}) == 0
    assert phases["judge"] == ["a,b,c"]


def test_judge_failure_still_propagates(monkeypatch, phases):
    phases["judge_rc"] = 4
    _run_stub(monkeypatch, 0, ["a"])
    assert cli.cmd_all(_args("a"), {}) == 4


def test_cmd_run_records_the_labels_it_ran(monkeypatch):
    """cmd_run sets args.ran_labels from what run_models actually processed —
    preflight-unrunnable models are excluded."""
    import crucibleforge.runner as runner

    entries = [{"name": n} for n in ("a", "b", "c")]
    monkeypatch.setattr(cli, "_apply_profile_arg",
                        lambda args, cfg: (cfg, [{"id": "X", "category": "coding"}], {}))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, m: entries)
    monkeypatch.setattr(cli, "_fail_fast",
                        lambda cfg, e, **k: (e[:2], {"c": "not served by provider p"}))

    class _Guard:
        def __init__(self, *a, **k): pass
        def busy(self): return None
        def serving(self): return None
        def restore(self): pass
    monkeypatch.setattr(cli, "_ProviderGuard", _Guard)
    monkeypatch.setattr(runner, "run_models", lambda cfg, e, cases, smoke=False: {
        "a": {"rows": 1, "load_s": 0, "device": "d"},
        "b": {"failed": True, "error": "transport"}})
    args = argparse.Namespace(models="a,b,c", categories=None, difficulty=None, cases=None,
                              fresh=False, force=False, no_link_check=True, smoke=False,
                              yes=True, cmd="all", judge=None, force_evict=False)
    assert cli.cmd_run(args, {"defaults": {}}) == 1
    assert args.ran_labels == ["a", "b"]
