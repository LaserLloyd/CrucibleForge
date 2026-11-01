"""Fixes for the independent Opus review of suite 3.9 (REVIEW-OPUS-39.md,
2026-10-29): bench-all deadline + ladder packs (B1, B2), errored and
ran-out rows count failed (B3, B4), the judge lease never on GPU 3 (S1),
SIGTERM in the judge phase (S2), partial runs (S3), private ladder packs
(S4), the judge lease marks the rig touched (S5), sessions in Efficiency
(S6), SSE edge cases (N1)."""
from __future__ import annotations

import pytest

from crucibleforge import judge, providers, studioforge


# ------------------------------------------------------------------ S1, S5

def _sf_cfg(**prov):
    return {"providers": {"sf": {"type": "studioforge", "base_url": "http://sf.invalid/v1",
                                 "lease": True, **prov}},
            "defaults": {}, "models": [], "judge": {"candidates": []}}


CAND = {"provider": "sf", "model_id": "j/judge-31b-GGUF/q8"}


@pytest.fixture
def lease_calls(monkeypatch):
    calls = {"acquire": []}

    def acquire(base_url, api_key, headers, devices, **kw):
        calls["acquire"].append(list(devices))
        return {"_lease_id": "NEW", "id": "NEW"}

    monkeypatch.setattr(studioforge, "acquire_lease", acquire)
    monkeypatch.setattr(studioforge, "release_lease", lambda *a, **k: True)
    monkeypatch.setattr(studioforge, "release_stale_leases", lambda *a, **k: [])
    monkeypatch.setattr(studioforge, "vacate_render_leases", lambda *a, **k: False)
    monkeypatch.setattr(studioforge, "list_leases", lambda *a, **k: [])   # GPU 3 is FREE
    monkeypatch.setattr(judge, "select_judge", lambda cfg, ex, override=None: dict(CAND))
    providers.clear_cache()
    yield calls
    providers.clear_cache()


def test_judge_lease_never_names_gpu3_even_when_it_is_free(monkeypatch, lease_calls):
    cfg = _sf_cfg(lease_devices=[0, 1, 2, 3], lease_devices_preferred=[0, 1])
    monkeypatch.setattr(studioforge, "placement_fits", lambda *a, **k: False)
    judge.acquire_judge_lease(cfg)
    providers.clear_cache()
    # no preferred cards, the whole configured set
    judge.acquire_judge_lease(_sf_cfg(lease_devices=[0, 1, 2, 3]))
    providers.clear_cache()
    # no device config: the rig's card list
    monkeypatch.setattr(studioforge, "gpu_indices", lambda *a, **k: [0, 1, 2, 3])
    judge.acquire_judge_lease(_sf_cfg())
    assert lease_calls["acquire"] == [[0, 1, 2], [0, 1, 2], [0, 1, 2]]


def test_judge_lease_marks_the_rig_touched_so_residents_come_back(lease_calls):
    cfg = _sf_cfg(lease_devices=[0, 1])
    judge.acquire_judge_lease(cfg)
    assert providers.get_provider(cfg, "sf")._touched is True


# ------------------------------------------------------------------ N1 (api)

def _serve(monkeypatch, body: str):
    import httpx
    from crucibleforge import api

    def handler(request):
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    real = httpx.Client

    class _C(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)
    monkeypatch.setattr(api.httpx, "Client", _C)


def _chunk(finish=None, **delta):
    import json
    ch = {"delta": delta}
    if finish:
        ch["finish_reason"] = finish
    return "data: " + json.dumps({"model": "m", "choices": [ch]})


def test_a_last_sse_line_without_its_newline_is_parsed(monkeypatch):
    from crucibleforge import api
    _serve(monkeypatch, _chunk(content="42") + "\n\n" + _chunk(finish="stop"))
    r = api.stream_chat("http://x", "", "m", [], max_tokens=8)
    assert r.response_text == "42" and r.finish_reason == "stop"


def test_done_after_content_counts_as_finished(monkeypatch):
    from crucibleforge import api
    _serve(monkeypatch, _chunk(content="42") + "\n\ndata: [DONE]\n\n")
    assert api.stream_chat("http://x", "", "m", [], max_tokens=8).response_text == "42"


def test_done_alone_is_still_a_dropped_stream(monkeypatch):
    from crucibleforge import api
    _serve(monkeypatch, "data: [DONE]\n\n")
    with pytest.raises(api.TransportError, match="dropped"):
        api.stream_chat("http://x", "", "m", [], max_tokens=8)


def test_a_passed_deadline_drops_the_request_without_a_retry(monkeypatch):
    import time
    from crucibleforge import api
    _serve(monkeypatch, _chunk(content="42") + "\n\n" + _chunk(finish="stop") + "\n\n")
    with pytest.raises(api.WindowClosed):
        api.stream_chat_retried("http://x", "", "m", [], max_tokens=8, deadline=time.time() - 1)
    r = api.stream_chat("http://x", "", "m", [], max_tokens=8, deadline=time.time() + 60)
    assert r.response_text == "42"


# ------------------------------------------------------------------ S2

def _judge_fixture(tmp_path, monkeypatch, n_rows, concurrency):
    import json
    from crucibleforge import config
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [{"bench_run_id": "r", "case_id": f"C{i:02d}", "repeat": 1, "turn": None,
             "rubric": "rp_single", "needs_judge": True, "response": "scene", "prompt": "p"}
            for i in range(n_rows)]
    (tmp_path / "transcripts_m.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    loads = []

    class FakeProv:
        type, name = "openai", "fake"

    class JC:
        model_id, thinking, label, provider = "j", False, "j @ fake", FakeProv()
        deadline = None

        def __init__(self):
            self.concurrency = concurrency

        def load(self):
            loads.append(1)
            return 0.0

    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: JC())
    monkeypatch.setattr(judge, "run_canary", lambda jc: None)
    monkeypatch.setattr(judge, "_private_case_ids", lambda cfg: set())
    cfg = {"models": [{"name": "m", "model_id": "m", "provider": "fake"}],
           "judge": {"samples": 1}, "providers": {}, "defaults": {}}
    return cfg, loads


@pytest.mark.parametrize("concurrency", [1, 4])
def test_sigterm_in_the_judge_phase_stops_without_reloading_the_judge(tmp_path, monkeypatch,
                                                                      concurrency):
    import threading
    from crucibleforge import api, config, stopflag
    cfg, loads = _judge_fixture(tmp_path, monkeypatch, 24, concurrency)
    calls, lock = [], threading.Lock()

    def fake_judge_row(jc, r, samples=1):
        with lock:
            calls.append(r["case_id"])
            n = len(calls)
        if n == 3:
            stopflag.ABORT.set()          # SIGTERM arrives during the phase
        if stopflag.ABORT.is_set():
            raise api.Aborted("stopping: stream dropped")
        return {"judge_failed": False, "judge_raw": "{}", "refused": False, "scores": {}}

    monkeypatch.setattr(judge, "judge_row", fake_judge_row)
    try:
        res = judge.run_judge(cfg, ["m"])
    finally:
        stopflag.ABORT.clear()
    assert loads == []                                  # no reload on the way out
    assert len(calls) <= 3 + concurrency               # queued rows never started
    assert res["stopped"] and res["left"] >= 24 - 3
    judged = [r for r in config.load_transcripts("m") if r.get("judge")]
    assert len(judged) == res["judged"] <= 2            # the aborted rows stay pending


def test_a_judge_deadline_leaves_the_rest_pending(tmp_path, monkeypatch):
    import time
    cfg, loads = _judge_fixture(tmp_path, monkeypatch, 6, 1)
    seen = []

    def fake_judge_row(jc, r, samples=1):
        seen.append(jc.deadline)
        return {"judge_failed": False, "judge_raw": "{}", "refused": False, "scores": {}}

    monkeypatch.setattr(judge, "judge_row", fake_judge_row)
    res = judge.run_judge(cfg, ["m"], deadline=time.time() - 1)
    assert seen == [] and res["stopped"] and res["left"] == 6
    res = judge.run_judge(cfg, ["m"], deadline=time.time() + 600)
    assert res["judged"] == 6 and seen and all(d for d in seen)


# ------------------------------------------------------- B3, B4, S6 (report)

def _pb():
    import test_points_board_39 as pb
    return pb


def _nsfw_row(cid, rep=1, **kw):
    from crucibleforge.judge import RUBRICS
    pb = _pb()
    dims = RUBRICS["nsfw_craft"]["dims"]
    base = dict(needs_judge=True, rubric="nsfw_craft", response="He kissed her. " * 40,
                grade=None, regime="deployed",
                judge={"scores": {**{d: 9 for d in dims}, "explicitness": 9}})
    base.update(kw)
    return pb._row(cid, "nsfw", rep, False, **base)


def _transport_error(cid, cat, rep=1, ts="2026-10-28T03:00:00+00:00"):
    pb = _pb()
    return pb._row(cid, cat, rep, False, run="err", ts=ts, grade="error",
                   error="transport failure: dropped", error_kind="transport",
                   finish_reason="error", response="")


WRITING = ["NX2-juniper", "XP3-named-peak", "NW1-salt-and-cedar"]


def test_an_errored_judged_row_counts_failed_not_dropped(tmp_path, monkeypatch):
    """B3: one newer transport-error row for NX2 used to drop NX2 from NSFW
    writing (the mean retaken over the other two); it counts 0 now."""
    pb = _pb()
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    good = pb._rows() + [_nsfw_row(c) for c in WRITING]
    st = pb._board(env, {"good": good, "err": good + [_transport_error("NX2-juniper", "nsfw")]})
    per = st["good"]["nsfw"]["gated_parts"]["per_case"]
    want = (per["XP3-named-peak"] + per["NW1-salt-and-cedar"]) / 3 * 4
    got = st["err"]["scorecard"]["parts"]["nsfw"]["nsfw_writing"]
    assert got == pytest.approx(want, abs=1e-6)
    assert st["err"]["nsfw"]["refuses"] == 0               # an error is not a refusal
    assert st["err"]["errored_rows"]["n"] == 1


def test_an_all_errored_judged_sub_score_is_zero_not_pending(tmp_path, monkeypatch):
    pb = _pb()
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    rows = pb._rows() + [_transport_error(c, "nsfw") for c in WRITING]
    st = pb._board(env, {"e": rows})
    assert st["e"]["scorecard"]["parts"]["nsfw"]["nsfw_writing"] == 0.0
    assert "rows errored (counted failed" in report_notes(st["e"])


def report_notes(s):
    from crucibleforge import report
    return report.notes_cell(s)


def test_a_ran_out_row_without_a_finished_rerun_counts_failed(tmp_path, monkeypatch):
    """B4: a judged row cut at the budget (finish=length) with no escalated
    re-run used to keep its full judged score; a deterministic one too."""
    pb = _pb()
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    good = pb._rows() + [_nsfw_row(c) for c in WRITING]
    cut = [r for r in good if r["case_id"] != "NX2-juniper"] + [
        _nsfw_row("NX2-juniper", finish_reason="length", truncated=True)]
    # a passing deterministic row that ran out
    cut = [r for r in cut if not (r["case_id"] == "RX13-recurrence-term" and r["repeat"] == 1)]
    cut.append(pb._row("RX13-recurrence-term", "reasoning", 1, False, finish_reason="length"))
    rerun_ok = _nsfw_row("NX2-juniper", run="esc", budget_rung=65536,
                         ts="2026-10-28T02:00:00+00:00")
    rerun_cut = _nsfw_row("NX2-juniper", run="esc2", budget_rung=131072, finish_reason="length",
                          ts="2026-10-28T02:30:00+00:00")
    st = pb._board(env, {"good": good, "cut": cut, "fixed": cut + [rerun_ok],
                         "still": cut + [rerun_ok, rerun_cut]})
    per = st["good"]["nsfw"]["gated_parts"]["per_case"]
    two = (per["XP3-named-peak"] + per["NW1-salt-and-cedar"]) / 3 * 4
    def w(k):
        return st[k]["scorecard"]["parts"]["nsfw"]["nsfw_writing"]
    assert w("cut") == pytest.approx(two, abs=1e-6)
    assert w("fixed") == pytest.approx(w("good"), abs=1e-6)       # the finished re-run counts
    assert w("still") == pytest.approx(two, abs=1e-6)             # re-run ran out too: failed
    # Reasoning (reasoning + math, 7 rows): the one that ran out -> 6 of 7
    assert st["cut"]["scorecard"]["parts"]["work"]["reasoning"] == pytest.approx(3 * 6 / 7)
    assert st["cut"]["efficiency"]["ran_out"] == 2 and st["cut"]["efficiency"]["pending"] == 2
    assert st["fixed"]["efficiency"]["pending"] == 1 and st["still"]["efficiency"]["pending"] == 2


def _session(cid, cat, rep, n_turns, length_turn=None, **final):
    pb = _pb()
    out = []
    for t in range(1, n_turns + 1):
        kw = {"turn": t, "response": "line", "grade": None,
              "finish_reason": "length" if t == length_turn else "stop"}
        if t == n_turns:
            kw.update(needs_judge=True, rubric="rp_deep",
                      conversation=[{"role": "user", "content": "u"},
                                    {"role": "assistant", "content": "line"}], **final)
        out.append(pb._row(cid, cat, rep, False, **kw))
    return out


def test_efficiency_and_errored_counts_include_sessions(tmp_path, monkeypatch):
    """S6: sessions (every turn row has a turn number) used to be invisible
    to Efficiency and to the errored n/N; one unit per session now."""
    pb = _pb()
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    rows = pb._rows() + _session("CC3-tea-hut", "rpdeep", 1, 4, length_turn=2,
                                 judge={"scores": {}, "elements": {"results": []}})
    rows += _session("CC4-second-pitch", "rpdeep", 1, 3,
                     judge={"scores": {}, "elements": {"results": []}})
    rows.append(_transport_error("RD1-marisol", "rpdeep"))
    st = pb._board(env, {"s": rows})
    e = st["s"]["efficiency"]
    n_work = len([r for r in pb._rows() if r["category"] not in ("perf", "instruct")])
    assert e["n"] == n_work + 3 and e["ran_out"] == 1
    assert st["s"]["errored_rows"] == {"n": 1, "total": n_work + 3}
    # the errored session and the one that ran out are scored 0 (B3/B4), never
    # left out of Deep RP / Conversation
    per = st["s"]["rpdeep"]["per_case"]
    assert per.get("RD1-marisol") == 0.0 and per.get("CC3-tea-hut") == 0.0


# ------------------------------------------------------------------ S3

def test_a_partial_run_is_not_extrapolated_and_ranks_below_complete_runs(tmp_path, monkeypatch):
    """S3: a model cut before its Reasoning rows got Work scaled from the
    measured parts ("scaled from x of y") and a full rank (#1 over complete
    models). Now: the measured points unscaled, marked partial, no Overall,
    unranked below every complete run."""
    from crucibleforge import report
    pb = _pb()
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    reason = {"reasoning", "math"}
    part = [r for r in pb._rows() if r["category"] not in reason]
    weak = pb._rows(fail_cases={"TZ06-three-parallel-one-turn", "CZ02-segment-crossings"})
    st = pb._board(env, {"p": part, "c": weak})
    cp, cc = st["p"]["scorecard"], st["c"]["scorecard"]
    assert cp["parts"]["work"]["reasoning"] is None                  # pending
    assert cp["work"] == pytest.approx(4.0 + 3.0)                   # not x 10/7
    assert "partial" in cp["labels"]["work"] and cp["total"] is None
    assert cc["total"] is not None
    labels, stats, _ = report.board_stats(None, env.cfg)
    assert report.rank_labels(labels, stats) == ["c", "p"]
    assert stats["p"]["headline"]["rank"] == "-" and stats["c"]["headline"]["rank"] == "1"
    md = report.generate(cfg=env.cfg)
    assert "scaled from" not in md


# ------------------------------------------------------- B1, B2 (bench-all)

import argparse  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402


def test_window_reserve_follows_the_judge_estimate_with_the_flag_as_floor():
    from crucibleforge import bench_all, estimate as est
    end = datetime(2026, 10, 29, 16, 0)
    w = bench_all.Window(end, floor_min=45)
    assert w.judge_cut == end - timedelta(minutes=bench_all.REPORT_TAIL_MIN)
    # little judge work: the floor; a lot (10 models x ~9 rows): the estimate
    assert w.reserve_min(2) == 45
    assert w.reserve_min(90) == pytest.approx(est.judge_min(90, 3))
    assert w.gen_end(90) == w.judge_cut - timedelta(minutes=est.judge_min(90, 3)
                                                    + bench_all.GEN_GRACE_MIN)
    start = datetime(2026, 10, 29, 10, 0)
    assert w.fits(start, 60, 30) and not w.fits(start, 400, 30)
    # in-flight rows after the stop: capped, never past the judge cut
    assert w.gen_cut(w.gen_end(90)) <= w.judge_cut


@pytest.fixture
def ba(monkeypatch, tmp_path):
    """bench_all.execute with every rig/model phase faked; a window that ends
    a few seconds from now (minutes scaled down to ~seconds)."""
    from crucibleforge import bench_all, cli, runner, stopflag
    from crucibleforge import config as cfgmod
    import crucibleforge.providers as pv
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)

    class Prov:
        def list_models(self, refresh=False):
            return {"x/a", "x/b", "x/c"}

    monkeypatch.setattr(pv, "get_provider", lambda cfg, name: Prov())
    monkeypatch.setattr(bench_all, "rig_arches", lambda prov: {})
    monkeypatch.setattr(bench_all, "format_plan", lambda p: "plan")
    # 1 "minute" = 0.05 s
    scale = 0.05 / 60
    for k in ("REPORT_TAIL_MIN", "JUDGE_STOP_LEAD_MIN", "GEN_GRACE_MIN", "MIN_JUDGE_MIN"):
        monkeypatch.setattr(bench_all, k, getattr(bench_all, k) * scale)
    st = {"end": None, "events": [], "plan": None}

    def deadlines(until, reserve, now=None):
        return None, st["end"]
    monkeypatch.setattr(bench_all, "deadlines", deadlines)
    monkeypatch.setattr(bench_all, "pending_equiv", lambda labels, cfg=None: 0.0)
    from crucibleforge import estimate
    monkeypatch.setattr(estimate, "judge_min", lambda n, samples=3: 0.01 * scale * 60 * n)

    def cmd_run(a, c):
        st["events"].append(("run", a.models, runner.GEN_DEADLINE[0]))
        return 0

    def cmd_judge(a, c):
        st["events"].append(("judge", a.models))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:      # a judge that would take far too long
            if a.judge_stop is not None and a.judge_stop.is_set():
                st["events"].append(("judge stopped", round(time.monotonic() - t0, 2)))
                return 0
            time.sleep(0.01)
        return 0

    def cmd_report(a, c, full_board=False):
        st["events"].append(("report", datetime.now()))
        return 0

    monkeypatch.setattr(cli, "cmd_run", cmd_run)
    monkeypatch.setattr(cli, "cmd_judge", cmd_judge)
    monkeypatch.setattr(cli, "cmd_report", cmd_report)
    monkeypatch.setattr(cli, "cmd_escalate", lambda a, c: st["events"].append(("esc",)) or 0)
    monkeypatch.setattr(cli, "_ladder_phases",
                        lambda a, c, labels: st["events"].append(("ladder", list(labels),
                                                                   a.ladder_admit)) or 0)
    monkeypatch.setattr(bench_all, "plan", lambda *a, **k: dict(st["plan"]))
    stopflag.ABORT.clear()
    runner.STOP.clear()
    yield st
    runner.STOP.clear()
    runner.WINDOW_STOP.clear()
    runner.GEN_DEADLINE[0] = None


def _args(**kw):
    a = dict(go=True, include_api=False, first=None, no_sync=True, no_prune=True,
             until="16:00", judge_reserve_min=0)
    a.update(kw)
    return argparse.Namespace(**a)


BA_CFG = {"providers": {"sf": {"type": "studioforge"}}, "models": [], "_path": "m.yaml"}


def _ba_plan(groups, judge=(), ladder=()):
    return {"add": [], "prune": [], "not_on_rig": [], "escalate": [], "rungs": [],
            "groups": groups, "judge": list(judge), "candidates": [], "ladder": list(ladder),
            "rig_models": 3, "case_count": 3}


def test_bench_all_finishes_everything_by_the_window_end(ba, monkeypatch):
    """B1: the judge phase had no deadline (3.8: generation stopped 02:15,
    the judge ran to 05:07). With a slow judge the run now ends — report
    rebuilt — before the --until time; a group whose estimate does not fit
    is not started."""
    from crucibleforge import bench_all
    ba["end"] = datetime.now() + timedelta(seconds=3)
    est = {"a": (0.001, 1.0), "b": (100000.0, 1.0)}           # b: ~70 days
    monkeypatch.setattr(bench_all, "group_estimate",
                        lambda cfg, labels, cases, add=None, prov=None: est[labels[0]])
    ba["plan"] = _ba_plan([{"models": ["a"], "cases": ["C1"]},
                           {"models": ["b"], "cases": ["C1"]}], judge=["a"])
    args = _args()
    bench_all.execute(args, dict(BA_CFG))
    kinds = [e[0] for e in ba["events"]]
    assert ("run", "a") == ba["events"][0][:2] and ba["events"][0][2]   # in-flight cap armed
    assert not any(e[0] == "run" and e[1] == "b" for e in ba["events"])
    assert "judge stopped" in kinds
    report_at = [e[1] for e in ba["events"] if e[0] == "report"]
    assert report_at and all(t <= ba["end"] for t in report_at)
    assert any("left for the next run: b" in n for n in args.bench_all_notes)


def test_bench_all_runs_due_ladder_packs_inside_the_window(ba, monkeypatch):
    """B2: bench-all never climbed the ladder; it now runs cli._ladder_phases
    (as `all` does) for the plan's due models and this run's, with a window
    admission check."""
    from crucibleforge import bench_all
    ba["end"] = datetime.now() + timedelta(hours=10)
    monkeypatch.setattr(bench_all, "group_estimate",
                        lambda cfg, labels, cases, add=None, prov=None: (0.001, 1.0))
    ba["plan"] = _ba_plan([{"models": ["a"], "cases": ["C1"]}],
                          ladder=[{"model": "c", "sets": ["work/code#1"], "jobs": 2}])
    bench_all.execute(_args(), dict(BA_CFG))
    lad = [e for e in ba["events"] if e[0] == "ladder"]
    assert lad and lad[0][1] == ["a", "c"] and callable(lad[0][2])
    assert [e[0] for e in ba["events"]].index("judge") < [e[0] for e in ba["events"]].index("ladder")


def test_due_ladder_packs_keep_the_nightly_from_marking_itself_done():
    from crucibleforge import bench_all
    cfg = {"providers": {"sf": {"type": "studioforge"}},
           "models": [{"name": "a", "model_id": "org/a", "provider": "sf"}]}
    kw = dict(board_labels=["a"], rows_for=lambda l: [{"case_id": "C1", "grade": "pass"}],
              pending_for=lambda l: 0, attempts_for=lambda l: {}, judge_id="j",
              case_ids=["C1"], rungs=[])
    p = bench_all.plan(cfg, {"org/a"}, ladder_for=lambda l: None, **kw)
    assert bench_all.nothing_to_do(p)
    p = bench_all.plan(cfg, {"org/a"},
                       ladder_for=lambda l: {"names": ["work/code#1"], "jobs": 3}, **kw)
    assert not bench_all.nothing_to_do(p)
    import re
    txt = bench_all.format_plan(p)
    # the nightly wrapper's done-check: grep -E '^  (> generate|> escalate|> judge|\+ register)'
    assert any(re.match(r"^  (> generate|> escalate|> judge|\+ register)", ln)
               for ln in txt.splitlines()), txt


def test_window_closed_mid_request_writes_no_row_and_is_not_a_failure(monkeypatch, tmp_path):
    from crucibleforge import api, runner
    from crucibleforge import config as cfgmod
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)

    class P:
        name, type = "fake", "openai"

        def chat(self, model_id, messages, **kw):
            assert kw.get("deadline") == 123.0
            raise api.WindowClosed("the run's time window closed: request dropped")

    runner.GEN_DEADLINE[0] = 123.0
    try:
        ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "thinking": False},
                          P(), 8192)
        with pytest.raises(api.WindowClosed):
            ctx._chat([{"role": "user", "content": "x"}], max_tokens=8)
    finally:
        runner.GEN_DEADLINE[0] = None
    # the window's stop is recorded as such: the meta is not FAILED
    runner.WINDOW_STOP.set()
    try:
        summary = {}
        monkeypatch.setattr(runner, "provider_for", lambda cfg, e: P())
        runner._record_stop({"_profile": "bench"}, {"name": "m", "model_id": "m"}, "m",
                            summary, None, None, None)
    finally:
        runner.WINDOW_STOP.clear()
    import json
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["failed"] is False and meta.get("window_stopped")
    assert summary["m"].get("window")


# ------------------------------------------------------------------ S4

def test_private_ladder_packs_never_reach_the_public_board(tmp_path, monkeypatch):
    """S4: ladder.roots() returned the private ladder dir for every audience
    and private_scope() did not know a pack's own question ids."""
    import json
    from crucibleforge import config as cfgmod, ladder, profiles, report
    pb = _pb()
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    pdir = tmp_path / "cases-private"
    d = pdir / "ladder" / "nsfw" / "nsfw_writing"
    d.mkdir(parents=True)
    (d / "set-7.json").write_text(json.dumps({"cases": [
        {"id": "ZP1-secret-pack", "category": "nsfw", "prompt": "p", "rubric": "nsfw_craft",
         "max_tokens": 100}]}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "PRIVATE_ENABLED", True)
    monkeypatch.setattr(cfgmod, "PRIVATE_CASES_DIR", pdir)
    prof = profiles.load_profile("bench", env.cfg)
    local = ladder.discover(prof)
    public = ladder.discover(report._audience_profile(prof, {**env.cfg, "_audience": "public"}))
    assert any(s["n"] == 7 for s in local.get("nsfw/nsfw_writing") or [])
    assert not any(s["n"] == 7 for s in public.get("nsfw/nsfw_writing") or [])
    scope = profiles.private_scope(env.cfg)
    assert "ZP1-secret-pack" in scope["case_ids"]
    assert any("set-7.json" in t for t in scope["terms"])
    pub_ids = report._profile_case_ids({**env.cfg, "_audience": "public"}, with_tiers=True)
    assert pub_ids is not None and "ZP1-secret-pack" not in pub_ids


def test_rows_in_flight_past_the_generation_cap_are_dropped_not_failed(tmp_path, monkeypatch):
    """B1: after the window's stop, rows in flight used to run to completion
    with no cap (a 65k bank row: ~55 min). Past GEN_DEADLINE the request is
    dropped: no row (the job stays pending), the model is not FAILED."""
    import json
    from crucibleforge import api, config, providers, runner, stopflag
    from test_runner_stop_39 import _cfg as stop_cfg, _cases
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)

    def chat(self, model_id, messages, **kw):
        dl = kw.get("deadline")
        while not (dl and time.time() >= dl):        # a long generation
            time.sleep(0.02)
        raise api.WindowClosed("the run's time window closed: request dropped")

    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": True}

    def close():
        runner.WINDOW_STOP.set()
        runner.STOP.set()
    stopflag.ABORT.clear()
    runner.GEN_DEADLINE[0] = time.time() + 0.6
    threading.Timer(0.3, close).start()
    t0 = time.time()
    try:
        summary = runner.run_models(stop_cfg(), [entry], _cases())
    finally:
        runner.GEN_DEADLINE[0] = None
        runner.WINDOW_STOP.clear()
        runner.STOP.clear()
    assert time.time() - t0 < 5
    assert summary["m"].get("window")
    assert config.load_transcripts("m") == []
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["failed"] is False and meta.get("window_stopped")
