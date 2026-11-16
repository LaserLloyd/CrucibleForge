"""pytrap (port of the fast-fail question PYTRAP): reference 1.0 on 16 seeds per level, floors, per-line results schema,
determinism / ffseed, engine wiring (gradeable chain with element: check)."""
import json
import statistics

import pytest

from crucibleforge import chains, gen, graders
from crucibleforge.gen import pytrap as pt

SEED = "4.5.0"
CASE = "X"
SEEDS = range(1, 17)


def _g(level=1, ffseed=None, key=None, n=None):
    g = {"type": "pytrap", "level": level}
    if ffseed is not None:
        g["ffseed"] = ffseed
    if key is not None:
        g["key"] = key
    if n is not None:
        g["n"] = n
    return g


def _mat(g):
    return pt.materialize(g, release_seed=SEED, case_id=CASE)


def _ref(g):
    return pt.reference(g, release_seed=SEED, case_id=CASE)


def test_registered_and_grader_wired():
    assert "pytrap" in gen.families()
    assert "pytrap" in graders.GRADERS
    assert set(pt.LEVELS) == {1, 3}
    assert len(pt.TEMPLATES) >= 15


@pytest.mark.parametrize("level", [1, 3])
def test_reference_is_one_on_16_seeds(level):
    for s in SEEDS:
        g = _g(level, ffseed=s)
        m = _mat(g)
        r = pt.grade_pytrap(_ref(g), m["grader_config"])
        assert r["score"] == 1.0 and r["points"] == 100.0 and r["q"] == 1.0, (level, s)
        assert all(x["pass"] for x in r["results"])
        assert pt.grade(_ref(g), m) == 1.0


def _floor_scores(level):
    out = {}
    for s in SEEDS:
        g = _g(level, ffseed=s)
        m = _mat(g)
        inst = pt._inst_of(g, SEED, CASE)
        cfg = m["grader_config"]
        for name, ans in (("empty", ""), ("naive", pt.naive_reading(inst)), ("baseline", pt.baseline(inst)),
                          ("all_None", "\n".join(f"### {x['id']}\n" + "\n".join("None" for _ in x["true"])
                                                 for x in inst["hidden"]["snips"])),
                          ("all_True", "\n".join(f"### {x['id']}\n" + "\n".join("True" for _ in x["true"])
                                                 for x in inst["hidden"]["snips"]))):
            out.setdefault(name, []).append(pt.grade_pytrap(ans, cfg)["score"])
    return out


@pytest.mark.parametrize("level", [1, 3])
def test_floors(level):
    sc = _floor_scores(level)
    assert max(sc["empty"]) == 0.0
    for name, v in sc.items():
        assert statistics.mean(v) <= 0.30 + 1e-9, (level, name)   # the baseline is the 30-point anchor by construction
        assert max(v) <= 0.40, (level, name)
    assert statistics.mean(sc["baseline"]) == pytest.approx(0.30)


def test_results_schema_and_partial_credit():
    g = _g(1, ffseed=2)
    m = _mat(g)
    cfg = m["grader_config"]
    ref = _ref(g).split("\n")
    r = pt.grade_pytrap("\n".join(ref), cfg)
    n = m["meta"]["n_lines"]
    assert len(r["results"]) == n
    ids = [x["id"] for x in r["results"]]
    assert len(set(ids)) == n and ids[0] == "S1-L1"
    for x in r["results"]:
        assert set(x) >= {"id", "pass", "weight", "kind", "detail"} and x["weight"] in (0.0, 1.0)
        assert x["kind"] in pt.TEMPLATES
    json.dumps(r)
    ref[1] = "zzz"                       # one wrong line costs exactly one check
    r2 = pt.grade_pytrap("\n".join(ref), cfg)
    assert [x["pass"] for x in r2["results"]].count(False) == 1 and not r2["results"][0]["pass"]
    tw = sum(x["weight"] for x in r2["results"])
    assert r2["q"] == pytest.approx(1 - r2["results"][0]["weight"] / tw) and r2["score"] < 1.0
    # a missing line shifts the rest of that block: not free
    del ref[1]
    assert pt.grade_pytrap("\n".join(ref), cfg)["q"] < 1.0


def test_invalid_replies_score_zero_with_all_checks_failed():
    m = _mat(_g(1, ffseed=1))
    for bad in ("", "no idea", "def solve():\n    return 1\n"):
        r = pt.grade_pytrap(bad, m["grader_config"])
        assert r["score"] == 0.0 and len(r["results"]) == m["meta"]["n_lines"]
        assert not any(x["pass"] for x in r["results"])


def test_reply_formats_parse_alike():
    g = _g(1, ffseed=1)
    ref = _ref(g)
    cfg = _mat(g)["grader_config"]
    assert pt.grade_pytrap("```\n" + ref + "```", cfg)["score"] == 1.0
    alt = "\n".join(("**" + l[4:] + "**") if l.startswith("### ") else l + "  " for l in ref.split("\n"))
    assert pt.grade_pytrap(alt, cfg)["score"] == 1.0


def test_reply_cannot_set_the_truth():
    """nothing but the predicted lines is read from the reply: a reply that echoes the answer key's shape
    (extra headers, prose, a different line count) never scores above its matching lines"""
    g = _g(1, ffseed=1)
    cfg = _mat(g)["grader_config"]
    r = pt.grade_pytrap("### S9\n" + _ref(g), cfg)           # a stray header does not remap S1..S5
    assert r["score"] == 1.0                                  # the real blocks are still graded in place
    assert pt.grade_pytrap("### S1\n" + "\n".join(["x"] * 50), cfg)["q"] == 0.0


def test_no_answer_leak_in_prompt():
    for lv in (1, 3):
        inst = pt._inst_of(_g(lv, ffseed=2), SEED, CASE)
        for s in inst["hidden"]["snips"]:
            assert "\n".join(s["true"]) not in inst["prompt"]
        assert "CPython" not in inst["prompt"]               # the short neutral header (a longer one tripped a safeguard filter)
        assert inst["prompt"].startswith("For each Python program below")


def test_snippet_lines_and_naive_difference():
    for lv, tier in ((1, 1), (3, 3)):
        lo, hi = pt.LINES[tier]
        for s in (1, 2):
            for sn in pt.make_instance(s, tier)["hidden"]["snips"]:
                assert lo <= len(sn["true"]) <= hi and len(sn["true"]) == len(sn["naive"])
                assert all(sn["true"]) and sum(sn["diff"]) >= 0.5 * len(sn["true"])


def test_n_is_a_prefix():
    a = pt.make_instance(2, 1, 8)["hidden"]["snips"]
    assert pt.make_instance(2, 1, 5)["hidden"]["snips"] == a[:5]
    assert pt.make_instance(2, 1, 1)["hidden"]["snips"] == a[:1]


def test_deterministic_and_seed_effects():
    for lv in (1, 3):
        assert _mat(_g(lv, key="a")) == _mat(_g(lv, key="a"))
    assert _mat(_g(key="a")) != _mat(_g(key="b"))
    other = pt.materialize(_g(key="a"), release_seed="other", case_id=CASE)
    assert other["prompt"] != _mat(_g(key="a"))["prompt"]


@pytest.mark.parametrize("level", [1, 3])
def test_ffseed_reproduces_the_fastfail_instance(level):
    """the ff instance of seed s = the same RNG stream; pinned by the first snippet's shape (traps, line counts) so a drift
    in the templates is caught, and by identity of ffseed with the engine seed path"""
    for s in (1, 2, 3):
        a = _mat(_g(level, ffseed=s))
        assert a == _mat(_g(level, ffseed=s))
        assert a["prompt"] == pt.make_instance(s, level)["prompt"]
    assert _mat(_g(level, ffseed=1))["prompt"] != _mat(_g(level, ffseed=2))["prompt"]


def test_ffseed_matches_recorded_ff_digest():
    import hashlib
    got = {}
    for tier in (1, 3):
        for s in (1, 2, 3):
            snips = pt.make_instance(s, tier)["hidden"]["snips"]
            got[f"{tier}:{s}"] = hashlib.sha1(json.dumps([[x["code"], x["true"]] for x in snips]).encode()).hexdigest()[:12]
    assert got == FF_DIGESTS


# sha1 of [[code, true lines], ...] of the 5 snippets, computed from the ff module (branch t-pytrap c96cb21)
FF_DIGESTS = {'1:1': '1895569f1b47', '1:2': 'f6703e1149c4', '1:3': 'fdb20b5fee50', '3:1': '2fcd37fe0eaf', '3:2': '3ff0123fa73e', '3:3': 'ac49a85f8dbd'}


def test_mutants_do_not_score_one():
    g = _g(1, ffseed=3)
    m = _mat(g)
    for ans in pt.mutants(g, release_seed=SEED, case_id=CASE):
        assert pt.grade(ans, m) < 1.0


def test_engine_score_matches_family_grade():
    g = _g(3, ffseed=2)
    case = {"id": CASE, "category": "coding", "generator": g}
    gen.materialize_case(case, SEED)
    assert gen.engine_score(case, _ref(g)) == 1.0
    wrong = pt.naive_reading(pt._inst_of(g, SEED, CASE))
    assert gen.engine_score(case, wrong) == pytest.approx(pt.grade(wrong, case))


def _prof(items):
    return {"name": "t", "cases": {"coding": []},
            "scores": {"work": {"parts": {"main": {"max": 10, "scoring": ["coding"], "cases": []}}}},
            "chains": {"work/main": {"flat": items}}}


def test_chain_check_gradeable_and_elements():
    chains._judge_reason.cache_clear()
    chains._shape_reason.cache_clear()
    prof = _prof([{"family": "pytrap", "levels": {1: 6}, "element": "check", "element_weight": "check",
                   "average": "unit", "ceiling": 8000}])
    chains.check_gradeable(prof)
    cs = chains.chain_cases(prof)
    assert cs
    inst = chains.instance(cs[0], 1, 1, release_seed="R")
    case = chains.materialize(inst)
    assert case["grader"] == "pytrap"
    g = dict(case["generator"])
    ans = pt.reference(g, release_seed=gen.release_seed(), case_id=case["id"])
    res = chains.check_results(case, ans)
    assert res and len(res) == case["meta"]["n_lines"] and all(r[1] == 1.0 for r in res)
    bad = chains.check_results(case, "")
    assert len(bad) == len(res) and not any(r[1] for r in bad)
