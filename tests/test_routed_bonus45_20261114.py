"""Bonus packs on the routed 4.5 board (the owner's ruling, relayed by the lead 2026-11-14): the 4.x
convention. A part's ladder packs open in the 4.x chain (standard share and every set of every
lower level at or above ``ladder.unlock_share(prof)``), each pack adds share x its points (1) to
its part, section and Overall; NSFW keeps its own packs, never in Overall. Max = standard + one
per pack. Margin (G2): pack SE = cluster SE over its units; section 90% margin = 1.645 x
sqrt(se_main^2 + sum se_pack^2); Overall combines the same way.

Today no routed part has ladder rows (line 744 drops them until K's fix), so these tests use a
hand-made routed card where the ladder detail is already present, plus today's shape (no sets),
where nothing may move."""
from __future__ import annotations

import copy
import math

import pytest

from crucibleforge import report


def _prof(unlock=None):
    p = {
        "name": "t", "routing": {"calibration": "x.json"},
        "cases": {"perf": ["P1"]},
        "chains": {"work/bk_a": {}, "work/bk_b": {}, "chat/bk_s": {}, "nsfw/mn": {}},
        "scores": {
            "work": {"label": "Coding", "parts": {
                "bk_a": {"label": "A", "max": 6, "scoring": ["coding"], "cases": []},
                "bk_b": {"label": "B", "max": 4, "scoring": ["coding"], "cases": []}}},
            "chat": {"label": "Chat", "parts": {
                "bk_s": {"label": "S", "max": 10, "scoring": ["rpdeep"], "cases": []}}},
            "nsfw": {"label": "NSFW", "in_total": False, "parts": {
                "mn": {"label": "MN", "max": 10, "scoring": ["nsfw"], "cases": []}}}}}
    if unlock is not None:
        p["ladder"] = {"unlock": unlock}
    return p


def _sec(points, se):
    run = {"theta": 0.0, "se": se, "psi": {"u": 0.0}, "slope": 1.0, "points": points,
           "se_method": "cluster"}
    return {"points": points, "se_points": se, "margin_90": 1.645 * se, "_run": run}


def _set(n, level, share=None, state="pending"):
    return {"n": n, "level": level, "points": 1.0, "state": state, "share": share}


def _det(std, sets, mx=6.0):
    return {"std_share": std, "unlock_share": std, "max": mx, "points": None, "sets": sets}


def _card(work_a=None, work_b=None, chat=None, nsfw=None):
    return {"ladder": {"work": {"bk_a": work_a or _det(0.8, [], 6.0),
                                "bk_b": work_b or _det(0.7, [], 4.0)},
                       "chat": {"bk_s": chat or _det(0.8, [], 10.0)},
                       "nsfw": {"mn": nsfw or _det(0.5, [], 10.0)}}}


SCALE = {"sections": {"work": _sec(86.0, 5.0), "chat": _sec(80.0, 4.0), "nsfw": _sec(60.0, 8.0)}}


def _cs():
    return {"ended": 3, "units": 3, "max": 6.0, "points": 4.8, "mean": 0.8, "complete": True,
            "elements": 3, "mean_level_held": None, "se_points": None, "se_unit": "element", "icc": None,
            "margin_90": None, "turns": 3, "tokens": {"prompt": 0, "thinking": 0, "completion": 0},
            "tokens_per_element": None, "cost_usd": None}


def _hl(card=None, units=None, prof=None):
    hl = {"S": {}, "m": {}}
    report._apply_scale45(hl, copy.deepcopy(SCALE), prof or _prof(), card, units)
    return hl


def test_todays_shape_no_ladder_sets_changes_nothing():
    base = _hl()
    got = _hl(_card())
    got.pop("maxed")                                    # MAXED is read off the card (maxed45)
    assert got == base                                  # card present, no sets: identical
    assert "bonus45" not in base and "max45" not in base


def test_a_scored_pack_adds_share_times_points_to_part_section_and_overall():
    card = _card(work_a=_det(0.95, [_set(1, 1, 0.8, "scored"), _set(2, 2)]))
    units = {"work/bk_a#1": [1.0, 0.6, 0.8]}
    hl = _hl(card, units)
    base = _hl()
    assert hl["bonus45"]["work"] == pytest.approx(0.8)
    assert hl["S"]["work"] == round(8.6 + 0.8, 1) and hl["std45"]["work"] == 8.6
    assert hl["S"]["overall"] == round(base["sort"] + 0.8, 1)
    assert hl["sort"] == pytest.approx(base["sort"] + 0.8)
    assert hl["max45"] == {"work": 12, "chat": 10, "nsfw": 10, "overall": 22}
    # margins: pack SE = cluster SE over its units (sd / sqrt(n)), x its points
    xs = [1.0, 0.6, 0.8]
    se_pack = math.sqrt(sum((x - 0.8) ** 2 for x in xs) / (3 * 2))
    assert hl["m"]["work"] == pytest.approx(1.645 * math.sqrt(0.5 ** 2 + se_pack ** 2))
    assert hl["m"]["overall"] == pytest.approx(1.645 * math.sqrt(0.5 ** 2 + 0.4 ** 2 + se_pack ** 2))
    # the standard sections are untouched where no pack scored
    assert hl["S"]["chat"] == base["S"]["chat"] and hl["m"]["chat"] == base["m"]["chat"]
    packs = hl["packs45"]["work"]["bk_a"]
    assert packs[0]["state"] == "scored" and packs[0]["pts"] == pytest.approx(0.8)
    assert packs[0]["n_units"] == 3 and packs[0]["margin"] == pytest.approx(1.645 * se_pack)


def test_a_second_pack_is_locked_with_its_reason():
    card = _card(work_a=_det(0.95, [_set(1, 1, 0.8, "scored"), _set(2, 2)]))
    p2 = _hl(card)["packs45"]["work"]["bk_a"][1]
    assert p2["state"] == "locked"
    assert p2["reason"] == "needs pack 1 at or above the 90% threshold"


def test_an_opened_pack_with_no_units_is_not_run_never_zero():
    card = _card(work_a=_det(0.95, [_set(1, 1, 0.95, "scored"), _set(2, 2)]))
    hl = _hl(card, {"work/bk_a#1": [1.0, 0.9]})
    p2 = hl["packs45"]["work"]["bk_a"][1]
    assert p2["state"] == "pending" and p2["reason"] == "not run" and p2["pts"] is None
    assert hl["bonus45"]["work"] == pytest.approx(0.95)


def test_a_part_below_the_threshold_locks_its_packs_with_the_reason():
    card = _card(work_a=_det(0.85, [_set(1, 1, 0.9, "scored")]))   # a stale share never counts
    hl = _hl(card)
    p1 = hl["packs45"]["work"]["bk_a"][0]
    assert p1["state"] == "locked" and p1["pts"] is None
    assert p1["reason"] == "part below the 90% threshold (85%)"
    assert hl["bonus45"]["work"] == 0 and hl["S"]["work"] == _hl()["S"]["work"]
    assert hl["max45"]["work"] == 11                       # the pack exists: it is in the max


def test_the_threshold_is_the_profiles_never_a_literal():
    card = _card(work_a=_det(0.85, [_set(1, 1, 0.5, "scored")]))
    hl = _hl(card, prof=_prof(unlock=0.8))
    assert hl["packs45"]["work"]["bk_a"][0]["state"] == "scored"
    assert hl["bonus45"]["work"] == pytest.approx(0.5)
    card2 = _card(work_a=_det(0.95, [_set(1, 1, 0.75, "scored"), _set(2, 2)]))
    p2 = _hl(card2, prof=_prof(unlock=0.8))["packs45"]["work"]["bk_a"][1]
    assert p2["reason"] == "needs pack 1 at or above the 80% threshold"


def test_nsfw_packs_add_to_nsfw_never_to_overall():
    card = _card(nsfw=_det(0.95, [_set(1, 1, 0.6, "scored")], 10.0))
    hl, base = _hl(card), _hl()
    assert hl["S"]["nsfw"] == round(6.0 + 0.6, 1) and hl["bonus45"]["nsfw"] == pytest.approx(0.6)
    assert hl["S"]["overall"] == base["S"]["overall"] and hl["m"]["overall"] == base["m"]["overall"]
    assert hl["max45"]["nsfw"] == 11 and hl["max45"]["overall"] == 20


def test_errored_pack_is_said_not_scored():
    card = _card(work_a=_det(0.95, [_set(1, 1, None, "errored")]))
    p1 = _hl(card)["packs45"]["work"]["bk_a"][0]
    assert p1["state"] == "errored" and p1["pts"] is None and "not 0" in p1["reason"]


def test_display_marks_the_standard_boundary():
    card = _card(work_a=_det(0.95, [_set(1, 1, 0.8, "scored"), _set(2, 2)]))
    hl = _hl(card, {"work/bk_a#1": [1.0, 0.6, 0.8]})
    assert report.bonus45_tag(hl, "work") == " /12 = 8.6 + 0.8 bonus"
    assert report.bonus45_tag(hl, "chat") == ""            # no packs: the cell is as before
    assert report.bonus45_tag(hl, "overall").startswith(" /22 = ")
    assert report.max45_label(hl, "work", 10) == "/12 = 10 standard + 2 packs"
    assert report.max45_label(_hl(), "work", 10) == "/10"


def test_routed_board_prints_one_line_per_pack():
    card = _card(work_a=_det(0.95, [_set(1, 1, 0.8, "scored"), _set(2, 2)]))
    hl = _hl(card, {"work/bk_a#1": [1.0, 0.6, 0.8]})
    stats = {"m": {"headline": hl, "chain_stats": {"work/bk_a": _cs()}}}
    out = "\n".join(report.render_board45(["m"], stats, _prof()))
    assert "| m | work/bk_a | pack 1 (level 1) | scored | 0.80 / 1 | 3 |" in out
    assert "| m | work/bk_a | pack 2 (level 2) | locked: needs pack 1 at or above the 90% threshold | - | - | - |" in out
    assert "8.6 standard + 0.8 bonus" in out


def test_no_pack_table_when_no_part_has_packs():
    stats = {"m": {"headline": _hl(_card()), "chain_stats": {"work/bk_a": _cs()}}}
    out = "\n".join(report.render_board45(["m"], stats, _prof()))
    assert "Bonus packs" not in out and "bonus" not in out


def test_pack_se_needs_two_units():
    assert report.pack_se45([0.5]) is None and report.pack_se45([]) is None
    assert report.pack_se45([1.0, 0.0]) == pytest.approx(0.5)


def test_pack_unit_shares_one_unit_per_question_repeats_pooled(monkeypatch):
    lad = {"Q1": ("work/bk_a", 1), "Q2": ("work/bk_a", 1), "Q3": ("work/bk_a", 1), "X": ("work/gone", 1)}
    monkeypatch.setattr(report, "_ladder_levels", lambda prof: lad)
    monkeypatch.setattr(report, "_set_weights", lambda prof, ids, scoring=None: {"coding": 1.0})
    monkeypatch.setattr(report, "_judged_weights", lambda w: True)
    share = {"Q1": 100.0, "Q2": 50.0}

    def fake_stats(label, cfg, rows=None, meta=None, era=None):
        cid = rows[0]["case_id"]
        assert len({r["case_id"] for r in rows}) == 1          # one question per unit
        return {"cid": cid, "n": len(rows), "pending_judge": cid == "Q3"}
    monkeypatch.setattr(report, "model_stats", fake_stats)
    monkeypatch.setattr(report, "scorecard",
                        lambda s, sc, partial=frozenset(): {"work": share[s["cid"]]})
    rows = [{"case_id": "Q1"}, {"case_id": "Q1"}, {"case_id": "Q2"}, {"case_id": "Q3"},
            {"case_id": "X"}, {"case_id": "S1"}]
    got = report.pack_unit_shares(_prof(), "m", None, rows, None, None)
    assert got == {"work/bk_a#1": [1.0, 0.5]}   # Q3 waits for its judge; X is not a shown part


# ---------------------------------------------------------------- the run's pack_gate reason

def test_a_locked_pack_says_the_runs_pack_gate_reason():
    det = _det(0.85, [_set(1, 1)])
    det["pack_gate"] = {"target": "work/bk_a", "share": 0.85, "threshold": 0.9, "opened": False,
                        "reason": "share below the threshold"}
    p1 = _hl(_card(work_a=det))["packs45"]["work"]["bk_a"][0]
    assert p1["reason"] == "share below the threshold (85% < 90%)"
    det2 = _det(None, [_set(1, 1), _set(2, 2)])
    det2["opened"] = False
    det2["pack_gate"] = {"share": 0.95, "threshold": 0.9, "opened": False, "reason": "route below top"}
    ps = _hl(_card(work_a=det2))["packs45"]["work"]["bk_a"]
    assert ps[0]["reason"] == "route below top"                                 # the run's words
    assert ps[1]["reason"] == "needs pack 1 at or above the 90% threshold"     # no record: ours


def test_a_scored_pack_on_one_unit_is_marked_in_the_table():
    card = _card(work_a=_det(0.95, [_set(1, 1, 0.8, "scored")]))
    hl = _hl(card, {"work/bk_a#1": [0.8]})
    stats = {"m": {"headline": hl, "chain_stats": {"work/bk_a": _cs()}}}
    out = "\n".join(report.render_board45(["m"], stats, _prof()))
    assert "| m | work/bk_a | pack 1 (level 1) | scored | 0.80 / 1 | 1 | - † |" in out
    assert "† A scored pack on fewer than 2 units has no SE" in out


# ---------------------------------------------------------------- ties on the ranked totals

def _sec_t(points, se, tag):
    run = {"theta": 0.0, "se": se, "psi": {f"{tag}1": 0.1, f"{tag}2": -0.1}, "slope": 1.0,
           "points": points, "se_method": "cluster"}
    return {"points": points, "se_points": se, "margin_90": 1.645 * se, "_run": run}


def _two(work_a, work_b, card_a=None, card_b=None, units=None):
    prof = _prof()
    stats = {}
    for l, w, c, t in (("a", work_a, card_a, "x"), ("b", work_b, card_b, "y")):
        secs = {"work": _sec_t(w, 2.0, t + "w"), "chat": _sec_t(80.0, 2.0, t + "c")}
        hl = {"S": {}, "m": {}}
        report._apply_scale45(hl, {"sections": secs}, prof, c, units)
        stats[l] = {"headline": hl, "scale45": {"sections": secs}}
    order = sorted(stats, key=lambda l: -stats[l]["headline"]["sort"])
    return order, report._rank45(order, stats, prof)


def test_tied_on_standard_a_scored_pack_clears_the_tie():
    assert _two(80.0, 80.0)[1] == {"a": "1", "b": "1="}                       # no packs: tied
    pack = _card(work_a=_det(0.95, [_set(1, 1, 1.0, "scored")]))
    none = _card(work_a=_det(0.95, [_set(1, 1)]))                             # b: open, not run
    order, lab = _two(80.0, 80.0, pack, none, {"work/bk_a#1": [1.0, 1.0, 1.0]})
    assert order == ["a", "b"] and lab == {"a": "1", "b": "2"}


def test_apart_on_standard_bonus_brings_them_inside_the_margin():
    assert _two(85.0, 80.0)[1] == {"a": "1", "b": "2"}                        # 0.5 apart: separated
    none = _card(work_a=_det(0.95, [_set(1, 1)]))
    pack = _card(work_a=_det(0.95, [_set(1, 1, 0.4, "scored")]))
    order, lab = _two(85.0, 80.0, none, pack, {"work/bk_a#1": [0.3, 0.5]})
    assert order == ["a", "b"] and lab == {"a": "1", "b": "1="}               # 8.5 vs 8.0 + 0.4


# ---------------------------------------------------------------- report.html / board.json split

def test_html_row_carries_the_standard_bonus_split_keyed_like_its_scores():
    card = _card(work_a=_det(0.95, [_set(1, 1, 0.8, "scored"), _set(2, 2)]))
    hl = _hl(card, {"work/bk_a#1": [1.0, 0.6, 0.8]})
    defs = report.board_defs(_prof())
    row = report._scale45_row({"headline": hl, "scorecard": {"score_defs": defs}})
    grp = next(d["group"] for d in defs if d["key"] == "work")
    assert row["bonus_tag"] == {"overall": report.bonus45_tag(hl, "overall"), grp: " /12 = 8.6 + 0.8 bonus"}
    assert row["max45"]["work"] == 12 and row["bonus45"]["work"] == pytest.approx(0.8)
    # no packs: no split, no extra fields
    plain = report._scale45_row({"headline": _hl(), "scorecard": {"score_defs": defs}})
    assert "bonus_tag" not in plain and "max45" not in plain


def test_board_script_prints_the_split_after_the_score():
    from pathlib import Path
    js = (Path(report.__file__).parent / "templates" / "board" / "script.js").read_text(encoding="utf-8")
    assert "function bonusTag(r, k)" in js and "+ bonusTag(r, k) + '</span>'" in js
