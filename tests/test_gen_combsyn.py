"""GEN2-F: combsyn (planted combinator synthesis in a seeded rewrite calculus). S8-DESIGN generator contract plus
an independent reducer written from the PROMPT text and an independent brute-force check of the size limits."""
import itertools
import re
import statistics

import pytest

from crucibleforge import gen
from crucibleforge.gen import combsyn as cs

LEVELS = [1, 2, 3, 4]          # 30-seed contract; levels 5-6 enumerate deeper and get 3 seeds below


def _g(lv):
    return {"type": "combsyn", "level": lv}


def _mat(lv, i):
    return gen.get("combsyn").materialize(_g(lv), release_seed="t", case_id=f"C{i}")


# ---- independent calculus: terms are nested lists, rules read from the prompt ---------------------------------
def _read(s):
    """'x z (y z)' -> nested lists (left-assoc)."""
    toks = re.findall(r"[A-Za-z]|\(|\)", s)

    def seq(i):
        cur = None
        while i < len(toks) and toks[i] != ")":
            if toks[i] == "(":
                item, i = seq(i + 1)
                i += 1
            else:
                item, i = toks[i], i + 1
            cur = item if cur is None else [cur, item]
        return cur, i
    return seq(0)[0]


def _rules(prompt):
    out = {}
    for m in re.finditer(r"^    ([A-Z]) ((?:[xyz] )+) ->  (.+)$", prompt, re.M):
        out[m.group(1)] = (m.group(2).split(), _read(m.group(3)))
    return out


def _sub(body, env):
    return [_sub(body[0], env), _sub(body[1], env)] if isinstance(body, list) else env.get(body, body)


def _whnf_norm(t, rules, fuel):
    """Normal form by head reduction then arguments (a different algorithm from the generator's stepper)."""
    while True:
        args, h = [], t
        while isinstance(h, list):
            args.insert(0, h[1])
            h = h[0]
        if h in rules and len(args) >= len(rules[h][0]):
            fuel[0] -= 1
            assert fuel[0] > 0
            params, body = rules[h]
            t = _sub(body, dict(zip(params, args)))
            for x in args[len(params):]:
                t = [t, x]
            continue
        out = h
        for x in args:
            out = [out, _whnf_norm(x, rules, fuel)]
        return out


def _count(t):
    return _count(t[0]) + _count(t[1]) if isinstance(t, list) else 1


def _tasks(prompt):
    return {m.group(1): (_read(m.group(2)), int(m.group(3)))
            for m in re.finditer(r"^(T\d)\. T a b c  ->  (.+?)\s+\(at most (\d+) constants\)$", prompt, re.M)}


def _terms(answer):
    ns = {}
    exec(answer, ns)
    return ns["TERMS"]


def test_registered():
    assert "combsyn" in gen.families()


@pytest.mark.parametrize("lv", LEVELS)
def test_deterministic_distinct_sizes_and_reference(lv):
    seen, ps = set(), []
    fam = gen.get("combsyn")
    for i in range(30):
        m = _mat(lv, i)
        assert m == _mat(lv, i)
        seen.add(m["prompt"])
        ps.append(len(m["prompt"]) / 4)
        rules, tasks, terms = _rules(m["prompt"]), _tasks(m["prompt"]), _terms(m["reference"])
        assert len(rules) == cs.LEVELS[lv]["consts"]
        assert set(tasks) == set(terms) == set(m["grader_config"]["test_groups"])
        assert len(tasks) == len(cs.LEVELS[lv]["sizes"]) + len(cs.LEVELS[lv]["planted"])
        assert len({str(w) for w, _ in tasks.values()}) == len(tasks)          # distinct targets
        for tid, (want, limit) in tasks.items():
            t = _read(terms[tid])
            assert _count(t) <= limit
            assert _whnf_norm([[[t, "a"], "b"], "c"], rules, [5000]) == want, (lv, i, tid)
    assert len(seen) == 30
    assert 470 <= min(ps) and max(ps) <= 760, (min(ps), max(ps))
    want = cs.LEVELS[lv]["answer_tokens"]
    got = statistics.median(len(fam.reference(_g(lv), release_seed="meas", case_id=f"X{i}")) / 4 for i in range(30))
    assert abs(got - want) <= max(3, 0.15 * want), (got, want)


@pytest.mark.parametrize("lv", LEVELS)
def test_reference_and_mutants_in_sandbox(lv):
    fam = gen.get("combsyn")
    for i in range(3):
        m = _mat(lv, i)
        n = m["meta"]["n_groups"]
        assert fam.grade(fam.reference(_g(lv), release_seed="t", case_id=f"C{i}"), m) == 1.0
        muts = fam.mutants(_g(lv), release_seed="t", case_id=f"C{i}")
        for mu in muts[:-1]:
            assert abs(fam.grade(mu, m) - (n - 1) / n) < 1e-9
        assert fam.grade(muts[-1], m) == 0.0 and fam.grade("", m) == 0.0
    assert gen.verify_case({"id": "C0", "category": "coding", "generator": _g(lv)}, "t") == []


def _all_terms(names, size):
    if size == 1:
        yield from names
        return
    for i in range(1, size):
        for f, x in itertools.product(list(_all_terms(names, i)), list(_all_terms(names, size - i))):
            yield [f, x]


def test_certified_minimum_is_a_true_minimum_for_small_sizes():
    """Independent brute force (own reducer): no term smaller than the certified minimum reaches a target with
    m <= 4, so 'at most m + slack' is a real constraint and not a loose one."""
    for i in range(4):
        m = _mat(2, i)
        rules, tasks = _rules(m["prompt"]), _tasks(m["prompt"])
        for q, (want, _limit) in zip(m["meta"]["targets"], tasks.values()):
            if q["min"] > 4:
                continue
            for size in range(1, q["min"]):
                for t in _all_terms(sorted(rules), size):
                    try:
                        got = _whnf_norm([[[t, "a"], "b"], "c"], rules, [300])
                    except (AssertionError, RecursionError):
                        continue
                    assert got != want, (i, q, t)


def test_limits_and_literal_gate():
    fam = gen.get("combsyn")
    m = _mat(2, 0)
    terms = _terms(m["reference"])
    n = len(terms)
    name = sorted(_rules(m["prompt"]))[0]

    def ans(d):
        return "```python\nTERMS = " + repr(d) + "\n```"
    assert fam.grade(ans(terms), m) == 1.0
    assert abs(fam.grade(ans({**terms, "T1": " ".join([name] * 40)}), m) - (n - 1) / n) < 1e-9     # over the limit
    assert abs(fam.grade(ans({**terms, "T1": "a b"}), m) - (n - 1) / n) < 1e-9                    # inert symbols
    assert abs(fam.grade(ans({**terms, "T1": "(" + name}), m) - (n - 1) / n) < 1e-9               # malformed
    assert fam.grade("```python\nX = " + repr(terms) + "\nTERMS = X\n```", m) == 0.0
    # spacing is free: the same term without spaces still counts
    assert fam.grade(ans({k: v.replace(" ", "") for k, v in terms.items()}), m) == 1.0


@pytest.mark.parametrize("lv", [5, 6, 7])
def test_deep_levels(lv):
    fam = gen.get("combsyn")
    seen = set()
    for i in range(3 if lv < 7 else 2):
        m = _mat(lv, i)
        assert m == fam.materialize(_g(lv), release_seed="t", case_id=f"C{i}")
        seen.add(m["prompt"])
        assert m["meta"]["certified_size"] >= (7 if lv == 7 else 6)
        rules, tasks, terms = _rules(m["prompt"]), _tasks(m["prompt"]), _terms(m["reference"])
        for q, (tid, (want, limit)) in zip(m["meta"]["targets"], tasks.items()):
            t = _read(terms[tid])
            assert _count(t) <= limit
            assert _whnf_norm([[[t, "a"], "b"], "c"], rules, [5000]) == want
            assert q["planted"] or limit == q["min"] == m["meta"]["certified_size"]
    assert len(seen) == (3 if lv < 7 else 2)
    m = _mat(lv, 0)
    assert fam.grade(fam.reference(_g(lv), release_seed="t", case_id="C0"), m) == 1.0
    assert all(fam.grade(mu, m) < 1.0 for mu in fam.mutants(_g(lv), release_seed="t", case_id="C0"))


def test_hard_item_cap_declared_on_bundled_cases_only():
    # T2 2b (FC2): the 65,536-token cap is stated in the prompt and applied by the runner; single-target variants keep 24,576
    from crucibleforge.gen import combsyn as cs
    m = cs.materialize({"type": "combsyn", "level": 2, "key": "cap"}, release_seed="4.5.0", case_id="CS-cap")
    assert m["meta"]["hard_item_cap"] == 65536 and "65,536 tokens" in m["prompt"]
