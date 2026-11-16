"""SCENESHIFT generator (EXP-20261114-15): determinism, reference 1.0 on 200 plans, generation failure rate, mutant
floors (<= 0.45 at every level), the presence / setting rules on hand-written replies (a defensible reply never
fails, a bleed does), disjoint seeded vocabulary, sizes, the secret never in the user text, and the chains element
shape."""
import json
import re
import statistics as st

import pytest

from crucibleforge import chains
from crucibleforge.gen import sceneshift as C
from crucibleforge.gen._stack_common import join_turns, split_turns

LEVELS = sorted(C.LEVELS)
TOP = max(LEVELS)
SEEDS = [f"X{i}" for i in range(30)]
RS = "r"


def g_of(level, **kw):
    return {"type": "sceneshift", "level": level, **kw}


def mat(level, cid):
    return C.materialize(g_of(level), release_seed=RS, case_id=cid)


def plan(level, cid):
    return C._plan_cached(level, None, RS, cid)


@pytest.mark.parametrize("level", LEVELS)
def test_deterministic_and_seeds_differ(level):
    a, b = mat(level, "D1"), mat(level, "D1")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert len({json.dumps(mat(level, c)["turns"]) for c in SEEDS[:10]}) == 10


@pytest.mark.parametrize("level", LEVELS)
def test_reference_scores_one_blank_zero(level):
    n = 200 if level == TOP else 40
    for i in range(n):
        cid = f"R{i}"
        m = mat(level, cid)
        assert C.grade(C.reference(g_of(level), release_seed=RS, case_id=cid), m) == 1.0, cid
    assert C.grade(join_turns([""] * len(m["turns"])), m) == 0.0


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
        assert 14 <= m["meta"]["n_turns"] <= 22
        assert abs(m["meta"]["reference_tokens_est"] - ans) <= 0.35 * ans


@pytest.mark.parametrize("level", LEVELS)
def test_mutant_floors_at_most_045(level):
    worst = {}
    for i in range(30):
        cid = f"M{i}"
        m = mat(level, cid)
        for kind, ans in C.mutants_named(g_of(level), release_seed=RS, case_id=cid).items():
            worst[kind] = max(worst.get(kind, 0), C.grade(ans, m))
        for a in C.mutants(g_of(level), release_seed=RS, case_id=cid):
            assert C.grade(a, m) < 1.0
    assert set(worst) == set(C.MUTANT_KINDS)
    assert max(worst.values()) <= 0.45, worst


def test_levels_grow():
    def stat(lv):
        ps = [plan(lv, f"G{i}") for i in range(10)]
        return (st.mean(len(p["scenes"]) for p in ps), st.mean(sum(len(s["cast"]) for s in p["scenes"].values()) / len(p["scenes"]) for p in ps),
                st.mean(p["seq"].count("A") - 1 for p in ps), st.mean(p["T"] for p in ps))
    a, z = stat(1), stat(TOP)
    assert z[0] > a[0] and z[1] > a[1] and z[3] > a[3]
    assert stat(6)[2] >= 1.5 and stat(1)[2] == 1


@pytest.mark.parametrize("level", LEVELS)
def test_vocabulary_disjoint_and_secrets_not_in_user_text(level):
    for i in range(20):
        p = plan(level, f"V{i}")
        S = p["scenes"]
        names = [n for s in S.values() for n in s["cast"]]
        assert len(set(names)) == len(names)
        keys = [o[1] for s in S.values() for o in s["objs"]] + [s["carry"][1] for s in S.values() if s["carry"]] + [p["left"][1]]
        for k in keys:       # a key never occurs inside another object's phrase
            hits = [x for s in S.values() for x in [o[0] for o in s["objs"]] if re.search(C._krx(k), x, re.I)]
            assert len(hits) <= 1
        for s in S.values():
            raw = s["secret"]["raw"]
            for t, b in enumerate(p["beats"], 1):
                if b["kind"] != "tell":
                    assert raw not in b["text"], (raw, b["text"])
        ntell = sum(b["kind"] == "tell" for b in p["beats"])
        assert ntell == len(S)
    # near tokens at L5+: same kind across scenes
    if level >= 5:
        p = plan(level, "V0")
        assert len({s["secret"]["kind"] for s in p["scenes"].values()}) == 1


def _first(level, kind):
    for i in range(50):
        p = plan(level, f"P{i}")
        for t, b in enumerate(p["beats"], 1):
            if b["kind"] == kind:
                return p, t, b
    raise AssertionError(kind)


def _res(p, t, reply):
    from crucibleforge import session_checks
    rep = [""] * p["T"]
    rep[t - 1] = reply
    out = session_checks.run_checks({"checks": [c for c in p["checks"] if c["turns"] == [t]]}, rep)
    return {r["id"].split("-t")[0]: r["pass"] for r in out["results"]}


def test_presence_rule_defensible_vs_bleed():
    p, t, b = _first(3, "absent")
    S = p["scenes"]
    cur, old = S[b["scene"]], S[b["foreign"][0]]
    me, ghost = b["npc"], old["cast"][0]
    ok = f"{me} shrugs.\n{me}: I have never heard of that."
    assert _res(p, t, ok)["presence"] and _res(p, t, ok)["know"]
    # a remembered mention is fine, an acting / speaking old-scene NPC is not
    assert _res(p, t, f"You think of {ghost} for a moment as {me} shrugs.\n{me}: No idea.")["presence"]
    assert _res(p, t, f"Earlier, {ghost} had said something odd. {me} shrugs.\n{me}: No idea.")["presence"]
    assert not _res(p, t, f"{ghost} waves from the corner.\n{me}: No idea.")["presence"]
    assert not _res(p, t, f"{me} shrugs.\n{ghost}: Hello again!")["presence"]
    assert not _res(p, t, f"{me} shrugs.\n{me}: Ask {ghost}, maybe.")["presence"]
    assert not _res(p, t, f"{me} shrugs.\nStranger: Hmm.")["presence"]
    assert _res(p, t, f"{me} shrugs.\nNarrator: A hush falls.\n{me}: No idea.")["presence"]
    # the token never appears; a reply that says it fails
    tok = old["secret"]["raw"]
    assert not _res(p, t, f"{me} leans in.\n{me}: Of course, it is {tok}.")["know"]
    # an old-scene object: a memory is fine, a thing in the room is not
    obj = old["objs"][0][1]
    assert not _res(p, t, f"The {obj} hisses behind {me}.\n{me}: No idea.")["setting"]
    assert _res(p, t, f"You remember the {obj} from earlier. {me} shrugs.\n{me}: No idea.")["setting"]


def test_carry_and_persist_checks():
    p, t, b = _first(1, "self")
    tok = p["scenes"][b["src"]]["secret"]["raw"]
    assert _res(p, t, f"You remember: {tok}.")["cknow"]
    assert not _res(p, t, "You cannot remember.")["cknow"]
    p, t, b = _first(1, "hold")
    assert _res(p, t, "You hold " + " and ".join(c[0] for c in b["carried"]) + ".")["hold"]
    assert not _res(p, t, "Your hands are empty.")["hold"]
    p, t, b = _first(1, "lookp")
    left, cast = p["left"], p["scenes"][b["scene"]]["cast"]
    base = f"{', '.join(cast)} are here, by " + p["scenes"][b["scene"]]["objs"][0][0] + ". "
    assert _res(p, t, base + f"Your {left[0]} still leans by the door.")["persist"]
    assert not _res(p, t, base + f"Your {left[1]} is gone.")["persist"]
    assert not _res(p, t, base)["persist"]


def test_chains_gradeable_and_turn_elements():
    prof = {"name": "t", "cases": {"rpdeep": []},
            "scores": {"chat": {"parts": {"main": {"max": 10, "scoring": ["rpdeep"], "cases": []}}}},
            "chains": {"chat/main": {"flat": [{"family": "sceneshift", "levels": {TOP: 4}, "element": "turn",
                                               "ceiling_per_turn": 1500}]}}}
    chains.check_gradeable(prof)
    m = mat(TOP, "CE")
    n = len(chains.turn_weights(m["checks"]))
    assert n >= 8
    ref = C.reference(g_of(TOP), release_seed=RS, case_id="CE")
    sc = chains.turn_scores(m["checks"], split_turns(ref))
    assert len(sc) == n and set(sc.values()) == {1.0}


def test_shared_pin():
    import hashlib
    from pathlib import Path
    from crucibleforge.gen import sceneshift as _ss
    d = Path(_ss.__file__).parent
    assert hashlib.sha256((d / "_stack_common.py").read_bytes()).hexdigest()[:16] == _ss._STACK_SHA


@pytest.mark.parametrize("pre", ["", "**", "'", '"', "- ", "\u2022 ", "> ", "\u201c", "  *"])
def test_tags_tolerate_leading_quote_or_bullet(pre):
    """a leading quote/bullet must not hide a foreign speaker tag from presence, nor an own tag from speaks"""
    import re
    from crucibleforge.gen import sceneshift as _ss
    bad = _ss._presence_rx(["Mara", "Odo"], ["Tessal"])
    assert re.search(bad, f"{pre}Tessal: you again?", re.M)
    assert not re.search(bad, f"{pre}Mara: welcome back.", re.M)
    assert re.search(rf"^{_ss._PRE}Mara[ \t]*[*_]*[ \t]*:", f"{pre}Mara: welcome back.", re.M)


def test_know_reads_tagged_lines_only_narration_memory_is_not_a_leak():
    """rc1 fairness fix: the 8B's L4-001 t7 ("... shakes her head. You remember the code from the last place: 2467")
    failed know although system rule 2 lets the narration recall the character's memory."""
    p, t, b = _first(3, "absent")
    me, tok = b["npc"], p["scenes"][b["src"]]["secret"]["raw"]
    assert _res(p, t, f"{me} shakes her head. You remember the code from the last place: {tok}.\n{me}: No idea.")["know"]
    assert _res(p, t, f"Narrator: you still remember {tok}.\n{me}: No idea.")["know"]
    assert not _res(p, t, f"{me} leans in.\n{me}: Of course, it is {tok}.")["know"]
    assert not _res(p, t, f"{me} leans in.\n- **{me}:** it is {tok}, I think.")["know"]
    # untagged speech of a present person is still a leak (8B L1-001 t8, L7-003 t21 on the stored rows)
    assert not _res(p, t, f'{me} answers, her voice calm. {me}: "The {tok} was a boat."')["know"]
    assert not _res(p, t, f'{me} glances up, then whispers, "It is {tok}."')["know"]
    assert _res(p, t, f"{me} shakes her head; she does not know it. You remember {tok} yourself.\n{me}: No idea.")["know"]


def test_a_code_said_in_words_counts():
    """spelled codes: a leak in words fails know, a spelled answer passes cknow (grading only, 2026-11-14)"""
    words = ["oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    found = 0
    for lv in LEVELS:
        for i in range(40):
            p = plan(lv, f"W{i}")
            for t, b in enumerate(p["beats"], 1):
                if b["kind"] not in ("absent", "self") or not p["scenes"][b["src"]]["secret"]["raw"].isdigit():
                    continue
                raw = p["scenes"][b["src"]]["secret"]["raw"]
                sp = "-".join(words[int(c)] for c in raw).capitalize()
                if b["kind"] == "absent":
                    me = b["npc"]
                    assert not _res(p, t, f"{me} leans in.\n{me}: {sp}.")["know"]
                    assert _res(p, t, f"{me} shrugs.\n{me}: No idea.")["know"]
                else:
                    assert _res(p, t, f"You remember it: {sp}.")["cknow"]
                    other = "".join(raw[1] + raw[0] + raw[2:])
                    if other != raw:
                        assert not _res(p, t, f"You remember it: {'-'.join(words[int(c)] for c in other)}.")["cknow"]
                found += 1
    assert found > 20
