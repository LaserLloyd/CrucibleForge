"""SCENELADDER generator (EXP-20261114-32): the ladder is ordered by construction (K strictly rises, D never falls,
graded turns and probe mix fixed), the reference scores 1.0, deny-all and tell-all score below 1 on every rung, who
heard what is stated in the narration and agrees with the probes, the knowledge checks read what the addressed person
says (tagged or quoted, never the narration's memory) and a format slip costs only the speaks credit."""
import hashlib
import json
from pathlib import Path

import pytest

from crucibleforge import chains, session_checks
from crucibleforge.gen import sceneladder as C
from crucibleforge.gen._stack_common import join_turns, split_turns

RUNGS = sorted(C.LEVELS)
TOP = max(RUNGS)
RS = "r"
SEEDS = [f"X{i}" for i in range(30)]


def g_of(level):
    return {"type": "sceneladder", "level": level}


def mat(level, cid):
    return C.materialize(g_of(level), release_seed=RS, case_id=cid)


def plan(level, cid):
    return C._plan_cached(level, None, RS, cid)


def _mix(p):
    out = {}
    for b in p["beats"]:
        if b["kind"] in ("look", "neg", "pos", "self", "attrib", "now"):
            k = "selfish" if b["kind"] in ("self", "attrib", "now") else b["kind"]
            out[k] = out.get(k, 0) + 1
    return out


@pytest.mark.parametrize("level", RUNGS)
def test_deterministic_and_seeds_differ(level):
    assert json.dumps(mat(level, "D1"), sort_keys=True) == json.dumps(mat(level, "D1"), sort_keys=True)
    assert len({json.dumps(mat(level, c)["turns"]) for c in SEEDS[:10]}) == 10


@pytest.mark.parametrize("level", RUNGS)
def test_reference_one_blank_zero_and_mutants_below_one(level):
    for i in range(40):
        cid = f"R{i}"
        m = mat(level, cid)
        assert C.grade(C.reference(g_of(level), release_seed=RS, case_id=cid), m) == 1.0, cid
        named = C.mutants_named(g_of(level), release_seed=RS, case_id=cid)
        for k in ("deny-all", "tell-all", "forgets-everything", "echo-the-user") + (("no-update",) if level >= 6 else ()):
            assert C.grade(named[k], m) < 1.0, (cid, k)
    assert C.grade(join_turns([""] * len(m["turns"])), m) == 0.0


def test_ladder_monotone_by_construction():
    """seeded: K strictly rises, D never falls, 8 graded turns and the same probe mix on every rung"""
    rows = []
    for lv in RUNGS:
        ks, ds, mixes, graded = set(), set(), set(), set()
        for c in SEEDS:
            p = plan(lv, c)
            ks.add(p["K"]); ds.add(p["D"])
            mixes.add(json.dumps(_mix(p), sort_keys=True))
            graded.add(len(chains.turn_weights(p["checks"])))
        assert len(ks) == len(ds) == len(mixes) == len(graded) == 1, lv
        rows.append((ks.pop(), ds.pop(), mixes.pop(), graded.pop()))
    assert all(a[0] < b[0] for a, b in zip(rows, rows[1:])), rows
    assert all(a[1] <= b[1] for a, b in zip(rows, rows[1:])), rows
    assert {r[2] for r in rows} == {json.dumps({"look": 1, "neg": 3, "pos": 2, "selfish": 2}, sort_keys=True)}
    assert {r[3] for r in rows} == {8}


def test_turn_two_is_a_greeting_and_no_probe_comes_before_it():
    for lv in RUNGS:
        p = plan(lv, "G")
        assert p["beats"][1]["kind"] == "greet"
        assert all(b["kind"] in ("arrive", "greet", "leave", "tell") for b in p["beats"][:3])


@pytest.mark.parametrize("level", RUNGS)
def test_who_heard_is_stated_and_secrets_unique(level):
    for c in SEEDS[:10]:
        p = plan(level, c)
        S = p["scenes"]
        raws = [s["secret"]["raw"] for s in S.values()]
        assert len(set(raws)) == len(raws)
        names = [n for s in S.values() for n in s["cast"]] + [n for n in (p["trav"], p["travl"]) if n]
        assert len(set(names)) == len(names)
        assert len({n[:3] for n in names}) == len(names)        # one name per sound-alike family
        for b in p["beats"]:
            if b["kind"] == "tell":
                assert S[b["scene"]]["secret"]["raw"] in b["text"]
                assert "hears" in b["text"] or "hear it" in b["text"]
            if b["kind"] in ("neg", "pos", "self", "attrib", "now"):
                assert S[b["about"]]["secret"]["raw"] not in b["text"]
                assert "new" not in S[b["about"]] or S[b["about"]]["new"]["raw"] not in b["text"]


def _find(level, kind, who=None):
    for i in range(60):
        p = plan(level, f"P{i}")
        for t, b in enumerate(p["beats"], 1):
            if b["kind"] == kind and (who is None or b.get("npc") == (p["trav"] if who == "trav" else
                                                                     p["travl"] if who == "travl" else b.get("npc"))):
                return p, t, b
    raise AssertionError(kind)


def _res(p, t, reply):
    rep = [""] * p["T"]
    rep[t - 1] = reply
    out = session_checks.run_checks({"checks": [c for c in p["checks"] if c["turns"] == [t]]}, rep)
    return {r["id"].split("-t")[0]: r["pass"] for r in out["results"]}


def _turn(p, t, reply):
    rep = [""] * p["T"]
    rep[t - 1] = reply
    return chains.turn_scores(p["checks"], rep)[t]


def test_negative_probe_reads_the_addressed_persons_speech():
    p, t, b = _find(5, "neg", "travl")
    me, tok = b["npc"], p["scenes"][b["about"]]["secret"]["raw"]
    assert _res(p, t, f"{me} shrugs.\n{me}: No idea, sorry.")["know"]
    assert not _res(p, t, f"{me} smiles.\n{me}: Sure, it is {tok}.")["know"]
    assert not _res(p, t, f'{me} smiles. {me}: "It is {tok}."')["know"]
    assert not _res(p, t, f'{me} glances around, then whispers, "It is {tok}."')["know"]
    # the narration's own memory is not the person knowing it (system rule 3)
    assert _res(p, t, f"You remember the code yourself: {tok}. {me} only shrugs.\n{me}: No idea.")["know"]


def test_positive_probe_needs_the_person_to_say_it_and_a_format_slip_costs_only_speaks():
    p, t, b = _find(5, "pos", "trav")
    me, tok = b["npc"], p["scenes"][b["about"]]["secret"]["raw"]
    assert _res(p, t, f"{me} leans in.\n{me}: It is {tok}.")["pknow"]
    assert not _res(p, t, f"{me} shrugs.\n{me}: No idea.")["pknow"]
    assert not _res(p, t, f"You remember it yourself: {tok}.\n{me}: No idea.")["pknow"]
    untagged = f'{me} leans in and whispers, "It is {tok}."'
    r = _res(p, t, untagged)
    assert r["pknow"] and not r["speaks"]
    full, slip, deny = _turn(p, t, f"{me} leans in.\n{me}: It is {tok}."), _turn(p, t, untagged), \
        _turn(p, t, f"{me} shrugs.\n{me}: No idea.")
    assert full == 1.0 and 0.5 < slip < 1.0 and deny == 0.0


def test_traveller_may_name_the_place_they_were_at():
    p, t, b = _find(4, "pos", "trav")
    A = p["scenes"]["A"]
    me, tok = b["npc"], A["secret"]["raw"]
    r = _res(p, t, f"{me} nods.\n{me}: {A['teller']} told us both back at the {A['kind'].split()[-1]}: {tok}.")
    assert r["presence"] and r["pknow"]


def test_chains_gradeable_and_turn_elements():
    prof = {"name": "t", "cases": {"rpdeep": []},
            "scores": {"chat": {"parts": {"main": {"max": 10, "scoring": ["rpdeep"], "cases": []}}}},
            "chains": {"chat/main": {"flat": [{"family": "sceneladder", "levels": {TOP: 4}, "element": "turn",
                                               "ceiling_per_turn": 1500}]}}}
    chains.check_gradeable(prof)
    m = mat(TOP, "CE")
    ref = C.reference(g_of(TOP), release_seed=RS, case_id="CE")
    sc = chains.turn_scores(m["checks"], split_turns(ref))
    assert len(sc) == 8 and set(sc.values()) == {1.0}


@pytest.mark.parametrize("level", RUNGS)
def test_sizes_pinned(level):
    import statistics as st
    from crucibleforge.gen._stack_common import TURN_SEP
    row = C.LEVELS[level]
    pt, ck, at = [], [], []
    for i in range(30):
        g = g_of(level)
        m = C.materialize(g, release_seed="meas", case_id=f"X{i}")
        pt.append(m["meta"]["prompt_tokens_est"]); ck.append(m["meta"]["n_checks"])
        at.append(len(C.reference(g, release_seed="meas", case_id=f"X{i}").replace(TURN_SEP, "")) / 4)
    assert (min(pt), max(pt)) == row["size_prompt"] and (min(ck), max(ck)) == row["size_checks"]
    assert abs(st.median(at) - row["answer_tokens"]) <= 3


def test_shared_pins():
    d = Path(C.__file__).parent
    assert hashlib.sha256((d / "_stack_common.py").read_bytes()).hexdigest()[:16] == C._STACK_SHA
    assert hashlib.sha256((d / "sceneshift.py").read_bytes()).hexdigest()[:16] == C._SCENESHIFT_SHA


def test_a_code_said_in_words_counts():
    """DeepSeek flash wrote 'Ivar: Seven-one-three-nine.' (hosted screen, R3): the person said the code"""
    for i in range(60):
        p = plan(3, f"W{i}")
        hit = [(t, b) for t, b in enumerate(p["beats"], 1) if b["kind"] in ("pos", "neg")
               and p["scenes"][b["about"]]["secret"]["raw"].isdigit()]
        if hit:
            break
    for t, b in hit:
        me, raw = b["npc"], p["scenes"][b["about"]]["secret"]["raw"]
        words = "-".join(["oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][int(c)]
                         for c in raw).capitalize()
        r = _res(p, t, f"{me} leans in.\n{me}: {words}. Same as I said.")
        assert (r["pknow"] if b["kind"] == "pos" else not r["know"]), (b["kind"], words)
        assert (_res(p, t, f"{me} leans in.\n{me}: {' '.join(raw)}.")["pknow"] if b["kind"] == "pos"
                else not _res(p, t, f"{me}: {' '.join(raw)}.")["know"])


def test_changed_secret_rungs_state_every_hearing_and_probe_beliefs():
    """R6/R7: the old value is told with the other person and the courier present, the other person steps out, the
    change is told with only the courier present; R7: the courier passes the new value to C's other person while C's
    teller is out of earshot. Stale / new / none follow from that and nothing else."""
    for lv in (6, 7):
        for c in SEEDS[:10]:
            p = plan(lv, c)
            A = p["scenes"]["A"]
            kinds = [b["kind"] for b in p["beats"]]
            assert kinds.index("tell") < kinds.index("step") < kinds.index("update")
            up = p["beats"][kinds.index("update")]["text"]
            assert A["new"]["raw"] in up and p["trav"] in up and f"{A['other']} is still out" in up
            vals = {(b["npc"], b["value"]) for b in p["beats"] if b["kind"] == "pos"}
            assert (A["other"], "old") in vals
            assert any(v == "new" for _, v in vals)
            if lv == 7:
                rt = p["beats"][kinds.index("retell")]["text"]
                C = p["scenes"]["C"]
                assert A["new"]["raw"] in rt and C["other"] in rt and f"{C['teller']} is busy" in rt


def _first(level, kind, value=None):
    for i in range(60):
        p = plan(level, f"S{i}")
        for t, b in enumerate(p["beats"], 1):
            if b["kind"] == kind and (value is None or b.get("value") == value):
                return p, t, b
    raise AssertionError(kind)


def test_stale_person_must_give_the_old_value_and_not_the_new():
    p, t, b = _first(6, "pos", "old")
    A, me = p["scenes"]["A"], b["npc"]
    old, new = A["secret"]["raw"], A["new"]["raw"]
    assert _turn(p, t, f"{me} leans in.\n{me}: It is {old}.") == 1.0
    r = _res(p, t, f"{me} leans in.\n{me}: It is {new}.")
    assert not r["pknow"] and not r["stale"] and _turn(p, t, f"{me} leans in.\n{me}: It is {new}.") == 0.0
    # a present person who heard the change may speak up: only the addressed person is read
    others = [n for n in b["here"] if n != me and n == A["teller"]]
    if others:
        assert _turn(p, t, f"{me} leans in.\n{me}: It is {old}.\n{others[0]}: It changed, it is {new} now.") == 1.0


def test_now_question_needs_the_new_value():
    p, t, b = _first(6, "now")
    A = p["scenes"]["A"]
    assert _res(p, t, f"It was {A['secret']['raw']}, but now it is {A['new']['raw']}.")["cnow"]
    assert not _res(p, t, f"It is {A['secret']['raw']}.")["cnow"]


def test_retold_news_reaches_only_the_listener():
    p, t, b = _first(7, "pos", "new")
    while b["scene"] != "C":
        t += 1
        b = p["beats"][t - 1]
    assert b["kind"] in ("pos", "neg")
    for t2, b2 in enumerate(p["beats"], 1):
        if b2["visit"] == b["visit"] and b2["kind"] in ("pos", "neg"):
            C = p["scenes"]["C"]
            assert (b2["kind"] == "pos") == (b2["npc"] == C["other"])
