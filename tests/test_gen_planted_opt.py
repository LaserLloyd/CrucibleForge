"""planted_opt (T1 1d): determinism, reference/mutants/lazy, reproduction of the fast-fail instances, prompt hygiene,
engine wiring, and the generic baselines' anchor (build/T1-MAIN-CODING.md)."""
import copy

import pytest

from crucibleforge import gen, graders
from crucibleforge.gen import planted_opt as po

SEED = "4.5.0"


def _g(level=4, ffseed=3):
    return {"type": "planted_opt", "level": level, "ffseed": ffseed}


def test_registered_and_grader_wired():
    assert "planted_opt" in gen.families()
    assert "optimize" in graders.GRADERS


def test_deterministic_and_seed_effects():
    a = po.materialize(_g(2, 1), release_seed=SEED, case_id="D")
    assert a == po.materialize(_g(2, 1), release_seed=SEED, case_id="D")
    assert a != po.materialize(_g(2, 2), release_seed=SEED, case_id="D")
    k1 = po.materialize({"type": "planted_opt", "level": 2, "key": "a"}, release_seed=SEED, case_id="D")
    k2 = po.materialize({"type": "planted_opt", "level": 2, "key": "b"}, release_seed=SEED, case_id="D")
    assert k1["grader_config"]["seed"] != k2["grader_config"]["seed"]


@pytest.mark.parametrize("ffseed,ref", [(1, 2261), (2, 2209), (3, 2206)])
def test_reproduces_fastfail_level4_instances(ffseed, ref):
    # the fast-fail harness (wt-ff, ff/q/planted_opt2.py) reported these reference costs
    m = po.materialize(_g(4, ffseed), release_seed=SEED, case_id="X")
    assert m["meta"]["ref_cost"] == ref and m["meta"]["n"] == 400


def test_planted_assignment_is_feasible_and_costs_the_reference():
    inst = po.instance(3, 4)
    assert po.violated(inst["terms"], inst["ref_x"]) == 0
    assert po.cost_of(inst["terms"], inst["ref_x"]) == inst["ref_cost"]


def test_reference_mutants_and_lazy_answers():
    g = _g(2, 1)
    m = po.materialize(g, release_seed=SEED, case_id="R")
    assert po.grade(po.reference(g, release_seed=SEED, case_id="R"), m) == 1.0
    for x in po.mutants(g, release_seed=SEED, case_id="R"):
        assert po.grade(x, m) < 1.0
    assert po.grade("", m) == 0.0
    assert po.grade("def solve(n, k, terms):\n    return None\n", m) == 0.0
    assert gen.verify_case({"id": "R", "generator": copy.deepcopy(g)}, SEED) == []


def test_invalid_replies_score_zero():
    m = po.materialize(_g(2, 1), release_seed=SEED, case_id="R")
    cfg = m["grader_config"]
    for code in ("```python\ndef solve(n, k, terms):\n    return [0] * (n + 1)\n```",
                 "```python\ndef solve(n, k, terms):\n    return [5] * n\n```",
                 "```python\ndef solve(n, k, terms):\n    return [True] * n\n```",
                 "```python\ndef solve(n, k, terms):\n    while True:\n        pass\n```"):
        assert po.grade_optimize(code, cfg)["score"] == 0.0


def test_prompt_does_not_name_the_structure():
    p = po.materialize(_g(4, 1), release_seed=SEED, case_id="P")["prompt"].lower()
    for w in ("linear", "modular", "mod 5", "equation", "affine", "gaussian", "planted", "hidden"):
        assert w not in p
    assert "20,000 tokens" in p


_ANNEAL_N = """
def _anneal_n(n, k, tabs, of, x, idx, rng, iters, T0, T1):
    rr = rng.random; ri = rng.randrange; ex = math.exp
    T = T0
    for it in range(iters):
        if it % 500 == 0:
            T = T0 * (T1 / T0) ** (it / iters)
        v = ri(n)
        a = x[v]
        b = ri(k - 1)
        if b >= a:
            b += 1
        dv = b - a
        d = 0
        ov = of[v]
        for t, s in ov:
            tb = tabs[t]
            d += tb[idx[t] + dv * s] - tb[idx[t]]
        if d <= 0 or rr() < ex(-d / T):
            x[v] = b
            for t, s in ov:
                idx[t] += dv * s
"""


def test_generic_baseline_anchors_near_30_points():
    # The anchor (BASE_FRAC) is a constant, so grading never runs a baseline. This test checks that the generic
    # annealer (po.BASE's move set and schedule) lands well below the structure-finding solvers. po.BASE is
    # CPU-time-boxed, so its result fell with machine load (18.2 points under load vs ~30 unloaded); here the
    # same annealer runs a FIXED iteration count in-process, so the result is deterministic whatever the load
    # (ffseed 3, 7M iterations: cost 3256, 23.2 points; ~12-19 CPU s).
    import random
    inst = po.instance(3, 4)
    g = {}
    exec(po.COMMON + _ANNEAL_N, g)
    rng = random.Random(1)
    tabs, of = g["_prep"](inst["n"], po.K, inst["terms"])
    x = [rng.randrange(po.K) for _ in range(inst["n"])]
    idx = g["_idx"](inst["terms"], po.K, x)
    g["_anneal_n"](inst["n"], po.K, tabs, of, x, idx, rng, 7_000_000, 10.0, 0.3)
    cost = po.cost_of(inst["terms"], x)
    pts = po.points(cost, po.cost_base(inst), inst["ref_cost"])
    assert cost == 3256 and pts == 23.2, (cost, pts)
    assert 15.0 <= pts <= 45.0


# ---- the work clock (seg PACKS, Nov 14): a reply grades the same on any machine at any load
def _wcfg(cpu_s=1.0):
    cfg = dict(po.materialize(_g(2, 1), release_seed=SEED, case_id="W")["grader_config"])
    cfg["cpu_s"] = cpu_s
    return cfg


_COUNT = """```python
import time, random
CPU_LIMIT_S = 1.0
def solve(n, k, terms):
    t0 = time.process_time(); it = 0
    while time.process_time() - t0 < 0.5 * CPU_LIMIT_S:
        it += 1
    r = random.Random()
    return [(it + r.randrange(k) + random.randrange(k)) % k for _ in range(n)]
```"""


def test_work_clock_is_deterministic_and_seeds_random():
    a = po.grade_optimize(_COUNT, _wcfg())
    b = po.grade_optimize(_COUNT, _wcfg())
    assert a["detail"] == b["detail"] and a["cost"] == b["cost"], (a, b)
    assert 0.4 <= a["cpu_s"] <= 1.0


def test_work_clock_limit_and_no_threads():
    over = "```python\nimport time\ndef solve(n, k, terms):\n    while time.process_time() < 1.5:\n        pass\n    return [0] * n\n```"
    r = po.grade_optimize(over, _wcfg())
    assert r["score"] == 0.0 and "work limit" in r["detail"], r
    thr = ("```python\nimport threading\ndef solve(n, k, terms):\n    t = threading.Thread(target=lambda: None)\n"
           "    t.start(); t.join()\n    return [0] * n\n```")
    assert po.grade_optimize(thr, _wcfg())["score"] == 0.0


def test_prompt_states_the_work_clock():
    p = po.materialize(_g(4, 1), release_seed=SEED, case_id="P")["prompt"]
    assert "work clock" in p and "same amount of work on any machine" in p


def test_grade_detail_names_the_interpreter():
    # a work-clock grade is reproducible only on the same Python build; the detail says which one graded it
    import platform
    r = po.grade_optimize(_COUNT, _wcfg())
    assert r["py"] == platform.python_version() and f"py {r['py']}" in r["detail"], r
    assert ("not the pinned" in r["detail"]) == (r["py"] != po.PINNED_PY)
