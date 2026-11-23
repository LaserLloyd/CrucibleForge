"""`ladder-judge` inside `all` must hand the judge's GPU lease back: a second reference run (2026-11-19) had the
session judge's lease still held when the first pack round asked for the same cards, and the round waited out its
busy window. The standalone command only looked fine because the process exit released the lease."""
from __future__ import annotations

import argparse

import pytest

from crucibleforge import cli, rpladder_run


class _Guard:
    made = []

    def __init__(self, cfg, entries, include_judge=True, force_evict=False, judge=None):
        self.restored = 0
        _Guard.made.append(self)

    def restore(self):
        self.restored += 1


@pytest.fixture
def lj(monkeypatch):
    _Guard.made = []
    monkeypatch.setattr(cli, "_ProviderGuard", _Guard)
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, cfg: (cfg, [], {"judge": {"model_id": "j"}}))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, models: [{"name": "a"}])
    monkeypatch.setattr(rpladder_run, "part_windows", lambda prof, w: [1, 3])
    monkeypatch.setattr(rpladder_run, "summary_lines", lambda out: [])
    monkeypatch.setattr("os.path.exists", lambda p: False)
    return argparse.Namespace(models="a", profile="bench45", plan=False, windows=None, until=None, judge=None,
                              rung_check=False, force=False, attach=False, client=None)


def test_the_session_judge_gives_its_lease_back(lj, monkeypatch):
    monkeypatch.setattr(rpladder_run, "run", lambda *a, **k: {"rung_check_complete": True})
    assert cli.cmd_ladder_judge(lj, {}) == 0
    assert [g.restored for g in _Guard.made] == [1]


def test_the_lease_goes_back_when_the_placement_fails(lj, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("judge died")
    monkeypatch.setattr(rpladder_run, "run", boom)
    with pytest.raises(RuntimeError):
        cli.cmd_ladder_judge(lj, {})
    assert [g.restored for g in _Guard.made] == [1]
