"""implied tier-1 pool growth: the everyday domains of _implied_t1.py. Three stated-rule variants each; reference 1.0 on >= 12 seeds; the
anchors (literal / do-nothing = 30 points, pad-everything < 0.6); the sibling pin."""
import hashlib
from pathlib import Path

import pytest

from crucibleforge.gen import implied as im
from crucibleforge.gen import _implied_t1 as T

NEWD = sorted(T.BUILD)
SEEDS = list(range(1, 25))


#: domains allowed 2 implied checks: backup lost lastgood/onlycopy (two strikes, zero variance in the hosted field)
LOWN = {"backup": 2}

def _drawn(d, s):
    """the ids an instance draws, without the builder's EXTRA coverage list"""
    f = im.BUILD[d]

    def g(*a, **k):
        o = f(*a, **k)
        o.pop("EXTRA", None)
        return o
    im.BUILD[d] = g
    try:
        return [e["id"] for e in _inst(d, s)["hidden"]["exps"]]
    finally:
        im.BUILD[d] = f


def _inst(d, s):
    return im.make_instance(s, 1, d)


def _grade(inst, code):
    return im.grade_implied("```python\n" + code + "\n```", {"seed": inst["public"]["seed"], "tier": 1,
                                                              "domain": inst["public"]["domain"]})["score"]


def test_bank_grew_and_tier3_is_unchanged():
    assert len(im.DOMS) == 7 + len(NEWD) and 12 <= len(im.DOMS) <= 20 and set(NEWD) <= set(im.DOMS)
    assert im.DOMS_ALL == ["sched", "dj", "monitor", "meds", "thermo", "split", "backup", "pay", "notify"]
    assert set(im.DEAL_DOMS[3]) == set(im.DOMS_BASE)
    assert {im.pick_domain(s, 1) for s in range(1, 60)} == set(im.DOMS)


def test_t1_sibling_pin():
    f = Path(im.__file__).with_name("_implied_t1.py")
    assert hashlib.sha256(f.read_bytes()).hexdigest()[:16] == im._T1_SHA


NV = {d: 4 if d in ("mileage", "invoices", "shipping") else 3 for d in NEWD}


def _seeds_of(d, v, n):
    out, sd = [], 0
    while len(out) < n:
        sd += 1
        if _inst(d, sd)["public"]["params"]["v"] == v:
            out.append(sd)
    return out


@pytest.mark.parametrize("d", NEWD)
def test_variants_differ_in_the_text_and_counts_hold(d):
    rules = {}
    for s in range(1, 41):
        inst = _inst(d, s)
        v = inst["public"]["params"]["v"]
        fn = inst["public"]["fn"]["main"]
        rules.setdefault(v, set()).add(inst["prompt"].replace(fn, "F").replace("\n", " "))
        ex = inst["hidden"]["exps"]
        kinds = [e["kind"] for e in ex]
        drawn = _drawn(d, s)          # the level-1 coverage checks appended after the drawn ones (2026-11-15) are not drawn
        assert [e["id"] for e in ex[:len(drawn)]] == drawn
        assert kinds.count("x") == 3 and LOWN.get(d, 3) <= len(drawn) - 3 <= 6, (d, s, kinds)
        assert len(inst["prompt"]) < 3500
        for e in ex:
            assert e["why"] not in inst["prompt"]
    assert sorted(rules) == list(range(NV[d]))
    # variants are different briefs (a stated rule), not the same brief renamed: no text is shared between variants
    for v1 in rules:
        for v2 in rules:
            if v1 < v2:
                assert not (rules[v1] & rules[v2])


@pytest.mark.parametrize("d", NEWD)
def test_reference_is_full_on_24_seeds_and_alt_accepted(d):
    for s in SEEDS:
        inst = _inst(d, s)
        assert im.check(inst, im.reference_code(inst))["q"] == 1.0, (d, s)
    for v in range(NV[d]):
        for s in _seeds_of(d, v, 2):
            inst = _inst(d, s)
            assert im.check(inst, im.alt(inst))["q"] == 1.0, (d, v, s)


@pytest.mark.parametrize("d", NEWD)
def test_anchors_literal_null_pad(d):
    for s in [x for v in range(NV[d]) for x in _seeds_of(d, v, 1)] + [4]:
        inst = _inst(d, s)
        m = im.materialize({"type": "implied", "level": 1, "domain": d, "key": f"k{s}"}, release_seed="r", case_id="X")
        lit, nul, pad = (_grade(inst, f(inst)) for f in (im._literal, im.null, im.pad))
        assert max(lit, nul) == pytest.approx(0.30), (d, s, lit, nul)
        assert pad < 0.6, (d, s, pad)
        assert _grade(inst, im.reference_code(inst)) == 1.0
        assert m["meta"]["domain"] == d


# ------------------------------------------------------------------ the base domains' extra stated-rule variants
VD = list(im.VARIANT_DOMS)


@pytest.mark.parametrize("d", VD)
def test_base_variant_0_is_the_original_brief(d):
    """variant 0 = the brief of the fast-fail harness, byte for byte; ffseed pins it; the other variants differ in the text"""
    for s in range(1, 13):
        g = {"type": "implied", "level": 1, "domain": d, "ffseed": s}
        m = im.materialize(g, release_seed="r", case_id="X")
        assert m["grader_config"]["variant"] == 0
        assert m["prompt"] == im.make_instance(s, 1, d, 0)["prompt"]
        assert im.make_instance(s, 1, d, 1)["prompt"] != m["prompt"] != im.make_instance(s, 1, d, 2)["prompt"]
    # the seed picks the variant otherwise, all three turn up, tier 3 never varies
    vs = {im.make_instance(s, 1, d)["public"]["params"]["v"] for s in range(1, 41)}
    assert vs == {0, 1, 2}
    assert {im.make_instance(s, 3, d)["public"]["params"]["v"] for s in range(1, 20)} == {0}


@pytest.mark.parametrize("d", VD)
@pytest.mark.parametrize("v", [0, 1, 2])
def test_base_variant_reference_alt_and_anchors(d, v):
    seeds = _seeds_of(d, v, 12)
    for s in seeds:
        inst = _inst(d, s)
        assert inst["public"]["params"]["v"] == v
        assert im.check(inst, im.reference_code(inst))["q"] == 1.0, (d, v, s)
    for s in seeds[:3]:
        inst = _inst(d, s)
        assert im.check(inst, im.alt(inst))["q"] == 1.0, (d, v, s)
        lit, nul, pad = (_grade(inst, f(inst)) for f in (im._literal, im.null, im.pad))
        assert max(lit, nul) == pytest.approx(0.30), (d, v, s, lit, nul)
        assert pad < 0.6, (d, v, s, pad)
        ex = inst["hidden"]["exps"]
        assert [e["kind"] for e in ex].count("x") == 3 and LOWN.get(d, 3) <= len(ex) - 3 <= 6


def test_variant_flows_through_the_engine_grader():
    g = {"type": "implied", "level": 1, "domain": "pay", "rule_variant": 2}
    m = im.materialize(g, release_seed="r", case_id="X")
    assert m["grader_config"]["variant"] == 2 and "1.5 times" in m["prompt"]
    assert im.grade(im.reference(g, release_seed="r", case_id="X"), m) == 1.0
    g1 = {**g, "rule_variant": 1}
    m1 = im.materialize(g1, release_seed="r", case_id="X")
    assert "unpaid" in m1["prompt"] and im.grade(im.reference(g1, release_seed="r", case_id="X"), m1) == 1.0
