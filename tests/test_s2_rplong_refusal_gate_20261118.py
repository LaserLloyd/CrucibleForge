"""S2 (fix round 2026-11-18, AUDIT-ITEMS FIX-NOW 2): long scenes zeroed a refusal only when the judge flagged it. An
all-refusal transcript under a verdict that is not ``refused`` kept 0.13-0.36 of four cases (the regex items pass on a
refusal for free). ``rpdeep.score_row`` now runs ``graders.refusal_heuristic`` itself: a refusal on a majority of the
graded turns zeroes the row on the bench45 rules; a clean in-character transcript keeps its regex credit."""
from __future__ import annotations

import pytest

from crucibleforge import config, profiles, rpdeep

pytestmark = pytest.mark.live_ladder

IDS = ["RD1-marisol", "RD4-kettle-saddle", "CC3-tea-hut", "CC4-second-pitch", "RV1-three-at-the-table",
       "RV2-skarren-hut", "WR1-ninth-string", "WR2-red-thread-key"]
REF = "I'm sorry, but I can't continue this role-play. I'm not able to write that content."
CLEAN = '"Evening," she says, setting the kettle down. She glances at the door and waits.'


@pytest.fixture(scope="module")
def cases():
    prof = profiles.load_profile("bench45")
    profiles.activate_release_seed(prof)
    profiles.activate_cases_root(prof)
    profiles.apply_profile(prof, {})
    profiles.set_active("bench45")
    allc = {c["id"]: c for c in config.load_cases()}
    return {cid: allc[cid] for cid in IDS}


def _row(c, rep, judge=None):
    conv = [{"role": "system", "content": c.get("system", "")}]
    turns = []
    for k, t in enumerate(c.get("turns") or [], 1):
        u = t if isinstance(t, str) else t.get("user", "")
        conv += [{"role": "user", "content": u}, {"role": "assistant", "content": rep}]
        turns.append({"turn": k, "response": rep, "finish_reason": "stop"})
    row = {"case_id": c["id"], "profile": "bench45", "conversation": conv, "response": rep}
    if judge is not None:
        row["judge"] = judge
    return row, turns


NOT_REFUSED = {"elements": {"results": []}, "scores": {}}


@pytest.mark.parametrize("cid", IDS)
def test_an_all_refusal_transcript_scores_zero_when_the_judge_does_not_flag_it(cases, cid):
    c = cases[cid]
    for j in (NOT_REFUSED, None):
        row, turns = _row(c, REF, j)
        s = rpdeep.score_row(row, c, turns)
        assert s["status"] == "ok" and s["refused"] is True and s["rate"] in (0.0, None), (cid, s.get("rate"))
        assert not any(i["pass"] or i.get("credit") for i in s["items"])


@pytest.mark.parametrize("cid", IDS)
def test_a_clean_in_character_transcript_is_not_refused(cases, cid):
    c = cases[cid]
    row, turns = _row(c, CLEAN, NOT_REFUSED)
    s = rpdeep.score_row(row, c, turns)
    assert not s.get("refused")


def test_one_refusal_shaped_line_does_not_decide_the_row():
    case = {"turns": ["a", "b", "c", "d"]}
    assert not rpdeep.refused_by_code([REF, CLEAN, CLEAN, CLEAN], case)
    assert not rpdeep.refused_by_code([REF, REF, CLEAN, CLEAN], case)       # half is not a majority
    assert rpdeep.refused_by_code([REF, REF, REF, CLEAN], case)
    # an OOC probe turn is not graded for this
    assert rpdeep.refused_by_code([REF, REF, CLEAN, CLEAN], {**case, "ooc_turns": [3]})


def test_the_41_board_keeps_its_rule(cases, monkeypatch):
    c = cases["CC3-tea-hut"]
    row, turns = _row(c, REF, NOT_REFUSED)
    row["profile"] = "bench"
    monkeypatch.setattr(profiles, "active_name", lambda *a, **k: "bench")     # the frozen 4.1 board
    s = rpdeep.score_row(row, {**c, "v2": False}, turns)
    assert not s.get("refused")
