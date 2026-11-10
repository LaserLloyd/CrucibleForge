"""Suite 3.9 runner: escalation out of the timed path (EVAL-FABLE M1, run
side), API concurrency, the rig lock only around rig phases and the rung
wall cap (S10)."""
from __future__ import annotations

import argparse

import pytest

from crucibleforge import bench_all, cli, config, providers, runner, stopflag
from crucibleforge.api import RowTimeout


@pytest.fixture(autouse=True)
def _flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    providers.clear_cache()
    yield
    providers.clear_cache()


def _all_args(**kw):
    base = dict(cmd="all", models="m", profile=None, smoke=False, plan=False, judge=None,
                escalate=False, no_escalate=False, no_tiers=False, no_ladder=False,
                timing=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def phases(monkeypatch):
    calls = []

    def run(args, cfg):
        calls.append("run")
        args.ran_labels = ["m"]
        return 0

    monkeypatch.setattr(cli, "cmd_run", run)
    monkeypatch.setattr(cli, "cmd_escalate", lambda a, c: calls.append("escalate") or 0)
    monkeypatch.setattr(cli, "cmd_judge", lambda a, c: calls.append("judge") or 0)
    monkeypatch.setattr(cli, "cmd_report", lambda a, c, full_board=False: calls.append("report") or 0)
    monkeypatch.setattr(cli, "_tier_phases", lambda a, c, labels: 0)
    monkeypatch.setattr(cli, "_ladder_phases", lambda a, c, labels: 0, raising=False)
    return calls


# --------------------------------------------------- escalation (M1)

def test_all_does_not_escalate_by_default(phases):
    assert cli.cmd_all(_all_args(), {"models": []}) == 0
    assert "escalate" not in phases and phases[:2] == ["run", "judge"]


def test_all_escalates_only_with_the_flag_and_no_escalate_is_a_no_op(phases):
    cli.cmd_all(_all_args(escalate=True), {"models": []})
    assert phases[:3] == ["run", "escalate", "judge"]
    phases.clear()
    cli.cmd_all(_all_args(no_escalate=True), {"models": []})
    assert "escalate" not in phases


def test_bench_all_escalates_only_with_the_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    calls = []

    class Prov:
        def list_models(self, refresh=False):
            return {"x/m"}

    monkeypatch.setattr(bench_all, "rig_arches", lambda prov: {})
    import crucibleforge.providers as pv
    monkeypatch.setattr(pv, "get_provider", lambda cfg, name: Prov())
    plan = {"add": [], "prune": [], "groups": [], "judge": [], "not_on_rig": [],
            "rig_models": 1, "case_count": 1, "candidates": [],
            "escalate": [{"model": "m", "jobs": 2}], "rungs": [65536, 131072]}
    monkeypatch.setattr(bench_all, "plan", lambda *a, **k: dict(plan))
    monkeypatch.setattr(bench_all, "format_plan", lambda p: "plan")
    monkeypatch.setattr(cli, "cmd_escalate", lambda a, c: calls.append("escalate") or 0)
    monkeypatch.setattr(cli, "cmd_report", lambda a, c, full_board=False: 0)
    cfg = {"providers": {"sf": {"type": "studioforge"}}, "_path": str(tmp_path / "m.yaml")}
    args = argparse.Namespace(go=True, include_api=False, first=None, no_sync=True,
                              no_prune=True, until=None, judge_reserve_min=45)
    bench_all.execute(args, cfg)
    assert calls == []
    assert any("--escalate" in n for n in args.bench_all_notes)
    args.escalate = True
    bench_all.execute(args, cfg)
    assert calls == ["escalate"]


# ----------------------------------------------------- API concurrency

def test_hosted_apis_default_to_eight_requests_at_once():
    cfg = {"providers": {"api": {"type": "openai", "base_url": "http://a.invalid/v1"},
                         "slow": {"type": "openai", "base_url": "http://b.invalid/v1",
                                  "concurrency": 3},
                         "sf": {"type": "studioforge", "base_url": "http://c.invalid/v1"}}}
    assert providers.get_provider(cfg, "api").concurrency == 8
    assert providers.get_provider(cfg, "slow").concurrency == 3        # config overrides
    sf = providers.get_provider(cfg, "sf")
    assert sf.concurrency_explicit is False                            # rig: follow its slots


# ------------------------------------------------------------- rig lock

def _cfg_models(tmp_path):
    return {"providers": {"api": {"type": "openai", "base_url": "http://a.invalid/v1"},
                          "sf": {"type": "studioforge", "base_url": "http://c.invalid/v1"}},
            "models": [{"name": "mm", "provider": "api", "model_id": "M"},
                       {"name": "omega", "provider": "sf", "model_id": "o"}],
            "defaults": {}, "judge": {"candidates": []}}


def test_a_hosted_only_run_does_not_take_the_rig_lock_up_front(tmp_path):
    cfg = _cfg_models(tmp_path)
    ns = argparse.Namespace
    assert cli.all_hosted(ns(cmd="all", models="mm"), cfg) is True
    assert cli.all_hosted(ns(cmd="run", models="mm"), cfg) is True
    assert cli.all_hosted(ns(cmd="all", models="mm,omega"), cfg) is False
    assert cli.all_hosted(ns(cmd="bench-all", models=None), cfg) is False
    assert cli.all_hosted(ns(cmd="judge", models="mm"), cfg) is False


def _locked(path) -> bool:
    fcntl = pytest.importorskip("fcntl")  # POSIX only; skips the lock tests on Windows
    with open(path, "a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return False


def test_a_rig_phase_holds_the_lock_only_for_its_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(cli, "_ancestor_holds", lambda p: False)
    # isolate from a lock another test's main() left in this xdist worker
    monkeypatch.setitem(cli._RIG_LOCK, "f", None)
    lock = tmp_path / ".rig.lock"
    with cli.rig_phase(needed=True, what="judge phase"):
        assert _locked(lock)
        with cli.rig_phase(needed=True):            # nested: no second acquisition
            assert _locked(lock)
        assert _locked(lock)
    assert not _locked(lock) and cli._RIG_LOCK["f"] is None
    with cli.rig_phase(needed=False):
        assert not lock.exists() or not _locked(lock)


# ------------------------------------------------------------ rung wall cap

def test_a_rung_request_past_the_wall_cap_is_a_run_out_row(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    seen = []

    def chat(self, model_id, messages, **kw):
        seen.append(kw.get("max_wall_s"))
        raise RowTimeout("request exceeded its 2700s wall-clock ceiling")

    P = providers.Provider
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "list_models", lambda self, refresh=False: {"m"})
    cfg = {"defaults": {"context_length": 4096, "repeats": {},
                        "run_plan": {"escalation_max_wall_s": 2700}},
           "providers": {"api": {"type": "openai", "base_url": "http://a.invalid/v1"}},
           "judge": {"candidates": []}, "models": []}
    cases = [{"id": f"C{i}", "category": "coding", "prompt": "x", "max_tokens": 64,
              "grader": "contains", "grader_config": {"needles": ["ok"]}} for i in range(4)]
    entry = {"name": "m", "model_id": "m", "provider": "api", "thinking": True}
    jobs = {(None, f"C{i}", 1) for i in range(4)}
    summary = runner.run_models(cfg, [entry], cases, jobs_by_label={"m": jobs}, rung=65536)
    assert summary["m"]["failed"] is False                 # 4 timeouts never abort the model
    assert set(seen) == {2700.0}
    rows = config.load_transcripts("m")
    assert len(rows) == 4 and all(r["grade"] == "fail" and r["finish_reason"] == "wall_timeout"
                                  and r["budget_rung"] == 65536 for r in rows)
    # not escalated again (a bigger rung only takes longer)
    assert runner.escalation_jobs(rows, 131072) == set()
