"""Routed 4.5 board display fixes (Admin B, 2026-11-14), found by Admin D rendering the board
on real routed rows: the suite identity, 4.5 Notes instead of the 4.1 Notes/Efficiency/Room,
the whole run's cost, rows on an older stamp, one Overall convention (/20), pack-only parts
as pack lines, and the stage of a cell. Display only: no number changes here."""
from __future__ import annotations

from crucibleforge import report


def _prof():
    return {
        "name": "bench45", "release_seed": "4.5.0", "composition": "main-routed",
        "routing": {"calibration": "x.json"},
        "cases": {"perf": ["P1"], "tooluse": ["TZ16"]},
        "chains": {"work/bk_a": {}, "work/bk_u": {}, "chat/bk_s": {}},
        "scores": {
            "work": {"label": "Coding", "parts": {
                "bk_a": {"label": "A", "max": 5, "scoring": ["coding"], "cases": []},
                "bk_t": {"label": "Tools", "max": 1, "scoring": ["tooluse"]},
                "bk_u": {"label": "Works out unknown systems", "max": 0, "scoring": ["coding"], "cases": []}}},
            "chat": {"label": "Chat", "parts": {
                "bk_s": {"label": "S", "max": 5, "scoring": ["rpdeep"], "cases": []}}},
            "nsfw": {"label": "NSFW", "in_total": False, "parts": {
                "rp": {"label": "RP", "max": 2, "scoring": ["nsfw"], "cases": ["N1"]}}}}}


_ROUTE = {"rule": "score", "theta": 1.0874, "se": 1.0684, "locator_units": 4, "unlock": True,
          "modules": [{"name": "mid", "share": "half"}, {"name": "top", "share": "half"}],
          "module": "mid", "share": "half"}


def _cs(ended, units, mx=5.0, points=None, mean=None, stale=0):
    s = {"ended": ended, "units": units, "max": mx, "points": points, "mean": mean, "complete": ended == units,
         "elements": ended, "mean_level_held": None, "se_points": None, "se_unit": "element", "icc": None,
         "margin_90": None, "turns": ended, "tokens": {"prompt": 0, "thinking": 0, "completion": 0},
         "tokens_per_element": None, "cost_usd": None}
    if stale:
        s["stale_units"] = stale
    return s


# ---------------------------------------------------------------- 1 suite identity

def test_suite_of_a_routed_run_is_the_45_profile_not_the_41_cases_stamp():
    meta = {"profile": "bench45", "composition": "main-routed", "bench_revision": "4.1.0+3d9be8cc"}
    assert report.suite45_id(_prof(), meta) == "4.5.0 bench45/main-routed"
    s = {"suites": ["4.1.0"], "suite45": report.suite45_id(_prof(), meta), "era": {"old": False}}
    assert report.suite_cell(s) == "4.5.0 bench45/main-routed"
    assert report.suite_cell({"suites": ["4.1.0"], "era": {"old": False}}) == "4.1.0"     # 4.1 unchanged


def test_header_names_the_45_suite_on_a_routed_board():
    head = report.suite45_head(_prof(), "4.1.0+3d9be8cc")
    assert head.startswith("suite 4.5.0 (profile bench45, composition main-routed")
    assert "flat cases 4.1.0+3d9be8cc" in head


# ---------------------------------------------------------------- 2 notes: route + units

def test_routed_notes_say_the_route_and_units_never_partial_cases():
    s = {"route45": _ROUTE, "chain_stats": {"work/bk_a": _cs(3, 9), "chat/bk_s": _cs(10, 10)},
         "coverage": {"notes": ["partial (5/104 cases)", "conformance WARN: c7"]}}
    n = report.notes45_cell(s)
    assert "partial (" not in n and "conformance WARN: c7" in n
    assert "route: locator (4 units) -> mid (half) + top (half), packs unlocked" in n
    assert "units ended 13/19" in n


def test_no_route_recorded_is_said():
    s = {"route45": None, "chain_stats": {"work/bk_a": _cs(0, 9)}, "coverage": {"notes": []}}
    assert report.notes45_cell(s).startswith("route: none recorded; units ended 0/9")


def test_routed_scorecard_drops_efficiency_and_room():
    hdr = report.scorecard45_header(["Coding (/10)", "Chat (/10)"], "Overall (/20)", has_cost=True)
    assert "Efficiency" not in hdr and "Room" not in hdr      # Class / Lean are back (lead 2026-11-14)
    assert hdr[:3] == ["#", "Model", "Overall (/20)"] and "Run cost" in hdr and hdr[-1] == "Notes"


# ---------------------------------------------------------------- 3 whole-run cost

def test_routed_cost_cell_is_the_whole_run_and_labels_the_counted_rows():
    s = {"cost_main_usd": 0.08, "cost_basis": "billed", "cost_all_usd": 0.3042, "cost_all_basis": "billed"}
    assert report.cost45_cell(s) == "$0.30 (counted rows $0.08)"
    s2 = {"cost_main_usd": 0.30, "cost_basis": "billed", "cost_all_usd": 0.30, "cost_all_basis": "billed"}
    assert report.cost45_cell(s2) == "$0.30"
    assert report.cost45_cell({"cost_main_usd": None}) == "—"


def test_all_rows_cost_counts_rows_off_the_board():
    rows = [{"case_id": "a", "chain": {"part": "work/bk_a"}, "cost_usd": 0.25, "profile": "bench45",
             "metrics": {"prompt_tokens": 10, "completion_tokens": 20}},
            {"case_id": "b", "chain": {"part": "work/bk_a"}, "cost_usd": 0.05, "profile": "bench45",
             "metrics": {"prompt_tokens": 10, "completion_tokens": 20}},
            {"case_id": "c", "cost_usd": 9.0, "profile": "bench",
             "metrics": {"prompt_tokens": 10, "completion_tokens": 20}}]
    usd, basis = report.all_rows_cost("m", rows, None, None, _prof())
    assert abs(usd - 0.30) < 1e-9 and basis == "billed"


# ---------------------------------------------------------------- 4 older stamp

def test_stale_units_counts_units_whose_rows_are_not_current():
    idx = {"U1": {}, "U2": {}, "U3": {}}
    all_rows = [{"chain": {"id": "U1", "part": "work/bk_a"}}, {"chain": {"id": "U1", "part": "work/bk_a"}},
                {"chain": {"id": "U2", "part": "work/bk_a"}}, {"chain": {"id": "U3", "part": "work/bk_a"}},
                {"chain": {"id": "ZZ", "part": "work/bk_a"}}]
    board = [{"chain": {"id": "U3", "part": "work/bk_a"}}]
    assert report.stale_units(all_rows, board, idx) == {"work/bk_a": 2}


def test_units_ended_cell_says_older_stamp_not_not_run():
    cells = report.chain_cells45(_cs(0, 9, stale=9), routed=True)
    assert cells[1] == "0/9 · 9 on an older stamp, not counted"
    assert report.chain_cells45(_cs(0, 9), routed=True)[1] == "0/9"


# ---------------------------------------------------------------- 5 one Overall convention

def test_generated_items_summary_uses_the_scorecard_convention_not_generated_maxima():
    hl = {"units": "scale45", "S": {"work": 7.9, "chat": 8.5, "nsfw": None, "overall": 16.4},
          "m": {"work": 2.5, "chat": 0.8, "overall": 2.6}}
    stats = {"m": {"headline": hl, "chain_stats": {"work/bk_a": _cs(9, 9, points=3.8),
                                                   "chat/bk_s": _cs(10, 10, points=4.2)}}}
    prof = _prof()
    prof["chains"]["nsfw/rp"] = {}                    # NSFW with a cell: shown /10, never in Overall
    out = "\n".join(report.render_board45(["m"], stats, prof))
    assert "/ 19" not in out and "/ 9 |" not in out.split("| Model | Section")[-1]
    assert "| m | Overall | 16.4 / 20 | 2.6 |" in out
    # fix round 2026-11-18 (R5): the 4.5.0 board names the Coding section "Coding (small tasks)"
    assert "| m | Coding (small tasks) | 7.9 / 10 | 2.5 |" in out
    assert "| m | NSFW (separate, not in Overall) | - / 10 | - |" in out


# ---------------------------------------------------------------- 6 pack-only part

def test_a_pack_only_part_prints_as_a_pack_line_not_a_zero_point_section():
    s = _cs(2, 2, mx=0.0, points=0.0, mean=0.84)
    s["se_points"], s["margin_90"] = 0.0, 0.0
    cells = report.chain_cells45(s, routed=True)
    assert cells[0] == "pack 84% · n=2 (pack, not in the total)"
    assert "0.00 / 0" not in cells and cells[4] == "-" and cells[6] == "-"


def test_maxima_footnote_marks_the_pack_part():
    t = report.maxima45_footnote(report.board_defs(_prof()))
    assert "Works out unknown systems (pack, not in the total)" in t
    assert "Works out unknown systems 0" not in t


# ---------------------------------------------------------------- 7 stage

def test_cells_table_shows_the_rows_stage_when_they_carry_it():
    per = {"fam:L1": {"section": "work", "units": 2, "mean": 0.5, "pack": False, "rule": "zero",
                      "ran_out_units": 0, "ran_out_share": 0.0, "not_measured": False, "effort": False,
                      "stages": ["locator"]},
           "tr:L8": {"section": "work", "units": 2, "mean": 0.6, "pack": True, "rule": "zero",
                     "ran_out_units": 0, "ran_out_share": 0.0, "not_measured": False, "effort": False,
                     "stages": []}}
    st = {"m": {"scale45": {"cells": per}, "headline": {"units": "scale45"}}}
    out = "\n".join(report.cells45_tables(["m"], st, _prof()))
    assert "| Cell | Section | Stage | Ran-out rule | m |" in out
    assert "| fam:L1 | work | locator |" in out and "| tr:L8 (pack) | work | module (pack) |" not in out
    assert "| tr:L8 (pack) | work | - |" in out


def test_cells_table_has_no_stage_column_when_no_row_carries_it():
    per = {"fam:L1": {"section": "work", "units": 2, "mean": 0.5, "pack": False, "rule": "zero",
                      "ran_out_units": 0, "ran_out_share": 0.0, "not_measured": False, "effort": False,
                      "stages": []}}
    out = "\n".join(report.cells45_tables(["m"], {"m": {"scale45": {"cells": per}}}, _prof()))
    assert "| Cell | Section | Ran-out rule | m |" in out and "Stage" not in out.split("\n\n")[1]


def test_cells45_collects_the_stage_off_the_rows():
    from crucibleforge import scale
    us = [{"id": "U-r1-a1", "cell": "fam:L1", "kind": "chain", "section": "work", "rule": "zero",
           "ran_out": 0, "n": 1, "cluster": "U", "x": 1.0}]
    rows = [{"chain": {"id": "U", "rung": 1, "attempt": 1, "family": "fam", "level": 1}, "stage": "locator"}]
    cal = {"cells": {"fam:L1": {"b": 0.0, "section": "work"}}}
    cells = report._cells45(_prof(), rows, us, scale.unit_ran_out(us), set(), cal)
    assert cells["fam:L1"]["stages"] == ["locator"]


# ---------------------------------------------------------------- lead item 3: empty NSFW section

def test_no_nsfw_chain_cell_no_nsfw_section_on_the_routed_board():
    prof = _prof()
    prof["scores"]["nsfw"]["parts"]["rp"]["cases"] = ["TZ16"]   # a 4.1 part that board_defs keeps
    assert "nsfw" in [d["key"] for d in report.board_defs(prof) if d.get("parts")]
    assert [d["key"] for d in report.shown_defs45(prof)] == ["work", "chat"]
    hl = {"units": "scale45", "S": {"work": 7.9, "chat": 8.5, "nsfw": None, "overall": 16.4}, "m": {}}
    out = "\n".join(report.render_board45(["m"], {"m": {"headline": hl, "chain_stats": {
        "work/bk_a": _cs(9, 9, points=3.8)}}}, prof))
    assert "NSFW" not in out
    prof["chains"]["nsfw/rp"] = {}                               # a cell: shown /10, never added
    assert [d["key"] for d in report.shown_defs45(prof)] == ["work", "chat", "nsfw"]


# ---------------------------------------------------------------- lead item 4: report.html / board.json

def test_html_row_of_a_routed_run_carries_the_45_cells():
    s = {"scorecard": {"chat": None, "coding": None, "total": None, "tok_per_s": 1.0, "score_defs": [],
                       "weights": {}, "components": {}},
         "headline": {"units": "scale45", "S": {"work": 7.9, "chat": 8.5, "overall": 16.4}, "m": {}},
         "meta": {"provider": "deepseek", "provider_type": "openai"},
         "coverage": {"notes": ["partial (5/104 cases)"], "status": "partial (5/104 cases)"},
         "suite45": "4.5.0 bench45/main-routed", "suites": ["4.1.0"], "route45": _ROUTE,
         "chain_stats": {"work/bk_a": _cs(3, 9, stale=6)}, "room": {"total": None},
         "cost_main_usd": 0.08, "cost_basis": "billed", "cost_all_usd": 0.30, "cost_all_basis": "billed"}
    r = report.html_rows(["m"], {"m": s}, None)[0]
    assert r["suite"] == "4.5.0 bench45/main-routed"
    assert "partial (" not in r["notes"] and r["notes"].startswith("route: locator (4 units)")
    assert "partial (" not in (r["coverage"] or "") and "units ended 3/9" in r["coverage"]
    assert r["room_cell"] is None and r["room"] is None
    assert r["cost_usd"] == 0.30 and r["cost_counted_usd"] == 0.08


def test_routed_html_has_no_41_tier_legend_and_no_empty_nsfw_column(monkeypatch):
    prof = _prof()
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: prof)
    monkeypatch.setattr(report, "_cards", lambda *a, **k: {})
    monkeypatch.setattr(report, "scorecard_footnote", lambda *a, **k: "TIER-LEGEND-4.1")
    seen = {}
    import crucibleforge.templates.board as tb
    monkeypatch.setattr(tb, "render_html", lambda rows, **kw: seen.update(kw) or "")
    monkeypatch.setattr(report, "html_rows", lambda *a, **k: [])
    defs = [{"key": k, "group": k, "label": k, "parts": [{"key": "p", "label": "p"}]} for k in ("work", "chat", "nsfw")]
    st = {"m": {"suite45": "4.5.0 bench45/main-routed", "scorecard": {"score_defs": defs}, "coverage": {}}}
    report.render_report_html(["m"], st, None)
    assert not any("TIER-LEGEND-4.1" in f for f in seen["footer"])
    assert [c["key"] for c in seen["scores"]] == ["work", "chat"]
    assert any(f.startswith("suite 4.5.0 (profile bench45") for f in seen["footer"][-1:]) or \
        "suite 4.5.0" in seen["footer"][-1]
