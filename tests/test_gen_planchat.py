"""PLANCHAT generator (EXP-20261114-13): determinism, reference 1.0 on 200 plans, the simulator's sub-checks catch
what they should, generation failure rate, red-team floors, measured sizes, feasibility/tightness invariants and the
chains element shape (per-turn)."""
import json
import statistics as st

import pytest

from crucibleforge import chains, plansim as ps, session_checks
from crucibleforge.gen import planchat as C
from crucibleforge.gen._stack_common import join_turns, split_turns

LEVELS = sorted(C.LEVELS)
TOP = max(LEVELS)
SEEDS = [f"X{i}" for i in range(30)]
RS = "r"


def g_of(level, **kw):
    return {"type": "planchat", "level": level, **kw}


def mat(level, cid, **kw):
    return C.materialize(g_of(level, **kw), release_seed=RS, case_id=cid)


def plan(level, cid, scoring="all"):
    return C._plan_cached(level, None, scoring, RS, cid)


def turn_mean(m, replies, skip_first=False):
    sc = chains.turn_scores(m["checks"], replies)
    v = [x for t, x in sc.items() if not (skip_first and t == 1)]
    return sum(v) / len(v)


@pytest.mark.parametrize("level", LEVELS)
def test_deterministic_and_seeds_differ(level):
    a, b = mat(level, "D1"), mat(level, "D1")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    g = g_of(level)
    assert C.reference(g, release_seed=RS, case_id="D1") == C.reference(g, release_seed=RS, case_id="D1")
    assert C.mutants(g, release_seed=RS, case_id="D1") == C.mutants(g, release_seed=RS, case_id="D1")
    assert len({json.dumps(mat(level, c)["turns"]) for c in SEEDS[:10]}) == 10


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("scoring", ["all", "share"])
def test_reference_scores_one_blank_zero(level, scoring):
    n = 200 if (level == TOP and scoring == "all") else 30
    for i in range(n):
        cid = f"R{i}"
        m = mat(level, cid, scoring=scoring)
        assert C.grade(C.reference(g_of(level, scoring=scoring), release_seed=RS, case_id=cid), m) == 1.0, cid
    assert C.grade(join_turns([""] * len(m["turns"])), m) == 0.0
    assert C.grade(join_turns(["Sure, noted."] * len(m["turns"])), m) == 0.0


def test_generation_failure_rate_under_five_percent():
    att = fails = 0
    for lv in LEVELS:
        for i in range(60):
            p = plan(lv, f"F{lv}-{i}")
            att += p["attempt"] + 1
            fails += p["attempt"]
    assert fails / att < 0.05


@pytest.mark.parametrize("level", LEVELS)
def test_sizes_pinned(level):
    L = C.LEVELS[level]
    (plo, phi), (clo, chi), ans = L["size_prompt"], L["size_checks"], L["answer_tokens"]
    for cid in SEEDS:
        m = mat(level, cid)
        assert plo <= m["meta"]["prompt_tokens_est"] <= phi, (cid, m["meta"])
        assert clo <= m["meta"]["n_checks"] <= chi
        assert m["meta"]["n_turns"] <= 16 and m["meta"]["n_turns"] == L["T"]
        assert abs(m["meta"]["reference_tokens_est"] - ans) <= 0.35 * ans


@pytest.mark.parametrize("level", LEVELS)
def test_feasibility_tightness_and_previous_plan_invalid(level):
    L = C.LEVELS[level]
    for i in range(25):
        p = plan(level, f"T{i}")
        for t, (stt, ref) in enumerate(zip(p["states"], p["refs"]), 1):
            n, _ = ps.solve(stt, cap=L["K"] + 1)
            assert 1 <= n <= L["K"], (level, i, t, n)
            assert ps.evaluate(stt, ps.render(ref))["valid"][0]
            assert len(stt["req"]) <= 8
            if t > 1:
                assert not ps.evaluate(stt, ps.render(p["refs"][t - 2]))["valid"][0]


def test_levers_grow():
    def stat(lv):
        ps_ = [plan(lv, f"G{i}") for i in range(20)]
        kinds = {e["kind"] for p in ps_ for e in p["events"]}
        return len(ps_[0]["world"]["names"]), ps_[0]["world"]["names"] and C.LEVELS[lv]["T"], kinds, C.LEVELS[lv]["K"]
    a, b = stat(1), stat(TOP)
    assert a[0] < b[0] and a[1] < b[1] and a[3] > b[3]
    assert {"retract", "group", "lunch", "slow", "fixed"} <= b[2]
    assert not ({"retract", "group", "lunch", "slow"} & a[2])


def _bad(level, cid, mutate):
    p = plan(level, cid)
    st_ = p["states"][-1]
    return st_, ps.render(p["refs"][-1]), mutate


def test_simulator_subchecks_catch_each_defect():
    # build a state with every feature from a top-level plan
    found = False
    for i in range(60):
        p = plan(TOP, f"S{i}")
        stt = p["states"][-1]
        if stt["fixed"] and stt["group"] and LUNCH_IN(stt):
            found = True
            break
    if not found:
        stt = None
        for i in range(60):
            for s_ in plan(TOP, f"S{i}")["states"]:
                if s_["fixed"]:
                    stt = s_
                    break
            if stt:
                break
    ref = [x for x in ps.solve(stt, cap=1)[1]]
    ok = ps.evaluate(stt, ps.render(ref))
    assert ok["valid"][0]
    first = ref[0]
    n = first[2]
    # off by a minute (duration), too early (travel/hours), dropped stop (stops), extra stop (excluded), late (deadline)
    cases = {
        "duration": [(first[0], first[1] + 1, n)] + ref[1:],
        "stops": ref[1:],
        "excluded": ref + [(ref[-1][1] + 500, ref[-1][1] + 520, "Nowhere Special")] if ref[-1][1] + 520 < 1440 else ref[:] + [(1400, 1420, "Nowhere Special")],
        "parse": None,
    }
    assert not ps.evaluate(stt, ps.render(cases["duration"]))["duration"][0]
    assert not ps.evaluate(stt, ps.render(cases["stops"]))["stops"][0]
    assert not ps.evaluate(stt, ps.render(cases["excluded"]))["excluded"][0]
    assert not ps.evaluate(stt, "no plan here")["parse"][0]
    # shift every stop 3 hours later: hours and deadline fail
    late = [(s + 180, e + 180, x) for s, e, x in ref]
    r = ps.evaluate(stt, ps.render(late))
    assert not r["deadline"][0] and not r["hours"][0] and not r["valid"][0]
    # fixed: move the fixed visit by one minute (only when nothing else changes the verdict we assert on)
    fx = next(iter(stt["fixed"]))
    moved = [(s + 1, e + 1, x) if x == fx else (s, e, x) for s, e, x in ref]
    assert not ps.evaluate(stt, ps.render(moved))["fixed"][0]


def LUNCH_IN(stt):
    return ps.LUNCH in stt["req"]


def test_parse_is_lenient_about_decoration_and_strict_about_times():
    p = plan(1, "P1")
    stt = p["states"][0]
    ref = p["refs"][0]
    base = ps.render(ref)
    deco = "\n".join(f"- **{ln}**" for ln in base.splitlines()) + "\nHere you go."
    assert ps.evaluate(stt, deco)["valid"][0]
    en = base.replace("-", "–")
    assert ps.evaluate(stt, en)["valid"][0]
    am = "\n".join(f"{ln[:5]}am" for ln in base.splitlines())
    assert not ps.evaluate(stt, am)["parse"][0]
    # closing time boundary is inclusive: shift a visit to end exactly at close, still hours-ok
    s0, e0, n0 = ref[0]
    o, c, d = stt["places"][n0]
    r = ps.evaluate(stt, ps.render([(c - d, c, n0)] + ref[1:]))
    assert r["hours"][0]


@pytest.mark.parametrize("level", LEVELS)
def test_floors(level):
    res = {k: [] for k in C.FLOOR_KINDS}
    n = 25
    for i in range(n):
        p = plan(level, f"FL{i}")
        m = mat(level, f"FL{i}")
        for k in C.FLOOR_KINDS:
            rep = C.floor_replies(p, k, C.rng(p["seed"], k))
            res[k].append(turn_mean(m, rep, skip_first=(k == "repeat-last")))
    for k, v in res.items():
        assert st.mean(v) <= 0.30, (level, k, st.mean(v))
    # greedy orders are rejected by the generator at every turn
    assert st.mean(res["greedy-open"]) == st.mean(res["greedy-close"]) == st.mean(res["greedy-near"]) == 0.0


def test_share_scoring_floor_is_higher_than_all_or_nothing():
    a = s = 0.0
    for i in range(20):
        p = plan(TOP, f"SH{i}", "share")
        ma, ms = mat(TOP, f"SH{i}"), mat(TOP, f"SH{i}", scoring="share")
        rep = C.floor_replies(p, "repeat-last", C.rng(p["seed"], "x"))
        a += turn_mean(ma, rep, True)
        s += turn_mean(ms, rep, True)
    assert s > a + 0.2 * 20


def test_mutants_all_fail_and_top_level_shape():
    for i in range(5):
        g, cid = g_of(TOP), f"M{i}"
        m = mat(TOP, cid)
        ms = C.mutants(g, release_seed=RS, case_id=cid)
        assert ms and all(C.grade(a, m) < 1.0 for a in ms)
    m = mat(TOP, "S1")
    assert m["judge_elements"] == [] and m["programmatic"] is True and m["meta"]["n_turns"] <= 16
    assert "09:10-09:40 Bakery" in m["system"] and "closing time minus visit length" in m["system"]


def test_named_subchecks_ride_along_at_weight_zero():
    m = mat(TOP, "N1")
    subs = {c["sub"] for c in m["checks"] if not c["weight"]}
    assert {"parse", "stops", "hours", "duration", "travel", "deadline", "excluded"} <= subs
    rep = split_turns(C.reference(g_of(TOP), release_seed=RS, case_id="N1"))
    res = session_checks.run_checks({"checks": m["checks"]}, rep)
    assert all(r["pass"] for r in res["results"])


def test_chains_gradeable_and_turn_elements():
    prof = {"name": "t", "cases": {"rpdeep": []},
            "scores": {"chat": {"parts": {"main": {"max": 10, "scoring": ["rpdeep"], "cases": []}}}},
            "chains": {"chat/main": {"flat": [{"family": "planchat", "levels": {TOP: 4}, "element": "turn",
                                               "ceiling_per_turn": 3000}]}}}
    chains.check_gradeable(prof)
    cs = chains.chain_cases(prof)
    case = chains.materialize(chains.instance(cs[0], 1, 1))
    assert len(chains.turn_weights(case["checks"])) == C.LEVELS[TOP]["T"]
    m = mat(TOP, "CE")
    ref = C.reference(g_of(TOP), release_seed=RS, case_id="CE")
    assert set(chains.turn_scores(m["checks"], split_turns(ref)).values()) == {1.0}
