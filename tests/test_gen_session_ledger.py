"""SESSION-LEDGER (H2: derived and conditional state): determinism, seeds differ, reference 1.0 over 30 seeds per
level, named mutants below 1.0 for every mechanic the level has, lazy floors, sizes, the _stack_common pin, keys that
never depend on the model's own text, and the fairness invariants (no scripted line names the presence character; none
names the exit character from the exit turn on; a correction never flips a surcharge decision)."""
import hashlib
import inspect
import json
import re
from pathlib import Path

import pytest

from crucibleforge import chains, gen
from crucibleforge.gen import session_ledger as S

SEEDS = [f"X{i}" for i in range(30)]
LEVELS = (1, 2, 3, 4, 5)
# level -> (prompt tokens, checks, turns)
SIZES = {1: ((620, 760), (9, 12), 6), 2: ((920, 1080), (22, 26), 9), 3: ((1180, 1360), (31, 36), 12),
         4: ((1300, 1480), (37, 43), 15), 5: ((1500, 1760), (37, 43), 15)}


def g(level, **kw):
    return {"type": "session_ledger", "level": level, **kw}


def mat(level, cid, rs="r"):
    return S.materialize(g(level), release_seed=rs, case_id=cid)


@pytest.mark.parametrize("level", LEVELS)
def test_deterministic(level):
    assert json.dumps(mat(level, "D1"), sort_keys=True) == json.dumps(mat(level, "D1"), sort_keys=True)
    assert S.reference(g(level), release_seed="r", case_id="D1") == S.reference(g(level), release_seed="r", case_id="D1")


def test_seeds_differ():
    outs = {json.dumps([mat(3, c)["system"], mat(3, c)["turns"]]) for c in SEEDS[:10]}
    assert len(outs) == 10
    assert mat(3, "S")["turns"] != S.materialize(g(3, key="other"), release_seed="r", case_id="S")["turns"]
    assert mat(3, "S")["turns"] != mat(3, "S", rs="r2")["turns"]


@pytest.mark.parametrize("level", LEVELS)
def test_reference_mutants_floors_sizes(level):
    (plo, phi), (clo, chi), nt = SIZES[level]
    L = S.LEVELS[level]
    want = {"start-state", "final-state-for-callback", "accepts-false-memory", "speaks-out-of-room",
            "never-voices-presence-npc", "do-nothing", "silent-scene"}
    want |= {f"ignores-{f}" for f, on in (("surcharge", L["surcharge"]), ("returns", L["returns"]),
                                           ("corrections", L["corr"]), ("debt", L["debt"])) if on}
    if L["exit"]:
        want.add("speaks-after-exit")
    if L.get("decoys"):
        want.add("counts-interference")
    for cid in SEEDS:
        m = mat(level, cid)
        ref = S.reference(g(level), release_seed="r", case_id=cid)
        assert S.grade(ref, m) == 1.0
        assert len(m["turns"]) == nt and len(S.split_turns(ref)) == nt
        assert plo <= m["meta"]["prompt_tokens_est"] <= phi, m["meta"]
        assert clo <= m["meta"]["n_checks"] <= chi
        named = S.mutants_named(g(level), release_seed="r", case_id=cid)
        assert want <= set(named), sorted(want - set(named))
        for n, a in named.items():
            assert S.grade(a, m) < 1.0, n
        assert S.grade(named["do-nothing"], m) == 0.0
        assert S.grade(named["start-state"], m) <= 0.8
        assert S.grade(S.join_turns([""] * nt), m) == 0.0


@pytest.mark.parametrize("level", LEVELS)
def test_fairness_invariants(level):
    for cid in SEEDS:
        p = S._plan(g(level), "r", cid)
        R, cast, ids = p["rules"], p["cast"], p["scene_turn_ids"]
        scene = {s: p["turns"][ids[s - 1] - 1] for s in range(1, len(ids) + 1)}
        # the presence character is never named by a scripted user line
        assert not any(re.search(rf"\b{cast[0]}\b", t) for t in p["turns"]), cid
        if R.get("exit"):
            for s, t in scene.items():
                if s >= R["exit"]["t"]:
                    assert not re.search(rf"\b{cast[1]}\b", t), (cid, s)
        # every OOC question asks for a stated answer form and the card states the clock rule
        assert "Only the minutes" in p["system"] and "Keep each scene reply brief" in p["system"]
        assert not re.search(r"\b\d+\s*(?:to|-)\s*\d+\s+words\b", p["system"])        # no word count anywhere
        sim = p["sim"]
        if sim["cutoff"] is not None:
            # a correction never moves a purchase across the surcharge time
            bt = S._believed_times(sim["start"], sim["acts"])
            tt = [x["time"] for x in sim["snaps"]]
            for i, a in enumerate(sim["acts"]):
                if a["k"] in ("buy", "buyn"):
                    assert (bt[i] >= sim["cutoff"]) == (tt[i] >= sim["cutoff"]) and tt[i] != sim["cutoff"]


def test_key_independent_of_model_text():
    """Two different but compliant scene texts score the same: the key never reads the model's replies."""
    m = mat(3, "K1")
    ref = S.split_turns(S.reference(g(3), release_seed="r", case_id="K1"))
    alt = [x.replace("lamps flicker", "candles gutter") for x in ref]
    assert S.grade(S.join_turns(alt), m) == 1.0


def test_answer_line_tolerance():
    """Leading quote / bullet / '>' / bold labels and units after numbers are accepted."""
    m = mat(2, "T1")
    ref = S.split_turns(S.reference(g(2), release_seed="r", case_id="T1"))
    last = ref[-1].splitlines()
    styles = [lambda s: "- " + s, lambda s: "> " + s, lambda s: re.sub(r"^(A\d+):", r"**\1:**", s),
              lambda s: '"' + s]
    for f in styles:
        ref2 = ref[:-1] + ["\n".join(f(x) for x in last)]
        assert S.grade(S.join_turns(ref2), m) == 1.0, f(last[1])
    unit = ref[:-1] + ["\n".join(re.sub(r"^(A2: \d+)$", rf"\1 {S._plan(g(2), 'r', 'T1')['sc']['cur']}", x) for x in last)]
    assert S.grade(S.join_turns(unit), m) == 1.0


def test_stack_common_pin():
    d = Path(S.__file__).parent
    assert hashlib.sha256((d / "_stack_common.py").read_bytes()).hexdigest()[:16] == S._STACK_SHA
    src = Path(inspect.getsourcefile(gen.get("session_ledger"))).read_bytes()
    assert S._STACK_SHA.encode() in src


def test_engine_shape():
    """The chains engine accepts the family as a flat cell with per-check elements."""
    assert chains._shape_reason("session_ledger", None, 3, 1, "check") is None
    assert chains._shape_reason("session_ledger", None, 3, 1, "item") is None
