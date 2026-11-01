"""Suite 4.0 "lite" (2026-10-31): Class / Lean columns, the overflow rule
(a row that ran out never counts on the board; Room has the retry), the
single retry pass at the next rung, the planner's measured timing and the
5-minute pack rule."""
from __future__ import annotations

import json

import pytest

from crucibleforge import estimate as est
from crucibleforge import headline as h
from crucibleforge import ladder, profiles, report
from tests import test_points_board_39 as pb


# ------------------------------------------------------------ Class / Lean

def test_class_bands_come_from_the_profile():
    prof = profiles.load_profile("bench", None)
    bands = prof["headline"]["classes"]
    assert bands == {"S": 16, "A": 13, "B": 10, "C": 0}
    assert h.capability_class(17.2, prof) == "S"
    assert h.capability_class(14.3, prof) == "A"
    assert h.capability_class(11.7, prof) == "B"
    assert h.capability_class(3.0, prof) == "C"
    assert h.capability_class(None, prof) is None
    assert h.capability_class(12.0, {"headline": {"classes": {"X": 12, "Y": 0}}}) == "X"


def test_lean_is_centred_on_the_board_median():
    prof = {"headline": {"lean_gap": 1.5}}
    # a weak coder with a judge-compressed chat score: raw gap 3 would say
    # chat-leaning; centred on the board it is balanced
    med = h.board_medians([(6.0, 5.0), (7.0, 8.0), (6.5, 6.0), (None, 3.0)])
    assert med == (6.5, 6.0)
    assert h.lean(6.0, 3.0 + 2.5, prof, med) == "balanced"
    assert h.lean(8.0, 4.0, prof, med) == "chat-leaning"      # (1.5) - (-2.0) = 3.5
    assert h.lean(5.0, 9.0, prof, med) == "code-leaning"
    assert h.lean(None, 9.0, prof, med) is None
    assert profiles.load_profile("bench", None)["headline"]["lean_gap"] == 1.5


def test_board_shows_class_lean_and_room(tmp_path, monkeypatch):
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    st = pb._board(env, {"a": pb._rows(), "b": pb._rows(fail_cases={"TZ06-three-parallel-one-turn"})})
    assert st["a"]["headline"]["class"] in {"S", "A", "B", "C"}
    assert st["a"]["headline"]["lean"] in {"balanced", "chat-leaning", "code-leaning", None}
    md = report.generate(cfg=env.cfg)
    assert "| Class | Lean |" in md and "Class = a coarse capability band from Overall" in md
    html = (env.results / "report.html").read_text(encoding="utf-8")
    data = json.loads(html.split('id="board-data">', 1)[1].split("</script>", 1)[0])
    assert [c["key"] for c in data["main"]][3:5] == ["class", "lean"]
    assert {"class", "lean"} <= set(data["rows"][0])
    rj = json.loads((env.results / "report.json").read_text(encoding="utf-8"))["models"]["a"]
    assert "class" in rj["headline"] and "lean" in rj["headline"]


# ------------------------------------------------------------ overflow rule

def test_a_ran_out_row_never_counts_even_after_its_retry(tmp_path, monkeypatch):
    """4.0: escalation_on_board false — the board keeps the failed run-out row,
    Room carries the retry."""
    env = pb._setup(tmp_path, monkeypatch, lambda p: (pb._work_only(p),
                                                    p.update(escalation_on_board=False)))
    base = pb._rows()
    cut = [r for r in base if not (r["case_id"] == "CZ05-optimal-bst-cost" and r["repeat"] == 1)]
    cut.append(pb._row("CZ05-optimal-bst-cost", "coding", 1, True, finish_reason="length"))
    rerun = pb._row("CZ05-optimal-bst-cost", "coding", 1, False, run="esc", budget_rung=65536,
                    ts="2026-10-28T02:00:00+00:00")
    st = pb._board(env, {"pending": cut, "escalated": cut + [rerun]})
    p_code = st["pending"]["scorecard"]["parts"]["work"]["code"]
    assert st["escalated"]["scorecard"]["parts"]["work"]["code"] == pytest.approx(p_code)
    assert p_code < 3.0
    md = report.generate(cfg=env.cfg)
    assert "| Room |" in md and "a row that ran out counts as failed on the board" in md


def test_the_live_profile_retries_once_at_the_next_rung_only():
    prof = profiles.load_profile("bench", None)
    assert prof["escalation_rungs"] == [65536] and prof["escalation_on_board"] is False


def test_bench_all_ladder_round_never_escalates_inline():
    import inspect
    from crucibleforge import bench_all
    src = inspect.getsource(bench_all._ladder_round)
    assert "escalate=False, no_escalate=True" in src
    src = inspect.getsource(bench_all.execute)
    assert 'rungs=str(p["rungs"][0])' in src


# ------------------------------------------------------------ estimates

def test_escalation_minutes_per_model():
    assert est.escalation_min([], 65536) == 0.0
    one = est.escalation_min([1], 65536)
    # one job: a reload + 65536 tokens at ESCALATION_TOK_S, no calibration factor beyond 1.2
    assert one == pytest.approx((est.RELOAD_S + 65536 / est.ESCALATION_TOK_S) * est.CALIBRATION / 60)
    assert est.escalation_min([4], 65536) == pytest.approx(one)          # 4 wide slots
    assert est.escalation_min([1, 1], 65536) == pytest.approx(2 * one)   # two reloads


def test_measured_speed_uses_the_models_own_rows(tmp_path, monkeypatch):
    from crucibleforge import config as cfgmod
    res = tmp_path / "results"
    res.mkdir()
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", res)
    rows = [{"bench_run_id": "r", "case_id": "A", "repeat": 1, "turn": t, "category": "rpdeep",
             "metrics": {"total_s": 60.0}} for t in (1, 2, 3)]
    rows += [{"bench_run_id": "r", "case_id": "B", "repeat": 1, "category": "nsfw",
              "metrics": {"total_s": 50.0}},
             {"bench_run_id": "r", "case_id": "P", "repeat": 1, "category": "perf",
              "metrics": {"total_s": 999.0}}]
    (res / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                                             encoding="utf-8")
    (res / "meta_m.json").write_text(json.dumps({"rounds": [{"ctx": 40960, "workers": 4}]}),
                                     encoding="utf-8")
    cases = [{"id": "A1-x", "category": "rpdeep", "turns": [1] * 3},
             {"id": "B", "category": "nsfw"}, {"id": "C", "category": "nsfw"}]
    for r in rows:
        if r["case_id"] == "A":
            r["case_id"] = "A1-x"
    (res / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                                             encoding="utf-8")
    m = est.measured_speed("m", cases, True)
    assert m["cases"] == {"A1-x": 180.0, "B": 50.0}              # turns summed, perf left out
    assert m["turns"]["A1-x"] == 3
    assert m["workers"] == {40960: 4}
    assert est.job_seconds(cases[0], True, m) == 180.0
    # 4.0: a session merged to fewer turns scales by turns; an unmeasured
    # session of a measured family (WR2 after WR1) takes the family's per-turn time
    assert est.job_seconds({**cases[0], "turns": [1] * 2}, True, m) == pytest.approx(120.0)
    assert est.job_seconds({"id": "A2-y", "category": "rpdeep", "turns": [1] * 5}, True, m) \
        == pytest.approx(300.0)
    # an unmeasured case: its category default x the model's factor for that category
    assert est.job_seconds(cases[2], True, m) == pytest.approx(
        est.RIG_SECS["nsfw"] * (50.0 / est.RIG_SECS["nsfw"]))


# ------------------------------------------------------------ pack time rule

def test_pack_verify_warns_over_the_five_minute_limit(tmp_path):
    prof = profiles.load_profile("bench", None)
    long = sorted(ladder.LADDER_DIR.glob("chat/character/set-*.json"))[0]
    _, notes = ladder.verify_pack(long, prof)
    assert any(n.startswith("WARNING: planner estimate") for n in notes)
    short = ladder.LADDER_DIR / "nsfw" / "nsfw_writing" / "set-1.json"
    _, notes = ladder.verify_pack(short, prof)
    assert any(n.startswith("time: planner estimate") for n in notes)
    assert est.PACK_LIMIT_MIN == 5.0


# ------------------------------------------------------------ code review fixes

def test_stored_repeats_above_the_profile_count_never_count(tmp_path, monkeypatch):
    """Review H1: 3.9 ran tooluse twice; with repeats 1 the repeat-2 rows
    leave the board, so every model is scored over the same repeats."""
    def edit(p):
        pb._work_only(p)
        p["repeats"]["tooluse"] = 1
    env = pb._setup(tmp_path, monkeypatch, edit)
    rows = pb._rows(fail={("TZ06-three-parallel-one-turn", 2), ("TZ11-clinic-ask-then-correct", 2)})
    st = pb._board(env, {"two": rows, "one": [r for r in rows if not (
        r["category"] == "tooluse" and r["repeat"] == 2)]})
    kept = report.board_filter("two", env.cfg)
    assert not [r for r in kept if r["category"] == "tooluse" and r["repeat"] == 2]
    assert st["two"]["scorecard"]["parts"]["work"]["agentic"] == pytest.approx(
        st["one"]["scorecard"]["parts"]["work"]["agentic"])
    assert st["two"]["scorecard"]["parts"]["work"]["agentic"] == pytest.approx(4.0)


def test_lean_only_for_complete_models(tmp_path, monkeypatch):
    """Review M1: a partial model gets no Lean."""
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    rows = pb._rows()
    st = pb._board(env, {"full": rows, "part": [r for r in rows if r["category"] != "math"]})
    assert st["part"]["headline"]["S"]["overall"] is None
    assert st["part"]["headline"]["lean"] is None and st["part"]["headline"]["class"] is None


def test_plan_lines_never_name_private_cases():
    """Review M2: the plan reaches the posted run report: private ids are counted."""
    from crucibleforge import bench_all
    p = {"rig_models": 1, "add": [], "skipped_rig": [], "candidates": ["m"], "not_on_rig": [],
         "prune": [], "groups": [{"models": ["m"], "cases": ["C1", "SECRET-1", "SECRET-2"]}],
         "judge": [], "case_count": 3, "escalate": [], "rungs": [], "ladder":
         [{"model": "m", "sets": ["secretcat/main#1"], "jobs": 3}],
         "per_model": {"m": {"regenerate": ["C1", "SECRET-1", "SECRET-2"], "rejudge": ["SECRET-1"]}},
         "hosted_pending": [], "_private": {"ids": {"SECRET-1", "SECRET-2"},
                                            "terms": ["SECRET-1", "SECRET-2", "secretcat"]}}
    text = bench_all.format_plan(p)
    assert "SECRET" not in text and "secretcat" not in text
    assert "regenerate: [C1, private (2)]" in text and "re-judge: [private (1)]" in text


def test_hosted_partial_models_keep_the_rebuild_open():
    """Review H2: a boarded hosted model short of cases is reported, the
    nightly's done-check line is printed, nothing is generated for it."""
    from tests.test_bench_all import CASES, JUDGE, _cfg, _done, _m, _plan
    from crucibleforge import bench_all
    cfg = _cfg([_m("a", "org/a"), _m("api", "x", prov="ds")])
    p = _plan(cfg, ["org/a", JUDGE], rows={"a": _done(*CASES), "api": _done("C1")},
              board=["a", "api"])
    assert p["hosted_pending"] == [{"model": "api", "cases": 2}]
    assert not p["groups"]
    text = bench_all.format_plan(p)
    assert "  ! hosted rows pending: api (2 case(s))" in text
    assert "(hosted rows pending)" in text


def test_a_model_longer_than_the_window_starts_when_first():
    """Review M4: never skipped forever — first in the queue, it runs to the cut."""
    from datetime import datetime, timedelta
    from crucibleforge import bench_all
    start = datetime(2026, 11, 1, 1, 0)
    win = bench_all.Window(start + timedelta(hours=2), 45.0)
    p = {"groups": [{"models": ["slow"], "cases": ["A"]}, {"models": ["slow2"], "cases": ["A"]}],
         "judge": [], "rungs": []}
    lines = bench_all.schedule_lines(p, {}, win, start, estimate_for=lambda g: (300.0, 1.0),
                                     pending_for=lambda labels: 0.0)
    text = "\n".join(lines)
    assert "generate slow (1 cases): ~300 min is longer than the window — starts first" in text
    assert "left for the next run (1): slow2" in text


def test_jobs_start_longest_expected_first():
    """Review M5 (gemma test: the two writing sessions started 5 min late):
    with a per-case estimate the longest SEQUENTIAL job is scheduled first."""
    from crucibleforge import runner
    cases = [{"id": "B1", "category": "codebank"}, {"id": "WR1", "category": "rpdeep", "turns": [1] * 6},
             {"id": "CL3", "category": "coding"}, {"id": "WR2", "category": "rpdeep", "turns": [1] * 6}]
    jobs = [(c, 1, None) for c in cases]
    secs = {"B1": 500, "WR1": 1450, "CL3": 150, "WR2": 1515}
    order = [j[0]["id"] for j in runner.schedule_jobs(jobs, lambda c: secs[c["id"]])]
    assert order == ["WR2", "WR1", "B1", "CL3"]
    # without an estimate: the 3.9 category order (bank first, then sessions)
    assert [j[0]["id"] for j in runner.schedule_jobs(jobs)][:1] == ["B1"]


def test_a_partial_part_never_unlocks_its_pack(tmp_path, monkeypatch):
    """Fresh-clone smoke (2026-10-31): two of six function cases at 100 % must
    not open the Functions pack — the part is partial until all six exist."""
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    pack = ["CZ08-triple-substring", "CZ09-range-kth", "CZ11-lex-triple-substring"]
    pb._set_file(env, "work/code", 1, pack)
    rows = [r for r in pb._rows() if r["case_id"] != "CZ02-segment-crossings"]   # a code case missing
    st = pb._board(env, {"p": rows})
    det = st["p"]["scorecard"]["ladder"]["work"]["code"]
    assert det["unlock_share"] is None and det["sets"][0]["state"] == "locked"
    prog = report.ladder_progress("p", env.cfg)
    assert prog["work/code"]["std"] is None
    assert ladder.due(prog["work/code"]["std"], {}, ladder.discover(
        profiles.load_profile("bench", env.cfg))["work/code"], 0.9) == []


def test_an_nsfw_only_canary_failure_is_its_own_error(monkeypatch):
    """Fresh-clone smoke: a hosted judge that softens explicit text failed the
    canary and the whole judge phase aborted — Chat went unjudged too. An
    NSFW-only failure is now CanaryNsfwOnly (run_judge then judges the rest)."""
    from types import SimpleNamespace
    from crucibleforge import judge
    monkeypatch.setattr(judge, "judge_row", lambda jc, row, **kw: {"scores": {}})
    jc = SimpleNamespace(label="j", concurrency=4, cfg=None)
    monkeypatch.setattr(judge, "_canary_problem",
                        lambda k, out: "explicitness=2" if k == "explicit" else None)
    with pytest.raises(judge.CanaryNsfwOnly):
        judge.run_canary(jc, long_probe=False)
    monkeypatch.setattr(judge, "_canary_problem",
                        lambda k, out: "too lenient" if k in ("explicit", "bad") else None)
    with pytest.raises(judge.JudgeError) as e:
        judge.run_canary(jc, long_probe=False)
    assert not isinstance(e.value, judge.CanaryNsfwOnly)
    assert judge._nsfw_row({"category": "nsfw"}) and not judge._nsfw_row({"category": "rpdeep"})


def test_an_unjudged_nsfw_row_does_not_blank_other_categories(tmp_path, monkeypatch):
    """Fresh-clone smoke: one NSFW row held back by an NSFW-refusing judge
    blanked Chat too; pending is tracked per category now."""
    env = pb._setup(tmp_path, monkeypatch, pb._work_only)
    held = pb._row("WL2-lighthouse", "nsfw", 1, False, needs_judge=True, rubric="nsfw_craft",
                   response="text", grade=None)
    st = pb._board(env, {"n": pb._rows() + [held]})
    assert st["n"]["pending_categories"] == ["nsfw"]
    assert st["n"]["scorecard"]["work"] is not None
    assert st["n"]["scorecard"]["nsfw"] is None


def test_an_empty_stop_reply_is_retried_once():
    """Validation item 10: an empty finish=stop reply is sent once more; an
    overflow (finish=length) is not retried here."""
    from types import SimpleNamespace
    from crucibleforge import runner
    from crucibleforge.api import ChatResult
    replies = [ChatResult(response_text="", finish_reason="stop"),
               ChatResult(response_text="answer", finish_reason="stop")]
    ctx = SimpleNamespace(extra_body=None, recovery_enabled=False, budget=lambda m: m,
                          model_id="m", _chat=lambda msgs, **kw: replies.pop(0),
                          _overflowed=lambda r: False, _stopped_without_answer=lambda r: False)
    r = runner._Ctx.call(ctx, [{"role": "user", "content": "x"}], max_tokens=10)
    assert r.response_text == "answer" and getattr(r, "empty_retry", False)
    cut = ChatResult(response_text="", finish_reason="length")
    assert not runner._empty_stop(cut)
    assert runner._empty_stop(ChatResult(response_text="", reasoning_text="hm", finish_reason="stop"))
    assert not runner._empty_stop(ChatResult(response_text="", finish_reason="stop",
                                             tool_calls=[{"name": "f"}]))


def test_wr1_regex_fixes():
    """Validation item 7: F4 takes grandad and periphrases, Z5 is not broken by
    "Mr.", R4 does not flag the NEW relation."""
    import re
    from crucibleforge import config
    case = next(c for c in config.load_cases(["rpdeep"]) if c["id"] == "WR1-ninth-string")
    assert len(case["turns"]) == 3
    pat = {c["id"]: re.compile(c["pattern"], re.I | re.M) for c in case["checks"] if c.get("pattern")}
    f4 = pat["F4-granddaughter-both-sections"]
    for s in ("her grandad hums", "his son's girl comes in", "Gran is late", "the granddaughter"):
        assert f4.search(s), s
    z5 = pat["Z5-last-paragraph-20-words"]
    ok = ("A\n\nMr. Hale lifts the lid and one loose string hums low under the dust of "
          "the winter the canal froze.")
    assert len(ok.split("\n\n")[1].split()) == 20 and z5.search(ok)
    assert not z5.search("A\n\nMr. Hale lifts the lid.")
    r4 = pat["R4-old-name-gone"]
    assert not r4.search("Margit, the child of his daughter, waits.")
    assert r4.search("his daughter comes in") and r4.search("Ines waves")


def test_cl_cases_carry_their_own_budget_and_never_escalate():
    """4.0 lite (validation 2026-10-31: 11/18 CL rows hit 24,576 before any
    code): every CL case is sent 65,536 in one pass, the load widens to fit,
    and a CL row that still runs out is not re-run at the 65,536 rung."""
    import json
    from pathlib import Path
    from crucibleforge import runner
    cases = json.loads((Path(__file__).resolve().parents[1] / "cases" / "coding.json").read_text(encoding="utf-8"))
    cl = [c for c in cases if c["id"].startswith("CL")]
    assert cl and all(c.get("budget") == 65536 for c in cl)
    cfg = {"defaults": {"bank_budget": 65536}}
    case = dict(cl[0], category="coding")
    assert runner.fixed_budget(cfg, case) == 65536
    assert runner.wide_context(cfg, [case]) == 65536 + runner.ESCALATION_CTX_PAD

    class Ctx:
        ctx_len = 73728
        rung = None
    assert runner.row_budget(Ctx(), case, 500) == min(65536, 73728 - 500 - runner.BANK_WINDOW_SLACK)
    Ctx.rung = 65536
    assert runner.row_budget(Ctx(), case, 500) is None


def test_board_footnote_states_the_chat_and_nsfw_rp_noise():
    from crucibleforge import tiers
    prof = profiles.load_profile("bench", None)
    defs = tiers.scores(prof)
    txt = report.points_footnote(defs, prof)
    assert "one generated session per case (±~0.1 per case between runs)" in txt
    assert "items every model on the 2026-10-31 board passed weigh half" in txt
    assert "3 sessions, ± is a 3-case bootstrap, read as rough" in txt
