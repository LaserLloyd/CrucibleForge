"""Long scenes (the eight 4.1 deep cases) graded the 4.5 way, on real stored rows (scrub audit 2026-11-15, lead
items 10, 11, 12, RD4 H2, and RV2 N1 from the reader check EXP-20261115-39). The case file is shared with 4.1, so every change lives in the bench45 profile
(``rpdeep_overrides``, ``quote_own_turns``) and a row run under ``bench`` (4.1) must score exactly as before.

Fixture: tests/fixtures/rpdeep_real_rows_20261115.json.gz = five stored pilot runs (row + its turn rows) copied
from D2's pilot results (MiniMax RD1 + CC3, Sonnet RD4 + RV1, the reference model's RV2)."""
import gzip
import json
from pathlib import Path

import pytest

from crucibleforge import profiles, rpdeep

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "rpdeep_real_rows_20261115.json.gz"


def _runs():
    return {(e["model"], e["case_id"]): e for e in json.loads(gzip.open(FIX, "rt").read())}


def _case(cid):
    return next(c for c in json.loads((ROOT / "cases" / "rpdeep.json").read_text(encoding="utf-8")) if c["id"] == cid)


def _items(model, cid, profile):
    """Score one stored run as a board of ``profile`` would: the row's profile AND the active (board) profile.
    (On the bench45 board a reused bench row is graded the 4.5 way, ``reuse.from``; a 4.1 board is ``bench``.)
    The autouse conftest fixture resets the active profile after each test."""
    profiles.set_active(profile)
    e = _runs()[(model, cid)]
    row = {**e["row"], "profile": profile}
    turns = [{**t, "profile": profile} for t in e["turns"]]
    return {i["id"]: i for i in rpdeep.score_row(row, _case(cid), turns)["items"]}


def test_fixture_rows_were_run_under_bench45():
    for e in _runs().values():
        assert e["row"].get("profile") == "bench45", (e["model"], e["case_id"])


def test_bench_41_profile_has_neither_switch():
    assert profiles.profile_switch("bench", "rpdeep_overrides") is None
    assert profiles.profile_switch("bench", "quote_own_turns") is None
    assert profiles.profile_switch("bench45", "quote_own_turns") is True


def test_every_override_names_a_real_element_and_applies():
    ov = profiles.profile_switch("bench45", "rpdeep_overrides")
    for cid, els in ov.items():
        case = _case(cid)
        ids = {x["id"] for x in (case.get("checks") or []) + (case.get("judge_elements") or [])}
        assert set(els) <= ids, (cid, set(els) - ids)
        rpdeep.apply_overrides(case, els)   # a pattern_sub whose text is gone raises


def test_pattern_sub_refuses_text_not_in_the_pattern():
    case = _case("RD4-kettle-saddle")
    with pytest.raises(ValueError):
        rpdeep.apply_overrides(case, {"H2-barometer-aloud": {"pattern_sub": ["no such text", "x"]}})


def test_wrong_turn_quote_fails_on_45_only():
    # MiniMax RD1 L2-tape: the element reads turn 1, the judge's quote is from turn 4
    i45 = _items("mm", "RD1-marisol", "bench45")["L2-tape"]
    assert not i45["pass"] and "not in turn(s) [1]" in i45["detail"]
    assert _items("mm", "RD1-marisol", "bench")["L2-tape"]["pass"]


def test_widened_elements_accept_a_quote_from_the_turns_their_question_names():
    rv1 = _items("sonnet", "RV1-three-at-the-table", "bench45")
    assert rv1["CB1-the-mare-shoe"]["pass"], rv1["CB1-the-mare-shoe"]
    rv2 = _items("ref", "RV2-skarren-hut", "bench45")
    assert rv2["CB1-the-leaking-boot"]["pass"], rv2["CB1-the-leaking-boot"]


@pytest.mark.parametrize("model,cid,el", [("mm", "CC3-tea-hut", "W2-short-on-request"),
                                          ("sonnet", "RD4-kettle-saddle", "N2-pip-age-now"),
                                          ("ref", "RV2-skarren-hut", "N1-nobody-else-tells")])
def test_faulty_questions_are_unscored_on_45_only(model, cid, el):
    assert _items(model, cid, "bench45")[el].get("scored") is False
    assert _items(model, cid, "bench")[el].get("scored", True) is True


def test_rv2_e1_is_unscored_on_both_boards():
    """E1 was already pruned (scored: false) in the shared case file before 4.5; the bench45 override only states it."""
    for prof in ("bench45", "bench"):
        assert _items("ref", "RV2-skarren-hut", prof)["E1-dom-arrived-wednesday"].get("scored") is False


def test_barometer_read_digit_by_digit_counts_on_45():
    # Sonnet RD4 t3/t10: "Nine-nine-six. Dropping."
    assert _items("sonnet", "RD4-kettle-saddle", "bench45")["H2-barometer-aloud"]["pass"]
    assert not _items("sonnet", "RD4-kettle-saddle", "bench")["H2-barometer-aloud"]["pass"]


def test_rate_ignores_unscored_items_on_45():
    e = _runs()[("sonnet", "RD4-kettle-saddle")]
    res = rpdeep.score_row({**e["row"], "profile": "bench45"}, _case("RD4-kettle-saddle"),
                           [{**t, "profile": "bench45"} for t in e["turns"]])
    scored = [i for i in res["items"] if i.get("scored", True)]
    assert all(i["id"] != "N2-pip-age-now" for i in scored)
