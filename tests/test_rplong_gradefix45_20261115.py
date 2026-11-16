"""Long scenes graded the 4.5 way (scrub audit 2026-11-15, FIXNOW items 10-12, RD4 H2, and RV2 N1 from the reader
check EXP-20261115-39 LONGSCENE-REPORT): bench45's ``rpdeep_overrides`` + ``quote_own_turns``. The eight cases are
shared with the frozen 4.1 board, so every change is a bench45 key: the ``bench`` profile reads the case file as it
is and keeps its verdicts. Real rows: DeepSeek flash's pilot rows (tests/fixtures/rc4_roleplay_rows_20261115.json.gz)."""
from __future__ import annotations

import copy
import gzip
import json
import re
from pathlib import Path

import pytest

from crucibleforge import profiles, report, rpdeep

ROWS = json.loads(gzip.open(Path(__file__).parent / "fixtures" / "rc4_roleplay_rows_20261115.json.gz").read())


@pytest.fixture
def b45(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "rc4-main")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    return p


def _row(case_id):
    (r,) = [r for r in ROWS["deepseek-flash"] if r.get("needs_judge") and r["case_id"] == case_id]
    return copy.deepcopy(r)


def _items(row, case_id):
    case = report._cases_by_id()[case_id]
    return {i["id"]: i for i in rpdeep.score_row(row, case, [])["items"]}


def _stored(row, eid):
    return next(e for e in row["judge"]["elements"]["results"] if e["id"] == eid)


def test_the_switches_are_bench45_only(b45):
    assert profiles.profile_switch("bench45", "quote_own_turns") is True
    assert profiles.profile_switch("bench45", "rpdeep_overrides")
    for name in ("bench",):
        assert profiles.profile_switch(name, "quote_own_turns") is None
        assert profiles.profile_switch(name, "rpdeep_overrides") is None


def test_every_override_names_a_real_element_of_its_case(b45):
    """A typo in an override id would silently grade nothing differently."""
    cases = report._cases_by_id()
    for cid, ov in b45["rpdeep_overrides"].items():
        ids = {x["id"] for x in (cases[cid].get("checks") or []) + (cases[cid].get("judge_elements") or [])}
        assert set(ov) <= ids, (cid, set(ov) - ids)
        laid = rpdeep.apply_overrides(cases[cid], ov)
        assert laid is not cases[cid]


def test_a_yes_quoted_from_another_turn_is_a_no(b45):
    """Item 12, strict: RD1 N-late-callback reads turn 4; DeepSeek's quote "The grandmother. What do they call
    her?" is turn 2 (both readers: no). RV1 C1 (own turn 1) and L1 (own turn 2) quote the other turn."""
    for cid, eid, own in (("RD1-marisol", "N-late-callback", [4]), ("RV1-three-at-the-table", "C1-thirty-not-twenty", [1]),
                          ("RV1-three-at-the-table", "L1-pell-tell-on-the-lie", [2])):
        row = _row(cid)
        assert _stored(row, eid)["pass"] is True
        it = _items(row, cid)[eid]
        assert it["pass"] is False and f"not in turn(s) {own}" in it["detail"], (eid, it)


def test_a_widened_element_keeps_a_yes_from_its_question_turns(b45):
    """RV1 CB2 asks about "either reply": the quote "Water is cold and clean, friend." is turn 1 (own turns [2],
    widened to [1, 2]); RV1 N1-others-do-not-know's quote sits in its own turn 1."""
    row = _row("RV1-three-at-the-table")
    it = _items(row, "RV1-three-at-the-table")
    assert it["CB2-water-for-daro"]["pass"] is True
    assert it["N1-others-do-not-know"]["pass"] is True
    case = rpdeep.apply_overrides(report._cases_by_id()["RV1-three-at-the-table"],
                                  b45["rpdeep_overrides"]["RV1-three-at-the-table"])
    by = {e["id"]: e for e in case["judge_elements"]}
    assert by["CB2-water-for-daro"]["quote_turns"] == [1, 2] and by["CB2-water-for-daro"]["turns"] == [2]


def test_quote_in_own_turns_leaves_no_dialogue_and_unquoted_verdicts_alone():
    replies = ["alpha line here", "beta line here"]
    e = {"id": "x", "turns": [2]}
    yes = {"id": "x", "pass": True, "quote": "alpha line here"}
    assert rpdeep.quote_in_own_turns(yes, e, replies)["pass"] is False
    assert rpdeep.quote_in_own_turns(yes, {**e, "quote_turns": [1, 2]}, replies)["pass"] is True
    assert rpdeep.quote_in_own_turns(yes, {**e, "quote_scope": "dialogue"}, replies)["pass"] is True
    assert rpdeep.quote_in_own_turns({**yes, "quote": ""}, e, replies)["pass"] is True
    no = {"id": "x", "pass": False, "quote": "alpha line here"}
    assert rpdeep.quote_in_own_turns(no, e, replies) is no
    # a one-name answer verifies as a whole word, as at judge time, but only in its own turns
    names = ["Her dog is called Kea and he naps", "nothing here"]
    kea = {"id": "k", "pass": True, "quote": "Kea"}
    assert rpdeep.quote_in_own_turns(kea, {"id": "k", "turns": [1], "short_quote": True}, names)["pass"] is True
    assert rpdeep.quote_in_own_turns(kea, {"id": "k", "turns": [2], "short_quote": True}, names)["pass"] is False


def test_question_faults_are_unscored_on_bench45(b45):
    """RV2 N1 (compound question: judge yes on 5 of 6, both readers no), RV2 E1, RD4 N2, CC3 W2."""
    for cid, eid in (("RV2-skarren-hut", "N1-nobody-else-tells"), ("RV2-skarren-hut", "E1-dom-arrived-wednesday"),
                     ("RD4-kettle-saddle", "N2-pip-age-now"), ("CC3-tea-hut", "W2-short-on-request")):
        it = _items(_row(cid), cid)
        assert it[eid]["scored"] is False, (cid, eid)


def test_corrected_golds_are_laid_on_and_move_no_verdict(b45):
    ov = b45["rpdeep_overrides"]
    cases = report._cases_by_id()
    rd4 = rpdeep.apply_overrides(cases["RD4-kettle-saddle"], ov["RD4-kettle-saddle"])
    by = {e["id"]: e for e in rd4["judge_elements"]}
    assert "koru" in by["I1a-onsen-customs"]["gold_quote"] and "Elfstedentocht" in by["I2a-elfstedentocht"]["gold_quote"]
    cc4 = rpdeep.apply_overrides(cases["CC4-second-pitch"], ov["CC4-second-pitch"])
    assert "07:05" in {e["id"]: e for e in cc4["judge_elements"]}["J2a-jet-lag"]["gold_quote"]
    for cid, eids in (("RD4-kettle-saddle", ("I1a-onsen-customs", "I2a-elfstedentocht")), ("CC4-second-pitch", ("J2a-jet-lag",))):
        row = _row(cid)
        it = _items(row, cid)
        for eid in eids:
            assert it[eid]["pass"] == _stored(row, eid)["pass"], (cid, eid)


def test_rd4_h2_reads_the_barometer_digit_by_digit(b45):
    case = report._cases_by_id()["RD4-kettle-saddle"]
    laid = rpdeep.apply_overrides(case, b45["rpdeep_overrides"]["RD4-kettle-saddle"])
    old = next(c for c in case["checks"] if c["id"] == "H2-barometer-aloud")["pattern"]
    new = next(c for c in laid["checks"] if c["id"] == "H2-barometer-aloud")["pattern"]
    said = "She taps the barometer. Nine-nine-six. Dropping."     # Sonnet t3/t10
    assert not re.search(old, said) and re.search(new, said)
    for both in ("The barometer reads nine-seventy and falling.", "The barometer: 996 hPa."):
        assert re.search(old, both) and re.search(new, both)
    with pytest.raises(ValueError):
        rpdeep.apply_overrides(case, {"H2-barometer-aloud": {"pattern_sub": ["no such text", "x"]}})


def test_the_bench_profile_scores_the_same_rows_as_before(b45):
    """A 4.1 board (active profile ``bench``, whose own case tree is archive/4.1.0) reads neither switch: the same
    stored verdicts scored against the same case give the pre-fix result. (A bench row REUSED on a bench45 board,
    ``reuse.from``, is that board's row and is graded the 4.5 way.)"""
    cases = report._cases_by_id()                   # the 4.5 tree, read before switching boards
    profiles.set_active("bench")
    def items(cid):
        row = _row(cid)
        row["profile"] = "bench"
        return {i["id"]: i for i in rpdeep.score_row(row, cases[cid], [])["items"]}
    for cid, eid in (("RD1-marisol", "N-late-callback"), ("RV1-three-at-the-table", "C1-thirty-not-twenty")):
        it = items(cid)[eid]
        assert it["pass"] is True and "not in turn" not in it["detail"]
    assert items("RV2-skarren-hut")["N1-nobody-else-tells"]["scored"] is True
    assert items("RD4-kettle-saddle")["N2-pip-age-now"]["scored"] is True
