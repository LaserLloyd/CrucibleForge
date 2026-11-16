"""Class / Lean / MAXED on the routed 4.5 board, the 4.x way (lead 2026-11-14, the site's "Reading
the board"): Class = band of Overall INCLUDING bonus (headline.capability_class: S 16, A 13, B 10,
C below); Lean = (Chat - board median Chat) - (Coding - board median Coding), medians over
complete, non-provisional current rows (headline.lean, gap 1.5); MAXED = at or above
``ladder.unlock_share(prof)`` on a part's standard share and on every one of its bonus packs
(nothing harder left). A staged Claude Code validation row takes no Class and no Lean."""
from __future__ import annotations

from crucibleforge import report


def _prof(unlock=None):
    p = {
        "name": "t", "routing": {"calibration": "x.json"},
        "cases": {"perf": ["P1"]},
        "chains": {"work/bk_a": {}, "work/bk_b": {}, "work/bk_u": {}, "chat/bk_s": {}},
        "scores": {
            "work": {"label": "Coding", "parts": {
                "bk_a": {"label": "A", "max": 6, "scoring": ["coding"], "cases": []},
                "bk_b": {"label": "B", "max": 4, "scoring": ["coding"], "cases": []},
                "bk_u": {"label": "U", "max": 0, "scoring": ["coding"], "cases": []}}},
            "chat": {"label": "Chat", "parts": {
                "bk_s": {"label": "S", "max": 10, "scoring": ["rpdeep"], "cases": []}}}}}
    if unlock is not None:
        p["ladder"] = {"unlock": unlock}
    return p


def _det(std, sets=(), complete=True, n_ex=0):
    return {"std_share": std, "unlock_share": std, "max": 6.0, "n_excluded": n_ex,
            "sets": list(sets), "chains": {"complete": complete}}


def _set(n, level, share=None, state="pending"):
    return {"n": n, "level": level, "points": 1.0, "state": state, "share": share}


def _card(a, b=None, s=None):
    return {"ladder": {"work": {"bk_a": a, "bk_b": b or _det(0.5), "bk_u": {"std_share": None, "max": 0.0,
                                                                          "sets": []}},
                       "chat": {"bk_s": s or _det(0.5)}}}


# ---------------------------------------------------------------- MAXED

def test_a_part_with_no_pack_is_maxed_at_the_threshold_on_its_standard():
    mx = report.maxed45(_card(_det(0.95)), _prof())
    assert mx["work"] == {"parts": ["bk_a"], "category": False}
    assert report.maxed45(_card(_det(0.89)), _prof())["work"]["parts"] == []
    # the threshold is the profile's, never a literal
    assert report.maxed45(_card(_det(0.85)), _prof(unlock=0.8))["work"]["parts"] == ["bk_a"]


def test_maxed_needs_every_pack_at_the_threshold_too():
    assert report.maxed45(_card(_det(0.95, [_set(1, 1, 0.95, "scored")])), _prof())["work"]["parts"] == ["bk_a"]
    assert report.maxed45(_card(_det(0.95, [_set(1, 1, 0.7, "scored")])), _prof())["work"]["parts"] == []
    assert report.maxed45(_card(_det(0.95, [_set(1, 1)])), _prof())["work"]["parts"] == []   # not run


def test_a_partial_or_excluded_part_is_never_maxed():
    assert report.maxed45(_card(_det(0.95, complete=False)), _prof())["work"]["parts"] == []
    assert report.maxed45(_card(_det(0.95, n_ex=1)), _prof())["work"]["parts"] == []


def test_a_section_is_maxed_when_every_standard_part_is():
    card = _card(_det(0.95), _det(0.92), _det(0.99))
    mx = report.maxed45(card, _prof())
    assert mx["work"] == {"parts": ["bk_a", "bk_b"], "category": True}    # the pack-only part does not count
    assert mx["chat"]["category"] is True


# ---------------------------------------------------------------- Class / Lean

def _row(work, chat, prov=False, cc=False):
    st = {"headline": {"S": {"work": work, "chat": chat,
                             "overall": None if work is None or chat is None else work + chat}},
          "era": {"old": False}, "coverage": {"provisional": prov}}
    if cc:
        st["validation_cc"] = True
    return st


def test_class_is_the_band_of_overall_including_bonus_and_lean_is_against_medians():
    stats = {"a": _row(9.0, 7.5), "b": _row(6.0, 8.0), "c": _row(4.0, 4.0)}
    report.class_lean(list(stats), stats, _prof())
    assert [stats[l]["headline"]["class"] for l in "abc"] == ["S", "A", "C"]
    # medians: chat 7.5, code 6.0; a: (7.5-7.5)-(9-6) = -3 -> code-leaning; b: 0.5-0 -> balanced
    assert stats["a"]["headline"]["lean"] == "code-leaning"
    assert stats["b"]["headline"]["lean"] == "balanced"
    assert stats["c"]["headline"]["lean"] == "code-leaning"     # (4-7.5)-(4-6) = -1.5: at the gap


def test_a_validation_row_takes_no_class_and_never_moves_the_medians():
    stats = {"a": _row(9.0, 7.5), "b": _row(6.0, 8.0), "cc": _row(10.0, 10.0, prov=True, cc=True)}
    report.class_lean(list(stats), stats, _prof())
    assert stats["cc"]["headline"]["class"] is None and stats["cc"]["headline"]["lean"] is None
    assert stats["a"]["headline"]["lean"] == "code-leaning"     # medians over a and b only


def test_an_incomplete_row_has_no_class_and_no_lean():
    stats = {"a": _row(9.0, None)}
    report.class_lean(["a"], stats, _prof())
    assert stats["a"]["headline"]["class"] is None and stats["a"]["headline"]["lean"] is None


def test_routed_scorecard_has_class_and_lean_again():
    hdr = report.scorecard45_header(["Coding (/10)", "Chat (/10)"], "Overall (/20)", has_cost=False)
    assert hdr[:5] == ["#", "Model", "Overall (/20)", "Class", "Lean"]
    assert "Efficiency" not in hdr and "Room" not in hdr
