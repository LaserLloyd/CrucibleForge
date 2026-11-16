"""implied level 5 (port of ff/q/implied3.py): reference 1.0, anchors, results schema, sha pin, ff reproduction, gradeable sets."""
import hashlib
import json
from pathlib import Path

import pytest

from crucibleforge import chains, gen
from crucibleforge.gen import implied as im

SEED, CASE = "4.5.0", "X"
SEEDS = list(range(1, 13))
#: sha256[:16] of json.dumps([prompt, fn, hidden], sort_keys=True) of the ff instance
#: (ff t-implied @ 811314b, re-pinned for the F-055 panel revision: quiet/nosev dropped, burst accept-either, taken fixed)
#: re-pinned 2026-11-14: meds3 `critcap` dropped (it keyed a 4th push against the brief's 3-per-60-minutes rule); the prompt and
#: every other check are the ff instance's, the hidden block lacks critcap
#: re-pinned 2026-11-15 (FIXNOW 21+22): `renag` earlier reminder at the dose time, now = dose + 3 h; `dup` dropped; the
#: prompt is unchanged and every other scenario is byte-identical (tests/test_gen_implied_accept.py)
FF = {1: "bf4fd46b2f3a03b1", 2: "8383f90772237bc6", 3: "c3bef937b870733c", 7: "cfc957196b1051c4"}


def _g(ffseed):
    return {"type": "implied", "level": 5, "ffseed": ffseed}


def test_i3_sibling_pin():
    f = Path(im.__file__).with_name("_implied3.py")
    assert hashlib.sha256(f.read_bytes()).hexdigest()[:16] == im._I3_SHA


def test_level_registered_and_deterministic():
    assert 5 in im.LEVELS and im.LEVELS[5]["tier"] == 5
    a = im.materialize({"type": "implied", "level": 5}, release_seed=SEED, case_id=CASE)
    b = im.materialize({"type": "implied", "level": 5}, release_seed=SEED, case_id=CASE)
    assert a == b and a["grader"] == "implied" and a["meta"]["domain"] in im.DOMS5
    assert a["meta"]["n_scored"] == a["meta"]["n_exp"] + 3
    assert a["grader_config"]["domain"] == a["meta"]["domain"]
    c = im.materialize({"type": "implied", "level": 5}, release_seed=SEED, case_id="Y")
    assert c["grader_config"]["seed"] != a["grader_config"]["seed"]


@pytest.mark.parametrize("s", sorted(FF))
def test_ffseed_reproduces_the_fastfail_instance(s):
    inst = im._inst_of(_g(s), SEED, CASE)
    pub = inst["public"]
    assert pub["seed"] == s
    # ff prompt = ff WRAP + text; the public/hidden blocks are identical
    j = json.dumps([inst["prompt"], pub["fn"], inst["hidden"]], sort_keys=True)
    assert hashlib.sha256(j.encode()).hexdigest()[:16] == FF[s]


@pytest.mark.parametrize("s", SEEDS)
def test_reference_full_baseline_30_pad_and_null_low(s):
    g = _g(s)
    m = im.materialize(g, release_seed=SEED, case_id=CASE)
    assert im.grade(im.reference(g, release_seed=SEED, case_id=CASE), m) == 1.0
    inst = im._inst_of(g, SEED, CASE)
    lit, pad, null = [im.grade("```python\n" + f(inst) + "\n```", m) for f in (im._literal, im.pad, im.null)]
    assert max(lit, null) == pytest.approx(0.30)       # the anchor: the better trivial solution
    assert pad < 0.6       # pad may sit below the anchor: the panel-kept anti traps punish padding
    assert im.grade("no code", m) == 0.0


def test_results_schema_and_weights():
    g = _g(1)
    m = im.materialize(g, release_seed=SEED, case_id=CASE)
    r = im.grade_implied(im.reference(g, release_seed=SEED, case_id=CASE), m["grader_config"])
    exps = im._inst_of(g, SEED, CASE)["hidden"]["exps"]
    assert [x["id"] for x in r["results"]] == [e["id"] for e in exps] and len(exps) == 11   # dup dropped (FIXNOW 22)
    assert [x["weight"] for x in r["results"]] == [e["w"] for e in exps]
    assert all(x["pass"] and isinstance(x["id"], str) for x in r["results"])
    assert {x["kind"] for x in r["results"]} == {"x", "i", "a"}
    # partial credit: the literal solution fails some implied checks and passes the explicit ones
    inst = im._inst_of(g, SEED, CASE)
    rl = im.grade_implied("```python\n" + im._literal(inst) + "\n```", m["grader_config"])
    st = {x["id"]: x["pass"] for x in rl["results"]}
    assert st["x1"] and st["x2"] and st["x3"] and not st["nodup"] and "dup" not in st   # dup dropped (FIXNOW 22)


def test_brief_does_not_leak_the_expectations():
    p = im.materialize(_g(1), release_seed=SEED, case_id=CASE)["prompt"]
    assert "critical" in p and "3am" not in p and "quiet hours" not in p and "silently" not in p


def _prof(items):
    return {"name": "t", "cases": {"coding": []},
            "scores": {"work": {"parts": {"main": {"max": 10, "scoring": ["coding"], "cases": []}}}},
            "chains": {"work/main": {"flat": items}}}


@pytest.mark.parametrize("extra", [{}, {"items_per_prompt": 1}])
def test_gradeable_element_check_ceiling_40000(extra):
    chains._judge_reason.cache_clear()
    chains._shape_reason.cache_clear()
    item = {"family": "implied", "levels": {5: 4}, "element": "check", "element_weight": "check", "average": "unit",
            "ceiling": 40000, **extra}
    prof = _prof([item])
    chains.check_gradeable(prof)
    cs = chains.chain_cases(prof)
    assert len(cs) == 4
    inst = chains.instance(cs[0], 1, 1, release_seed="R")
    case = chains.materialize(inst)
    assert case["grader"] == "implied"
    cfg = case["grader_config"]
    g = {"type": "implied", "level": 5, "ffseed": cfg["seed"], "domain": cfg["domain"]}
    ans = im.reference(g, release_seed="R", case_id=case["id"])
    got = chains.check_results(case, ans)
    assert got is not None and len(got) >= 12 and all(x[1] == 1.0 for x in got)


# ------------------------------------------------------------------ distinct briefs (deal) and convention gates

def _dealt(level, n, domain=None, key="k1"):
    out = []
    for slot in range(n):
        g = {"type": "implied", "level": level, "key": f"c{slot}", "deal": {"key": key, "slot": slot, "of": n}}
        if domain:
            g["domain"] = domain
        out.append(im.materialize(g, release_seed=SEED, case_id=f"X{slot}")["prompt"])
    return out


def _dealt_doms(level, n, key="k1", domain=None):
    out = []
    for slot in range(n):
        g = {"type": "implied", "level": level, "key": f"c{slot}", "deal": {"key": key, "slot": slot, "of": n}}
        if domain:
            g["domain"] = domain
        out.append(im.materialize(g, release_seed=SEED, case_id=f"X{slot}")["meta"]["domain"])
    return out


def _dealt_keys(level, n, key="k1"):
    out = []
    for slot in range(n):
        g = {"type": "implied", "level": level, "key": f"c{slot}", "deal": {"key": key, "slot": slot, "of": n}}
        inst = im._inst_of(g, SEED, f"X{slot}")
        pr = inst["public"]["params"]
        out.append((inst["public"]["domain"], pr["v"] if level == 1 and "v" in pr else inst["prompt"]))
    return out


@pytest.mark.parametrize("level", [1, 3])
def test_dealt_set_has_distinct_briefs(level):
    tier = im.LEVELS[level]["tier"]
    pool = sum(len(t) for _, t in im._deal_pool(tier, None))
    ps = _dealt(level, 20)
    assert len(set(ps)) == min(20, pool)
    # the round-robin visits every domain once per block, so a domain's briefs are used up before any of them repeats: the
    # first (fewest briefs of a domain) x (domains) slots are all different (uneven banks repeat the small domains first)
    doms = im._deal_pool(tier, None)
    ps = _dealt_keys(level, pool + 5)
    k = min(len(t) for _, t in doms) * len(doms)
    assert len(set(ps[:k])) == k
    assert _dealt(level, 20) == _dealt(level, 20)           # deterministic
    assert _dealt(level, 20, key="k2") != _dealt(level, 20)  # release-keyed


@pytest.mark.parametrize("level", [1, 3])
def test_dealt_set_is_spread_over_domains(level):
    """round-robin over domains, not over texts: a set of 12 holds >= 5 domains and none dominates (monitor used to be
    36 of the 60 tier-3 texts, ~9 of 12 dealt briefs)"""
    doms = im.DEAL_DOMS[im.LEVELS[level]["tier"]]
    assert "monitor" not in doms and "notify" not in doms
    cap = -(-12 // len(doms)) + 1
    for key in ("k1", "k2", "k3", "release-9", "x"):
        got = _dealt_doms(level, 12, key=key)
        assert len(set(got)) >= min(5, len(doms))
        assert max(got.count(d) for d in set(got)) <= cap, got
        # a block of len(doms) consecutive slots visits every domain once
        assert len(set(_dealt_doms(level, len(doms), key=key))) == len(doms)


def test_tier3_deal_pin_still_reaches_monitor_and_notify():
    for d in ("monitor", "notify"):
        assert set(_dealt_doms(3, 4, domain=d)) == {d}
    assert len(set(_dealt(3, 4, domain="monitor"))) == 4      # distinct texts within the pinned domain


def test_dealt_domain_pin_and_ffseed_pins_still_work():
    ps = _dealt(1, 3, domain="pay")
    assert all("Payroll helper" in p for p in ps)
    m = im.materialize({"type": "implied", "level": 1, "ffseed": 5, "deal": {"key": "k", "slot": 2, "of": 9}},
                       release_seed=SEED, case_id=CASE)
    assert m["grader_config"]["seed"] == 5
    # level 5 has a single brief text: the dealt seeds still differ (different scenarios)
    seeds = {im.materialize({"type": "implied", "level": 5, "deal": {"key": "k", "slot": s, "of": 4}},
                            release_seed=SEED, case_id=CASE)["grader_config"]["seed"] for s in range(4)}
    assert len(seeds) == 4


def test_payroll_brief_states_the_time_base():
    p = im.make_instance(1, 1, "pay", 0)["prompt"]
    assert "Monday 00:00" in p and "above 1440" in p and "2760" in p and "3240" in p


_PAY_MOD = '''
def @FN@(shifts, rate):
    iv = []
    for s in shifts:
        a, b = s["start"] % 1440, s["end"] % 1440
        if b < a: b += 1440
        iv.append((a, b))
    iv.sort(); tot = 0; cs = ce = None
    for a, b in iv:
        if ce is None or a > ce:
            if ce is not None: tot += ce - cs
            cs, ce = a, b
        else: ce = max(ce, b)
    if ce is not None: tot += ce - cs
    return round(tot * rate / 60)
'''
_PAY_STRICT = '''
def @FN@(shifts, rate):
    assert all(0 <= s["start"] <= s["end"] <= 1440 for s in shifts)
    return round(sum(s["end"] - s["start"] for s in shifts) * rate / 60)
'''


def test_convention_gate_payroll():
    """a minute-of-day reading of the (now stated) week-minute times is a defensible solution and passes the visible gate;
    a solution that rejects an end above 1440 contradicts the brief and is zeroed by it"""
    for sd in range(1, 5):
        inst = im.make_instance(sd, 1, "pay", 0)
        ok = im._judge_all(inst, im._sub(_PAY_MOD, inst))
        assert ok["q"] == 1.0
        bad = im._judge_all(inst, im._sub(_PAY_STRICT, inst))
        assert bad["q"] < 0.2


def test_gradeable_with_deal_flag():
    chains._judge_reason.cache_clear()
    chains._shape_reason.cache_clear()
    prof = _prof([{"family": "implied", "levels": {1: 6}, "deal": True, "element": "check", "element_weight": "check",
                   "average": "unit", "ceiling": 20000}])
    chains.check_gradeable(prof)
    cs = chains.chain_cases(prof)
    ps = [chains.materialize(chains.instance(c, 1, 1, release_seed="R"))["prompt"] for c in cs]
    assert len(cs) == 6 and len(set(ps)) == 6


# ------------------------------------------------------------------ packing: never two slots of one domain in one prompt

def _dealt_domain_seq(level, key, n, domain=None):
    tier = im.LEVELS[level]["tier"]
    out = []
    for slot in range(n):
        g = {"type": "implied", "level": level, "deal": {"key": key, "slot": slot, "of": n}}
        if domain:
            g["domain"] = domain
        out.append(im.pick_domain(im._dealt_seed(g), tier) if not domain else domain)
    return out


@pytest.mark.parametrize("level", [1, 3])
def test_packed_prompts_never_repeat_a_domain(level):
    """chains packs P consecutive slots (aligned at multiples of P) into one prompt; the deal must not put one domain twice
    into such a group, for any P up to 4, whatever the set's size (nor across the block boundary of the round-robin)"""
    for ki in range(40):
        seq = _dealt_domain_seq(level, f"key{ki}", 60)
        for P in (2, 3, 4):
            for a in range(0, 60 - P + 1, P):
                grp = seq[a:a + P]
                assert len(set(grp)) == P, (level, ki, P, a, grp)


@pytest.mark.parametrize("level", [1, 3])
def test_deal_is_a_prefix_function_of_key_and_slot(level):
    """slots 1..n are identical whatever n is asked (a set that grows keeps its earlier prompts)"""
    for ki in range(6):
        full = _dealt_domain_seq(level, f"key{ki}", 60)
        for n in (3, 7, 13, 25, 40):
            assert _dealt_domain_seq(level, f"key{ki}", n) == full[:n]
        g = lambda n_, s_: {"type": "implied", "level": level, "deal": {"key": f"key{ki}", "slot": s_, "of": n_}}
        assert [im._dealt_seed(g(10, s)) for s in range(10)] == [im._dealt_seed(g(50, s)) for s in range(10)]


def test_deal_with_too_few_domains_fails_loudly_unless_pinned():
    saved = im.DEAL_DOMS[3]
    try:
        im.DEAL_DOMS[3] = tuple(saved[:5])
        im._deal_pool.cache_clear()
        with pytest.raises(ValueError, match="packed prompt"):
            im._dealt_seed({"type": "implied", "level": 3, "deal": {"key": "k", "slot": 0, "of": 5}})
    finally:
        im.DEAL_DOMS[3] = saved
        im._deal_pool.cache_clear()
    # a domain pin repeats on purpose
    assert set(_dealt_domain_seq(3, "k", 8, domain="pay")) == {"pay"}
