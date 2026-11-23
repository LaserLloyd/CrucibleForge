"""Lead 2026-11-19 (b): headline honesty (decision 4) and sessions judged by another judge (D13, decision 3)."""
import pytest

from crucibleforge import chains
from crucibleforge import report as R

DEFS = [{"key": "work", "group": "coding", "in_total": True,
         "parts": [{"key": "c1", "weight": 10.0, "short": "Code"}]},
        {"key": "chat", "group": "chat", "in_total": True,
         "parts": [{"key": "h2", "weight": 6.0, "short": "State"}, {"key": "rps", "weight": 4.0, "short": "RPS"}]}]
PROF = {"pending_scaling": "none", "judge": {"model_id": "board-judge"}}


def _stats(pending_rps=None, other=None, total=12.0, chat_of=None):
    pend = {"chat": [{"part": "rps", "points": 4.0, "why": pending_rps}]} if pending_rps else {}
    card = {"score_defs": DEFS, "pending_points": pend, "total": total,
            "measured_max": {"work": 10.0, "chat": chat_of if chat_of is not None else (6.0 if pending_rps else 10.0)}}
    if other:
        card["other_judge"] = {"chat/rps": other}
    return {"scorecard": card, "era": {"old": False}, "coverage": {}}


def test_a_row_missing_a_part_others_count_is_x_of_y_and_unranked():
    st = {"a": _stats(), "b": _stats(R.AGREEMENT_NOT_MEASURED_MODEL, total=9.0)}
    R.rank_parts45(["a", "b"], st, PROF)
    a, b = st["a"]["scorecard"], st["b"]["scorecard"]
    assert a["total"] == 12.0 and "overall_of" not in a                  # full maximum: plain
    assert b["total"] is None
    assert b["unranked_why"] == "not ranked: RPS judge agreement not measured for this model"
    assert b["overall_measured"] == {"sections": ["work", "chat"], "points": 9.0, "of": 16.0}


def test_when_no_row_counts_a_part_every_row_is_ranked_at_x_of_y():
    st = {l: _stats(R.AGREEMENT_FAILED, total=t) for l, t in (("a", 11.0), ("b", 9.0))}
    R.rank_parts45(["a", "b"], st, PROF)
    assert [st[l]["scorecard"]["total"] for l in "ab"] == [11.0, 9.0]
    assert st["a"]["scorecard"]["overall_of"] == 16.0
    hl = {"S": {"overall": 11.0, "chat": 4.0, "work": 7.0}, "m": {"overall": 0.8}, "overall_of": 16.0,
          "unscaled": {"chat": {"of": 6.0}}}
    assert R.headline_cell(hl, "overall") == "**11**.0 of 16 ± 0.8"
    assert R.section_cell45(hl, DEFS[0]) == "**7**.0 of 10"
    assert R.section_cell45(hl, DEFS[1]) == "**4**.0 of 6"


def test_a_row_judged_by_another_judge_is_never_ranked():
    st = {"a": _stats(), "b": _stats(other=["other-judge"])}
    R.rank_parts45(["a", "b"], st, PROF)
    assert st["a"]["scorecard"]["total"] == 12.0
    assert st["b"]["scorecard"]["total"] is None
    assert st["b"]["scorecard"]["unranked_why"] == \
        "not ranked: RPS judged by other-judge, not the board's judge: not comparable"


def test_the_points_cell_names_the_other_judge():
    hl = {"S": {"chat": 7.0}, "pts": {"chat/h2": 4.0, "chat/rps": 3.0}, "other_judge": {"chat/rps": ["other-judge"]}}
    d = {"key": "chat", "parts": [{"key": "h2", "weight": 6.0, "short": "State", "label": "State"},
                                  {"key": "rps", "weight": 4.0, "short": "RPS", "label": "RPS"}]}
    assert "RPS 3.0 (judged by other-judge, not the board's judge: not comparable)" in R.points_cell(hl, d)


def test_the_public_gate_refuses_placements_by_another_judge(monkeypatch):
    monkeypatch.setattr(R, "_tier_profile", lambda cfg: PROF)
    monkeypatch.setattr(R, "active_name", lambda cfg=None: "bench45")
    row = {"judge_ladder": {"complete": True, "instrument": {"judge_model": "other-judge"}}}
    held = R.held45([row], {}, {})
    assert any("1 session placement judged by other-judge, not the board's judge: not comparable" in h for h in held)
    mine = {"judge_ladder": {"complete": True, "instrument": {"judge_model": "board-judge"}}}
    assert not any("judged by" in h for h in R.held45([mine], {}, {}))


def test_the_board_reads_another_judges_placement_marked_and_prefers_its_own(monkeypatch):
    from crucibleforge import rpladder as RL
    monkeypatch.setattr(RL, "load", lambda: {})
    monkeypatch.setattr(RL, "stale_reason", lambda rec, cards, chk, jm=None, unit_sha=None: (
        "judge" if jm is not None and rec["instrument"]["judge_model"] != jm else None))
    ch = {"id": "U", "rung": 1, "attempt": 1, "passed": True}
    other = {"chain": ch, "ts": "2026-11-19T02", "judge_ladder": {"complete": True, "position": 2,
                                                                   "instrument": {"judge_model": "other-judge"}}}
    got = chains._newest_ladder([other], PROF)
    assert got[("U", 1, 1)]["other_judge"] == "other-judge"
    own = {"chain": ch, "ts": "2026-11-19T01", "judge_ladder": {"complete": True, "position": 3,
                                                                 "instrument": {"judge_model": "board-judge"}}}
    got = chains._newest_ladder([own, other], PROF)          # older, but the board's own judge: it wins
    assert got[("U", 1, 1)]["position"] == 3 and "other_judge" not in got[("U", 1, 1)]


def test_the_ladder_pass_accepts_another_judge_and_keys_it_under_its_own_id(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from crucibleforge import config
    from crucibleforge import rpladder as RL
    from crucibleforge import rpladder_run as LR
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(RL, "load", lambda: {"card": {}})
    monkeypatch.setattr(RL, "flat", lambda cards: ["x"])
    seen = []

    def load_check(res, cards, jm, *a, **k):
        seen.append(jm)
        return {"checked": [], "cells": {}}
    monkeypatch.setattr(RL, "load_check", load_check)

    def rung_check(cards, judge, cache, judge_model):
        judge([{"key": "k"}])
        return {}
    monkeypatch.setattr(RL, "rung_check", rung_check)
    monkeypatch.setattr(RL, "save_check", lambda *a, **k: None)
    monkeypatch.setattr("crucibleforge.judge._load_judge",
                        lambda *a, **k: SimpleNamespace(model_id="other-judge", deadline=None))
    monkeypatch.setattr(LR, "make_judge_fn", lambda jc, stats: (lambda reqs: [{} for _ in reqs]))
    out = LR.run({"models": []}, {"judge": {"model_id": "board-judge"}}, [], windows=[1], rung_check_only=True)
    assert out["other_judge"] == "other-judge"
    assert seen[0] == "board-judge" and set(seen[1:]) == {"other-judge"}   # the retry keys it under the judge that ran


@pytest.mark.shipping_default
def test_the_docs_say_what_another_judge_does():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for f in ("README.md", "docs/RUNNING.md"):
        t = " ".join((root / f).read_text(encoding="utf-8").split())
        assert "not the board's judge: not comparable" in t, f
        assert "never ranked" in t and "refuse" in t, f


def test_a_section_with_packs_also_says_of_what_when_the_board_is_short(monkeypatch):
    monkeypatch.setattr(R, "_std_pack45", lambda hl, d: (8.2, 10.0, 0.0, 0.0))
    hl = {"S": {"work": 9.1}, "m": {"work": 0.8}, "overall_of": 14.0}
    assert R.section_cell45(hl, DEFS[0]).startswith("**8**.2 of 10")
    assert R.section_cell45({**hl, "overall_of": None}, DEFS[0]) == "**8**.2 ± 0.8"
