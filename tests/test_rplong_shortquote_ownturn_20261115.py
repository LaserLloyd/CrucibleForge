"""Scrub item 26 (EXP-20261115-39 FIXNOW, 2026-11-15): bench45's own-turn quote check (``quote_in_own_turns``)
must accept the short whole-word quote that the stored-quote rule (``_stored_quote_ok``) just accepted on the same
turn. Real row: grok's pilot RV2 row, element N2-kit-holds-first-pressure, judge quote "No Warden." = the whole
spoken line "**Kit:** No Warden." of turn 2 (YES at the tag, NO on rc45 cd954a8)."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from crucibleforge import profiles, report, rpdeep

ROW = json.loads(gzip.open(Path(__file__).parent / "fixtures" / "rv2_grok_row_20261115.json.gz").read())
CID, EID = "RV2-skarren-hut", "N2-kit-holds-first-pressure"


@pytest.fixture
def b45(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "rc4-main")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    return p


def _replies(row):
    return [m.get("content") or "" for m in row["conversation"] if m.get("role") == "assistant"]


def test_stored_row_is_what_the_scrub_describes():
    r = ROW
    assert r["case_id"] == CID and r["profile"] == "bench45"
    (e,) = [x for x in r["judge"]["elements"]["results"] if x["id"] == EID]
    assert e["judge_pass"] is True and e["pass"] is False and e["quote"] == "No Warden."
    assert "**Kit:** No Warden." in _replies(r)[1].splitlines()


def test_grok_rv2_n2_short_whole_line_quote_passes(b45):
    case = report._cases_by_id()[CID]
    items = {i["id"]: i for i in rpdeep.score_row(copy.deepcopy(ROW), case, [])["items"]}
    assert items[EID]["pass"] is True, items[EID]
    assert items[EID]["detail"] == "judge: yes; short quote verified verbatim"


def test_own_turn_check_still_fails_a_short_quote_from_another_turn():
    e = {"id": "x", "turns": [1]}
    r = {"id": "x", "pass": True, "credit": 1.0, "quote": "No Warden."}
    assert rpdeep.quote_in_own_turns(r, e, ["Kit looks away.", "**Kit:** No Warden."])["pass"] is False
    assert rpdeep.quote_in_own_turns(r, {**e, "turns": [2]}, ["Kit looks away.", "**Kit:** No Warden."])["pass"]


def test_short_rule_is_whole_words_and_min_length():
    assert rpdeep._short_verbatim("No Warden.", "**Kit:** No Warden.")
    assert not rpdeep._short_verbatim("No Ward", "**Kit:** No Warden.")            # not a whole word
    assert not rpdeep._short_verbatim("No.", "No.")                                 # under SHORT_QUOTE_MIN
    r = {"id": "x", "pass": True, "credit": 1.0, "quote": "No Ward"}
    assert rpdeep.quote_in_own_turns(r, {"id": "x", "turns": [1]}, ["**Kit:** No Warden."])["pass"] is False
