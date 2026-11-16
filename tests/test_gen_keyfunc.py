"""G1 keyfunc generator: determinism, seeds differ, reference 1.0 (through the sandboxed
python_exec grader), mutants < 1.0, lazy 0.0, sizes, no dead rule."""
import hashlib
import json
import random

import pytest

from crucibleforge import graders
from crucibleforge.gen import keyfunc as K

needs_sandbox = pytest.mark.skipif(not graders.sandbox_available(), reason="bwrap not available")
G = lambda lv: {"type": "keyfunc", "level": lv}  # noqa: E731
KW = lambda i: dict(release_seed="t-rel", case_id=f"KF-{i}")  # noqa: E731
TOK = {1: (380, 620), 2: (520, 820), 3: (680, 1080), 4: (900, 1400), 5: (1100, 1650),
       6: (1650, 2050), 7: (1780, 2220), 8: (2050, 2450)}
HID = (6, 7, 8)
GROUPS = {6: (8, 6, 0), 7: (8, 5, 2), 8: (8, 5, 2)}      # visible, per hidden arm, both-arms groups
LAZY = "```python\ndef f(xs, t):\n    return (sum(xs), t)\n```"


@pytest.mark.parametrize("lv", sorted(K.LEVELS))
def test_deterministic_and_seeds_differ(lv):
    a = K.materialize(G(lv), **KW(1))
    assert a == K.materialize(G(lv), **KW(1))
    b = K.materialize(G(lv), **KW(2))
    assert a["prompt"] != b["prompt"] and a["grader_config"] != b["grader_config"]
    assert a["prompt"] != K.materialize(G(lv), release_seed="x", case_id="KF-1")["prompt"]
    assert a["prompt"] != K.materialize({**G(lv), "key": "salt"}, **KW(1))["prompt"]


@needs_sandbox
@pytest.mark.parametrize("lv", sorted(K.LEVELS))
def test_reference_mutants_lazy(lv):
    for i in range(2):
        m = K.materialize(G(lv), **KW(i))
        assert K.grade(K.reference(G(lv), **KW(i)), m) == 1.0
        for x in K.mutants(G(lv), **KW(i)):
            assert K.grade(x, m) < 1.0
        assert K.grade(LAZY, m) == 0.0
        assert K.grade("", m) == 0.0


@pytest.mark.parametrize("lv", sorted(K.LEVELS))
def test_sizes_within_docstring_table(lv):
    lo, hi = TOK[lv]
    for i in range(30):
        m = K.materialize(G(lv), release_seed="size", case_id=f"S{i}")
        assert lo <= m["meta"]["spec_tokens_est"] <= hi, (lv, i, m["meta"]["spec_tokens_est"])
        assert m["meta"]["n_groups"] >= 6
        if lv in HID:
            vis, per, both = GROUPS[lv]
            n_h = K.LEVELS[lv]["hidden"]
            mt = m["meta"]
            assert mt["group_classes"] == {"visible": vis, "hidden": per * n_h, "both": both}
            assert mt["n_hidden"] == n_h and set(mt["n_snaps"].values()) == {K.LEVELS[lv]["snaps"]}
            assert m["max_tokens"] == 6144


@pytest.mark.parametrize("lv", sorted(K.LEVELS))
def test_no_dead_rule(lv):
    """Replacing any single primitive step by a no-op changes some output."""
    import copy
    import random
    for i in range(5):
        key, level = K._instance_key(G(lv), "dead", f"D{i}")
        rules, acc0 = K.make_rules(key, level)
        rng = random.Random(i)
        probes = [K._rand_input(rng, big=j % 3 == 0) for j in range(60)] + [([], ""), ([5], "a")]
        base = [K.run(rules, xs, t, acc0) for xs, t in probes]
        for owner, cont, k, prim in list(K._walk_prims(rules)):
            saved = copy.deepcopy(prim)
            cont[k] = {"kind": "noop", "id": prim["id"]}
            changed = [K.run(rules, xs, t, acc0) for xs, t in probes] != base
            cont[k] = saved
            assert changed, f"dead step {prim}"


# ---------------------------------------------------------------- 4.5 hidden-arm levels (6-8)
GOLDEN_LEGACY = {"kf1-1": "37fa883ba3a5c408", "kf1-2": "8d97e828ef224a50", "kf1-3": "217b6d582971afc2",
                 "kf2-1": "10b501d3e6ff6757", "kf2-2": "b4565b9b6c34edbf", "kf2-3": "4785304f5279eb4c",
                 "kf3-1": "59c6c47517c183ce", "kf3-2": "499490d7d1c15a85", "kf3-3": "5714784f25e6c7f3",
                 "kf4-1": "4ba2629381884c94", "kf4-2": "2363a6a498dc4dbe", "kf4-3": "a7843cc79f9dee90",
                 "kf5-1": "5a907e68c1d757b6", "kf5-2": "e887b46b9acee2a2", "kf5-3": "21d92802636843df"}


def _case(lv, i):
    key, level = K._instance_key(G(lv), "size", f"S{i}")       # the seeds of the size test: one search each
    return K.generate(key, level)


def _share(src, groups):
    """share of groups whose asserts all pass for the f in ``src`` (a generated reference or mutant)"""
    ns: dict = {}
    exec(src, ns)                                               # our own generator's output
    ok = 0
    for code in groups.values():
        try:
            exec(code, {"f": ns["f"]})
            ok += 1
        except AssertionError:
            pass
    return ok / len(groups)


def test_levels_1_to_5_byte_identical_to_the_4_1_generator():
    for key, want in GOLDEN_LEGACY.items():
        lv, sd = int(key[2]), int(key[4])
        c = K.generate(sd, lv)
        got = hashlib.sha256(json.dumps([c["prompt"], c["grader_config"], c["reference"], c["mutants"]],
                                        sort_keys=True).encode()).hexdigest()[:16]
        assert got == want, key


@pytest.mark.parametrize("lv", HID)
def test_hidden_levels_30_distinct_specs_and_reference_is_one(lv):
    specs = set()
    for i in range(30):
        c = _case(lv, i)
        specs.add(c["prompt"])
        assert _share(c["reference"], c["grader_config"]["test_groups"]) == 1.0
    assert len(specs) == 30


@pytest.mark.parametrize("lv", HID)
def test_transcribe_only_pins_the_floor_and_near_miss_sits_between(lv):
    for i in range(30):
        c = _case(lv, i)
        names = c["meta"]["mutant_names"]
        groups = c["grader_config"]["test_groups"]
        sc = {n: _share(src, groups) for n, src in zip(names, c["mutants"])}
        assert 0.30 <= sc["transcribe-only"] <= 0.60, (lv, i, sc)
        visible = c["meta"]["group_classes"]["visible"] / len(groups)
        assert sc["transcribe-only"] == pytest.approx(visible)      # exactly the visible groups pass
        assert sc["transcribe-only"] < sc["near-miss"] < 1.0, (lv, i, sc)
        for n in ("drop-one-step", "off-by-one-constant", "wrong-initial-acc"):
            assert sc[n] < 1.0, (lv, i, n)


def _own_candidates(lv, first_snap):
    """re-enumerate the catalogue from the spec's own ranges (independent of keyfunc._space)"""
    out = [{"kind": k} for k in ("reverse_xs", "swap_pairs", "xor_prev", "reverse_t")]
    out += [{"kind": kd, "k": k} for kd in ("rotate_xs", "rotate_t") for k in range(1, 9)]
    out += [{"kind": "drop_every", "k": k} for k in (2, 3, 4)] + [{"kind": "sort_by_mod", "k": k} for k in range(2, 7)]
    out += [{"kind": kd, "k": k, "r": r} for kd in ("keep_mod", "upper_every") for k in (2, 3, 4) for r in range(k)]
    out += [{"kind": "map_mul_mod", "k": k, "m": m} for k in range(2, 50) for m in (61, 97, 101, 256, 1000)]
    out += [{"kind": "prefix_sums_mod", "m": m} for m in (97, 100, 256, 1000)]
    out += [{"kind": "caesar", "n": n} for n in range(1, 26)] + [{"kind": "digit_shift", "k": k} for k in range(1, 10)]
    out += [{"kind": "remove_mod", "k": k, "r": r} for k in range(5, 12) for r in range(k)]
    if K.LEVELS[lv]["catalogue"] == "int":
        (a0, _, _), (a1, _, _) = first_snap
        out += [{"kind": "add_k", "k": (a1 - a0) % 2**32}, {"kind": "xor_k", "k": a0 ^ a1}]
        out += [{"kind": "mul_k", "k": k} for k in (3, 5, 7, 11, 13, 17, 31, 37, 101, 257)]
        out = [c for c in out if c["kind"] not in ("add_k", "xor_k") or 3 <= c["k"] < 100000]
    return out


@pytest.mark.parametrize("lv", HID)
def test_snapshots_identify_each_hidden_arm_on_every_test_pre_state(lv):
    n = 30 if lv != 8 else 12
    for i in range(n):
        c = _case(lv, i)
        h = c["_hidden"]
        rules, acc0 = h["rules"], h["acc0"]
        arms = {r["then"]["id"]: r["then"] for r in rules if r["kind"] == "if" and r["then"].get("hidden")}
        ids = set(arms)
        pres = {a: [] for a in ids}
        for g in h["groups"].values():
            for xs, t in g:
                for aid, pre, _post in K._capture(rules, xs, t, acc0, ids):
                    pres[aid].append(pre)
        for aid, arm in arms.items():
            snaps = h["snaps"][aid]
            assert len(snaps) == K.LEVELS[lv]["snaps"]
            survivors = [cand for cand in _own_candidates(lv, snaps[0])
                         if all(K._apply({**cand, "id": "x"}, a) == b for a, b in snaps)]
            assert survivors, (lv, i, aid)
            for cand in survivors:
                for pre in pres[aid]:
                    assert K._apply({**cand, "id": "x"}, pre) == K._apply(arm, pre), (lv, i, aid, cand)


@pytest.mark.parametrize("lv", HID)
def test_hidden_arms_catalogue_and_fire_rate(lv):
    for i in range(30):
        c = _case(lv, i)
        rules, acc0 = c["_hidden"]["rules"], c["_hidden"]["acc0"]
        arms = [r for r in rules if r["kind"] == "if" and r["then"].get("hidden")]
        assert len(arms) == K.LEVELS[lv]["hidden"]
        for r in arms:
            kind = r["then"]["kind"]
            assert kind not in ("rotl", "rev_low8")
            allowed = K._CAT_LIST_STR + (K._CAT_INT if K.LEVELS[lv]["catalogue"] == "int" else [])
            assert kind in allowed
            rng = random.Random(1000 + i)                          # an independent probe stream
            hits = 0
            for j in range(200):
                xs, t = K._rand_input(rng, big=j % 3 == 0)
                tr: list = []
                K.run(rules, xs, t, acc0, trace=tr)
                hits += any(v for sid, v in tr if sid == r["id"])
            assert 0.30 <= hits / 200 <= 0.70, (lv, i, r["id"], hits / 200)    # an independent stream: sampling noise allowed
            assert 0.35 <= K._fire_rate(rules, acc0, r["then"]["id"]) <= 0.65     # the generator's own probes


def test_hidden_spec_states_the_catalogue_and_snapshots():
    c = _case(7, 0)
    p = c["prompt"]
    assert "HIDDEN STEPS" in p and "Catalogue" in p
    for aid, snaps in c["_hidden"]["snaps"].items():
        assert f"Snapshots of step {aid}:" in p
        assert p.count("before: acc=") >= len(snaps)
        assert f"Step {aid}. (Not described in words" in p
