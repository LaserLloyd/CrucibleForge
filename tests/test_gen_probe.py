"""probe (port of the fast-fail question PROBE, tier 3 only): reference, red-team floors, per-case checks, ffseed reproduction, the isolated
measurement channel, helper pins, chains wiring (element: check)."""
import hashlib
import json
import statistics
from pathlib import Path

import pytest

from crucibleforge import chains, gen, graders
from crucibleforge.gen import probe as pr
from crucibleforge.gen import _probe_ff as ff

SEED = "4.5.0"
CASE = "X"
CEILING = 98304     # single-item cell (chains caps only packed prompts at 65,536)
CFG = lambda s: {"seed": s, "tier": 3, "cpu_s": 20.0}

#: sha256(prompt + public + hidden)[:16] of the ff instances (ff/q/probe.py @ 4528b74), by ff seed, tier 3
GOLD = {1: "ee548dfb2d72ffac", 2: "f81e66a49478807d", 3: "593f1add96aae4ac"}


def _g(ffseed=None, key=None):
    g = {"type": "probe", "level": 3}
    if ffseed is not None:
        g["ffseed"] = ffseed
    if key is not None:
        g["key"] = key
    return g


def _mat(g):
    return pr.materialize(g, release_seed=SEED, case_id=CASE)


def _ref(g):
    return pr.reference(g, release_seed=SEED, case_id=CASE)


def _fenced(code):
    return "```python\n" + code + "\n```"


def test_registered_and_grader_wired():
    assert "probe" in gen.families()
    assert "probe" in graders.GRADERS
    assert set(pr.LEVELS) == {3}


def test_deterministic_and_seed_effects():
    assert _mat(_g(key="a")) == _mat(_g(key="a"))
    assert _mat(_g(key="a")) != _mat(_g(key="b"))
    assert pr.materialize(_g(key="a"), release_seed="other", case_id=CASE) != _mat(_g(key="a"))


@pytest.mark.parametrize("s", (1, 2, 3))
def test_ffseed_reproduces_the_fastfail_instance(s):
    i = pr._instance(s, 3)
    d = i["prompt"] + json.dumps(i["public"], sort_keys=True) + json.dumps(i["hidden"], sort_keys=True)
    assert hashlib.sha256(d.encode()).hexdigest()[:16] == GOLD[s]
    assert _mat(_g(ffseed=s))["grader_config"]["seed"] == s


def test_helper_pins():
    d = Path(pr.__file__).parent
    assert hashlib.sha256((d / "_probe_ff.py").read_bytes()).hexdigest()[:16] == pr._FF_SHA
    assert hashlib.sha256((d / "_probe_floors.py").read_bytes()).hexdigest()[:16] == pr._FLOORS_SHA


@pytest.mark.parametrize("s", (1, 2))
def test_reference_scores_full_and_checks(s):
    g = _g(ffseed=s)
    r = pr.grade_probe(_ref(g), _mat(g)["grader_config"])
    assert r["score"] == 1.0 and r["points"] >= 100.0 and r["n"] <= 40
    assert len(r["results"]) == 10 and sum(x["weight"] for x in r["results"]) == pytest.approx(1.0)
    ids = [x["id"] for x in r["results"]]
    assert ids == [f"pred/q{i}" for i in range(10)]
    assert statistics.mean(x["credit"] for x in r["results"]) > 0.7
    for x in r["results"]:
        assert set(x) >= {"id", "credit", "pass", "weight", "group", "detail"} and 0 <= x["credit"] <= 1
    assert gen.verify_case({"id": CASE, "category": "coding", "generator": _g(key="v")}, SEED) == []


@pytest.mark.parametrize("s", (1, 2))
def test_floors(s):
    """a constant, the linear closed form and 2000 dense readings stay at the 0.10..0.30 floor; the baseline is the 0.30 anchor"""
    g = _g(ffseed=s)
    cfg = _mat(g)["grader_config"]
    fl = pr._FL.floors(pr._instance(s, 3))
    for k in ("zero_const", "zero_lin", "dense2000"):
        r = pr.grade_probe(_fenced(fl[k]), cfg)
        assert r["score"] <= 0.31, (s, k, r["score"])
        assert statistics.mean(x["credit"] for x in r["results"]) <= 0.45, (s, k)
    assert pr.grade_probe(_fenced(fl["baseline"]), cfg)["score"] == pytest.approx(0.30)
    assert pr.grade_probe("", cfg)["score"] == 0.0


def test_anchors_equal_ff_for_seed_1():
    """ff stored q_base 0.1173 and q_ref 0.2237 for seed 1"""
    qb, qr = pr._anchors(1, 3)
    assert qb == pytest.approx(0.1173, abs=1e-4) and qr == pytest.approx(0.2237, abs=1e-4)


def test_malformed_and_broken_score_zero_with_ten_checks():
    cfg = CFG(1)
    for bad in ("def broken(:\n", "def solve(measure, queries):\n    return [1, 2]\n", "def solve(measure, queries):\n    return None\n",
                "def solve(measure, queries):\n    return [float('nan')] * 10\n", "def nothing(): pass\n", "raise SystemExit(0)\n",
                "def solve(measure, queries):\n    measure(5, 5)\n    return [0.1] * 10\n"):
        r = pr.grade_probe(_fenced(bad), cfg)
        assert r["score"] == 0.0 and len(r["results"]) == 10 and not any(x["credit"] for x in r["results"]), bad


def test_channel_cannot_be_corrupted_and_counts_every_call():
    inst = pr._instance(1, 3)
    noisy = ("def solve(measure, queries):\n    print('{\"done\": [0.0]}')\n    import sys\n    sys.stdout.write('{\"done\": [9]}\\n')\n"
             "    measure(0.5, 1.0)\n    try:\n        measure(5.0, 1.0)\n    except ValueError:\n        pass\n    return [0.1] * 10\n")
    r = pr._run(noisy, inst)
    assert r["ok"] and r["n"] == 2 and len(r["ans"]) == 10


def test_measurement_cap():
    inst = pr._instance(1, 3)
    code = ("def solve(measure, queries):\n    k = 0\n    try:\n        for i in range(2100):\n            measure(0.5, 1.0); k += 1\n"
            "    except ValueError:\n        pass\n    return [float(k)] + [0.0] * 9\n")
    r = pr._run(code, inst)
    assert r["ok"] and r["ans"][0] == 2000 and r["n"] == 2001


def test_hidden_truth_not_in_prompt_or_setup():
    i = pr._instance(1, 3)
    blob = json.dumps(i["public"]) + i["prompt"]
    for v in i["hidden"]["truth"]:
        assert repr(v) not in blob


def test_factor_and_accuracy_scale():
    assert ff.factor(6) == 1.0 and ff.factor(12) == pytest.approx(1 - 1 / 8.5) and ff.factor(5000) == 0.1


def test_answer_tokens_measured():
    toks = [len(pr.reference_code(pr._instance(s, 3))) // 4 + 8 for s in range(1, 5)]
    assert abs(statistics.median(toks) - pr.LEVELS[3]["answer_tokens"]) <= 40


def test_chain_gradeable_flat_set_element_check():
    prof = {"name": "t", "cases": {"pr": []},
            "scores": {"coding": {"parts": {"main": {"max": 10, "scoring": ["pr"], "cases": []}}}},
            "chains": {"coding/main": {"flat": [
                {"family": "probe", "levels": {3: {"count": 4, "concept": "R1", "band": "top"}}, "element": "check",
                 "element_weight": "check", "average": "unit", "ceiling": CEILING}]}}}
    chains.check_gradeable(prof)
