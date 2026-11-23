"""crucibleforge.scale: Rasch ability with fixed difficulties, SE, reference-form points, ties."""
from __future__ import annotations

import json
import math
import random

import pytest

from crucibleforge import scale

CAL = {"version": "t1",
       "cells": {"a:L1": {"b": -1.0, "section": "work"}, "a:L3": {"b": 0.0, "section": "work"},
                 "b:L2": {"b": 1.0, "section": "work"}},
       "families": {"c": {"base": -2.0, "step": 0.5}},
       "form": {"work": {"a:L1": 1, "a:L3": 2, "b:L2": 1}}}


def _units(theta, n_units=60, per=5, seed=1, cells=("a:L1", "a:L3", "b:L2"), prefix="u"):
    rng = random.Random(seed)
    out = []
    for i in range(n_units):
        c = cells[i % len(cells)]
        p = scale._sig(theta - scale.difficulty(CAL, c))
        x = sum(rng.random() < p for _ in range(per)) / per
        out.append({"id": f"{prefix}{i}", "cell": c, "section": "work", "x": x, "n": per, "cluster": f"{prefix}{i}"})
    return out


def test_difficulty_lookup_cells_then_family_trend():
    assert scale.difficulty(CAL, "a:L3") == 0.0
    assert scale.difficulty(CAL, "c:L4") == pytest.approx(0.0)       # base + step x level
    assert scale.difficulty(CAL, "zzz:L1") is None


@pytest.mark.parametrize("theta", [-1.5, 0.0, 1.2])
def test_ability_recovers_theta_within_its_se(theta):
    a = scale.ability(_units(theta, n_units=300, seed=7), CAL)
    assert abs(a["theta"] - theta) < 3 * a["se"]
    assert a["se_method"] == "cluster" and a["se"] == a["se_cluster"] and a["clusters"] == 300
    assert a["extreme"] is None


def test_extreme_runs_are_bounded_and_flagged():
    zero = [dict(u, x=0.0) for u in _units(0.0, 12)]
    one = [dict(u, x=1.0) for u in _units(0.0, 12)]
    z, o = scale.ability(zero, CAL), scale.ability(one, CAL)
    assert z["extreme"] == "all-zero" and o["extreme"] == "all-one"
    assert -scale.THETA_BOUND < z["theta"] < -1.5 and 1.5 < o["theta"] < scale.THETA_BOUND
    assert math.isfinite(z["se"]) and math.isfinite(o["se"])


def test_points_on_the_fixed_reference_form():
    pts, slope = scale.points(0.0, {"a:L1": 0.25, "a:L3": 0.5, "b:L2": 0.25}, CAL)
    want = 100 * (0.25 * scale._sig(1.0) + 0.5 * 0.5 + 0.25 * scale._sig(-1.0))
    assert pts == pytest.approx(want) and slope > 0
    # a model that never took a cell still gets its expected points there
    res = scale.score_run([u for u in _units(0.5, 60) if u["cell"] == "a:L1"], CAL)["work"]
    assert res["form_declared"] and res["form_cells"] == 3
    assert res["se_points"] == pytest.approx(res["slope"] * res["se"])


def test_uncalibrated_cells_are_reported_not_scored():
    us = _units(0.0, 30) + [{"id": "q", "cell": "nope:L9", "section": "work", "x": 1.0, "n": 3, "cluster": "q"}]
    r = scale.score_run(us, CAL)["work"]
    assert r["left_out"] == {"nope:L9": 1} and r["units"] == 30          # not a form cell: never in Main
    r = scale.score_run(us, CAL, cells={"work": {"a:L1", "a:L3", "b:L2", "nope:L9"}})["work"]
    assert r["uncalibrated_cells"] == ["nope:L9"] and r["units"] == 30


def test_cluster_se_counts_units_not_elements():
    """Ten elements per unit that always agree (all right or all wrong): weighted by elements,
    the binomial SE treats them as independent; the cluster SE (per unit) is wider and wins.
    The default weight (a unit counts once) does not overstate the information."""
    rng = random.Random(3)
    us = [{"id": f"u{i}", "cell": "a:L3", "section": "work", "x": float(rng.random() < 0.5), "n": 10,
           "cluster": f"u{i}"} for i in range(40)]
    a = scale.ability(us, CAL, weight="elements")      # elements counted as if independent
    assert a["se_cluster"] > 2 * a["se_model"] and a["se"] == a["se_cluster"]
    u1 = scale.ability(us, CAL)
    assert u1["se"] >= a["se"] * 0.9


def test_tie_paired_vs_unpaired():
    base = _units(0.3, 80, seed=11)
    # a near-identical twin on the SAME instances (shared clusters): paired, small SE, apart
    twin = [dict(u, x=min(1.0, u["x"] + (0.2 if i % 2 else 0.0))) for i, u in enumerate(base)]
    ra, rb = scale.score_run(base, CAL)["work"], scale.score_run(twin, CAL)["work"]
    t = scale.tie(ra, rb)
    assert t["paired"] and t["shared_clusters"] == 80
    # the same two runs scored as if on different instances: unpaired, wider SE
    other = scale.score_run([dict(u, cluster="x" + u["cluster"]) for u in twin], CAL)["work"]
    tu = scale.tie(ra, other)
    assert not tu["paired"] and tu["se_diff"] > t["se_diff"]
    assert tu["gap"] == pytest.approx(t["gap"])
    # the same run against itself: gap 0, tied
    assert scale.tie(ra, ra)["tied"]


def test_provisional_fit_orders_models_and_cells():
    runs = {"strong": _units(1.5, 90, seed=1, prefix="s"), "weak": _units(-1.0, 90, seed=2, prefix="w")}
    cal = scale.fit_difficulties(runs)
    assert cal["provisional"] and set(cal["cells"]) == {"a:L1", "a:L3", "b:L2"}
    b = {c: v["b"] for c, v in cal["cells"].items()}
    assert b["a:L1"] < b["a:L3"] < b["b:L2"]
    rs, rw = (scale.score_run(runs[m], cal)["work"] for m in ("strong", "weak"))
    assert rs["theta"] > rw["theta"] and rs["points"] > rw["points"]


def _row(blk_id, family, level, score, part="work/main", n_el=3, kind="flat"):
    els = [{"id": f"{blk_id}-e{i}", "score": score} for i in range(n_el)]
    return {"chain": {"id": blk_id, "kind": kind, "part": part, "ladder": "s", "rung": 1, "attempt": 1,
                      "family": family, "level": level, "score": score, "passed": score >= 0.5,
                      "elements": els}, "case_id": blk_id, "turn": None}   # stored rows always carry case_id


def test_cli_dry_path_on_stored_rows(tmp_path, capsys):
    for label, sc in (("good", 0.9), ("bad", 0.2)):
        rows = [_row(f"FLAT-a-{i:03d}-r1-a1", "a", 1 + 2 * (i % 2), sc if i % 4 else 0.5) for i in range(12)]
        (tmp_path / f"transcripts_{label}.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    calf = tmp_path / "cal.json"
    calf.write_text(json.dumps(CAL), encoding="utf-8")
    assert scale.main([f"good={tmp_path}", f"bad={tmp_path}", "--calibration", str(calf), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    g, b = out["results"]["good"]["work"], out["results"]["bad"]["work"]
    assert g["theta"] > b["theta"] and g["units"] == 12 and g["se"] > 0
    (t,) = out["ties"]
    assert t["paired"] and not t["tied"]
    assert scale.main([f"good={tmp_path}", f"bad={tmp_path}", "--fit-out", str(tmp_path / "fit.json")]) == 0
    assert "PROVISIONAL" in capsys.readouterr().out and json.loads((tmp_path / "fit.json").read_text(encoding="utf-8"))["provisional"]


def test_ran_out_rule_and_flag_in_scale():
    blk = {"score": 0.6, "elements": [{"score": 1.0, "outcome": "pass"}, {"score": 1.0, "outcome": "pass"},
                                      {"score": 1.0, "outcome": "ran out"}, {"score": 0.0, "outcome": "ran out"},
                                      {"score": 0.0, "outcome": "ran out"}]}
    assert scale.unit_score(blk, "zero") == (pytest.approx(0.6), 5, 3)
    assert scale.unit_score(blk, "exclude") == (pytest.approx(1.0), 2, 3)
    assert scale.unit_score({"score": 0.0, "outcome": "ran out"}, "exclude") == (None, 0, 1)
    us = [{"cell": "a:L1", "section": "work", "x": 1.0, "n": 2, "ran_out": 3, "rule": "exclude", "cluster": "u"}]
    rep = scale.ran_out_report(us)["a:L1"]
    assert rep["share"] == pytest.approx(0.6) and rep["ceiling_too_low"]


def test_g2_calibration_format_and_slope():
    g2 = {"theta": {"ref-q8": 0.0},
          "family": {"implied": [-0.33, 0.36, 3.0, 0.79]},
          "cells": [{"cell": "implied:L1:x3", "family": "implied", "level": 1, "d": -1.04, "slope": 0.79,
                     "concept": "C1", "band": "Low"},
                    {"cell": "istack:L7", "family": "istack", "level": 7, "d": -0.63, "slope": 1.28}]}
    cal = scale.normalise(g2)
    assert scale.item(cal, "implied:L1:x3") == (pytest.approx(-1.04), pytest.approx(0.79))
    assert scale.item(cal, "implied:L5") == (pytest.approx(-0.33 + 0.36 * 2), pytest.approx(0.79))  # trend
    assert scale.item(cal, "istack:L7:x5") == (pytest.approx(-0.63), pytest.approx(1.28))           # packing not in table
    assert scale.item(cal, "implied:L3:x3") is None                     # a packed cell never uses the trend
    us = [{"id": f"u{i}", "cell": "istack:L7:x5", "section": "chat", "x": 0.5, "n": 5, "cluster": f"u{i}"}
          for i in range(20)]
    assert scale.ability(us, cal)["theta"] == pytest.approx(-0.63, abs=1e-6)   # P = 0.5 at theta = b


# ------------------------------------------------------------------ case rows (4.1 anchors: TZ16, CB-C5, CL8)

CASE_CAL = {"version": "c1", "cells": {"TZ16": {"b": -0.95, "a": 1.0, "section": "work"},
                                       "CB-C5": {"b": 0.4, "section": "work"},
                                       "a:L1": {"b": -1.0, "section": "work"}},
            "form": {"work": {"TZ16": 0.5, "a:L1": 0.5}}}


def _case_row(cid, grade=None, rate=None, ts="2026-11-14T00:00:00", repeat=1, run="r1", **kw):
    r = {"case_id": cid, "category": "tooluse", "repeat": repeat, "ts": ts, "turn": None,
         "bench_run_id": run, "grade": grade, "finish_reason": "stop"}
    if rate is not None:
        r["elements"] = {"results": [{"pass": i < rate * 4} for i in range(4)], "rate": rate}
    r.update(kw)
    return r


def test_case_rows_are_units_with_their_table_difficulty():
    cal = scale.normalise(CASE_CAL)
    rows = [_case_row("TZ16-pickup-dst-delay", "fail", rate=0.5),
            _case_row("CB-C5-predict-pyhouse", "pass"),
            _case_row("P1-probe", "pass")]                        # not in the table: not scored
    us = scale.units(rows, cal=cal)
    by = {u["id"]: u for u in us}
    tz = by["TZ16-pickup-dst-delay#1"]
    assert tz["cell"] == "TZ16-pickup-dst-delay" and tz["x"] == pytest.approx(0.5) and tz["n"] == 1
    assert tz["section"] == "work" and tz["kind"] == "case" and tz["cluster"] == "case:TZ16-pickup-dst-delay"
    assert scale.item(cal, "TZ16-pickup-dst-delay") == (pytest.approx(-0.95), pytest.approx(1.0))
    assert scale.item(cal, "CB-C5-predict-pyhouse")[0] == pytest.approx(0.4)
    assert scale.item(cal, "CB-C50-other") is None                # a prefix only at a "-" boundary
    assert scale.item(cal, "P1-probe") is None
    res = scale.score_run(us, cal)
    r = res["work"]
    assert r["units"] == 1 and r["uncalibrated_cells"] == [] and "P1-probe#1" not in by   # only table cases
    assert r["left_out"] == {"CB-C5-predict-pyhouse": 1} and res["work+packs"]["units"] == 1   # not a form cell
    # the case is on the reference form (and in Main) by its table key
    assert r["form_declared"] and r["form_cells"] == 2


def test_case_unit_ability_is_the_table_difficulty_at_half_credit():
    cal = scale.normalise(CASE_CAL)
    rows = [_case_row("TZ16-pickup-dst-delay", rate=0.5, repeat=k) for k in range(1, 9)]
    a = scale.ability(scale.units(rows, cal=cal), cal)
    assert a["theta"] == pytest.approx(-0.95, abs=1e-6)
    assert a["clusters"] == 1                                      # repeats of one case are one cluster


def test_case_rows_newest_graded_ungraded_and_bench_run_filter():
    cal = scale.normalise(CASE_CAL)
    rows = [_case_row("TZ16-x", "fail", ts="2026-11-13T00:00:00", run="old"),
            _case_row("TZ16-x", "pass", ts="2026-11-14T00:00:00", run="new"),
            _case_row("CB-C5-y", "error"),                       # errored: not a unit
            _case_row("CB-C5-z", None, needs_judge=True)]         # pending judge: not a unit
    us = scale.units(rows, cal=cal)
    assert [(u["cell"], u["x"]) for u in us] == [("TZ16-x", 1.0)]
    old = scale.units(rows, cal=cal, bench_runs={"old"})
    assert [(u["cell"], u["x"]) for u in old] == [("TZ16-x", 0.0)]
    # without a table the case rows are not read (the chain-unit dry path is unchanged)
    assert scale.units(rows) == []


def test_case_rows_mix_with_chain_units_and_ran_out_rule():
    cal = scale.normalise(CASE_CAL)
    chain = {"chain": {"id": "FLAT-s-001", "kind": "flat", "part": "work/main", "ladder": "s", "rung": 1,
                       "attempt": 1, "family": "a", "level": 1, "score": 1.0, "passed": True,
                       "elements": [{"id": "e", "score": 1.0}]}, "turn": None}
    cut = _case_row("TZ16-x", "fail", finish_reason="length")
    us = scale.units([chain, cut], cal=cal)
    assert {u["kind"] for u in us} == {"chain", "case"}
    tz = next(u for u in us if u["kind"] == "case")
    assert tz["ran_out"] == 1 and tz["x"] == 0.0
    ex = scale.units([chain, cut], ran_out="exclude", cal=cal)
    assert next(u for u in ex if u["kind"] == "case").get("excluded")


# ------------------------------------------------------------------ margin: the cluster SE, not one coin per prompt

def _testlets(theta, n_units, checks=23, seed=5, spread=0.25):
    """Prompts of many checks each: x = a check share, close to P (a testlet is not one coin)."""
    rng = random.Random(seed)
    out = []
    for i in range(n_units):
        c = ("a:L1", "a:L3", "b:L2")[i % 3]
        p = scale._sig(theta - scale.difficulty(CAL, c) + rng.gauss(0, spread))
        x = sum(rng.random() < p for _ in range(checks)) / checks
        out.append({"id": f"t{i}", "cell": c, "section": "work", "x": x, "n": checks, "cluster": f"t{i}"})
    return out


def test_margin_uses_the_cluster_se_with_enough_units():
    a = scale.ability(_testlets(0.5, 12), CAL)
    assert a["se_method"] == "cluster" and a["se"] == pytest.approx(a["se_cluster"])
    assert a["se"] < 0.6 * a["se_model"]                  # the one-coin model SE was far too wide
    assert a["phi"] is not None and a["se_quasi"] == pytest.approx(a["se_model"] * math.sqrt(a["phi"]))
    r = scale.score_run(_testlets(0.5, 12), CAL)["work"]
    assert r["margin_90"] == pytest.approx(scale.Z_TIE * r["slope"] * r["se"])


def test_margin_falls_back_conservatively_below_eight_units_and_on_extremes():
    a = scale.ability(_testlets(0.5, 5), CAL)
    assert a["se_method"] == "max" and a["se"] == pytest.approx(max(a["se_model"], a["se_cluster"]))
    one = scale.ability([dict(u, x=1.0) for u in _testlets(0.5, 12)], CAL)
    assert one["extreme"] == "all-one" and one["se_method"] == "max"


def test_tie_no_longer_ties_everything():
    """Two runs about one logit apart on 12 testlets: the cluster margin separates them;
    with fewer than eight units each the conservative rule may not."""
    hi, lo = scale.score_run(_testlets(1.2, 12, seed=1), CAL)["work"], \
        scale.score_run(_testlets(-0.2, 12, seed=2), CAL)["work"]
    t = scale.tie(hi, lo)
    assert t["se_method"] == "cluster" and not t["tied"]
    small = scale.tie(scale.score_run(_testlets(1.2, 4, seed=1), CAL)["work"],
                      scale.score_run(_testlets(-0.2, 4, seed=2), CAL)["work"])
    assert small["se_method"] == "max" and small["se_diff"] > t["se_diff"]



# ------------------------------------------------------------------ guard: only Main cells make a Main ability

def test_pack_units_never_move_the_main_ability():
    main = _units(0.4, 30, seed=9, cells=("a:L1", "a:L3"))
    cal = dict(CAL, cells=dict(CAL["cells"], **{"pk:L5": {"b": 2.5, "section": "work"},
                                                "a:L9": {"b": 1.5, "section": "work", "band": "top"}}),
               form={"work": {"a:L1": 1, "a:L3": 1, "a:L9": 1}})
    base = scale.score_run(main, cal)["work"]
    pack = [{"id": f"p{i}", "cell": "pk:L5", "section": "work", "x": 0.0, "n": 3, "cluster": f"p{i}"} for i in range(10)]
    top = [{"id": f"t{i}", "cell": "a:L9", "section": "work", "x": 0.0, "n": 3, "cluster": f"t{i}"} for i in range(4)]
    parted = [dict(u, id=u["id"] + "q", cluster=u["cluster"] + "q", part="work/trace") for u in main[:6]]
    res = scale.score_run(main + pack + top + parted, cal, pack_parts={"work/trace"})
    r = res["work"]
    assert r["theta"] == pytest.approx(base["theta"]) and r["units"] == base["units"] == 30
    assert r["left_out"] == {"pk:L5": 10, "a:L9": 4, "a:L1": 3, "a:L3": 3}
    pk = res["work+packs"]
    assert pk["units"] == 20 and pk["margin_90"] > 0 and not pk["form_declared"]
