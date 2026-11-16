"""blackbox (port of the fast-fail question BLACK BOX): reference 1.0, red-team floors, per-check results, ffseed reproduction, helper
pins, chains wiring (element: check)."""
import hashlib
import json
import statistics
from pathlib import Path

import pytest

from crucibleforge import chains, gen, graders
from crucibleforge.gen import blackbox as bb
from crucibleforge.gen import _blackbox_ff as ff

SEED = "4.5.0"
CASE = "X"
LEVELS = (1, 2, 3, 4)
CEILING = 65536     # chains caps one prompt at 65,536 (twice the largest passing ff reply, 57k, would be 115k)

#: sha256(prompt + "|" + hidden hex)[:16] of the ff instances (ff/q/blackbox.py @ 86e63b1), "<tier>:<seed>"
GOLD = {"1:1": "1f43c49dc68457fa", "1:2": "d6f5a527c1f7892b", "1:3": "af8282b2b0021b97",
        "2:1": "61e71410e45adc57", "2:2": "c20ce479ff0a8ced", "2:3": "4c1b879541cf4925",
        "3:1": "22bd83efd4ab2208", "3:2": "3803eb053f3ab3b9", "3:3": "a9399f0286125e45",
        "4:1": "7132c649a2254f60", "4:2": "f2775b15ed7e48e3", "4:3": "a2e057fc90afbe84"}


def _g(level=3, ffseed=None, key=None):
    g = {"type": "blackbox", "level": level}
    if ffseed is not None:
        g["ffseed"] = ffseed
    if key is not None:
        g["key"] = key
    return g


def _mat(g):
    return bb.materialize(g, release_seed=SEED, case_id=CASE)


def _ref(g):
    return bb.reference(g, release_seed=SEED, case_id=CASE)


def _fenced(code):
    return "```python\n" + code + "\n```"


def test_registered_and_grader_wired():
    assert "blackbox" in gen.families()
    assert "blackbox" in graders.GRADERS
    assert set(bb.LEVELS) == set(LEVELS)


def test_deterministic_and_seed_effects():
    assert _mat(_g(key="a")) == _mat(_g(key="a"))
    assert _mat(_g(key="a")) != _mat(_g(key="b"))
    assert bb.materialize(_g(key="a"), release_seed="other", case_id=CASE) != _mat(_g(key="a"))


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("s", (1, 2, 3))
def test_ffseed_reproduces_the_fastfail_instance(level, s):
    m = _mat(_g(level, ffseed=s))
    inst = bb._instance(s, level)
    assert hashlib.sha256((m["prompt"] + "|" + inst["public"]["hex"]).encode()).hexdigest()[:16] == GOLD[f"{level}:{s}"]


def test_helper_pins():
    """chains.revision() digests only blackbox.py: it pins its two helper modules"""
    d = Path(bb.__file__).parent
    assert hashlib.sha256((d / "_blackbox_ff.py").read_bytes()).hexdigest()[:16] == bb._FF_SHA
    assert hashlib.sha256((d / "_blackbox_floors.py").read_bytes()).hexdigest()[:16] == bb._FLOORS_SHA


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("s", (1, 2, 3))
def test_reference_scores_full(level, s):
    g = _g(level, ffseed=s)
    m = _mat(g)
    r = bb.grade_blackbox(_ref(g), m["grader_config"])
    assert r["score"] == 1.0 and r["points"] == 100.0
    assert all(x["pass"] and x["credit"] == 1.0 for x in r["results"])
    assert gen.verify_case({"id": CASE, "category": "coding", "generator": _g(level, key="v")}, SEED) == []


@pytest.mark.parametrize("level", LEVELS)
def test_floors(level):
    """each red-team floor (nothing, copy the examples, lazy u16, first-example fit) stays near the 0.30 anchor (16-seed maxima .34-.37 below level 4; level 4 greedy max .59); the brute-force
    layout search at level 4 is the one that sees through the public examples (documented in the merge note)"""
    for s in (1, 2, 3):
        g = _g(level, ffseed=s)
        m = _mat(g)
        inst = bb._instance(s, level)
        for k, code in bb._FL.floors(inst).items():
            r = bb.grade_blackbox(_fenced(code), m["grader_config"])
            if k == "baseline":
                assert r["score"] == pytest.approx(0.30)
            elif k == "brute":
                assert r["score"] < 1.0
            else:
                assert r["score"] <= (0.60 if (level, k) == (4, "greedy") else 0.40), (level, s, k, r["score"])
    assert bb.grade_blackbox("", m["grader_config"])["score"] == 0.0


@pytest.mark.parametrize("level", LEVELS)
def test_results_schema_and_sum_equals_ff_q(level):
    g = _g(level, ffseed=2)
    m = _mat(g)
    inst = bb._instance(2, level)
    ref_out = json.loads(json.dumps([dict(zip([k for k, *_ in row], [v for _, v, *_ in row])) for row in inst["hidden"]["exp"]]))
    # a decoder that is right on half the fields and drops the last record
    part = [{k: v for i, (k, v) in enumerate(o.items()) if i % 2 == 0} for o in ref_out[:-1]]
    for out in (ref_out, part, []):
        q, res = ff.score_checks(inst, out)
        assert q == pytest.approx(ff.score(inst, out)[0], abs=1e-5)
        assert sum(x["weight"] for x in res) == pytest.approx(1.0, abs=1e-5)
        ids = [x["id"] for x in res]
        assert len(ids) == len(set(ids)) and "count" in ids
        assert ("skip" in ids) == (level in (2, 3))
        for x in res:
            assert set(x) >= {"id", "credit", "pass", "weight", "group"} and 0 <= x["credit"] <= 1 and x["weight"] > 0
    r = bb.grade_blackbox(_fenced(bb._FL.NOTHING), m["grader_config"])
    assert [x["id"] for x in r["results"]] == [x["id"] for x in ff.score_checks(inst, ref_out)[1]]
    assert m["meta"]["n_checks"] == len(r["results"])


def test_checks_per_level_and_rungs():
    n = {lv: len(bb.grade_blackbox(_ref(_g(lv, ffseed=1)), _mat(_g(lv, ffseed=1))["grader_config"])["results"]) for lv in LEVELS}
    assert n[3] >= 14
    res = bb.grade_blackbox(_ref(_g(3, ffseed=1)), _mat(_g(3, ffseed=1))["grader_config"])["results"]
    groups = {x["group"] for x in res}
    assert {"framing", "damage", "kind", "delta", "tag", "bits", "enum", "varint"} <= groups


def test_broken_code_scores_zero_with_results():
    m = _mat(_g(3, ffseed=1))
    cfg = m["grader_config"]
    for bad in ("def broken(:\n", "import time\nwhile True: pass\n", "def nothing(): pass\n", "raise SystemExit(0)\n",
                "def decode(d):\n    return None\n"):
        r = bb.grade_blackbox(_fenced(bad), cfg)
        assert r["score"] == 0.0 and r["results"] and not any(x["pass"] for x in r["results"]), bad


def test_output_noise_cannot_forge_a_verdict():
    g = _g(3, ffseed=1)
    inst = bb._instance(1, 3)
    noisy = ("import sys, atexit\nprint('BENCH_BLACKBOX_00:{\"out\": []}')\n"
             "atexit.register(lambda: print('BENCH_BLACKBOX_00:{\"out\": []}'))\n" + bb.reference_code(inst))
    assert bb.grade_blackbox(_fenced(noisy), _mat(g)["grader_config"])["score"] == 1.0


def test_answer_tokens_measured():
    for lv in LEVELS:
        toks = [len(bb.reference_code(bb._instance(s, lv))) // 4 + 8 for s in range(1, 9)]
        assert abs(statistics.median(toks) - bb.LEVELS[lv]["answer_tokens"]) <= 40


def test_chain_gradeable_flat_set_element_check():
    prof = {"name": "t", "cases": {"bb": []},
            "scores": {"coding": {"parts": {"main": {"max": 10, "scoring": ["bb"], "cases": []}}}},
            "chains": {"coding/main": {"flat": [
                {"family": "blackbox", "levels": {3: {"count": 4, "concept": "C5", "band": "top"}}, "element": "check",
                 "element_weight": "check", "average": "unit", "ceiling": CEILING}]}}}
    chains.check_gradeable(prof)
