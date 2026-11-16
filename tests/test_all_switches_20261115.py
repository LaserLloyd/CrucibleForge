"""`all` switches that replace the run-cc.sh monkey-patches (review, lead 2026-11-15).

run-cc.sh patched ``ladder.discover`` to keep only work/* sets and set ``cli._lockout_phase`` to a no-op, so a
Claude Code run (standard board only) could not reach an adult set or the Lockout. ``--ladder-sections work`` keeps
only the ladder sets of the named score sections (an unknown section is refused before anything runs) and
``--no-lockout`` skips the Lockout phase; neither changes a stored row or a score."""
from __future__ import annotations

import argparse

import pytest

from crucibleforge import cli, config, ladder, report, runner, stopflag


@pytest.fixture(autouse=True)
def _clean():
    runner.reset_drain()
    stopflag.ABORT.clear()
    runner.STOP.clear()
    yield
    runner.reset_drain()


def _args(**kw):
    base = dict(profile="p", smoke=False, no_tiers=False, no_ladder=False, no_judge=True, force_evict=False,
                ladder_sections=None, no_lockout=False)
    return argparse.Namespace(**{**base, **kw})


def test_the_parser_takes_both_switches():
    seen = {}
    real = cli.cmd_all

    def fake(args, cfg):
        seen.update(vars(args))
        return 0
    try:
        cli.cmd_all = fake
        cli.main(["--config", str(config.EXAMPLE_CONFIG_PATH), "all", "--profile", "bench45", "--models", "m", "--ladder-sections", "work", "--no-lockout",
                  "--plan"])
    except SystemExit:
        pass
    finally:
        cli.cmd_all = real
    assert seen.get("ladder_sections") == "work" and seen.get("no_lockout") is True


def _wire(monkeypatch, got):
    import crucibleforge.profiles as profiles
    monkeypatch.setattr(profiles, "load_profile", lambda name, cfg=None: {
        "name": "p", "ladder": {}, "scores": {"work": {}, "chat": {}, "nsfw": {}}})
    monkeypatch.setattr(ladder, "discover", lambda prof: {"work/c1": [{"n": 1}], "nsfw/w": [{"n": 1}],
                                                          "chat/x": [{"n": 1}]})
    monkeypatch.setattr(ladder, "unlock_share", lambda prof: 0.9)
    monkeypatch.setattr(report, "ladder_progress", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, cfg: (cfg, None, None))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, s: [{"name": n} for n in s.split(",")])

    def todo(label, cfg, prof, sets, unlock, tried=None):
        got.append(sorted(sets))
        return [], set(), []
    monkeypatch.setattr(cli, "ladder_todo", todo)


def test_ladder_sections_keeps_only_the_named_sections(monkeypatch):
    got = []
    _wire(monkeypatch, got)
    assert cli._ladder_phases(_args(ladder_sections="work"), {"defaults": {}}, ["a"]) == 0
    assert got == [["work/c1"]]
    got.clear()
    assert cli._ladder_phases(_args(ladder_sections="work,chat"), {"defaults": {}}, ["a"]) == 0
    assert got == [["chat/x", "work/c1"]]
    got.clear()
    assert cli._ladder_phases(_args(), {"defaults": {}}, ["a"]) == 0          # no switch: every set
    assert got == [["chat/x", "nsfw/w", "work/c1"]]


def test_an_unknown_section_is_refused_before_anything_runs(monkeypatch, capsys):
    import crucibleforge.profiles as profiles
    monkeypatch.setattr(profiles, "load_profile", lambda name, cfg=None: {
        "name": "p", "scores": {"work": {}, "chat": {}, "nsfw": {}}})
    ran = []
    monkeypatch.setattr(cli, "cmd_run", lambda args, cfg: ran.append(1) or 0)
    rc = cli.cmd_all(_args(ladder_sections="wrok", escalate=False, timing=False, plan=False), {"defaults": {}})
    assert rc == 2 and ran == []
    assert "wrok" in capsys.readouterr().err


def test_no_lockout_skips_the_lockout_phase(monkeypatch):
    import crucibleforge.profiles as profiles

    def boom(*a, **k):
        raise AssertionError("the Lockout phase read its profile")
    monkeypatch.setattr(profiles, "load_profile", boom)
    assert cli._lockout_phase(_args(no_lockout=True), {"defaults": {}}, ["a"]) == 0
