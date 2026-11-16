"""4.5-only speaker-tag tolerance in the shared engine (2026-11-14, lead; Admin T's review).

A speaker tag written after a leading quote or bullet ('Nell:, - Nell:, "Nell:) is a tag in 4.5:
``session_checks.speaker_lines`` and the voice-share check read it when the profile sets
``speaker_tag_lead: true`` (bench45). Without the key (every 4.1 profile) the patterns and the
grades are byte-identical to before.
"""
from __future__ import annotations

import pytest

from crucibleforge import profiles, session_checks as sc

SCENE = "'Nell: The ferry is late again.\n- Odo: Then we wait.\n**Nell:** Bring the lamp."


@pytest.fixture(autouse=True)
def _reset():
    sc.set_tag_lead(False)
    sc.clear_memo()
    yield
    sc.set_tag_lead(False)
    sc.clear_memo()


def test_off_is_the_4_1_reading():
    assert sc.speaker_lines(SCENE, "Nell") == "Bring the lamp."
    assert sc.speaker_lines(SCENE, "Odo") == ""


def test_on_reads_the_quoted_and_bulleted_tags():
    sc.set_tag_lead(True)
    assert sc.speaker_lines(SCENE, "Nell") == "The ferry is late again.\nBring the lamp."
    assert sc.speaker_lines(SCENE, "Odo") == "Then we wait."
    assert sc.speaker_lines("Nell said: no", "Nell") == ""               # still a line-start tag


def _case():
    return {"checks": [{"id": "nell-ferry", "type": "require_regex", "pattern": r"\bferry\b", "turns": [1],
                        "scope": "speaker:Nell", "weight": 1.0}]}


def test_a_4_1_row_regrades_identically_and_a_45_row_is_caught():
    off = sc.run_checks(_case(), [SCENE])
    assert off["results"][0]["pass"] is False                  # 4.1: the quoted tag is not Nell's line
    sc.set_tag_lead(True)
    on = sc.run_checks(_case(), [SCENE])                       # the memo keys on the switch
    assert on["results"][0]["pass"] is True
    sc.set_tag_lead(False)
    assert sc.run_checks(_case(), [SCENE]) == off


def test_the_voice_share_report_counts_the_quoted_tag_only_under_4_5():
    chk = {"id": "vs", "type": "speaker_spread", "speakers": ["Nell", "Odo"]}
    off = sc.run_checks({"checks": [chk]}, [SCENE])["results"][0]["value"]["words"]
    sc.set_tag_lead(True)
    on = sc.run_checks({"checks": [chk]}, [SCENE])["results"][0]["value"]["words"]
    assert off == {"Nell": 3, "Odo": 0}                      # 4.1: only the bold tag
    assert on == {"Nell": 8, "Odo": 3}


def test_the_profile_switch_sets_and_clears_it():
    base = {"name": "t-lead", "cases": {}}
    profiles.apply_profile({**base, "speaker_tag_lead": True}, {"defaults": {}})
    assert sc.TAG_LEAD is True
    profiles.apply_profile(base, {"defaults": {}})                # a 4.1 profile (no key) clears it
    assert sc.TAG_LEAD is False


def test_one_lead_form_across_the_engine_and_the_generators():
    """The lead before a tag is one shape everywhere: the engine's 4.5 switch, session_state's tag and (once
    seg-h1 lands) ensemble's opt-in ``lead=``; a drift would make one check tolerant and its sibling strict."""
    from crucibleforge.gen import ensemble, session_state
    assert sc.TAG_LEAD_RX == session_state._TAG_LEAD
    ens = getattr(ensemble, "TAG_LEAD", None)
    assert ens is None or ens == sc.TAG_LEAD_RX
