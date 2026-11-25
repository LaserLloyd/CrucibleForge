"""Cross-lane hooks of the 2026-11-18 fix round (merge of lanes S, P and R): what one lane wrote and another lane's
code has to read.

a. the board prints lane S's marks: a re-drawn job ("n jobs re-run", run ids in report.json) and a unit a provider
   content filter stopped off the adult page ("pending (provider filter)", the model unranked on that part, no zero);
b. the report uses lane P's checks: a stale row (grader stamp missing or moved) or a hosted row without a recorded
   effort marks the model on the local board (never ranked) and leaves it off a public board with the reason named;
c. the ladder judge pass treats a placement that met a substitute rung as stale (unit_sha passed, as on the board);
d. a hosted judge without a price is refused before generation (preflight);
e. the run-settings figure is one median per section, the footnotes use the 4.5.0 section words, and an adult-only
   folder prints no "units still running" and no "$0.00" for the standard test it does not hold;
f. after _board_rows only one run per job reaches chains._newest_blocks.
Toy boards and fixtures only: no model, no rig."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from crucibleforge import chains, config, preflight, providers, report
from crucibleforge import rpladder as RL
from crucibleforge import rpladder_run as LR

from _lane_r_toy import PASS, board, row, toys  # noqa: F401


def _rewrite(model, fn):
    p = Path(config.results_dir()) / f"transcripts_{model}.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = fn(rows)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _cfg(models):
    import _lane_r_toy as T
    cfg = T._tc._cfg(T.prof45())
    cfg["models"] = [{"name": m, "model_id": m, "provider": "p"} for m in models]
    return cfg


# ------------------------------------------------------------------ a. re-run mark
def test_reruns45_counts_jobs_and_keeps_the_run_ids():
    rows = [{"case_id": "A", "repeat": 1, "rerun": {"run_ids": ["r1", "r2"], "kept": "r2"}},
            {"case_id": "A", "repeat": 1, "rerun": {"run_ids": ["r1", "r2"], "kept": "r2"}},
            {"case_id": "B", "repeat": 2, "rerun": {"run_ids": ["r0", "r3"], "kept": "r3"}},
            {"case_id": "C", "repeat": 1}]
    got = report.reruns45(rows)
    assert got == {"jobs": 2, "by_job": {"A#r1": {"run_ids": ["r1", "r2"], "kept": "r2"},
                                         "B#r2": {"run_ids": ["r0", "r3"], "kept": "r3"}}}
    assert report.reruns45([{"case_id": "C"}]) is None
    assert "2 jobs re-run" in report.notes_cell({"reruns45": got})
    assert "re-run" not in report.notes_cell({})


def test_a_complete_redraw_is_noted_on_the_board_with_run_ids_in_the_data(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, {"m": PASS})

    def redraw(rows):
        # one generated unit drawn again by a later complete run
        uid = next(r["chain"]["id"] for r in rows if (r.get("chain") or {}).get("passed") is not None)
        extra = [{**copy.deepcopy(r), "bench_run_id": "redraw01", "ts": "2099-01-01T00:00:00"}
                 for r in rows if (r.get("chain") or {}).get("id") == uid]
        return rows + extra
    _rewrite("m", redraw)
    _, stats, _ = report.board_stats("m", _cfg(["m"]))
    rr = stats["m"]["reruns45"]
    assert rr["jobs"] >= 1 and all(v["kept"] == "redraw01" and len(v["run_ids"]) == 2
                                   for v in rr["by_job"].values())
    assert f"{rr['jobs']} job{'s' if rr['jobs'] != 1 else ''} re-run" in report.notes_cell(stats["m"])
    # report.json carries the stats as they are: the run ids are there
    assert "redraw01" in json.dumps(stats["m"], default=str)


# ------------------------------------------------------------------ a. provider filter
@pytest.mark.live_ladder
def test_a_filter_stop_off_the_adult_page_reads_pending_provider_filter_and_unranks(monkeypatch):
    from test_rpladder_board_20261115 import PART, ROWS, _load, _stats
    from test_s5_chain_filter_pending_20261118 import _filtered, _uid
    p = _load("standard", monkeypatch)
    base = config.dedupe_rows(copy.deepcopy(ROWS["deepseek-flash"]))
    s = _stats(p, _filtered(base, _uid(base)))
    card = s["scorecard"]
    pend = [x for x in (card.get("pending_points") or {}).get("chat") or [] if x["part"] == PART.split("/")[1]]
    assert pend and pend[0]["why"] == report.FILTER_WHY, pend
    # never a zero: the part holds its points as pending
    assert (card.get("parts") or {}).get("chat", {}).get(PART.split("/")[1]) in (None, 0) or pend
    assert "pending (provider filter)" in report.pending_points_text(pend)
    assert "pending (provider filter)" in report.notes_cell(s)
    assert card.get("total") is None
    assert "pending (provider filter)" in report.unranked_why(card, [d for d in __import__("crucibleforge.tiers", fromlist=["x"]).scores(p)
                                                                      if d.get("in_total", True)])


def test_chain_pending_why_order():
    assert report._chain_pending_why({"u": {"attempts": 0}}) == "not run"
    assert report._chain_pending_why({"u": {"attempts": 1, "pending_filter": True, "pending_judge": True}}) \
        == report.FILTER_WHY
    assert report._chain_pending_why({"u": {"attempts": 1, "pending_judge": True}}) == "the judge"
    assert report._chain_pending_why({"u": {"attempts": 1}, "v": {"attempts": 0}}) == "the rest of its units"


# ------------------------------------------------------------------ b. stale / undisclosed rows
def test_a_row_made_before_grader_stamps_is_marked_locally_and_left_off_public(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, {"m": PASS, "n": PASS})
    _rewrite("n", lambda rows: [{k: v for k, v in r.items() if k != "grader_stamp"} for r in rows])
    cfg = _cfg(["m", "n"])
    labels, stats, _ = report.board_stats("m,n", cfg)
    assert stats["n"]["held45"] == [report.STALE_NO_STAMP]
    assert stats["n"]["provisional"] and not stats["m"].get("held45")
    assert report.STALE_NO_STAMP in report.notes_cell(stats["n"])
    md = report.generate("m,n", write=False, cfg=cfg)
    assert row(md, "n")[1] == "-" and row(md, "m")[1] == "1"
    assert report.STALE_NO_STAMP in "|".join(row(md, "n"))
    assert row(md, "n")[3] == f"not ranked: {report.STALE_NO_STAMP}"      # no number in Overall
    pub = report.generate("m,n", write=False, public=True, cfg=cfg)
    with pytest.raises(AssertionError):
        row(pub, "n")
    assert f"*n: left off this board ({report.STALE_NO_STAMP}).*" in pub
    assert row(pub, "m")[1] == "1"


def test_a_row_graded_by_older_code_reads_stale(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, {"m": PASS})
    _rewrite("m", lambda rows: [{**r, "grader_stamp": "000000000000"} if r.get("grader_stamp") else r for r in rows])
    _, stats, _ = report.board_stats("m", _cfg(["m"]))
    held = stats["m"]["held45"]
    assert len(held) == 1 and held[0].startswith("stale: ") and "graded by older grading code" in held[0]


def test_a_hosted_row_without_an_effort_is_held(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, {"m": PASS}, metas={"m": {"provider_type": "openai", "hosted": True}})
    _rewrite("m", lambda rows: [{k: v for k, v in r.items() if k != "effort"} for r in rows])
    _, stats, _ = report.board_stats("m", _cfg(["m"]))
    assert any("without a recorded reasoning effort" in x for x in stats["m"]["held45"])
    # the same rows on a rig model are fine (settings_gate checks those)
    board_ok = report.held45([{"case_id": "x", "grader_stamp": None}], {"provider_type": "studioforge"}, None)
    assert board_ok == [report.STALE_NO_STAMP]


def test_the_41_archive_profile_is_exempt(monkeypatch):
    monkeypatch.setattr(report, "active_name", lambda cfg=None: "bench")
    assert report.held45([{"case_id": "x"}], {"provider_type": "openai"}, None) == []


# ------------------------------------------------------------------ c. substitute placements in the ladder pass
def test_the_ladder_pass_places_again_a_unit_whose_placement_met_a_substitute(tmp_path, monkeypatch):
    from test_rpladder_20261115 import _all_usable, _cards, _unit
    from test_s3_ladder_frozen_rung_20261118 import _rec
    cards = _cards()
    me = _unit(cards, "C1", 25, model="rung-model-2", seed=1)
    other = _unit(cards, "C1", 25, model="rung-model-2", seed=2)
    good = _rec(cards, me, other)
    good["windows"] = [1, 3]
    sub = copy.deepcopy(good)
    for pr in sub["pairs"]:
        if pr["rung"] == 2:
            pr["anchor_sha"] = RL.anchor_sha(other["replies"])
    assert RL.stale_reason(sub, cards, None, "j", unit_sha=RL.anchor_sha(me["replies"])) == "substitute"
    chk = _all_usable(cards)
    seen = []

    class Locks:
        def __init__(self, *_):
            pass

        def acquire(self, labels):
            return list(labels), []

        def release(self):
            pass
    monkeypatch.setattr(RL, "load", lambda: cards)
    monkeypatch.setattr(RL, "load_check", lambda *a, **k: chk)
    monkeypatch.setattr(LR, "current_session_rows", lambda label, cfg: {me["unit"]: rec_row[0]})
    monkeypatch.setattr(LR, "_units", lambda rows, cards_: ([dict(me)], lambda u: None))
    monkeypatch.setattr(RL, "place_units", lambda units, *a, **k: seen.append([u["unit"] for u in units]) or {})
    monkeypatch.setattr("crucibleforge.shard.LabelLocks", Locks)
    monkeypatch.setattr(config, "results_dir", lambda: tmp_path)
    prof = {"judge": {"model_id": "j"}}
    for rec, placed_again in ((good, False), (sub, True)):
        rec_row = [{"judge_ladder": rec}]
        seen.clear()
        LR.run({"models": []}, prof, ["m"], windows=[1, 3])
        assert seen == [[me["unit"]] if placed_again else []], (rec is sub, seen)


# ------------------------------------------------------------------ d. unpriced hosted judge
def test_an_unpriced_hosted_judge_is_refused_before_generation(monkeypatch):
    cfg = {"providers": {"or": {"type": "openai", "base_url": "https://example.invalid/v1"}},
           "defaults": {}, "models": [], "judge": {}}
    providers._CACHE.clear() if hasattr(providers, "_CACHE") else None
    monkeypatch.setattr(providers, "ALLOW_UNPRICED", False)       # a run with --allow-unpriced sets it for the process
    why = preflight.judge_unrunnable_reason(cfg, {"provider": "or", "model_id": "vendor/judge-1"})
    assert why and "without a price" in why
    assert preflight.judge_unrunnable_reason(
        cfg, {"provider": "or", "model_id": "vendor/judge-1", "price": {"input": 1, "output": 2}}) is None
    monkeypatch.setattr(providers, "ALLOW_UNPRICED", True)
    assert preflight.judge_unrunnable_reason(cfg, {"provider": "or", "model_id": "vendor/judge-1"}) is None


# ------------------------------------------------------------------ e. run settings and words
def test_reasoning_tokens_are_one_median_per_section():
    prof = {"scores": {"work": {"label": "Coding", "parts": {"a": {"max": 5, "scoring": ["coding"], "cases": []}}},
                       "chat": {"label": "Chat", "parts": {"c": {"max": 5, "scoring": ["coding"], "cases": []}}}}}
    rows = ([{"chain": {"part": "work/a"}, "reasoning_tokens": n} for n in (3000, 5000, 7000)]
            + [{"chain": {"part": "chat/c"}, "reasoning_tokens": n} for n in (10, 20, 30, 40, 50, 60, 70)])
    by = report.reasoning_by_section45(rows, prof)
    assert by == {"Coding": 5000, "Chat": 40}
    # the old single median mixed them: 55 (a role-play turn's size) for a model that thinks 5k on code
    assert report.disclosure45(rows)["reasoning_tokens_median"] == 55
    cell = report.disclosure45_cell({"disclosure45": {"thinking": "on", "effort": "high", "reasoning_by_section": by},
                                     "scorecard": {}})
    assert "median reasoning tokens Coding 5.0k, Chat 40" in cell


def test_footnotes_use_the_45_section_words():
    p45 = {"chains": {"x": {}}, "release_seed": "4.5.0"}
    lean = report.class_footnote(p45, False)
    assert "Role-play and memory − board median Role-play and memory" in lean and "Chat" not in lean
    assert "Coding (small tasks)" in report.cost_footnote(p45) and "Code and Chat" not in report.cost_footnote(p45)
    assert "Lean = (Chat − board median Chat)" in report.class_footnote({"name": "bench"}, False)
    assert report.cost_footnote({"name": "bench"}) == report.COST_FOOTNOTE
    assert report._session_note([{"key": "chat", "label": "Role-play and memory"}]).startswith(
        " Role-play and memory: one generated session")


def test_an_adult_only_folder_prints_no_running_units_and_no_zero_cost(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": PASS},
                         keep=lambda m, r: str((r.get("chain") or {}).get("part") or "").startswith("nsfw/"),
                         metas={"m": {"price": {"input": 1.0, "output": 2.0}, "provider_type": "openai"}})
    line = "|".join(row(md, "m"))
    assert "units still running" not in line and "$0.00" not in line, line
    assert stats["m"].get("no_standard_rows45") is True
    assert report.cost_cell(stats["m"]) == "-"


# ------------------------------------------------------------------ f. one run per job reaches _newest_blocks
def _unit_rows(rid, ts, passed, uid="U", case="FLAT-x-001-r1-a1"):
    return [{"case_id": case, "repeat": 1, "turn": None, "bench_run_id": rid, "ts": ts,
             "chain": {"id": uid, "rung": 1, "attempt": 1, "passed": passed, "score": float(passed)}}]


def test_only_one_run_per_job_reaches_newest_blocks():
    old = _unit_rows("aaaa", "2026-11-14T10:00:00", True)
    new = _unit_rows("bbbb", "2026-11-18T10:00:00", False)
    aborted = [{"case_id": "FLAT-x-001-r1-a1", "repeat": 1, "turn": 1, "bench_run_id": "cccc",
                "ts": "2026-11-19T10:00:00", "chain": {"id": "U", "rung": 1, "attempt": 1}}]
    kept = report._board_rows(old + new + aborted)
    runs = {(r["case_id"], r["repeat"]): set() for r in kept}
    for r in kept:
        runs[(r["case_id"], r["repeat"])].add(r["bench_run_id"])
    assert all(len(v) == 1 for v in runs.values()), runs
    blocks = chains._newest_blocks(kept)
    assert blocks[("U", 1, 1)]["passed"] is False          # the newest COMPLETE run, not the aborted one
    # an older complete run written later in the file never wins over the newer one
    assert chains._newest_blocks(report._board_rows(new + old))[("U", 1, 1)]["passed"] is False


# ------------------------------------------------------------------ found by the lint pass: the reload path
def test_a_wrong_model_reply_reloads_once_and_retries(monkeypatch):
    """runner._Ctx._chat logged the WrongModelError through a name it never bound (``e``), so the reload path
    died with NameError instead of reloading and retrying once."""
    import threading
    from crucibleforge import runner
    from crucibleforge.api import WrongModelError

    calls = []

    class Prov:
        def chat(self, mid, messages, **kw):
            calls.append(mid)
            if len(calls) == 1:
                raise WrongModelError("server answered with another model")
            return "ok"

        def switch_model(self, mid, ctx=None):
            calls.append(("switch", mid))
    ctx = object.__new__(runner._Ctx)
    ctx.__dict__.update(stall_s=None, max_wall_s=None, max_output=None, cc_effort=None, placement=None,
                        provider=Prov(), model_id="m", ctx_len=4096, reload_lock=threading.Lock())
    monkeypatch.setattr(runner._Ctx, "_observe", lambda self, r: None)
    monkeypatch.setattr(runner, "check_abort", lambda: None)
    assert ctx._chat([{"role": "user", "content": "x"}]) == "ok"
    assert calls == ["m", ("switch", "m"), "m"]


def test_a_held_row_keeps_its_earlier_reason_first(tmp_path, monkeypatch):
    """An unpublishable run that is also stale reads both reasons, the earlier one first."""
    monkeypatch.setattr(report, "results_dir", lambda: tmp_path)
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: None)
    monkeypatch.setattr(report, "board_view", lambda label, cfg: ([{"ts": "t", "case_id": "c"}], {"old": False}))
    monkeypatch.setattr(report, "tier_model_stats", lambda *a, **k: {"coverage": {}})
    for name in ("_packs_pending_provisional", "_cards", "_headline", "assert_no_claude_code"):
        monkeypatch.setattr(report, name, lambda *a, **k: None)
    monkeypatch.setattr(report, "row_facts", lambda *a, **k: {})
    monkeypatch.setattr(report, "room_stats", lambda *a, **k: {})
    monkeypatch.setattr(report, "_hidden", lambda cfg: False)
    monkeypatch.setattr(report, "held45", lambda *a, **k: [report.STALE_NO_STAMP])
    (tmp_path / "meta_stopped.json").write_text(json.dumps({"model_label": "stopped", "publishable": False,
                                                            "early_stop": {"reason": "token-limit"}}),
                                                encoding="utf-8")
    _labels, stats, _ = report.board_stats("stopped", {})
    assert stats["stopped"]["provisional_reason"] == ("not publishable: early stop (token-limit); "
                                                      + report.STALE_NO_STAMP)
