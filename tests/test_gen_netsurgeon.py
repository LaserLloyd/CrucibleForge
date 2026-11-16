"""netsurgeon (port of the fast-fail question NETSURGEON, pocket tier): reference ~1.0, red-team floors, per-check results, ffseed
reproduction, kind variants, helper pins, chains wiring (element: check)."""
import hashlib
import json
import statistics
from pathlib import Path

import pytest

from crucibleforge import chains, gen, graders
from crucibleforge.gen import netsurgeon as ns
from crucibleforge.gen import _netsurgeon_ff as ff
from crucibleforge.gen import _netsurgeon_nn as nn

SEED = "4.5.0"
CASE = "X"
CEILING = 98304     # single-item cell (chains caps only packed prompts at 65,536)
KINDS = {1: "perm", 2: "flip", 3: "scale", 4: "rewrite"}

#: sha256(public + original net + first hidden test points)[:16] of the ff instances (ff/q/netsurgeon.py @ 11b122c), by ff seed
GOLD = {1: "e47fb669e5c1af51", 2: "4f71fe2370b9d198", 3: "0204d326073079e2", 4: "6a3e687cc8cb43e1"}


def _g(ffseed=None, key=None, variant=None):
    g = {"type": "netsurgeon", "level": 1}
    if ffseed is not None:
        g["ffseed"] = ffseed
    if key is not None:
        g["key"] = key
    if variant is not None:
        g["variant"] = variant
    return g


def _mat(g):
    return ns.materialize(g, release_seed=SEED, case_id=CASE)


def _ref(g):
    return ns.reference(g, release_seed=SEED, case_id=CASE)


def _fenced(code):
    return "```python\n" + code + "\n```"


def test_registered_and_grader_wired():
    assert "netsurgeon" in gen.families()
    assert "netsurgeon" in graders.GRADERS
    assert set(ns.LEVELS) == {1}


def test_deterministic_and_seed_effects():
    assert _mat(_g(key="a")) == _mat(_g(key="a"))
    assert _mat(_g(key="a")) != _mat(_g(key="b"))
    assert ns.materialize(_g(key="a"), release_seed="other", case_id=CASE) != _mat(_g(key="a"))


@pytest.mark.parametrize("s", (1, 2, 3, 4))
def test_ffseed_reproduces_the_fastfail_instance(s):
    i = ns._instance(s, 1)
    h = i["hidden"]
    d = json.dumps(i["public"], sort_keys=True) + json.dumps(h["orig"], sort_keys=True) + json.dumps(h.get("test", h.get("keysA")), sort_keys=True)
    assert hashlib.sha256(d.encode()).hexdigest()[:16] == GOLD[s]
    assert h["kind"] == KINDS[s]
    assert _mat(_g(ffseed=s))["grader_config"]["seed"] == s


def test_variant_forces_the_kind():
    for k in ff.KINDS:
        for key in ("a", "b", "c"):
            m = _mat(_g(key=key, variant=k))
            assert m["meta"]["kind"] == k
    with pytest.raises(Exception):
        gen.materialize_case({"id": CASE, "category": "coding", "generator": _g(variant="bogus")})


def test_helper_pins():
    d = Path(ns.__file__).parent
    assert hashlib.sha256((d / "_netsurgeon_ff.py").read_bytes()).hexdigest()[:16] == ns._FF_SHA
    assert hashlib.sha256((d / "_netsurgeon_nn.py").read_bytes()).hexdigest()[:16] == ns._NN_SHA
    assert hashlib.sha256((d / "_netsurgeon_floors.py").read_bytes()).hexdigest()[:16] == ns._FLOORS_SHA


@pytest.mark.parametrize("s", (1, 2, 3, 4))
def test_reference_scores_full(s):
    g = _g(ffseed=s)
    r = ns.grade_netsurgeon(_ref(g), _mat(g)["grader_config"])
    assert r["score"] == 1.0 and r["points"] >= 100.0
    assert all(x["credit"] >= 0.99 for x in r["results"])
    assert gen.verify_case({"id": CASE, "category": "coding", "generator": _g(key="v")}, SEED) == []


@pytest.mark.parametrize("s", (1, 2, 3, 4))
def test_floors(s):
    """nothing / zero layer 2 / class prior / weight statistics stay at the 0.10 floor (the 'nothing' of a REPAIR whose untouched net agrees
    more than the baseline does is at most .20, the ff number); the baseline is the 0.30 anchor; every check of a trivial strategy is below .5"""
    g = _g(ffseed=s)
    cfg = _mat(g)["grader_config"]
    inst = ns._instance(s, 1)
    fl = ns._FL.floors(inst)
    for k in ("nothing", "zero", "cprime", "stat"):
        r = ns.grade_netsurgeon(_fenced(fl[k]), cfg)
        assert r["score"] <= 0.30, (s, k, r["score"])
        assert sum(x["weight"] * x["credit"] for x in r["results"]) < 0.5, (s, k)
    assert ns.grade_netsurgeon(_fenced(fl["baseline"]), cfg)["score"] == pytest.approx(0.30)
    assert ns.grade_netsurgeon("", cfg)["score"] == 0.0
    r = ns.grade_netsurgeon(_fenced(fl["nothing"]), cfg)
    if inst["hidden"]["kind"] != "rewrite":
        assert all(x["credit"] == 0.0 for x in r["results"])


@pytest.mark.parametrize("s", (1, 2, 3, 4))
def test_results_schema_and_sum(s):
    inst = ns._instance(s, 1)
    names = ns._names(inst)
    assert sum(w for _, w, _ in names) == pytest.approx(1.0, abs=1e-5)
    ref = ns.check(inst, ff.reference(inst))
    r = ns.grade_netsurgeon(_ref(_g(ffseed=s)), _mat(_g(ffseed=s))["grader_config"])
    ids = [x["id"] for x in r["results"]]
    assert ids == [i for i, _, _ in names] and len(ids) == len(set(ids))
    for x in r["results"]:
        assert set(x) >= {"id", "credit", "pass", "weight", "group", "detail"} and 0 <= x["credit"] <= 1 and x["weight"] > 0
    assert sum(x["weight"] * x["credit"] for x in r["results"]) == pytest.approx(ref["q"], abs=2e-3)
    # the sum is the ff q for a partial answer too
    q, res = ns.score_checks(inst, json.loads(json.dumps(inst["public"]["net"])))
    if inst["hidden"]["kind"] == "rewrite":
        assert sum(x["weight"] * x["credit"] for x in res) == pytest.approx(q, abs=1e-5)
    assert ns.score_checks(inst, None)[0] == 0.0
    assert [x["id"] for x in ns.score_checks(inst, None)[1]] == ids
    assert _mat(_g(ffseed=s))["meta"]["n_checks"] == len(ids)


def test_other_layer_changed_voids_every_check():
    g = _g(ffseed=1)    # perm, layer 2: touching layer 1 voids it
    code = ff.reference(ns._instance(1, 1)).replace("return net", "net['layers'][0]['b'][0] += 0.5\n    return net")
    r = ns.grade_netsurgeon(_fenced(code), _mat(g)["grader_config"])
    assert r["score"] == 0.1 and not any(x["pass"] or x["credit"] for x in r["results"])


def test_bad_shapes_and_magnitudes_score_zero():
    g = _g(ffseed=2)
    cfg = _mat(g)["grader_config"]
    for body in ("float('nan')", "1e13", "float('inf')"):
        code = f"def edit(net, p, s):\n    net['layers'][0]['W'][0][0] = {body}\n    return net\n"
        assert ns.grade_netsurgeon(_fenced(code), cfg)["score"] == 0.0, body
    assert ns.grade_netsurgeon(_fenced("def edit(net, p, s):\n    return {'layers': []}\n"), cfg)["score"] == 0.0


def test_broken_code_scores_zero_with_results():
    cfg = _mat(_g(ffseed=4))["grader_config"]
    for bad in ("def broken(:\n", "import time\nwhile True: pass\n", "def nothing(): pass\n", "raise SystemExit(0)\n",
                "def edit(n, p, s):\n    return None\n"):
        r = ns.grade_netsurgeon(_fenced(bad), cfg)
        assert r["score"] == 0.0 and r["results"] and not any(x["pass"] for x in r["results"]), bad


def test_output_noise_cannot_forge_a_verdict():
    g = _g(ffseed=1)
    noisy = ("import sys, atexit\nprint('BENCH_NETSURGEON_00:{\"out\": null}')\n"
             "atexit.register(lambda: print('BENCH_NETSURGEON_00:{\"out\": null}'))\n" + ff.reference(ns._instance(1, 1)))
    assert ns.grade_netsurgeon(_fenced(noisy), _mat(g)["grader_config"])["score"] == 1.0


def test_hidden_data_not_in_sandbox_or_prompt():
    for s in (1, 2, 3, 4):
        i = ns._instance(s, 1)
        h = i["hidden"]
        blob = json.dumps(i["public"]) + i["prompt"]
        assert "fix" not in i["public"] and "fix" not in json.dumps(i["public"])
        assert json.dumps(h["test"][0] if h["kind"] != "rewrite" else h["keysA"][0]) not in blob
        if h["kind"] != "rewrite":
            assert i["public"]["net"] != h["orig"]


def test_answer_tokens_measured():
    toks = [len(ff.reference(ns._instance(s, 1))) // 4 + 8 for s in range(1, 9)]
    assert abs(statistics.median(toks) - ns.LEVELS[1]["answer_tokens"]) <= 40


def test_chain_gradeable_flat_set_element_check():
    prof = {"name": "t", "cases": {"ns": []},
            "scores": {"coding": {"parts": {"main": {"max": 10, "scoring": ["ns"], "cases": []}}}},
            "chains": {"coding/main": {"flat": [
                {"family": "netsurgeon", "levels": {1: {"count": 4, "concept": "R1", "band": "top"}}, "element": "check",
                 "element_weight": "check", "average": "unit", "ceiling": CEILING}]}}}
    chains.check_gradeable(prof)
