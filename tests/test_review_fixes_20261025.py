"""Fable review of 3.8.0 (2026-10-25): the fixes that held up.

* the tool loop sends the model's top_p (it sent the API default 1.0, which
  Kimi refuses: 18 of 21 tool rows errored) and stamps the budget it sent;
* a rung the rig cannot serve, or one that keeps failing, is given up so the
  nightly's plan converges instead of re-trying it every night;
* rows still on old values rank below every current row.
"""
import json

from crucibleforge import bench_all, config, report, runner
from crucibleforge.api import ChatResult

from test_escalation_20261025 import _row


# ------------------------------------------------------- tool loop sampler

class _Prov:
    name = "fake"
    type = "openai"

    def __init__(self):
        self.calls = []

    def chat(self, model_id, messages, **kw):
        self.calls.append(kw)
        return ChatResult(response_text="It is 21 degrees.", finish_reason="stop",
                          served_model="m")


def test_the_tool_loop_sends_the_models_top_p_and_stamps_the_boosted_budget():
    prov = _Prov()
    cfg = {"defaults": {"thinking_max_tokens_factor": 4, "thinking_max_tokens_cap": 24576,
                        "thinking_max_tokens_floor": 16384}}
    ctx = runner._Ctx(cfg, {"name": "m", "model_id": "m", "provider": "fake", "thinking": True},
                      prov, 32768)
    case = {"id": "T", "category": "tools", "tool_script": [{"user": "weather?", "answer_contains": ["21"]}]}
    row = runner._run_tool_loop(case, {"case_id": "T", "max_tokens_sent": 1024}, ctx, 1024,
                                1.0, 7, top_p=0.95)
    assert prov.calls and all(c.get("top_p") == 0.95 for c in prov.calls)
    assert row["max_tokens_sent"] == ctx.budget(1024) >= 16384


def test_a_model_without_a_recommended_top_p_sends_none_not_one():
    prov = _Prov()
    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "provider": "fake",
                                         "thinking": False}, prov, 32768)
    case = {"id": "T", "category": "tools", "tool_script": [{"user": "hi"}]}
    runner._run_tool_loop(case, {"case_id": "T"}, ctx, 1024, 0.0, 42)
    assert all(c.get("top_p", "unset") is None for c in prov.calls)


# ------------------------------------------- escalation converges


def _meta(tmp_path, recs, started="2026-10-25T01:00:00+00:00"):
    (tmp_path / "meta_m.json").write_text(
        json.dumps({"started": started, "escalated": recs}), encoding="utf-8")


def test_an_unservable_rung_is_given_up_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    _meta(tmp_path, [{"ts": "2026-10-25T02:00:00+00:00", "failed": True, "rung": 131072,
                      "error": "escalation to 131072 tokens needs ctx >= 131072; rung skipped"}])
    assert runner.blocked_rungs("m") == {131072}
    rows = [_row("A", rung=65536, sent=65536)]          # still cut at 64k
    assert runner.escalation_todo(rows, "m", [65536, 131072]) == set()


def test_a_rung_is_given_up_after_repeated_failures_but_not_after_a_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    fail = {"ts": "2026-10-25T02:00:00+00:00", "failed": True, "rung": 65536, "error": "HTTP 507"}
    stop = {**fail, "error": "stopped by user"}
    _meta(tmp_path, [fail, stop, stop])
    assert runner.blocked_rungs("m") == set()
    _meta(tmp_path, [fail, fail])
    assert runner.blocked_rungs("m") == {65536}
    # a newer board run starts the count again
    _meta(tmp_path, [fail, fail], started="2026-10-26T01:00:00+00:00")
    assert runner.blocked_rungs("m") == set()


def test_bench_all_plan_converges_once_every_rung_is_given_up(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    refused = {"ts": "2026-10-25T02:00:00+00:00", "failed": True,
               "error": "needs ctx >= {r}; rung skipped"}
    _meta(tmp_path, [{**refused, "rung": 65536}, {**refused, "rung": 131072}])
    cfg = {"providers": {"sf": {"type": "studioforge"}},
           "models": [{"name": "m", "model_id": "rig/m", "provider": "sf"}]}
    p = bench_all.plan(cfg, {"rig/m"}, case_ids=["A"], judge_id="judge",
                       pending_for=lambda l: 0, attempts_for=lambda l: {},
                       rows_for=lambda l: [_row("A")], room_for=lambda l: [_row("A")],
                       rungs=[65536, 131072])
    assert p["escalate"] == [] and bench_all.nothing_to_do(p)


# ------------------------------------------------- old rows rank below


def test_old_values_never_outrank_a_current_row():
    stats = {
        "old-strong": {"era": {"old": True}, "scorecard": {"total": 95.0}},
        "new-weak": {"era": {"old": False}, "scorecard": {"total": 60.0}},
        "new-strong": {"era": {"old": False}, "scorecard": {"total": 80.0}},
    }
    assert report.rank_labels(list(stats), stats) == ["new-strong", "new-weak", "old-strong"]


# ------------------------------------------- orphaned "running" reports

def test_a_report_left_running_by_a_dead_process_is_marked_failed(tmp_path, monkeypatch):
    import os
    import socket

    from crucibleforge import cli
    monkeypatch.setattr(cli, "V2_RUNS_ROOT", tmp_path)

    def meta(rid, **kw):
        d = tmp_path / rid
        d.mkdir()
        (d / "meta.json").write_text(json.dumps({"id": rid, "producer": cli.V2_PRODUCER,
                                                 "status": "running", **kw}), encoding="utf-8")
        return d / "meta.json"

    dead = meta("dead", pid=2 ** 22 + 12345, host=socket.gethostname())
    live = meta("live", pid=os.getpid(), host=socket.gethostname())
    other_host = meta("elsewhere", pid=2 ** 22 + 12345, host="another-box")
    fresh_no_pid = meta("fresh")
    done = meta("done")
    done.write_text(json.dumps({"id": "done", "producer": cli.V2_PRODUCER, "status": "done"}),
                    encoding="utf-8")
    monkeypatch.setattr(cli, "_pid_is_crucibleforge", lambda pid: pid == os.getpid())
    marked = cli._v2_sweep_orphans("mine")
    assert marked == ["dead"]
    assert json.loads(dead.read_text(encoding="utf-8"))["status"] == "failed"
    for p in (live, other_host, fresh_no_pid):
        assert json.loads(p.read_text(encoding="utf-8"))["status"] == "running"
    assert json.loads(done.read_text(encoding="utf-8"))["status"] == "done"
