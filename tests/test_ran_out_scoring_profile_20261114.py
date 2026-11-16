"""The ran-out rule comes from the SCORING profile (lead 2026-11-14): a stored row is scored under
the rule the profile scoring it declares, never the rule of the composition it was made under
(``ran_out`` is not in the chain revision, so an old row stays current). The stored block's value
is only the fallback for a cell the profile does not name; two sets that share a cell with
different rules are refused at load. And (G2 ruling) a routed chain part's board share and its
pack gate are the mean of its Main units' weighted scale share. And (D's 8B route-only pass) a
routed profile whose calibration file lacks a cell the scale needs is refused at load."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from crucibleforge import chains, config, profiles, report, routing, scale


def _row(rule, outcomes, weights=None, cid="FLAT-implied1x3_L1-001", part="work/bk_implied"):
    els = [{"id": f"{cid}-i1-c{i}", "check": f"c{i}", "score": 1.0 if oc == "pass" else 0.0,
            "weight": (weights or [1.0] * len(outcomes))[i], "outcome": oc} for i, oc in enumerate(outcomes)]
    blk = {"id": cid, "family": "implied", "level": 1, "kind": "multi", "items": [1, 2, 3], "rung": 1,
           "attempt": 1, "part": part, "ladder": "implied1x3_L1", "score": 0.5, "passed": False,
           "outcome": "ran out", "elements": els, "pass_at": 0.5}
    if rule is not None:
        blk["ran_out"] = rule
    return {"case_id": cid, "chain": blk, "bench_run_id": "r1", "repeat": 1, "finish_reason": "stop"}


# 2 passes, 1 wrong, 3 ran out: zero -> 2/6, exclude -> 2/3
OUT = ["pass", "pass", "wrong", "ran out", "ran out", "ran out"]
CELL = "implied:L1:x3"


def test_a_row_made_under_zero_is_scored_exclude_under_an_exclude_profile():
    r = _row(None, OUT)
    assert scale.units([r])[0]["x"] == pytest.approx(2 / 6)                     # stored: zero
    u = scale.units([r], ran_out={CELL: "exclude"})[0]
    assert u["rule"] == "exclude" and u["x"] == pytest.approx(2 / 3)


def test_a_row_made_under_exclude_is_scored_zero_under_a_zero_profile():
    r = _row("exclude", OUT)
    assert scale.units([r])[0]["x"] == pytest.approx(2 / 3)                     # stored: exclude
    u = scale.units([r], ran_out={CELL: "zero"})[0]
    assert u["rule"] == "zero" and u["x"] == pytest.approx(2 / 6)
    # the not-measured basis and the ran-out report follow the same rule
    assert scale.ran_out_report([u])[CELL]["elements"] == 6
    assert scale.ran_out_report(scale.units([r]))[CELL]["elements"] == 6


def test_a_cell_the_profile_does_not_name_keeps_the_stored_rule():
    r = _row("exclude", OUT)
    u = scale.units([r], ran_out={"pytrap:L1": "zero"})[0]
    assert u["rule"] == "exclude" and u["x"] == pytest.approx(2 / 3)


def test_elemstats_reads_the_scoring_profiles_rule_when_given():
    r = _row(None, OUT)
    assert {e["rule"] for e in __import__("crucibleforge.elemstats").elemstats.elements([r]).values()} == {"zero"}
    from crucibleforge import elemstats
    assert {e["rule"] for e in elemstats.elements([r], {CELL: "exclude"}).values()} == {"exclude"}


@pytest.fixture
def main_v1(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "main-v1")
    return profiles.load_profile("bench45")


def test_the_rules_are_the_profiles_own_and_an_undeclared_set_is_zero(main_v1):
    rules = scale.ran_out_rules(main_v1)
    assert rules["pytrap:L1"] == "exclude" and rules["implied:L1:x3"] == "zero"
    # a stored cell name and the profile's agree (scale.cell_of on a materialised block)
    c = next(x for x in chains.chain_cases(main_v1) if x["id"].startswith("FLAT-implied1x3_L1"))
    assert scale.cell_of(chains.materialize(chains.instance(c, 1, 1))["chain"]) in rules


def test_two_sets_sharing_a_cell_with_different_rules_are_refused_at_load(main_v1):
    p = copy.deepcopy(main_v1)
    p["chains"]["work/bk_trace"]["flat"].append(
        {"name": "pytrap_b", "family": "pytrap", "levels": {1: 1}, "element": "check", "element_weight": "check",
         "average": "unit", "ceiling": 32768, "ran_out": "zero", "concept": "C2", "band": "mid"})
    with pytest.raises(config.ConfigError, match=r"cell pytrap:L1: set .* says ran_out exclude, set pytrap_b_L1 says zero"):
        scale.ran_out_rules(p)
    with pytest.raises(config.ConfigError, match="pytrap:L1"):
        profiles.apply_profile(p, {"defaults": {}})


# --- the part share: the weighted unit share, read by the board line and the pack gate --------------

def test_a_units_share_is_its_weighted_check_mean_not_the_plain_element_mean():
    # two checks pass (weight 0.25 each), two fail (weight 1 each): plain .5, weighted .2
    r = _row(None, ["pass", "pass", "wrong", "wrong"], weights=[0.25, 0.25, 1.0, 1.0])
    assert scale.units([r])[0]["x"] == pytest.approx(0.5 / 2.5)


def test_parts45_is_the_mean_of_the_parts_main_units():
    us = [{"kind": "chain", "part": "work/a", "cell": "f:L1", "section": "work", "x": 0.9},
          {"kind": "chain", "part": "work/a", "cell": "f:L1", "section": "work", "x": 0.7},
          {"kind": "chain", "part": "work/a", "cell": "f:L1", "section": "work", "excluded": True},
          {"kind": "chain", "part": "work/b", "cell": "g:L9", "section": "work", "x": 1.0}]   # not a Main cell
    cal = {"cells": {"f:L1": {"b": 0.0}, "g:L9": {"b": 1.0}}, "form": {"work": ["f:L1"]}}
    out = report._parts45(us, cal)
    assert out == {"work/a": {"share": pytest.approx(0.8), "units": 2, "excluded_units": 1}}


_spec = importlib.util.spec_from_file_location("_e2e", Path(__file__).with_name("test_routed_ladder_e2e_20261114.py"))
_e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_e2e)
_toys = _e2e._toys


def _stats(tmp_path, monkeypatch):
    pol = lambda lv, cid: "nope" if "-ax-" in cid or cid.startswith("PKA") else "PASS"   # noqa: E731
    prof, cfg, _calls, _summary, rc = _e2e._drive(tmp_path, monkeypatch, {"m": pol})
    assert rc == 0
    _, stats, _ = report.board_stats("m", cfg)
    return prof, stats["m"]


def test_the_gate_records_the_boards_part_share(tmp_path, monkeypatch):
    prof, s = _stats(tmp_path, monkeypatch)
    card = report.points_scorecard(s, {"work": {"coding": 1.0}}, prof)
    for pk in ("main", "aux"):
        d = card["ladder"]["work"][pk]
        p45 = s["scale45"]["parts"][f"work/{pk}"]["share"]
        assert round(d["std_share"], 4) == round(p45, 4)
        assert d["pack_gate"]["share"] == round(p45, 4)


@pytest.mark.parametrize("weighted, plain, opens", [(0.81, 0.79, True), (0.79, 0.81, False)])
def test_the_gate_opens_on_the_weighted_share_not_the_chain_score(tmp_path, monkeypatch, weighted, plain, opens):
    prof, s = _stats(tmp_path, monkeypatch)
    s = copy.deepcopy(s)
    s["scale45"]["parts"]["work/main"]["share"] = weighted
    s["chain_stats"]["work/main"]["share"] = plain
    d = report.points_scorecard(s, {"work": {"coding": 1.0}}, prof)["ladder"]["work"]["main"]
    assert d["pack_gate"]["opened"] is opens and d["std_share"] == weighted


# --- the routed calibration is checked at load --------------------------------------------------------

def _routed(monkeypatch, comp="main-routed"):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    return profiles.load_profile("bench45")


def _cal_without(tmp_path, prof, drop):
    sp = routing.spec(prof)
    cal, _ = routing.calibration(sp, config.ROOT_DIR)
    raw = json.loads((config.ROOT_DIR / sp["calibration"]).read_text(encoding="utf-8")) \
        if not Path(sp["calibration"]).is_absolute() else json.loads(Path(sp["calibration"]).read_text(encoding="utf-8"))
    need = routing.required_cells(prof)
    for c in need:                       # a complete table: every needed cell has a row
        raw.setdefault("cells", {}).setdefault(c, {"b": 0.0, "a": 1.0})
    for c in drop:                       # the cell, its unpacked row and its family trend all gone
        raw["cells"].pop(c, None)
        raw["cells"].pop(":".join(c.split(":")[:2]), None)
        raw.get("families", {}).pop(c.split(":")[0], None)
    p = tmp_path / "cal.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    prof = copy.deepcopy(prof)
    prof["routing"]["calibration"] = str(p)
    return prof


def test_a_complete_routed_calibration_loads(tmp_path, monkeypatch):
    prof = _cal_without(tmp_path, _routed(monkeypatch), [])
    routing.check_calibration(prof, config.ROOT_DIR)


def test_a_missing_locator_cell_is_refused_at_load_with_the_cell_named(tmp_path, monkeypatch):
    prof = _routed(monkeypatch)
    sp = routing.spec(prof)
    need = routing.required_cells(prof)
    loc = sorted(c for c, s in need.items() if set(s) & set(sp["locator"]))[0]
    bad = _cal_without(tmp_path, prof, [loc])
    with pytest.raises(config.ConfigError, match=rf"{loc} \(locator"):
        routing.check_calibration(bad, config.ROOT_DIR)
    with pytest.raises(config.ConfigError, match=loc):
        profiles.apply_profile(bad, {"defaults": {}})


def test_a_missing_module_cell_is_refused_at_load(tmp_path, monkeypatch):
    prof = _routed(monkeypatch)
    sp = routing.spec(prof)
    need = routing.required_cells(prof)
    mod = sorted(c for c, s in need.items() if not set(s) & set(sp["locator"]))[0]
    with pytest.raises(config.ConfigError, match=rf"{mod} \(module"):
        routing.check_calibration(_cal_without(tmp_path, prof, [mod]), config.ROOT_DIR)


def test_a_measured_zero_point_cell_is_not_required(monkeypatch):
    prof = _routed(monkeypatch)
    sp = routing.spec(prof)
    need = routing.required_cells(prof)
    scores = prof["scores"]
    zero = set()
    for sec in chains.sections(prof):
        s_, _, pk = sec["part"].partition("/")
        if float((scores[s_]["parts"][pk] or {}).get("max", 0) or 0) == 0:
            zero |= {fs["name"] for fs in sec["flat"]} | {lad["name"] for lad in sec["ladders"]}
    zero -= set(sp["locator"])
    assert zero, "main-routed has measured 0-point sets"
    assert not {s for ss in need.values() for s in ss} & zero


# --- G2's reading of score_run (lead 2026-11-14): the form decides Main; calibration decides +packs ---

def _u(cell, x, i):
    return {"id": f"{cell}-{i}", "cell": cell, "kind": "chain", "section": "work", "part": "work/p",
            "x": x, "n": 1, "cluster": f"{cell}-{i}", "rule": "zero", "ran_out": 0}


def _cal(extra=None):
    cal = {"cells": {"f:L1": {"b": 0.0}, "g:L2": {"b": 0.5}, **(extra or {})},
           "form": {"work": {"f:L1": 1.0, "g:L2": 0.0}}}
    return scale.normalise(cal)


def test_a_form_cell_at_weight_0_enters_the_main_ability_not_packs():
    us = [_u("f:L1", 0.5, i) for i in range(3)] + [_u("g:L2", 1.0, i) for i in range(3)]
    with_g = scale.score_run(us, _cal())
    without_g = scale.score_run(us[:3], _cal())
    assert "work+packs" not in with_g
    assert with_g["work"]["left_out"] == {}
    assert with_g["work"]["theta"] != without_g["work"]["theta"]          # g's units moved the ability


def test_an_uncalibrated_zero_point_cell_never_enters_main_or_packs():
    us = [_u("f:L1", 0.5, i) for i in range(3)] + [_u("h:L1", 1.0, i) for i in range(3)]
    res = scale.score_run(us, _cal())
    assert "work+packs" not in res                                       # nothing calibrated outside the form
    assert res["work"]["left_out"] == {"h:L1": 3}
    assert res["work"]["theta"] == scale.score_run(us[:3], _cal())["work"]["theta"]
    # a calibrated cell outside the form IS scored apart, and the uncalibrated one still is not in it
    us2 = us + [_u("k:L4", 1.0, i) for i in range(3)]
    res2 = scale.score_run(us2, _cal({"k:L4": {"b": 1.0}}))
    assert "work+packs" in res2 and res2["work+packs"]["uncalibrated_cells"] == ["h:L1"]
    assert res2["work"]["theta"] == res["work"]["theta"]


def test_lint_lists_every_routed_composition_and_warns_without_refusing(monkeypatch):
    monkeypatch.delenv(profiles.COMPOSITION_ENV, raising=False)
    res = dict(routing.lint("bench45"))
    assert "main-routed" in res and res["main-routed"] != "ok" and "istack:L4:x2" in res["main-routed"]
    assert all(isinstance(m, str) for m in res.values())


def test_required_cells_are_per_case_a_single_item_tail_is_its_own_cell(tmp_path, monkeypatch):
    # rc1-main's rt_istack_lo2: 5 items packed in twos = two istack:L2:x2 prompts + one single-item
    # istack:L2 prompt (G2 2026-11-14): both cells are required, each named the way scale.cell_of names it
    prof = _routed(monkeypatch, "rc1-main")
    cs = [c["chain_spec"] for c in chains.chain_cases(prof) if c["chain_spec"]["ladder"] == "rt_istack_lo2_L2"]
    assert sorted(len(s["items"]) for s in cs) == [1, 2, 2]
    need = routing.required_cells(prof)
    assert "rt_istack_lo2_L2" in need["istack:L2:x2"] and "rt_istack_lo2_L2" in need["istack:L2"]
    # a table with the :x2 row but no single-item row is refused, the single cell named
    sp = routing.spec(prof)
    raw = {"cells": {c: {"b": 0.0} for c in need if c != "istack:L2"}}
    p = tmp_path / "cal.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    prof = copy.deepcopy(prof)
    prof["routing"]["calibration"] = str(p)
    with pytest.raises(config.ConfigError, match=r"istack:L2 \(module: rt_istack_lo2_L2\)"):
        routing.check_calibration(prof, config.ROOT_DIR)
    assert sp["calibration"]
