"""Long-scene role-play rows judged under the shipping bench45 profile (element-only
mode) must store the OOC rescue inputs (refused / ooc_quote / in_character_votes)
in every fact group of the verdict. The element-only policy used to return before
ooc_evidence was set, so the keys never reached group_meta."""
from __future__ import annotations

from crucibleforge import judge
from tests.test_fixa_judge_20261109 import (OOC_REPLY, _CountJC, _all_no, _deep_row, _reply_ooc)

KEYS = ("refused", "ooc_quote", "in_character_votes")
BENCH45_SHIP = judge.judge_policy_config(None, "bench45")      # mode: elements, as shipped
ELS = [{"id": "fa", "q": "q", "turns": [1]}, {"id": "fb", "q": "q", "turns": [2]}]


def _pol():
    return judge.judge_policy({"case_id": "RD1-marisol", "rubric": "rp_deep", "judge_elements": ELS},
                              BENCH45_SHIP)


def test_element_mode_policy_carries_ooc_evidence_for_rp_deep():
    pol = _pol()
    assert pol["mode"] == "elements" and pol["ooc_evidence"] is True


def test_element_mode_policy_leaves_other_rubrics_alone():
    pol = judge.judge_policy({"case_id": "S1", "rubric": "steer",
                              "judge_elements": [{"id": "e", "q": "q", "dim": "obeyed"}]}, BENCH45_SHIP)
    assert "ooc_evidence" not in pol


def test_refused_ooc_group_stores_the_rescue_keys():
    jc = _CountJC(_reply_ooc)
    out = judge.judge_deep_row(jc, _deep_row(_pol(), ELS, turns=3), samples=1)
    assert out["groups"], "no fact group judged"
    for g in out["groups"]:
        assert all(k in g for k in KEYS)
        assert g["refused"] is True and g["ooc_quote"] == OOC_REPLY["ooc_quote"]
        assert g["in_character_votes"] and all(v is False for v in g["in_character_votes"])
    assert "ooc_quote" in jc.kw[0]["response_format"]["json_schema"]["schema"]["properties"]


def test_in_character_group_stores_the_keys_with_empty_evidence():
    out = judge.judge_deep_row(_CountJC(_all_no), _deep_row(_pol(), ELS, turns=3), samples=1)
    for g in out["groups"]:
        assert all(k in g for k in KEYS)
        assert g["refused"] is False and g["in_character_votes"] == [True]
        assert not g["ooc_quote"]


def test_41_profile_still_stores_nothing_new():
    pol = judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": ELS},
                             judge.judge_policy_config(None, "bench"))
    out = judge.judge_deep_row(_CountJC(_reply_ooc), _deep_row(pol, ELS, turns=3), samples=1)
    assert not set(KEYS) & set(out["groups"][0])
