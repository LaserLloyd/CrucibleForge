"""PLAN-451-LIMITS step 7 (2026-11-25): no silent loss. report.json carries ``losses`` per model and segment (cut,
empty, context, transport, clamped, over_budget, tolerated, points at stake) and the details get one line; and
(site work, same day) each segment's share of its section's bonus points, summing to ``pack_points``."""
from __future__ import annotations

from crucibleforge import report

TREE = {"code": {"label": "Code",
                 "writes": {"max": 4, "parts": {"c1_implied": 1.0}},
                 "fixes": {"max": 3, "cases": ["PATCHFIX-core-1-1"]},
                 "tools": {"max": 2, "parts": {"c5_tools": 1.0, "c5_toolgen": 1.0}}},
        "chat": {"label": "Chat",
                 "character": {"max": 4, "parts": {"j_rpsession": 0.75, "rplong": {"RV1-a": 0.125}}},
                 "remembers": {"max": 3.5, "parts": {"rplong": {"CC3-b": 0.5, "CC4-c": 0.5}, "h2_state": 1.0}}}}


def _r(cid, part=None, **k):
    base = {"case_id": cid, "repeat": 1, "bench_run_id": "b1", "ts": "2026-11-25T00:00:00", "turn": None,
            "response": "ok", "score": 1.0, "grade": "pass", "finish_reason": "stop"}
    if part:
        base["chain"] = {"part": part, "id": cid}
    return {**base, **k}


def test_one_row_of_each_class_is_counted():
    rows = [_r("W1", "work/c1_implied"),
            _r("W2", "work/c1_implied", finish_reason="length", score=0.0, response="", grade="fail"),
            _r("T1", "work/c5_toolgen", score=0.0, response="", grade="fail",
               console={"end": "empty reply", "family": "toolworld"}),
            _r("T2", "work/c5_toolgen", console={"calls_without_line_break": 2, "family": "toolworld"}),
            _r("T3", "work/c5_tools", grade="error", error="ctx_short: x", error_kind="ctx_short", score=None),
            _r("T4", "work/c5_tools", grade="error", error="HTTP 503", error_kind="transport", score=None),
            _r("PATCHFIX-core-1-1", tolerated={"bare_closer": 1, "rev": "et-1"}, budget_clamp={"route_max": 1}),
            _r("CC3-b", over_budget=500)]
    L = report.losses45(rows, TREE)
    assert L["code.writes"]["cut"] == 1 and L["code.writes"]["points_at_stake"] == round(4 * 1 / 2 * 1.0, 3)
    t = L["code.tools"]
    assert (t["empty"], t["tolerated"], t["context"], t["transport"]) == (1, 1, 1, 1)
    assert L["code.fixes"]["tolerated"] == 1 and L["code.fixes"]["clamped"] == 1
    assert L["chat.remembers"]["over_budget"] == 1
    line = report.losses_line(L)
    assert line.startswith("Lost rows: ") and "code.writes 1 cut (2.00 pt)" in line and "tolerated: 2 rows" in line
    assert not report.OLD_TREE_RE.search(line) and not any(report.OLD_TREE_RE.search(k) for k in L)
    assert report.losses45([_r("W1", "work/c1_implied")], TREE) == {}


def test_pack_points_per_segment_sum_to_the_section():
    card = {"chat": {"h2_state": {"points": 2.0, "std_share": 0.5, "max": 2.0},           # 1.0 over its standard
                     "j_rplong": {"points": 3.0, "chains": {"points": 2.0}, "max": 3.0}}}  # 1.0, shared by scenes
    packs = {"character": {"points": 0.0, "state": "locked"}, "remembers": {"points": 0.7, "state": "scored"}}
    by = report.pack_points_by_segment45(TREE, "chat", 1.9, card, 1.2, {}, 0.0, packs)
    assert round(sum(by.values()), 4) == 1.9
    assert by["character"] > 0                           # the locked Chat pack: its points come from the ladder
    assert by["remembers"] > by["character"]
    code = report.pack_points_by_segment45(TREE, "code", 1.5, {"work": {}}, 0.0,
                                          {"work/c5_tools": {"delta": 0.5}}, 1.0, None)
    assert code == {"writes": 0.0, "fixes": 1.0, "tools": 0.5}
