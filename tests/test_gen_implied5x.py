"""implied level 5, the extra briefs (car3 shared-car booking, lib3 library fines, wh3 warehouse planner): sha pins, reference 1.0 on
16 seeds each, floors (literal = anchor 0.30, pad-everything < 0.6, null), 15 scored checks of the house shape, gradeable sets,
dealt sets of distinct domains, ffseed reproduction of the medication brief."""
import hashlib
from pathlib import Path

import pytest

from crucibleforge import chains
from crucibleforge.gen import implied as im

SEED, CASE = "4.5.0", "X"
NEW = ["car3", "lib3", "wh3"]
N_IMPLIED = {"meds3": 8, "car3": 10, "lib3": 9, "wh3": 8}   # F-055 panel: meds3 quiet/nosev, wh3 dupfree/dupid/empty/future dropped;
#: scrub 2026-11-14: meds3 critcap (contradicts the brief), car3 onecar/onecarex (a rule the brief never states) dropped;
#: FIXNOW 22 (2026-11-15): meds3 dup dropped
PINS = {"car3": ("_implied5_car.py", "_CAR_SHA"), "lib3": ("_implied5_lib.py", "_LIB_SHA"), "wh3": ("_implied5_wh.py", "_WH_SHA")}


def _g(dom, sd):
    return {"type": "implied", "level": 5, "domain": dom, "ffseed": sd}


@pytest.mark.parametrize("dom", NEW)
def test_sibling_pin(dom):
    f, name = PINS[dom]
    assert hashlib.sha256(Path(im.__file__).with_name(f).read_bytes()).hexdigest()[:16] == getattr(im, name)


def test_domains_registered():
    assert im.DOMS5 == ["meds3"] + NEW
    seen = {im.pick5(s) for s in range(1, 9)}
    assert seen == set(im.DOMS5)             # every block of 4 consecutive seeds is one pass over all domains


@pytest.mark.parametrize("dom", NEW)
@pytest.mark.parametrize("sd", range(1, 17))
def test_reference_full_floors(dom, sd):
    g = _g(dom, sd)
    m = im.materialize(g, release_seed=SEED, case_id=CASE)
    assert m["meta"]["domain"] == dom and m["grader_config"]["domain"] == dom
    assert im.grade(im.reference(g, release_seed=SEED, case_id=CASE), m) == 1.0
    inst = im._inst_of(g, SEED, CASE)
    lit, pad, null = [im.grade("```python\n" + f(inst) + "\n```", m) for f in (im._literal, im.pad, im.null)]
    assert max(lit, null) == pytest.approx(0.30)      # the anchor
    assert pad < 0.6                                  # below the anchor is fine: anti traps punish padding
    assert im.grade("no code", m) == 0.0


@pytest.mark.parametrize("dom", NEW)
def test_shape_3_explicit_plus_implied(dom):
    inst = im._inst_of(_g(dom, 1), SEED, CASE)
    exps = inst["hidden"]["exps"]
    assert len(exps) == 3 + N_IMPLIED[dom] and inst["public"]["n_exp"] == N_IMPLIED[dom]
    assert [e["w"] for e in exps if e["kind"] == "x"] == [.25, .25, .25] and [e["id"] for e in exps[:3]] == ["x1", "x2", "x3"]
    assert all(e["w"] == 1 for e in exps[3:]) and len({e["id"] for e in exps}) == len(exps)
    assert len(inst["public"]["fn"]) == 3
    # the brief states every convention and does not leak the expectations
    for e in exps:
        assert e["why"] not in inst["prompt"]
    assert len(inst["prompt"]) < 3500


@pytest.mark.parametrize("dom", NEW)
def test_partial_credit_literal_passes_the_explicit_checks(dom):
    g = _g(dom, 2)
    m = im.materialize(g, release_seed=SEED, case_id=CASE)
    inst = im._inst_of(g, SEED, CASE)
    r = im.grade_implied("```python\n" + im._literal(inst) + "\n```", m["grader_config"])
    st = {x["id"]: x["pass"] for x in r["results"]}
    assert st["x1"] and st["x2"] and st["x3"] and 3 <= sum(st.values()) < len(st)


def test_scenarios_vary_with_the_seed_but_the_brief_text_does_not():
    for dom in NEW:
        a, b = im.make_instance(1, 5, dom), im.make_instance(2, 5, dom)
        assert a["prompt"] == b["prompt"] and a["hidden"] != b["hidden"]


def _prof(items):
    return {"name": "t", "cases": {"coding": []},
            "scores": {"work": {"parts": {"main": {"max": 10, "scoring": ["coding"], "cases": []}}}},
            "chains": {"work/main": {"flat": items}}}


def test_gradeable_dealt_l5_set_of_four_has_four_domains():
    chains._judge_reason.cache_clear()
    chains._shape_reason.cache_clear()
    prof = _prof([{"family": "implied", "levels": {5: 4}, "element": "check", "element_weight": "check", "average": "unit",
                   "ceiling": 40000, "deal": True}])
    chains.check_gradeable(prof)
    cs = chains.chain_cases(prof)
    assert len(cs) == 4
    doms, ps = [], []
    for c in cs:
        case = chains.materialize(chains.instance(c, 1, 1, release_seed="R"))
        doms.append(case["meta"]["domain"])
        ps.append(case["prompt"])
        got = chains.check_results(case, im.reference({"type": "implied", "level": 5, "ffseed": case["grader_config"]["seed"],
                                                       "domain": case["meta"]["domain"]}, release_seed="R", case_id=case["id"]))
        assert got is not None and len(got) == 3 + N_IMPLIED[case["meta"]["domain"]] and all(x[1] == 1.0 for x in got)
    assert sorted(doms) == sorted(im.DOMS5) and len(set(ps)) == 4


@pytest.mark.parametrize("n", [4, 8])
def test_dealt_sets_cover_every_domain(n):
    out = []
    for slot in range(n):
        g = {"type": "implied", "level": 5, "key": f"c{slot}", "deal": {"key": "k1", "slot": slot, "of": n}}
        out.append(im.materialize(g, release_seed=SEED, case_id=f"X{slot}")["meta"]["domain"])
    assert set(out[:4]) == set(im.DOMS5)
    assert len(im._brief_pool(5, None)) == 4


def test_ffseed_still_reproduces_the_medication_brief():
    for sd in (1, 2, 3, 7):
        assert im._inst_of({"type": "implied", "level": 5, "ffseed": sd}, SEED, CASE)["public"]["domain"] == "meds3"
