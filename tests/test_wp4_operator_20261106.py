"""WP4 (MakoBench operator, 2026-11-06): `bench-all --only`, `--lease-mode
night|day`, `--no-judge`, `--plan-json`, `keep_on_board`, and a bounded stop
for detached units. Without any of the new flags bench-all must behave
exactly as before (the 01:00 nightly argv)."""
from __future__ import annotations

import argparse
import json
from datetime import datetime

import pytest

from crucibleforge import bench_all, cli, providers, studioforge
from crucibleforge import config as cfgmod

CASES = ["C1", "C2", "C3"]
JUDGE = "judge/org/judge-q8"
_REAL_PLAN = bench_all.plan


@pytest.fixture(autouse=True)
def _settings_gate_passes(monkeypatch):
    """These fixtures carry no recommended sampler; the settings gate (2026-11-13) has its own
    tests (test_settings_gate_20261113.py), so here every model passes it."""
    from crucibleforge import settings_gate
    monkeypatch.setattr(settings_gate, "check", lambda cfg_p, entry, *a, **k: settings_gate.Verdict(
        ok=True, label=str(entry.get("name") or entry.get("model_id") or ""), basis="test"))


@pytest.fixture(autouse=True)
def _reset_lease_mode():
    providers.set_lease_mode(None)
    yield
    providers.set_lease_mode(None)


def _cfg(models, **prov):
    sf = {"type": "studioforge", "base_url": "http://rig:1234/v1", "lease": True,
          "lease_devices": [0, 1, 2, 3], **prov}
    return {"providers": {"sf": sf, "ds": {"type": "openai", "base_url": "http://api/v1"}},
            "models": models,
            "judge": {"candidates": [{"provider": "sf", "model_id": JUDGE}]}}


def _m(name, mid, prov="sf", **kw):
    return {"name": name, "model_id": mid, "provider": prov, **kw}


def _done(*ids):
    return [{"case_id": i, "grade": "pass"} for i in ids]


def _plan(cfg, rig, rows=None, pending=None, board=None, ladder=None, **kw):
    rows = rows or {}
    pending = pending or {}
    ladder = ladder or {}
    return bench_all.plan(cfg, set(rig), board_labels=board or [],
                          rows_for=lambda l: rows.get(l, []),
                          pending_for=lambda l: pending.get(l, 0),
                          attempts_for=lambda l: {}, rejudge_for=lambda l: set(),
                          ladder_for=lambda l: ladder.get(l),
                          judge_id=JUDGE, case_ids=CASES, rungs=[], **kw)


# ------------------------------------------------------------------ --only

def test_only_scopes_every_phase_of_the_plan():
    cfg = _cfg([_m("a", "org/a"), _m("b", "org/b"), _m("c", "org/c")])
    rows = {"a": _done("C1"), "b": _done("C1"), "c": _done(*CASES)}
    lad = {"c": {"names": ["x/y#1"], "jobs": 2}}
    rig = ["org/a", "org/b", "org/c", "org/new-7b"]
    full = _plan(cfg, rig, rows=rows, pending={"c": 1}, ladder=lad, register_new=True)
    assert full["groups"] == [{"models": ["a", "b"], "cases": ["C2", "C3"]},
                              {"models": ["new-7b"], "cases": CASES}]
    assert full["judge"] == ["a", "b", "c", "new-7b"] and full["ladder"]
    p = _plan(cfg, rig, rows=rows, pending={"c": 1}, ladder=lad, only=["a"])
    assert p["candidates"] == ["a"]
    assert p["add"] == []                                   # new-7b is not registered
    assert p["groups"] == [{"models": ["a"], "cases": ["C2", "C3"]}]
    assert p["judge"] == ["a"] and p["ladder"] == [] and p["escalate"] == []
    assert list(p["per_model"]) == ["a"]
    assert "--only: a" in bench_all.format_plan(p)


def test_only_by_rig_model_id_registers_just_that_model():
    cfg = _cfg([_m("a", "org/a")])
    p = _plan(cfg, ["org/a", "org/New-7B-Q8_0", "org/other-9b"], rows={"a": _done("C1")},
              only=["org/New-7B-Q8_0"])
    assert [x["model_id"] for x in p["add"]] == ["org/New-7B-Q8_0"]
    assert p["groups"] == [{"models": ["new-7b-q8-0"], "cases": CASES}]
    assert p["judge"] == ["new-7b-q8-0"]


def test_only_never_prunes_another_models_rows_and_reports_unknown_names():
    cfg = _cfg([_m("a", "org/a"), _m("gone", "org/gone")])
    p = _plan(cfg, ["org/a"], rows={"a": _done(*CASES)}, board=["a", "gone", "ghost"],
              only=["a", "typo"])
    assert p["prune"] == [] and p["not_on_rig"] == []
    assert p["only_unknown"] == ["typo"]
    assert "--only typo" in bench_all.format_plan(p)


class _Rig:
    type = "studioforge"

    def __init__(self, ids):
        self.ids = set(ids)

    def list_models(self, refresh=False):
        return self.ids


def _wire_execute(monkeypatch, tmp_path, cfg, rig, rows, pending):
    """execute() over fakes: records the labels every phase was given."""
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    for label in rows:
        (tmp_path / f"transcripts_{label}.jsonl").write_text("", encoding="utf-8")
    real_plan = _REAL_PLAN

    def fake_plan(cfg_, rig_ids, **kw):
        kw.pop("rig_arch", None)
        return real_plan(cfg_, rig_ids, rows_for=lambda l: rows.get(l, []),
                         pending_for=lambda l: pending.get(l, 0),
                         attempts_for=lambda l: {}, rejudge_for=lambda l: set(),
                         ladder_for=lambda l: {"names": ["s#1"], "jobs": 1},
                         room_for=lambda l: [], judge_id=JUDGE, case_ids=CASES,
                         rungs=[65536], **kw)
    monkeypatch.setattr(bench_all, "plan", fake_plan)
    from crucibleforge import runner
    monkeypatch.setattr(runner, "escalation_todo", lambda rows_, label, rungs: {(label, "C1")})
    monkeypatch.setattr(providers, "get_provider", lambda c, n: _Rig(rig))
    monkeypatch.setattr(bench_all, "rig_arches", lambda prov: {})
    calls = {"run": [], "judge": [], "escalate": [], "ladder": [], "report": 0}
    monkeypatch.setattr(cli, "cmd_run", lambda a, c: calls["run"].append(a.models) or 0)
    monkeypatch.setattr(cli, "cmd_judge", lambda a, c: calls["judge"].append(a.models) or 0)
    monkeypatch.setattr(cli, "cmd_escalate",
                        lambda a, c: calls["escalate"].append(a.models) or 0)
    monkeypatch.setattr(cli, "_ladder_phases",
                        lambda a, c, labels: calls["ladder"].append(list(labels)) or 0)

    def rep(a, c, full_board=False):
        calls["report"] += 1
        return 0
    monkeypatch.setattr(cli, "cmd_report", rep)
    return calls


def _args(**kw):
    base = dict(go=True, include_api=False, no_sync=False, no_prune=True, first=None,
                until=None, judge_reserve_min=45, escalate=True, no_escalate=False,
                no_ladder=False, skip_conformance=False, only=None, lease_mode=None,
                no_judge=False, plan_json=False, plan_start=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_execute_with_only_touches_only_that_model_including_the_judge(monkeypatch, tmp_path):
    """Two rig models with missing rows; --only a: generation, escalation,
    judge and ladder see a and nothing else."""
    cfg = _cfg([_m("a", "org/a"), _m("b", "org/b")])
    rows = {"a": _done("C1"), "b": _done("C2")}
    calls = _wire_execute(monkeypatch, tmp_path, cfg, ["org/a", "org/b"], rows, {"b": 3})
    args = _args(only="a")
    assert bench_all.execute(args, cfg) == 0
    assert calls["run"] == ["a"]
    assert calls["escalate"] == ["a"]
    assert calls["judge"] == ["a"]
    assert calls["ladder"] == [["a"]]
    assert args.models == "a"                     # the run report names only a
    # the same fakes without --only reach b everywhere (the test can fail)
    calls2 = _wire_execute(monkeypatch, tmp_path, cfg, ["org/a", "org/b"], rows, {"b": 3})
    assert bench_all.execute(_args(), cfg) == 0
    assert sorted(calls2["run"]) == ["a", "b"] and calls2["judge"] == ["a,b"]


def test_only_naming_nothing_refuses_instead_of_running_everything(monkeypatch, tmp_path):
    cfg = _cfg([_m("a", "org/a")])
    calls = _wire_execute(monkeypatch, tmp_path, cfg, ["org/a"], {"a": []}, {})
    assert bench_all.execute(_args(only="nope"), cfg) == bench_all.EXIT_USAGE
    assert calls["run"] == [] and calls["judge"] == []


# --------------------------------------------------------------- --no-judge

def test_no_judge_generates_but_never_judges_or_climbs_the_ladder(monkeypatch, tmp_path):
    cfg = _cfg([_m("a", "org/a")])
    calls = _wire_execute(monkeypatch, tmp_path, cfg, ["org/a"], {"a": _done("C1")}, {"a": 1})
    args = _args(only="a", no_judge=True)
    assert bench_all.execute(args, cfg) == 0
    assert calls["run"] == ["a"] and calls["judge"] == [] and calls["ladder"] == []
    assert any("judge skipped" in n for n in args.bench_all_notes)


def test_no_judge_window_keeps_no_judge_reserve():
    end = datetime(2026, 11, 6, 18, 0)
    w = bench_all.Window(end, 45)
    nj = bench_all.Window(end, 45, no_judge=True)
    assert w.reserve_min(10) >= 45 and nj.reserve_min(10) == 0.0
    assert nj.gen_end(10) > w.gen_end(10)


# ------------------------------------------------------------- lease modes

def _sf_cfg():
    return _cfg([], force_evict=True, busy_unload_after_s=60, lease_devices_preferred=[0, 1],
                render_vacate_mcp="http://cf:8700/mcp")


def test_no_flag_keeps_the_models_yaml_policy():
    p = providers.get_provider(_sf_cfg(), "sf")
    assert p.force_evict_policy and p.busy_unload_after_s == 60 and p.render_vacate_mcp
    assert p.request_priority == 2 and not p.day_single_card


def test_night_mode_never_cuts_a_busy_resident_but_keeps_bench_first():
    providers.set_lease_mode("night")
    p = providers.get_provider(_sf_cfg(), "sf")
    assert p.busy_unload_after_s is None
    assert p.force_evict_policy and p.render_vacate_mcp and p.lease_devices_preferred == [0, 1]
    assert p.request_priority == 2


def test_day_mode_policy_fields():
    providers.set_lease_mode("day")
    p = providers.get_provider(_sf_cfg(), "sf")
    assert p.busy_unload_after_s is None and not p.force_evict_policy and not p.force_evict
    assert p.render_vacate_mcp is None and p.lease_devices_preferred is None
    assert p.request_priority == 3 and p.day_single_card


class _FakeRig:
    """A rig answering the management API; records every lease body."""

    def __init__(self, protected_on=(0, 1), fits=(0, 1, 2), leases=()):
        self.protected_on = list(protected_on)
        self.fits = set(fits)
        self.leases = list(leases)
        self.lease_bodies: list[dict] = []
        self.paths: list[str] = []

    def mgmt(self, method, base, key, hdrs, path, json=None, timeout=None):
        self.paths.append(f"{method} {path}")
        if path == "/api/gpus":
            return 200, {"gpus": [{"index": i} for i in range(4)]}
        if path == "/api/models":
            return 200, {"models": [
                {"id": "chat/dark", "priority": 1, "settings": {"allowed_devices": self.protected_on}},
                {"id": "bg/old", "priority": 3, "settings": {"device_override": [3]}},
            ]}
        if path == "/api/status":
            return 200, {"loaded": []}
        if path == "/api/leases" and method == "GET":
            return 200, {"leases": self.leases}
        if path.endswith("/options"):
            return 200, {"model": {"placements": [
                {"devices": [d], "optimal": {"fits": d in self.fits}} for d in range(4)]}}
        if path == "/api/leases" and method == "POST":
            self.lease_bodies.append(dict(json))
            return 200, {"lease_id": "L1"}
        if path.endswith("/load-recommended") or path.endswith("/load"):
            return 200, {}
        if path.startswith("/api/leases/") and method == "DELETE":
            return 200, {}
        return 200, {}


def test_day_mode_takes_one_unprotected_card_and_never_forces(monkeypatch):
    rig = _FakeRig(protected_on=(0, 1), fits=(0, 1, 2, 3))
    monkeypatch.setattr(studioforge, "_mgmt", rig.mgmt)
    monkeypatch.setattr(studioforge, "wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(studioforge, "load_model", lambda *a, **k: 0.0)
    providers.set_lease_mode("day")
    p = providers.get_provider(_sf_cfg(), "sf")
    monkeypatch.setattr(p, "_resident_ready", lambda *a, **k: False)
    monkeypatch.setattr(p, "_remember_plan", lambda *a, **k: None)
    monkeypatch.setattr(p, "_start_keepalive", lambda: None)
    p.switch_model("org/new-7b")
    body = rig.lease_bodies[-1]
    assert body["devices"] == [2]                 # first card off the chat model's placement
    assert body["force"] is False
    assert not any("vacate" in x or "/unload" in x for x in rig.paths)
    p.release_lease()


def test_day_mode_refuses_when_the_model_fits_only_on_protected_cards(monkeypatch):
    rig = _FakeRig(protected_on=(0, 1), fits=(0, 1))
    monkeypatch.setattr(studioforge, "_mgmt", rig.mgmt)
    providers.set_lease_mode("day")
    p = providers.get_provider(_sf_cfg(), "sf")
    monkeypatch.setattr(p, "_resident_ready", lambda *a, **k: False)
    with pytest.raises(studioforge.DayCardRefused) as e:
        p.switch_model("org/big-32b")
    assert "protected" in str(e.value) and e.value.status == 409
    assert rig.lease_bodies == []


def test_day_mode_never_takes_a_card_leased_by_a_render_tenant(monkeypatch):
    rig = _FakeRig(protected_on=(0, 1), fits=(2, 3),
                   leases=[{"holder": "render-tenant", "kind": "render", "devices": [2]}])
    monkeypatch.setattr(studioforge, "_mgmt", rig.mgmt)
    assert studioforge.choose_day_card("u", "", None, "org/m", exclude=()) == [3]
    rig.fits = {2}
    with pytest.raises(studioforge.DayCardRefused):
        studioforge.choose_day_card("u", "", None, "org/m")


def test_day_mode_request_priority_is_background(monkeypatch):
    providers.set_lease_mode("day")
    p = providers.get_provider(_sf_cfg(), "sf")
    seen = {}

    def fake_stream(*a, **kw):
        seen.update(kw)
        return {}
    monkeypatch.setattr("crucibleforge.api.stream_chat_retried", fake_stream)
    p.chat("org/m", [{"role": "user", "content": "x"}])
    assert seen["priority"] == 3
    providers.set_lease_mode(None)
    q = providers.get_provider(_sf_cfg(), "sf")
    seen.clear()
    q.chat("org/m", [{"role": "user", "content": "x"}])
    assert seen["priority"] == 2


def test_day_mode_judge_lease_never_forces(monkeypatch):
    from crucibleforge import judge as jmod
    rig = _FakeRig(protected_on=(0,), fits=(1, 2))
    monkeypatch.setattr(studioforge, "_mgmt", rig.mgmt)
    providers.set_lease_mode("day")
    cfg = _sf_cfg()
    monkeypatch.setattr(jmod, "select_judge", lambda c, s, override=None:
                        {"provider": "sf", "model_id": JUDGE})
    jmod.acquire_judge_lease(cfg, force_evict=True)
    assert rig.lease_bodies and all(b["force"] is False for b in rig.lease_bodies)


def test_cmd_bench_all_resets_the_lease_mode(monkeypatch):
    seen = {}
    monkeypatch.setattr(bench_all, "execute",
                        lambda a, c: seen.setdefault("mode", providers.LEASE_MODE[0]) and 0)
    cli.cmd_bench_all(argparse.Namespace(lease_mode="day"), {})
    assert seen["mode"] == "day" and providers.LEASE_MODE[0] is None


def test_day_mode_execute_implies_no_judge(monkeypatch, tmp_path):
    cfg = _cfg([_m("a", "org/a")])
    calls = _wire_execute(monkeypatch, tmp_path, cfg, ["org/a"], {"a": _done("C1")}, {"a": 1})
    args = _args(only="a", lease_mode="day")
    assert bench_all.execute(args, cfg) == 0
    assert calls["run"] == ["a"] and calls["judge"] == [] and calls["ladder"] == []


# --------------------------------------------------------------- CLI argv

def test_new_flags_parse_and_plan_json_refuses_go(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: {"models": [], "providers": {}})
    monkeypatch.setattr(cli, "cmd_bench_all", lambda args, cfg: seen.setdefault("a", args) and 0)
    monkeypatch.setattr(cli, "acquire_rig_lock", lambda: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["bench-all", "--only", "a,b", "--lease-mode", "day", "--no-judge",
                  "--until", "18:00"])
    assert e.value.code == 0
    a = seen["a"]
    assert a.only == "a,b" and a.lease_mode == "day" and a.no_judge and not a.plan_json
    with pytest.raises(SystemExit) as e:
        cli.main(["bench-all", "--plan-json", "--go"])
    assert e.value.code == 2


def test_nightly_argv_still_parses_with_the_old_defaults(monkeypatch):
    """The 01:00 crucibleforge-rebuild-nightly.sh plan and launch argv."""
    seen = []
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: {"models": [], "providers": {}})
    monkeypatch.setattr(cli, "cmd_bench_all", lambda args, cfg: seen.append(args) or 0)
    with pytest.raises(SystemExit):
        cli.main(["bench-all", "--no-prune", "--escalate", "--until", "03:00"])
    a = seen[-1]
    assert a.only is None and a.lease_mode is None and not a.no_judge and not a.plan_json
    assert a.no_prune and a.escalate and a.until == "03:00" and not a.go


def test_detached_unit_has_a_bounded_stop(monkeypatch):
    import types
    monkeypatch.setattr(cli, "ENV_FILE", None)
    args = types.SimpleNamespace(cmd="bench-all", models=None, run_id="mbo-1-x")
    unit, cmd, rid = cli._detach_argv(args, ["bench-all", "--go", "--only", "a", "--detach"])
    assert "--property=TimeoutStopSec=45" in cmd and "--property=KillMode=mixed" in cmd
    assert cmd[:4] == ["systemd-run", "--user", "--collect", "--quiet"]
    assert unit == "crucibleforge-mbo-1-x" and "--only a" in cmd[-1]
    assert "--detach" not in cmd[-1] and "--run-id mbo-1-x" in cmd[-1]


def test_finish_meta_carries_the_exit_code_and_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path)
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path / "results")
    args = argparse.Namespace(cmd="bench-all", models="a", run_id="mbo-7", requester=None,
                              deliver_to=None, task_run_id=None, profile=None, judge=None,
                              categories=None, difficulty=None, cases=None, only="a",
                              lease_mode="day", no_judge=False)
    state = cli._v2_start(args)
    cfg = {"models": [], "providers": {}, "defaults": {}, "judge": {"candidates": []}}
    cli._v2_finish("bench-all", args, cfg, 4, state)
    meta = json.loads((tmp_path / "mbo-7" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed" and meta["exit_code"] == 4
    assert meta["bench_all"] == {"only": ["a"], "lease_mode": "day", "no_judge": True,
                                 "models": ["a"]}


# --------------------------------------------------------------- plan JSON

def test_plan_json_is_one_object_with_estimates_and_verdicts():
    cfg = _cfg([_m("a", "org/a"), _m("b", "org/b")])
    p = _plan(cfg, ["org/a", "org/b"], rows={"a": _done("C1"), "b": _done("C1")},
              pending={"b": 1})
    p["_private"] = {"ids": {"C3"}, "terms": []}
    est = {"a": (30.0, 2.0), "b": (500.0, 2.0)}
    start = datetime(2026, 11, 6, 1, 0)
    obj = bench_all.plan_json(p, cfg, until="03:00", start=start,
                              estimate_for=lambda g: est[g["models"][0]],
                              pending_for=lambda labels: 0.0, pending_label=lambda l: 1.0)
    text = bench_all.dump_plan_json(obj, p)
    back = json.loads(text)
    assert "\n" not in text and back["kind"] == "bench-all-plan"
    a, b = back["models"]["a"], back["models"]["b"]
    assert a["missing_cases"] == 2 and a["missing_public"] == ["C2"] and a["missing_private"] == 1
    assert a["gen_min"] == 30.0 and a["judge_min"] > 0 and a["verdict"] == "fits"
    assert b["verdict"] == "does_not_fit" and back["window"]["left"] == ["b"]
    assert back["window"]["verdict"] == "partial" and back["window"]["until"] == "03:00"
    assert "C3" not in text                               # private ids only counted
    # no --until: no window verdict
    obj = bench_all.plan_json(p, cfg, until=None, start=start,
                              estimate_for=lambda g: est[g["models"][0]],
                              pending_for=lambda labels: 0.0, pending_label=lambda l: 0.0)
    assert obj["models"]["a"]["verdict"] == "no_window"
    assert obj["window"]["verdict"] == "no_window"


def test_plan_json_window_agrees_with_the_text_schedule():
    cfg = _cfg([_m("a", "org/a"), _m("b", "org/b"), _m("c", "org/c")])
    p = _plan(cfg, ["org/a", "org/b", "org/c"],
              rows={"a": _done("C1"), "b": _done("C2"), "c": _done("C3")})
    est = {"a": (40.0, 2.0), "b": (200.0, 2.0), "c": (20.0, 1.0)}
    start = datetime(2026, 11, 6, 1, 0)
    _, end = bench_all.deadlines("03:00", 0, now=start)
    win = bench_all.Window(end, 45)
    ef = lambda g: est[g["models"][0]]          # noqa: E731
    pf = lambda labels: 0.0                     # noqa: E731
    lines = "\n".join(bench_all.schedule_lines(p, cfg, win, start, ef, pf))
    sched = bench_all.window_schedule(p, cfg, win, start, ef, pf)
    for label, row in sched["per_label"].items():
        if row["verdict"] == "does_not_fit":
            assert f"{label} (~" in lines.split("does not fit")[1]
        else:
            assert f"generate {label} " in lines


def test_plan_json_cli_prints_only_json(monkeypatch, tmp_path, capsys):
    cfg = _cfg([_m("a", "org/a"), _m("b", "org/b")])
    _wire_execute(monkeypatch, tmp_path, cfg, ["org/a", "org/b"],
                  {"a": _done("C1"), "b": _done("C1")}, {})
    monkeypatch.setattr(bench_all, "group_estimate", lambda *a, **k: (10.0, 1.0))
    monkeypatch.setattr(bench_all, "pending_equiv", lambda labels, cfg=None: 0.0)
    args = _args(go=False, plan_json=True, only="a", until="03:00", plan_start="01:00")
    assert bench_all.execute(args, cfg) == 0
    out = capsys.readouterr().out.strip()
    obj = json.loads(out)
    assert list(obj["models"]) == ["a"] and obj["only"] == ["a"]
    assert obj["models"]["a"]["verdict"] == "fits"


# ----------------------------------------------------------- keep_on_board

def test_keep_on_board_rows_are_never_pruned():
    cfg = _cfg([_m("kept", "org/kept", keep_on_board=True), _m("gone", "org/gone"),
                _m("off", "org/off", enabled=False, keep_on_board=True)])
    p = _plan(cfg, [], board=["kept", "gone", "off"])
    assert dict(p["prune"]) == {"gone": "no longer on the rig"}
    assert p["kept_on_board"] == ["kept"] and p["not_on_rig"] == ["gone"]
    assert "keep_on_board" in bench_all.format_plan(p)


def test_no_prune_is_still_honoured(monkeypatch, tmp_path):
    cfg = _cfg([_m("a", "org/a"), _m("gone", "org/gone")])
    _wire_execute(monkeypatch, tmp_path, cfg, ["org/a"], {"a": _done(*CASES), "gone": []}, {})
    monkeypatch.setattr(bench_all, "prune_results",
                        lambda *a, **k: pytest.fail("pruned under --no-prune"))
    assert bench_all.execute(_args(no_prune=True, escalate=False), cfg) in (0, 1)
