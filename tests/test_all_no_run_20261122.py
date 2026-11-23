"""`all --no-run`: skip the generation phase and carry on with judge -> ladder packs -> report over what is stored.
`all --categories nsfw` is the existing way to generate only the NSFW+ units. No model or provider is called."""
from __future__ import annotations

import argparse

import pytest

from crucibleforge import cli, config


def _args(**kw):
    base = dict(models="a,b", cmd="all", profile="p", smoke=False, no_judge=False, judge=None, no_run=True,
                fresh=False, plan=False, categories=None, cases=None, timing=False)
    return argparse.Namespace(**{**base, **kw})


@pytest.fixture
def wired(monkeypatch):
    rec = {"order": [], "judge_models": None, "have": {"a", "b"}}

    def no_run(*a, **k):
        raise AssertionError("cmd_run was called")
    monkeypatch.setattr(cli, "cmd_run", no_run)
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, cfg: (cfg, [], {"name": "p"}))
    monkeypatch.setattr(cli, "resolve_models",
                        lambda cfg, models: [{"name": n} for n in models.split(",")])
    monkeypatch.setattr(config, "load_transcripts", lambda label: [{"case_id": "x"}] if label in rec["have"] else [])

    def judge(args, cfg):
        rec["order"].append("judge")
        rec["judge_models"] = args.models
        return 0
    monkeypatch.setattr(cli, "cmd_judge", judge)
    monkeypatch.setattr(cli, "_session_judge_due", lambda args, cfg: False)
    monkeypatch.setattr(cli, "uses_ladder", lambda args, cfg: True)
    monkeypatch.setattr(cli, "_ladder_phases", lambda args, cfg, labels: rec["order"].append(("ladder", list(labels))) or 0)
    monkeypatch.setattr(cli, "_lockout_phase", lambda args, cfg, labels: 0)
    monkeypatch.setattr(cli, "_tier_phases", lambda args, cfg, labels: 0)
    monkeypatch.setattr(cli, "cmd_report", lambda args, cfg, full_board=False: rec["order"].append("report") or 0)
    return rec


def test_no_run_judges_runs_packs_and_reports_without_generating(wired):
    args = _args()
    assert cli.cmd_all(args, {}) == 0
    assert wired["order"] == ["judge", ("ladder", ["a", "b"]), "report"]
    assert wired["judge_models"] == "a,b"
    assert args.ran_labels == ["a", "b"]


def test_a_label_with_no_stored_rows_is_dropped_with_an_error_line(wired, capsys):
    wired["have"] = {"b"}
    args = _args()
    assert cli.cmd_all(args, {}) == 0
    assert args.ran_labels == ["b"]
    assert wired["judge_models"] == "b"
    assert "a: no stored rows" in capsys.readouterr().err


def test_no_label_with_rows_is_exit_2(wired, capsys):
    wired["have"] = set()
    assert cli.cmd_all(_args(), {}) == 2
    assert wired["order"] == []
    assert "--no-run" in capsys.readouterr().err


def test_no_run_with_fresh_is_refused(wired, capsys):
    assert cli.cmd_all(_args(fresh=True), {}) == 2
    assert wired["order"] == []
    assert "--fresh" in capsys.readouterr().err


def test_timing_still_prints_with_a_zero_generation_split(wired, capsys):
    assert cli.cmd_all(_args(timing=True), {}) == 0
    assert "generation" in capsys.readouterr().out


def test_without_the_flag_generation_still_runs(wired, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "cmd_run", lambda args, cfg: called.append(1) or setattr(args, "ran_labels", ["a"]) or 0)
    assert cli.cmd_all(_args(no_run=False), {}) == 0
    assert called == [1]


def test_flag_is_on_all_only_and_has_help(capsys):
    for cmd in ("run", "judge"):
        with pytest.raises(SystemExit):
            cli.main([cmd, "--no-run"])
    capsys.readouterr()
    with pytest.raises(SystemExit):
        cli.main(["all", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "--no-run" in out and "skip the generation phase" in out


def test_categories_nsfw_generates_only_the_adult_chain_units(monkeypatch):
    """The way to generate only NSFW+: `--categories nsfw`. The adult page's units are chain placeholders of
    category `nsfw`, so the filter leaves every fixed Code/Chat job (and every other chain) out of the round."""
    import types
    from crucibleforge import runner
    cases = ([{"id": f"FIX-{i}", "category": "coding"} for i in range(3)]
             + [{"id": "CHAIN-w-001", "category": "coding", "chain_spec": {"id": "CHAIN-w-001"}},
                {"id": "CHAIN-r-001", "category": "rpdeep", "chain_spec": {"id": "CHAIN-r-001"}},
                {"id": "CHAIN-a-001", "category": "nsfw", "chain_spec": {"id": "CHAIN-a-001", "part": "nsfw/x"}},
                {"id": "FLAT-a-001", "category": "nsfw", "chain_spec": {"id": "FLAT-a-001", "part": "nsfw/x"}}])
    entry = {"name": "m", "model_id": "m", "provider": "p"}
    got = {}
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, c: (c, list(cases), {}))
    monkeypatch.setattr(cli, "resolve_models", lambda c, m: [dict(entry)])
    monkeypatch.setattr(cli, "_unpriced_hosted", lambda c, e: [])
    monkeypatch.setattr(cli, "_fail_fast", lambda c, e, **k: (e, {}))
    monkeypatch.setattr(cli, "_settings_gate", lambda c, e: (e, {}))
    monkeypatch.setattr(cli, "_conformance_gate", lambda c, e, **k: (e, {}))
    monkeypatch.setattr(cli, "_ProviderGuard", lambda *a, **k: types.SimpleNamespace(
        busy=lambda: None, serving=lambda: None, restore=lambda: None))
    monkeypatch.setattr(runner, "run_models",
                        lambda c, e, cs, **k: got.update(ids=[x["id"] for x in cs], jobs=k.get("jobs_by_label")) or {"m": {"failed": True, "error": "stub"}})
    args = argparse.Namespace(models="m", categories="nsfw", difficulty=None, cases=None, smoke=False, fresh=False,
                              force=False, cmd="all", yes=True, no_link_check=True, allow_unpriced=True)
    cli.cmd_run(args, {"defaults": {}})
    assert got["ids"] == ["CHAIN-a-001", "FLAT-a-001"] and got["jobs"] is None
