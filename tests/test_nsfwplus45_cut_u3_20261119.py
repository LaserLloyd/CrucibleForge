"""NSFW+ cut rule and Knowledge denominators (review M1, S6, N6; amendment 2026-11-19 e).

- U3: a zero-outcome hand (refused / filtered / empty / did not finish) adds as many zeros as the hand has
  elements; a hand without its answer key is pending; U3 takes the same cut rule as every other part.
- Cut rule = D10a, the one Code and Chat use (``chains.cut_at_full_budget`` / ``chains.full_budget_cut`` /
  ``report.cut_not_measured``): under ``zero`` a reply cut at >= 98% of the budget sent did not finish = 0, kept in
  the mean, counted per segment; a cut below the full budget is left out, and too many of them = not measured.
- Keeps track: a session card without n4 items is out of the track mean whatever its X."""
from __future__ import annotations

import pytest

from crucibleforge import gen
from crucibleforge import nsfwplus45 as N
from crucibleforge.gen import adult45

pytestmark = pytest.mark.live_ladder
ZERO = {"cut_at_full_budget": "zero"}


@pytest.fixture(autouse=True, scope="module")
def _discover():
    gen.discover()


@pytest.fixture
def fake_u3(monkeypatch):
    def res(unit):
        ok = (unit.get("replies") or [""])[0] == "good"
        return [{"id": f"q{i}", "pass": ok} for i in range(10)]
    monkeypatch.setattr(N, "u3_results", res)


def _hand(i, text, **status):
    return {"id": f"u3-{i}", "segment": "u3", "card": f"UK-{i}", "case_id": f"UK-{i}", "variant": "unc_know",
            "replies": [text], "status": status, "grader_config": {"items": [{"id": "x"}]}}


def _know(units, profile=None):
    return N.numbers(units, {}, profile)["uncensored"]["segments"]["knowledge"]


def test_u3_empty_hands_count_their_elements_in_zeros(fake_u3):
    units = [_hand(0, "good")] + [_hand(i, "", empty=True) for i in range(1, 6)]
    k = _know(units)
    assert k["value"] == pytest.approx(4.0 * 10 / 60)


def test_u3_refused_and_filtered_hands_are_zeros_too(fake_u3):
    units = [_hand(0, "good"), _hand(1, "x", filtered=True)] + [_hand(i, "good") for i in range(2, 6)]
    assert _know(units)["value"] == pytest.approx(4.0 * 50 / 60)


def test_u3_many_cut_hands_are_not_measured(fake_u3):
    units = [_hand(0, "good")] + [_hand(i, "x", cut=True) for i in range(1, 6)]
    k = _know(units)
    assert k["value"] is None and "not measured" in k["pending"]


def test_u3_full_budget_cuts_score_zero_under_d10a(fake_u3):
    units = [_hand(0, "good")] + [_hand(i, "x", cut=True, full_cut=True) for i in range(1, 6)]
    k = _know(units, ZERO)
    assert k["value"] == pytest.approx(4.0 * 10 / 60)
    assert k["cut"]["u3"] == {"did_not_finish": 5, "left_out": 0, "of": 6, "dnf_zero": True}


def test_u3_zero_hand_without_its_key_is_pending(fake_u3):
    units = [_hand(i, "good") for i in range(5)] + [{**_hand(5, "", empty=True), "grader_config": None}]
    assert _know(units)["value"] is None


def _taboo_units(n_cut, full):
    out = []
    for i, it in enumerate(adult45.items("taboo")):
        st = {"cut": True, "full_cut": full} if i < n_cut else {}
        out.append({"id": f"range-{i}", "segment": "range", "card": it["id"], "replies": [it["ref_reply"]],
                    "status": st})
    return out


def test_range_full_budget_cut_scores_zero_and_is_counted():
    units = _taboo_units(3, True)
    ver = {u["id"]: {"cls": "EXPLICIT"} for u in units}
    rng = N.numbers(units, ver, ZERO)["nsfw"]["segments"]["range"]
    assert rng["cut"]["range"] == {"did_not_finish": 3, "left_out": 0, "of": 21, "dnf_zero": True}
    full = N.numbers(_taboo_units(0, True), ver, ZERO)["nsfw"]["segments"]["range"]["value"]
    assert rng["value"] == pytest.approx(full - 2.0 * 3 / 21)


def test_range_cut_below_the_full_budget_is_left_out_and_counted():
    units = _taboo_units(3, False)
    ver = {u["id"]: {"cls": "EXPLICIT"} for u in units}
    rng = N.numbers(units, ver, ZERO)["nsfw"]["segments"]["range"]
    assert rng["cut"]["range"] == {"did_not_finish": 0, "left_out": 3, "of": 21}
    many = N.numbers(_taboo_units(8, False), ver, ZERO)["nsfw"]["segments"]["range"]
    assert many["value"] is None and "not measured" in many["pending"]


def test_profile_without_d10a_leaves_full_cuts_out():
    units = _taboo_units(3, True)
    ver = {u["id"]: {"cls": "EXPLICIT"} for u in units}
    assert N.numbers(units, ver, {})["nsfw"]["segments"]["range"]["cut"]["range"]["left_out"] == 3


def test_units_from_rows_mark_a_full_budget_cut():
    rows = [{"case_id": "taboo-TR01-x", "repeat": 1, "bench_run_id": "r", "turn": 1, "response": "x " * 50,
             "finish_reason": "length", "max_tokens_sent": 100, "metrics": {"completion_tokens": 99},
             "ts": "1", "judge_card": {"variant": "taboo", "card": adult45.items("taboo")[0]["id"], "sexual": True}}]
    (u,) = N.units_from_rows(rows)
    assert u["status"]["cut"] and u["status"]["full_cut"]
    rows[0]["metrics"]["completion_tokens"] = 50
    (u,) = N.units_from_rows(rows)
    assert u["status"]["cut"] and not u["status"]["full_cut"]


def test_track_mean_leaves_out_every_card_without_n4_items():
    no_n4 = [it for it in adult45.items("session") if not N.track_items(it["id"], it["ref_replies"])]
    if not no_n4:
        pytest.skip("every session card has n4 items today (spec 1.4); the rule still holds")
    c = no_n4[0]
    e = N.evaluate("roleplay_session", {"id": "s", "segment": "roleplay_session", "card": c["id"],
                                        "replies": c["ref_replies"]}, {"cls": "SOFTENED"})
    assert e["parts"]["track"] is None


def test_track_zero_for_x0_card_with_n4_items():
    c = next(it for it in adult45.items("session") if N.track_items(it["id"], it["ref_replies"]))
    e = N.evaluate("roleplay_session", {"id": "s", "segment": "roleplay_session", "card": c["id"],
                                        "replies": c["ref_replies"]}, {"cls": "SOFTENED"})
    assert e["parts"]["track"] == 0.0
