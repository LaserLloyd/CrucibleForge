"""solversyn (T2 2b, FC2): determinism, prompt identity with fast-fail, reference/mutants/lazy answers, engine wiring,
stored-reply reproduction, and the generic baseline's anchor (build/T2-FRONTIER-CODING.md section 2b)."""
import copy

import pytest

from crucibleforge import gen, graders
from crucibleforge.gen import solversyn as ss

SEED = "4.5.0"


def _g(ffseed=3):
    return {"type": "solversyn", "level": 5, "ffseed": ffseed}


def test_registered_and_grader_wired():
    assert "solversyn" in gen.families()
    assert "solversyn" in graders.GRADERS


@pytest.mark.parametrize("seed", [3, 4, 6])
def test_instance_shape_and_determinism(seed):
    a = ss.materialize(_g(seed), release_seed=SEED, case_id="D")
    assert a == ss.materialize(_g(seed), release_seed=SEED, case_id="D")
    inst = ss.instance(seed)
    sizes = {}
    for _, m in inst["targets"].values():
        sizes[m] = sizes.get(m, 0) + 1
    assert sizes == ss.SIZES and len(inst["public"]["rules"]) == 5
    assert ss.materialize(_g(3), release_seed=SEED, case_id="D") != ss.materialize(_g(4), release_seed=SEED, case_id="D")


def test_uncached_seed_refused():
    with pytest.raises(ValueError):
        ss.materialize(_g(5), release_seed=SEED, case_id="D")


@pytest.mark.parametrize("seed", [3, 4, 6])
def test_certified_witnesses_are_correct_at_minimum_size(seed):
    inst = ss.instance(seed)
    q, detail = ss.score_terms(inst, dict(inst["refs"]))
    assert q == 1.0 and all(c == 1.0 for _, c in detail.values())


def test_reference_mutants_and_lazy_answers():
    g = _g(3)
    m = ss.materialize(g, release_seed=SEED, case_id="R")
    assert ss.grade(ss.reference(g, release_seed=SEED, case_id="R"), m) == 1.0
    for x in ss.mutants(g, release_seed=SEED, case_id="R"):
        assert ss.grade(x, m) < 0.2
    assert ss.grade("", m) == 0.0
    assert ss.grade("def solve(rules, targets, limits):\n    return None\n", m) == 0.0
    assert gen.verify_case({"id": "R", "generator": copy.deepcopy(g)}, SEED) == []


def test_size_plus_one_gets_half_credit_and_wrong_gets_none():
    inst = ss.instance(3)
    tid = next(t for t, (_, m) in inst["targets"].items() if m == 5)
    ref = inst["refs"][tid]
    q0, d0 = ss.score_terms(inst, {tid: ref})
    assert d0[tid] == (5, 1.0)
    assert ss.score_terms(inst, {tid: "(" + ref + ")"})[1][tid][1] == 1.0     # parentheses do not matter
    assert ss.score_terms(inst, {tid: ref + " " + ref[0]})[1][tid][1] in (0.0, 0.5)
    assert ss.score_terms(inst, {tid: "zz"})[1][tid][1] == 0.0


def test_prompt_is_clean():
    p = ss.materialize(_g(3), release_seed=SEED, case_id="P")["prompt"]
    assert "20,000 tokens" in p and "10 CPU seconds" in p
    for w in ("witness", "certified", "planted", "tier"):
        assert w not in p.lower()


def test_baseline_anchor():
    # the generic enumerator (BASE) solves sizes 5-6 only: q = Q_BASE exactly (13/73, 13/73, 17/73) -> about 30 points
    for seed in (3, 4, 6):
        r = ss.grade_solversyn("```python\n" + ss.BASE + "\n```", {"seed": seed, "level": 5, "cpu_s": 10.0})
        assert abs(r["q"] - ss.Q_BASE[seed]) < 0.06, (seed, r)
        assert 20.0 <= r["points"] <= 45.0, (seed, r)
