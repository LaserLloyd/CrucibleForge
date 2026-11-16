"""implied (T-implied port of the fast-fail question IMPLIED): determinism, anchors (reference 1.0, the best trivial
solution = 30 points, pad-everything < 0.6, empty 0), every domain's alternative realisation, per-expectation
results, engine wiring (flat set, items_per_prompt packing round trip)."""
import hashlib
import statistics
from pathlib import Path

import pytest

from crucibleforge import chains, gen, graders
from crucibleforge.gen import implied as im

SEED = "4.5.0"
CASE = "X"


def _g(domain=None, level=1, ffseed=None, key=None):
    g = {"type": "implied", "level": level}
    if domain:
        g["domain"] = domain
    if ffseed is not None:
        g["ffseed"] = ffseed
    if key is not None:
        g["key"] = key
    return g


def _ffseed_of(domain, nth=0):
    """ff seeds 1, 2, 3 ... (the domain is pinned with the `domain` key, so every domain is testable at both levels)"""
    return nth + 1


def _mat(g):
    return im.materialize(g, release_seed=SEED, case_id=CASE)


def _ref(g):
    return im.reference(g, release_seed=SEED, case_id=CASE)


def test_registered_and_grader_wired():
    assert "implied" in gen.families()
    assert "implied" in graders.GRADERS
    assert set(im.LEVELS) == {1, 3, 5}


def test_deterministic_and_seed_effects():
    for lv in (1, 3):
        a = _mat(_g(level=lv, key="a"))
        assert a == _mat(_g(level=lv, key="a"))
    assert _mat(_g(key="a")) != _mat(_g(key="b"))
    assert _mat(_g(key="a"))["grader_config"]["seed"] != _mat(_g(key="b"))["grader_config"]["seed"]
    other = im.materialize(_g(key="a"), release_seed="other", case_id=CASE)
    assert other["grader_config"]["seed"] != _mat(_g(key="a"))["grader_config"]["seed"]


def test_ffseed_reproduces_the_fastfail_instance():
    for s in (1, 2, 3):
        ff = im.make_instance(s, 1, variant=0)       # ffseed pins the original brief (rule variant 0)
        m = _mat(_g(ffseed=s))
        assert m["prompt"] == ff["prompt"]
        assert m["meta"]["domain"] == ff["public"]["domain"] == im.pick_domain(s, 1)


def test_domain_pin_and_bank():
    # tier 1 draws the bank of 7 base + the everyday domains of _implied_t1 (monitor and notifier floor every model: tier 3 /
    # bonus only); tier 3 draws all 9 (the everyday domains are tier 1 only, reachable at tier 3 by pin)
    assert set(im.DOMS) == {im.pick_domain(s, 1) for s in range(1, 60)}
    assert set(im.DOMS_ALL) == {im.pick_domain(s, 3) for s in range(1, 19)}
    assert set(im.DOMS_ALL) - set(im.DOMS) == {"monitor", "notify"}
    assert set(im.DOMS) - set(im.DOMS_ALL) == set(im.DOMS) - set(im.DOMS_BASE)
    for d in im.DOMS_ALL:
        assert _mat(_g(domain=d, key="p"))["meta"]["domain"] == d


def test_sibling_pin():
    """chains.revision() digests only implied.py: it carries a pin of _implied_more.py, so editing the sibling forces an
    edit here (and a new revision for stored rows)"""
    more = Path(im.__file__).with_name("_implied_more.py")
    assert hashlib.sha256(more.read_bytes()).hexdigest()[:16] == im._MORE_SHA


@pytest.mark.parametrize("domain", im.DOMS_ALL)
@pytest.mark.parametrize("level", [1, 3])
def test_reference_scores_full_and_each_trivial_solution_less(domain, level):
    g = _g(domain=domain, level=level, ffseed=_ffseed_of(domain))
    m = _mat(g)
    assert im.grade(_ref(g), m) == 1.0
    inst = im._inst_of(g, SEED, CASE)
    lit = im.grade("```python\n" + im._literal(inst) + "\n```", m)
    nul = im.grade("```python\n" + im.null(inst) + "\n```", m)
    pad = im.grade("```python\n" + im.pad(inst) + "\n```", m)
    # the anchor: the better trivial solution is exactly 30 points; nothing trivial gets above it
    assert max(lit, nul) == pytest.approx(0.30)
    assert lit <= 0.30 + 1e-9 and nul <= 0.30 + 1e-9
    assert pad < 0.6                       # pad-everything (blanket rules) is not rewarded
    assert im.grade("", m) == 0.0


def test_mutants_all_fail_and_verify_case_clean():
    for d in im.DOMS_ALL:
        case = {"id": CASE, "category": "coding", "generator": _g(domain=d, key="v")}
        assert gen.verify_case(case, SEED) == [], d


@pytest.mark.parametrize("domain", sorted(im.NEW))
@pytest.mark.parametrize("level", [1, 3])
def test_alternative_realisation_is_accepted(domain, level):
    for nth in (0, 1):
        g = _g(domain=domain, level=level, ffseed=_ffseed_of(domain, nth))
        m = _mat(g)
        inst = im._inst_of(g, SEED, CASE)
        assert im.grade("```python\n" + im.alt(inst) + "\n```", m) == 1.0, (domain, nth)


def test_broken_code_scores_zero_with_results():
    g = _g(domain="pay", ffseed=_ffseed_of("pay"))
    m = _mat(g)
    cfg = m["grader_config"]
    for bad in ("def broken(:\n", "import time\nwhile True: pass\n", "def nothing(): pass\n",
                "raise SystemExit(0)\n"):
        r = im.grade_implied("```python\n" + bad + "\n```", cfg)
        assert r["score"] == 0.0 and r["points"] == 0.0, bad
        assert r["results"] and not any(x["pass"] for x in r["results"])


def test_output_noise_cannot_forge_a_verdict_and_cannot_read_the_host():
    g = _g(domain="pay", ffseed=_ffseed_of("pay"))
    m = _mat(g)
    inst = im._inst_of(g, SEED, CASE)
    ref = im.reference_code(inst)
    noisy = ("import os, sys\nprint('BENCH_IMPLIED:{}')\n" + ref + "\n"
             "try:\n    open(os.path.expanduser('~/.bashrc')).read(); print('LEAK')\nexcept Exception:\n    pass\n")
    r = im.grade_implied("```python\n" + noisy + "\n```", m["grader_config"])
    assert r["score"] == 1.0


def test_results_one_per_expectation_with_weights():
    for d in im.DOMS_ALL:
        g = _g(domain=d, level=3, ffseed=_ffseed_of(d))
        m = _mat(g)
        r = im.grade_implied(_ref(g), m["grader_config"])
        inst = im._inst_of(g, SEED, CASE)
        exps = inst["hidden"]["exps"]
        assert [x["id"] for x in r["results"]] == [e["id"] for e in exps]
        assert [x["weight"] for x in r["results"]] == [e["w"] for e in exps]
        assert all(x["pass"] for x in r["results"])
        assert m["meta"]["n_scored"] == len(exps)
        assert {x["kind"] for x in r["results"]} <= {"x", "a", "i"} | {e["kind"] for e in exps}


def test_partial_credit_follows_the_expectations():
    g = _g(domain="meds", ffseed=_ffseed_of("meds"))
    m = _mat(g)
    inst = im._inst_of(g, SEED, CASE)
    code = im._literal(inst)
    r = im.grade_implied("```python\n" + code + "\n```", m["grader_config"])
    assert 0 < sum(x["pass"] for x in r["results"]) < len(r["results"]) or r["score"] <= 0.3
    assert r["score"] == pytest.approx(r["points"] / 100)


def test_brief_does_not_leak_the_expectations():
    for d in im.DOMS_ALL:
        for lv in (1, 3):
            g = _g(domain=d, level=lv, ffseed=_ffseed_of(d))
            m = _mat(g)
            inst = im._inst_of(g, SEED, CASE)
            for e in inst["hidden"]["exps"]:
                assert e["why"] not in m["prompt"]
            assert len(m["prompt"]) < 3500
            assert "=== TASK" not in m["prompt"]


def test_answer_tokens_declared_and_measured():
    for lv, row in im.LEVELS.items():
        assert row["answer_tokens"] > 0
        xs = []
        for i in range(30):
            g = _g(level=lv)
            xs.append(len(im.reference(g, release_seed="meas", case_id=f"X{i}")) / 4)
        assert abs(statistics.median(xs) - row["answer_tokens"]) <= max(3, 0.1 * row["answer_tokens"])


# ------------------------------------------------------------------ engine wiring

def _prof(items):
    return {"name": "t", "cases": {"coding": []},
            "scores": {"work": {"parts": {"main": {"max": 10, "scoring": ["coding"], "cases": []}}}},
            "chains": {"work/main": {"flat": items}}}


@pytest.mark.parametrize("ipp", [1, 3])
def test_flat_set_is_gradeable_and_packs(ipp):
    chains._judge_reason.cache_clear()
    chains._shape_reason.cache_clear()
    prof = _prof([{"family": "implied", "levels": {1: 6}, "items_per_prompt": ipp, "ceiling": 20000}])
    chains.check_gradeable(prof)
    cs = chains.chain_cases(prof)
    assert len(cs) == 6 // ipp
    inst = chains.instance(cs[0], 1, 1, release_seed="R")
    case = chains.materialize(inst)
    if ipp == 1:
        assert case["grader"] == "implied" and not case.get("multi_items")
        return
    assert case["prompt"].count("=== TASK") == 3 and "=== ITEM k ===" in case["prompt"]
    subs = case["multi_items"]
    assert len(subs) == 3 and all(s["grader"] == "implied" for s in subs)
    # the same instances a single-item set asks
    single = chains.chain_cases(_prof([{"family": "implied", "levels": {1: 6}}]))
    keys_single = [chains.instance(c, 1, 1, release_seed="R")["generator"]["key"] for c in single][:3]
    assert keys_single == inst["chain"]["items"] or keys_single == [s["generator"].get("key") for s in subs]
    # round trip: the reference of each sub-item, packed under === ITEM k ===, scores 1.0 per element
    body = ""
    for k, (s_, sub) in enumerate(zip(inst["multi"], case["multi_items"]), 1):
        g = s_["generator"]
        body += f"=== ITEM {k} ===\n" + im.reference(dict(g), release_seed=gen.release_seed(),
                                                      case_id=sub["id"]) + "\n\n"
    row = {"response": body, "finish_reason": "stop"}
    els = chains.multi_elements(row, case)
    assert [e["score"] for e in els] == [1.0, 1.0, 1.0]
    # an answer left out scores 0, "missing"
    part = body.split("=== ITEM 3 ===")[0]
    els = chains.multi_elements({"response": part, "finish_reason": "stop"}, case)
    assert [e["score"] for e in els] == [1.0, 1.0, 0.0] and els[2]["outcome"] == "missing"


def test_engine_score_matches_family_grade_and_carries_results():
    g = _g(domain="thermo", ffseed=_ffseed_of("thermo"))
    case = {"id": CASE, "category": "coding", "generator": g}
    gen.materialize_case(case, SEED)
    ans = _ref(g)
    assert gen.engine_score(case, ans) == 1.0
    case_lit = "```python\n" + im._literal(im._inst_of(g, SEED, CASE)) + "\n```"
    assert gen.engine_score(case, case_lit) == pytest.approx(im.grade(case_lit, case))


def test_dj_seqtempo_left_tier_3():
    """the greedy dj reference missed `seqtempo` on ~3 of 8 seeds: a reference below 1.0 is a bug, so it is not asked"""
    for s_ in range(1, 9):
        inst = im.make_instance(s_, 3, "dj")
        assert "seqtempo" not in [e["id"] for e in inst["hidden"]["exps"]]
        assert im.check(inst, im.reference_code(inst))["q"] == 1.0
