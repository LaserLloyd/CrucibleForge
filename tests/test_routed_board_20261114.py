"""The routed 4.5 board (D's routed-board note, 2026-11-14): 4.1 parts with no cell are not
listed, the headline is the section scales ± 90%, pack-only parts that ran are shown (not
pending), the route decides, never a share gate, and the run cost counts the chain rows."""
from __future__ import annotations

import pytest

from crucibleforge import report, tiers


def _prof():
    return {
        "name": "t", "routing": {"calibration": "x.json"},
        "cases": {"perf": ["P1"], "tooluse": ["TZ16"]},
        "chains": {"work/bk_a": {}, "work/bk_u": {}, "chat/bk_s": {}},
        "scores": {
            "work": {"label": "Coding", "parts": {
                "frontier": {"label": "Frontier", "max": 0, "scoring": ["coding"]},
                "code": {"label": "Code", "max": 2, "scoring": ["coding"]},
                "bk_a": {"label": "A", "max": 5, "scoring": ["coding"], "cases": []},
                "bk_t": {"label": "Tools", "max": 1, "scoring": ["tooluse"]},
                "bk_u": {"label": "U", "max": 0, "scoring": ["coding"], "cases": []}}},
            "chat": {"label": "Chat", "parts": {
                "conv": {"label": "Conv", "max": 3, "scoring": ["chat_checks"], "cases": ["RD1"]},
                "bk_s": {"label": "S", "max": 5, "scoring": ["rpdeep"], "cases": []}}},
            "nsfw": {"label": "NSFW", "in_total": False, "parts": {
                "rp": {"label": "RP", "max": 2, "scoring": ["nsfw"], "cases": ["N1"]}}}}}


def test_board_defs_keep_only_parts_with_cells():
    prof = _prof()
    got = {d["key"]: [p["key"] for p in d["parts"]] for d in report.board_defs(prof)}
    assert got == {"work": ["bk_a", "bk_t", "bk_u"], "chat": ["bk_s"], "nsfw": []}
    unrouted = {**prof, "routing": None}
    assert report.board_defs(unrouted) == tiers.scores(unrouted)       # 4.1 / unrouted unchanged


def _sec(points, se, psi):
    run = {"theta": 0.0, "se": se, "psi": psi, "slope": 1.0, "points": points, "se_method": "cluster"}
    return {"points": points, "se_points": se, "margin_90": 1.645 * se, "_run": run}


def test_headline_is_the_section_scales_with_90pct_margins():
    hl = {"S": {"work": 17.8, "chat": 9.1, "overall": 26.9}, "m": {}}
    report._apply_scale45(hl, {"sections": {"work": _sec(78.7, 15.2, {"a": 1.0}),
                                            "chat": _sec(85.2, 4.9, {"b": 1.0})}}, _prof())
    # the 4.x convention (headline.overall: sum): each section out of 10, Overall the sum on /20
    assert hl["S"]["work"] == 7.9 and hl["m"]["work"] == pytest.approx(1.645 * 1.52)
    assert hl["S"]["chat"] == 8.5 and hl["S"]["overall"] == pytest.approx(16.4, abs=0.05)
    assert hl["m"]["overall"] == pytest.approx(1.645 * (1.52 ** 2 + 0.49 ** 2) ** 0.5)
    assert hl["S"]["nsfw"] is None and hl["units"] == "scale45"
    # a section with no scale yet: no Overall, never the 4.1 number
    hl2 = {"S": {"overall": 26.9}, "m": {}}
    report._apply_scale45(hl2, {"sections": {"work": _sec(78.7, 15.2, {"a": 1.0})}}, _prof())
    assert hl2["S"]["overall"] is None and hl2["sort"] is None


def test_rank45_ties_only_when_every_section_ties():
    prof = _prof()
    sc = {"a": {"work": _sec(80, 5, {"u1": 0.1, "u2": -0.1}), "chat": _sec(80, 5, {"u3": 0.1, "u4": -0.1})},
          "b": {"work": _sec(79, 5, {"u5": 0.1, "u6": -0.1}), "chat": _sec(78, 5, {"u7": 0.1, "u8": -0.1})},
          "c": {"work": _sec(20, 1, {"u9": 0.1, "u0": -0.1}), "chat": _sec(20, 1, {"ua": 0.1, "ub": -0.1})}}
    stats = {}
    for l, secs in sc.items():
        hl = {"S": {}, "m": {}}
        report._apply_scale45(hl, {"sections": secs}, prof)
        stats[l] = {"headline": hl, "scale45": {"sections": secs}}
    assert report._rank45(["a", "b", "c"], stats, prof) == {"a": "1", "b": "1=", "c": "3"}


def test_routed_columns_show_the_route_never_a_share_gate():
    card = {"subscores": {"work": {"standard": 8.7, "standard_max": 10.0},
                          "chat": {"standard": 9.1, "standard_max": 10.0}}}
    defs = report.board_defs(_prof())
    cols = report._routed_columns(card, defs, {"module": "top", "share": "half", "unlock": True})
    assert set(cols) == {"work", "chat"}
    assert cols["work"]["unlocked"] is True and cols["work"]["unlocked_by"] == "route"
    assert "unlock_gate" not in cols["work"] and cols["work"]["route_module"] == "top"
    assert report._routed_columns(card, defs, None)["chat"]["state"] == "no route yet"


def test_run_cost_counts_the_chain_rows():
    st = {"cost_usd": 0.0008, "cost_main_usd": 0.0008, "cost_separate_usd": 0.0, "cost_basis": "billed",
          "cost_estimated_rows": 0, "cost_unknown_rows": 0}
    rows = [{"case_id": "FLAT-a", "chain": {"part": "work/bk_a"}, "cost_usd": 0.25,
             "metrics": {"prompt_tokens": 10, "completion_tokens": 20}},
            {"case_id": "FLAT-n", "chain": {"part": "nsfw/rp"}, "cost_usd": 0.05,
             "metrics": {"prompt_tokens": 10, "completion_tokens": 20}}]
    report._add_chain_cost(st, "m", rows, None, None, _prof())
    assert st["cost_main_usd"] == pytest.approx(0.2508) and st["cost_separate_usd"] == pytest.approx(0.05)
    assert st["cost_usd"] == pytest.approx(0.3008) and st["cost_chains_usd"] == pytest.approx(0.30)
    assert report.cost_cell(st) == "$0.25"


def test_points_cell_shows_a_pack_only_part_apart():
    d = report.board_defs(_prof())[0]
    hl = {"units": "scale45", "S": {"work": 78.7},
          "pts": {"work/bk_a": 3.8, "work/bk_t": 1.0, "work/bk_u": 0.0}, "m": {},
          "pack_mean": {"work/bk_u": 0.84}}
    cell = report.points_cell(hl, d)
    assert "U packs 84% (scored apart)" in cell and "pending" not in cell and not cell.startswith("78.7")


# ------------------------------------------------------------------ ran-out per cell (lead 2026-11-14, Q1)

from crucibleforge import scale  # noqa: E402

_CAL = {"cells": {"fam:L1": {"b": 0.0, "section": "work"}, "fam:L2": {"b": 0.5, "section": "work"},
                  "zz:L1": {"b": -0.5, "section": "work"}}}


def _units(cell, n, ran_out, rule, x=0.8):
    """n units on a cell; the first ``ran_out`` of them ran out on one of their elements."""
    out = []
    for i in range(n):
        ro = int(i < ran_out)
        u = {"id": f"{cell}-{i}", "cell": cell, "kind": "chain", "section": "work", "rule": rule,
             "ran_out": ro, "n": 3, "cluster": f"{cell}-{i}"}
        if ro and rule == "exclude" and i % 2:
            u["excluded"] = True                  # a unit cut everywhere: nothing left to score
        else:
            u["x"] = x
        out.append(u)
    return out


def _run(us):
    nm = scale.mark_not_measured(us)
    return nm, scale.score_run(us, _CAL)["work"]


def test_an_exclude_cell_above_25pct_ran_out_units_leaves_the_scale_and_the_model_keeps_a_score():
    us = _units("fam:L1", 10, 3, "exclude", x=1.0) + _units("fam:L2", 10, 0, "exclude", x=0.6) \
        + _units("zz:L1", 10, 0, "zero", x=0.7)
    nm, a = _run(us)
    assert nm == ["fam:L1"] and all(u.get("not_measured") for u in us if u["cell"] == "fam:L1")
    assert a["points"] is not None and a["units"] == 20          # the other two cells place the model


def test_at_20pct_the_cell_is_kept():
    us = _units("fam:L1", 10, 2, "exclude") + _units("fam:L2", 10, 0, "exclude", x=0.6)
    nm, a = _run(us)
    assert nm == [] and not any(u.get("not_measured") for u in us)


def test_a_zero_rule_cell_is_untouched():
    us = _units("zz:L1", 10, 6, "zero", x=0.2) + _units("fam:L2", 10, 0, "exclude", x=0.6)
    nm, _a = _run(us)
    assert nm == [] and not any(u.get("not_measured") for u in us)


def test_the_share_is_on_units_not_elements():
    # one cut generation marks several checks of one unit: 1 unit of 4 (25%), not 3 elements of 12
    us = _units("fam:L1", 4, 1, "exclude")
    us[0]["ran_out"] = 3
    assert scale.unit_ran_out(us)["fam:L1"]["share"] == 0.25 and scale.mark_not_measured(us) == []
    assert scale.ran_out_report(us)["fam:L1"]["ran_out"] == 3     # the element count stays as it was


def _st(cells, partly=0):
    return {"scale45": {"cells": cells},
            "headline": {"units": "scale45", "scale45": {"work": {"not_measured": ["c"] * partly}}}}


def test_cells_table_prints_units_ran_out_and_not_measured():
    a = {"fam:L1": {"section": "work", "units": 4, "mean": None, "pack": False, "rule": "exclude",
                    "ran_out_units": 2, "ran_out_share": 0.5, "not_measured": True, "effort": False},
         "tr:L8": {"section": "work", "units": 4, "mean": 0.62, "pack": True, "rule": "zero",
                   "ran_out_units": 0, "ran_out_share": 0.0, "not_measured": False, "effort": False}}
    out = "\n".join(report.cells45_tables(["m"], {"m": _st(a, 1)}, _prof()))
    assert "not measured · n=4 · ran out 2/4 units" in out
    assert "tr:L8 (pack)" in out and "62% · n=4" in out             # a 4-seed pack cell shows its n
    assert "Effort-ordered" not in out                                # no profile key: no effort line
    d = {"key": "work"}
    assert report._partly_tag(_st(a, 1)["headline"], d) == " (partly measured: 1 cell)"


def test_effort_line_only_from_the_profile_key():
    assert report.effort_cells(_prof()) == []
    prof = {**_prof(), "routing": {"calibration": "x.json", "effort_cells": ["planted_opt:L4"]}}
    assert report.effort_cells(prof) == ["planted_opt:L4"]
    c = {"planted_opt:L4": {"section": "work", "units": 4, "mean": 0.5, "pack": True, "rule": "exclude",
                            "ran_out_units": 0, "ran_out_share": 0.0, "not_measured": False, "effort": True,
                            "tokens_per_unit": 14200.0}}
    out = "\n".join(report.cells45_tables(["m"], {"m": _st(c)}, prof))
    assert "Effort-ordered cells (outside every sum)" in out and "50% at 14.2k output tok/unit" in out


def test_cells45_reads_effort_tokens_off_the_rows():
    prof = {**_prof(), "routing": {"calibration": "x.json", "effort_cells": ["fam:L1"]}}
    us = _units("fam:L1", 2, 0, "exclude")
    us[0]["id"], us[1]["id"] = "U-r1-a1", "U-r1-a2"
    rows = [{"chain": {"id": "U", "rung": 1, "attempt": a, "family": "fam", "level": 1},
             "metrics": {"completion_tokens": t}, "bench_run_id": "b", "repeat": 0}
            for a, t in ((1, 1000), (1, 500), (2, 3000))]
    cells = report._cells45(prof, rows, us, scale.unit_ran_out(us), set(), _CAL)
    assert cells["fam:L1"]["effort"] and cells["fam:L1"]["tokens_per_unit"] == pytest.approx(2250.0)


def test_nsfw_never_enters_the_routed_overall():
    """An in_total: false section with a scale of its own (NSFW, /10 on its own page) is shown but
    never added: Overall stays the sum of Coding + Chat."""
    hl = {"S": {}, "m": {}}
    secs = {"work": _sec(70.0, 10.0, {"a": 1.0}), "chat": _sec(80.0, 10.0, {"b": 1.0}),
            "nsfw": _sec(90.0, 10.0, {"c": 1.0})}
    prof = _prof()
    prof["chains"]["nsfw/rp"] = {}
    assert [d["key"] for d in report._scale_sections(prof)] == ["work", "chat", "nsfw"]
    report._apply_scale45(hl, {"sections": secs}, prof)
    assert hl["S"]["nsfw"] == 9.0                                  # shown, on its own
    assert hl["S"]["overall"] == pytest.approx(15.0)
    assert hl["m"]["overall"] == pytest.approx(1.645 * (1.0 + 1.0) ** 0.5)


# ------------------------------------------------------------------ routing.effort_cells (validated at load)

def _routed(monkeypatch, effort=None):
    from crucibleforge import chains, profiles
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "main-routed")
    prof = profiles.load_profile("bench45")
    if effort is not None:
        prof = {**prof, "routing": {**prof["routing"], "effort_cells": effort}}
    return prof, sorted(routing_cells(prof))


def routing_cells(prof):
    from crucibleforge import routing
    return routing._cells(prof)


def test_effort_cells_a_good_list_is_kept(monkeypatch):
    from crucibleforge import routing
    prof, cells = _routed(monkeypatch)
    pick = [cells[0], cells[-1].rsplit(":x", 1)[0]]
    prof, _ = _routed(monkeypatch, pick)
    assert routing.spec(prof)["effort_cells"] == pick
    assert report.effort_cells(prof) == pick


def test_effort_cells_a_cell_not_in_the_composition_is_refused(monkeypatch):
    from crucibleforge import routing
    from crucibleforge.config import ConfigError
    prof, _ = _routed(monkeypatch, ["nosuchfamily:L3"])
    with pytest.raises(ConfigError, match="no set of this composition makes cell 'nosuchfamily:L3'"):
        routing.spec(prof)


@pytest.mark.parametrize("bad", [["planted_opt-L4"], ["planted_opt:4"], ["planted_opt:L4:4"], "planted_opt:L4",
                                 [3]])
def test_effort_cells_a_malformed_name_is_refused(monkeypatch, bad):
    from crucibleforge import routing
    from crucibleforge.config import ConfigError
    prof, _ = _routed(monkeypatch, bad)
    with pytest.raises(ConfigError, match="effort_cells: a list of"):
        routing.spec(prof)


def test_without_the_key_there_is_no_effort_line(monkeypatch):
    from crucibleforge import routing
    prof, _ = _routed(monkeypatch)
    assert "effort_cells" not in prof["routing"]
    assert routing.spec(prof)["effort_cells"] == [] and report.effort_cells(prof) == []
    out = "\n".join(report.cells45_tables(["m"], {"m": _st({})}, prof))
    assert "Effort-ordered" not in out
