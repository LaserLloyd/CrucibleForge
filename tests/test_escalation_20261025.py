"""Escalation round ("Room" column, 2026-10-25).

The board budget (24576) is the same for every model, so a long thinker that
needs 60k tokens on a hard program scores 0 on it. After a run, the jobs whose
answer ran out of budget are re-run on bigger rungs (65536, then 131072 for
what still runs out) with the model reloaded wide enough. Those rows carry
``budget_rung``: they never count on the board, and the report's Room column
is Overall with them swapped in. Maintainer: "smaller context with an
increased retry round for anything that doesn't have enough".
"""
import json

from crucibleforge import bench_all, config, report, runner
from crucibleforge.api import ChatResult
import pytest

# suite 3.9: these tests are written against the 3.8 case set and test the
# board/escalation mechanism, not the profile — run them on the frozen 3.8
# profile (conftest.bench_38)
pytestmark = pytest.mark.usefixtures("bench_38")



def _row(cid, finish="length", thinking=True, rung=None, sent=24576, cat="coding",
         repeat=1, turn=None, run="r1", ts="2026-10-25T01:00:00+00:00", **kw):
    from crucibleforge.version import revision
    r = {"bench_run_id": run, "bench_revision": revision(), "profile": "bench",
         "case_id": cid, "repeat": repeat, "turn": turn, "category": cat,
         "finish_reason": finish, "thinking": thinking, "max_tokens_sent": sent,
         "grade": "fail" if finish == "length" else "pass", "ts": ts,
         "metrics": {"tok_per_s": 30.0}, **kw}
    if rung:
        r["budget_rung"] = rung
    return r


# ------------------------------------------------------------- the rungs

def test_rungs_come_from_the_profile_and_only_count_above_the_cap():
    d = {"defaults": {"thinking_max_tokens_cap": 24576}}
    assert runner.escalation_rungs(d) == [65536, 131072]
    d["defaults"]["escalation_rungs"] = [131072, 16384, 65536]
    assert runner.escalation_rungs(d) == [65536, 131072]
    d["defaults"]["escalation_rungs"] = []
    assert runner.escalation_rungs(d) == []


def test_the_bench_profile_turns_escalation_on():
    from crucibleforge.profiles import apply_profile, load_profile
    cfg, _ = apply_profile(load_profile("bench", {}), {})
    assert runner.escalation_rungs(cfg) == [65536, 131072]


# ---------------------------------------------------- which jobs re-run

def test_only_thinking_jobs_that_ran_out_below_the_rung_are_picked():
    rows = [
        _row("A"),                                   # ran out at 24k -> re-run
        _row("B", finish="stop"),                    # finished
        _row("C", thinking=False),                   # non-thinking: its case budget
        _row("P1", cat="perf"),                      # timing row
        _row("D", skipped=True),                     # never ran
        _row("E", grade="error"),                    # errored, not a budget problem
        _row("S", turn=1, finish="stop", cat="rp"),  # a session: turn 2 ran out
        _row("S", turn=2, cat="rp"),
    ]
    assert runner.escalation_jobs(rows, 65536) == {(None, "A", 1), (None, "S", 1)}


def test_a_lower_rung_that_finished_is_not_re_run_and_one_still_cut_is():
    done = _row("A", finish="stop", rung=65536, sent=65536)
    cut = _row("B", rung=65536, sent=65536)
    assert runner.escalation_jobs([done, cut], 65536) == set()
    assert runner.escalation_jobs([done, cut], 131072) == {(None, "B", 1)}
    assert runner.escalation_jobs([_row("C", rung=131072, sent=131072)], 131072) == set()


# ------------------------------------------------ board vs Room view

def _seed(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / "transcripts_m.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_rung_rows_never_count_on_the_board_but_win_in_the_room_view(tmp_path, monkeypatch):
    cid = "IZ01-logistics-47-words"
    base = _row(cid, cat="instruct")
    esc = _row(cid, cat="instruct", finish="stop", rung=65536, sent=65536, run="e1",
               ts="2026-10-25T02:00:00+00:00")
    _seed(tmp_path, monkeypatch, [base, esc])
    board = report.board_filter("m", None)
    assert [r["bench_run_id"] for r in board] == ["r1"]
    room = report.board_filter("m", None, with_rungs=True)
    assert [r["bench_run_id"] for r in room] == ["e1"]


def test_a_later_board_rerun_supersedes_an_older_escalation(tmp_path, monkeypatch):
    cid = "IZ01-logistics-47-words"
    esc = _row(cid, cat="instruct", finish="stop", rung=65536, run="e1",
               ts="2026-10-25T02:00:00+00:00")
    rerun = _row(cid, cat="instruct", run="r2", ts="2026-10-26T01:00:00+00:00")
    _seed(tmp_path, monkeypatch, [_row(cid, cat="instruct"), esc, rerun])
    room = report.board_filter("m", None, with_rungs=True)
    assert [r["bench_run_id"] for r in room] == ["r2"]
    # ...and the job is due again
    assert runner.escalation_jobs(room, 65536) == {(None, cid, 1)}


def test_room_cell():
    assert report.room_cell({}) == ""
    assert report.room_cell({"room": {"total": None, "ran_out": 0}}) == "="
    assert report.room_cell({"room": {"total": None, "ran_out": 3}}) == "…"
    assert report.room_cell({"room": {"total": 81.04, "pending": 0}}) == "81.0"
    assert report.room_cell({"room": {"total": 81.04, "pending": 1}}) == "81.0…"


# ------------------------------------------- the round, end to end

CASES = [
    {"id": "IZ01-logistics-47-words", "category": "instruct", "prompt": "long one",
     "max_tokens": 6144, "grader": "contains", "grader_config": {"needles": ["ok"]}},
    {"id": "IZ03-no-letter-e-three-lines", "category": "instruct", "prompt": "short one",
     "max_tokens": 6144, "grader": "contains", "grader_config": {"needles": ["ok"]}},
]


def _cut():
    return ChatResult(response_text="partial", reasoning_text="think " * 20,
                      finish_reason="length", completion_tokens=24576, served_model="m")


def _ok():
    return ChatResult(response_text="ok", reasoning_text="think", finish_reason="stop",
                      completion_tokens=900, served_model="m")


def _harness(tmp_path, monkeypatch, needs):
    """``needs`` = output budget the long case needs to finish."""
    from crucibleforge import providers
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    sent, loads = [], []

    def chat(self, model_id, messages, **kw):
        sent.append(kw.get("max_tokens"))
        if "long one" in messages[-1]["content"] and kw.get("max_tokens", 0) < needs:
            return _cut()
        return _ok()

    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    monkeypatch.setattr(providers.Provider, "switch_model",
                        lambda self, mid, ctx=None: loads.append(ctx) or 0.0)
    cfg = {"defaults": {"context_length": 32768, "repeats": {}, "regime": "deployed",
                        "thinking_max_tokens_factor": 4, "thinking_max_tokens_cap": 24576,
                        "thinking_max_tokens_floor": 16384,
                        "reasoning_overflow_recovery": False},
           "providers": {"fake": {"type": "openai", "base_url": "http://127.0.0.1:9/v1"}},
           "judge": {"candidates": []}, "models": [], "_profile": "bench"}
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": True,
             "context_length": 32768}
    runner.run_models(cfg, [entry], CASES)
    return cfg, entry, sent, loads


def _room(label):
    return report.board_filter(label, None, with_rungs=True)


def test_the_round_re_runs_only_what_ran_out_and_stops_once_it_fits(tmp_path, monkeypatch):
    cfg, entry, sent, loads = _harness(tmp_path, monkeypatch, needs=60000)
    assert sorted(sent) == [24576, 24576]
    sent.clear(), loads.clear()
    out = runner.escalate_models(cfg, [entry], CASES, rungs=[65536, 131072], room_rows=_room)
    assert list(out) == [65536]                  # 64k was enough: no 128k round
    assert sent == [65536]                       # one job, on the rung's budget
    assert loads and loads[0] >= 65536 + runner.ESCALATION_CTX_PAD
    rows = config.load_transcripts("m")
    esc = [r for r in rows if r.get("budget_rung")]
    assert len(esc) == 1 and esc[0]["case_id"] == "IZ01-logistics-47-words"
    assert esc[0]["max_tokens_sent"] == 65536 and esc[0]["grade"] == "pass"
    assert esc[0]["bench_run_id"] not in {r["bench_run_id"] for r in rows if not r.get("budget_rung")}
    # the board still says it failed at 24k; the Room view says it passed
    board = {r["case_id"]: r["grade"] for r in report.board_filter("m", None)}
    room = {r["case_id"]: r["grade"] for r in _room("m")}
    assert board["IZ01-logistics-47-words"] == "fail" and room["IZ01-logistics-47-words"] == "pass"
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["escalated"][-1]["rung"] == 65536 and meta.get("finished")


def test_what_still_runs_out_climbs_to_the_next_rung(tmp_path, monkeypatch):
    cfg, entry, sent, loads = _harness(tmp_path, monkeypatch, needs=100000)
    sent.clear()
    out = runner.escalate_models(cfg, [entry], CASES, rungs=[65536, 131072], room_rows=_room)
    assert list(out) == [65536, 131072] and sent == [65536, 131072]
    top = _room("m")
    assert {r["case_id"]: r.get("budget_rung") for r in top}["IZ01-logistics-47-words"] == 131072
    assert runner.escalation_jobs(top, 131072) == set()


def test_a_rung_the_rig_cannot_serve_is_refused_not_silently_cut(tmp_path, monkeypatch):
    from crucibleforge import providers
    cfg, entry, sent, loads = _harness(tmp_path, monkeypatch, needs=60000)
    monkeypatch.setattr(providers.Provider, "live_context", lambda self, mid: 32768)
    sent.clear()
    out = runner.escalate_models(cfg, [entry], CASES, rungs=[65536], room_rows=_room)
    assert out[65536]["m"]["failed"] and "needs ctx" in out[65536]["m"]["error"]
    assert sent == []
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["escalated"][-1]["failed"] is True


# ------------------------------------------------------ bench-all plan

def test_bench_all_plans_the_round_and_is_not_done_until_it_ran():
    cfg = {"providers": {"sf": {"type": "studioforge"}},
           "models": [{"name": "m", "model_id": "rig/m", "provider": "sf"}]}
    kw = dict(case_ids=["A"], judge_id="judge", pending_for=lambda l: 0,
              attempts_for=lambda l: {}, rows_for=lambda l: [_row("A", finish="stop")],
              rungs=[65536, 131072])
    p = bench_all.plan(cfg, {"rig/m"}, room_for=lambda l: [_row("A")], **kw)
    assert p["escalate"] == [{"model": "m", "jobs": 1}] and "m" in p["judge"]
    assert not bench_all.nothing_to_do(p)
    assert "> escalate to 65536/131072 tokens: m (1 job(s))" in bench_all.format_plan(p)
    p = bench_all.plan(cfg, {"rig/m"}, room_for=lambda l: [_row("A", finish="stop", rung=65536)],
                       **kw)
    assert p["escalate"] == [] and bench_all.nothing_to_do(p)
