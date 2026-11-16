"""LRVX: determinism, seeds differ, reference = 1.0 / lazy = 0.0, named mutants fail exactly their own unit,
scaling with level, the classic (LRV1 regraded) variant's voice units."""
import json

import pytest

from crucibleforge import gen as G
from crucibleforge import session_checks
from crucibleforge.gen import lrvx as R

SEEDS = [f"X{i}" for i in range(10)]
LVS = sorted(R.LEVELS["scene"])


def g_of(level, variant="scene", **kw):
    return {"type": "lrvx", "level": level, "variant": variant, **kw}


def mat(level, cid, rs="r"):
    return R.materialize(g_of(level), release_seed=rs, case_id=cid)


@pytest.mark.parametrize("level", LVS)
def test_deterministic_and_seeded(level):
    assert json.dumps(mat(level, "D"), sort_keys=True) == json.dumps(mat(level, "D"), sort_keys=True)
    assert mat(level, "S1")["turns"] != mat(level, "S2")["turns"]
    assert mat(level, "S1")["turns"] != mat(level, "S1", rs="r2")["turns"]


@pytest.mark.parametrize("level", LVS)
def test_reference_lazy(level):
    for cid in SEEDS:
        m = mat(level, cid)
        ref = R.reference(g_of(level), release_seed="r", case_id=cid)
        assert R.grade(ref, m) == 1.0
        assert R.grade("", m) == 0.0
        assert R.grade(R.TURN_SEP.join(["Okay."] * len(m["turns"])), m) < 0.2
        assert not m["judge_elements"]


@pytest.mark.parametrize("level", LVS)
def test_named_mutants(level):
    for cid in SEEDS[:8]:
        m = mat(level, cid)
        for name, (ans, want) in R.mutants_named(g_of(level), release_seed="r", case_id=cid).items():
            run = session_checks.run_checks(m, R.split_turns(ans))
            got = {r["id"] for r in run["results"] if not r["pass"]}
            assert set(want) <= got, (cid, name)
            # a token spliced into a counted line may also break that line's voice unit; nothing else may fail
            assert all(x.startswith("voice-") for x in got - set(want)), (cid, name, got - set(want))
            if name in ("voice-slip", "silent", "absent-speaks", "puppet", "secret-early"):
                assert got == set(want), (cid, name)


def test_scales_with_level():
    n = {lv: [mat(lv, c)["meta"]["n_checks"] for c in SEEDS[:5]] for lv in LVS}
    for a, b in ((1, 2), (2, 3), (3, 4), (4, 5), (6, 7), (7, 8), (8, 9)):   # L6/L7 are the hard-rule branch (fewer events than L5)
        assert min(n[b]) > max(n[a]) * 0.9, (a, b)
    chars = [len(R._plan(g_of(lv), "r", "Z")["chars"]) for lv in LVS]
    assert chars[:5] == sorted(chars[:5]) and chars[0] < chars[4]
    assert sum(r["kind"] in R.HARD for r in R._plan(g_of(7), "r", "Z")["chars"][0]["rules"]) == 2
    assert sum(r["kind"] in R.HARD for r in R._plan(g_of(9), "r", "Z")["chars"][0]["rules"]) == 4


def test_every_token_is_on_the_card_or_in_the_turns():
    for lv in LVS:
        p = R._plan(g_of(lv), "r", "C")
        text = p["system"] + " ".join(p["turns"])
        for s in p["secrets"]:
            assert s["place"] in p["system"] and s["phrase"] in p["system"] and s["phrase"].capitalize() in " ".join(p["turns"])
            assert s["place"] not in " ".join(p["turns"])
        for ev in p["know"]:
            assert ev["v1"] in " ".join(p["turns"])
            assert ev["v1"] not in " ".join(R.TELLS[i][1] for i in range(3))
            assert ev["v1"] not in p["system"]


def test_engine_agreement():
    for cid in SEEDS[:2]:
        assert G.verify_case({"id": cid, "category": "rpdeep", "generator": g_of(3)}, "r") == []
    assert "lrvx" in G.families()


def test_classic_voice_units():
    m = R.materialize(g_of(1, "classic"), release_seed="r", case_id="c")
    assert len(m["turns"]) == 13
    assert not any(c["id"].startswith("W1") for c in m["checks"])
    assert sum(1 for c in m["checks"] if c["id"].startswith("voice-")) == 13 * 5
    ok = ('**Ottoline:** "Twenty-eight years, Inspector. The tide has turned that many winters on this quay."\n'
          '**Jory:** "Fourteen years. Not ten."\n**Dilys:** "Mind, the stove draws well."\n'
          '**Pip:** "There are 3 chairs, and I am nervous..."\n**Barnaby:** "Now then, the bread is warm and the kettle is on."\n')
    run = session_checks.run_checks({"checks": m["checks"]}, [ok] + [""] * 12)
    t1 = {r["id"]: r["pass"] for r in run["results"] if r["id"].endswith("-t1")}
    assert all(t1.values()), t1
    bad = ok.replace('"Fourteen years. Not ten."', '"I think fourteen years, do you not?"')
    run = session_checks.run_checks({"checks": m["checks"]}, [bad] + [""] * 12)
    assert not {r["id"]: r["pass"] for r in run["results"]}["voice-Jory-t1"]
    assert R.grade("", m) == 0.0


def test_classic_reference_and_verify():
    g = g_of(1, "classic")
    m = R.materialize(g, release_seed="r", case_id="c")
    ref = R.reference(g, release_seed="r", case_id="c")
    assert R.grade(ref, m) == 1.0, [r for r in session_checks.run_checks({"checks": m["checks"]}, R.split_turns(ref))["results"] if not r["pass"]]
    assert R.grade(R.TURN_SEP.join(["Okay."] * 13), m) < 0.2
    assert G.verify_case({"id": "c", "category": "rpdeep", "generator": g}, "r") == []
    # LRV1's own weight mass: 4 callbacks at 2.0, voice 2.0 per character summed over turns
    w = {"voice": 0.0, "cb": 0.0}
    for c in m["checks"]:
        if c.get("ftype") == "voice":
            w["voice"] += c["weight"]
        if c["id"].startswith("CB"):
            w["cb"] += c["weight"]
    assert abs(w["voice"] - 10.0) < 1e-6 and abs(w["cb"] - 8.0) < 1e-6


def test_classic_pen_callback_ignores_the_steward_surname():
    """T4 grader check: Barnaby's surname is Quill, so 'Quill' in turns 5-6 passed the dry-pen callback on 3 of 3 stored passes."""
    m = R.materialize(g_of(1, "classic"), release_seed="r", case_id="c")
    def cb3(text5, text6=""):
        run = session_checks.run_checks({"checks": m["checks"]}, [""] * 4 + [text5, text6] + [""] * 7)
        return {r["id"]: r["pass"] for r in run["results"]}["CB3-dry-pen"]
    assert not cb3('**Jory:** "Madder red. Quill\'s dye. Pilot wears the same."')
    assert not cb3('**Pip:** "Mr Quill knits 6 of them..."')
    assert cb3('**Barnaby:** "Now then, there is ink in the pot, and a spare pen for you."')
    assert cb3('**Barnaby:** "Now then, a quill pen is on the shelf."')
