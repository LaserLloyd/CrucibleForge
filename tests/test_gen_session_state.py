"""SESSION-STATE generator (G5): determinism, seeds differ, reference 1.0, mutants < 1.0, lazy 0.0,
sizes inside the docstring table for 30 seeds per level, engine shape, key independent of the
model's own text."""
import json

import pytest

from crucibleforge import session_checks
from crucibleforge.gen import session_state as S

SEEDS = [f"X{i}" for i in range(30)]
# level -> (prompt tokens, reference tokens, checks); mirrors the docstring table
SIZES = {
    "state": {1: ((339, 382), (200, 225), (5, 5)), 2: ((368, 411), (275, 310), (7, 7)),
              3: ((437, 487), (355, 390), (9, 9)), 4: ((452, 511), (420, 455), (9, 9)),
              5: ((504, 559), (490, 530), (10, 10)), 6: ((529, 575), (560, 615), (10, 10)),
              7: ((550, 601), (563, 614), (11, 11)), 8: ((642, 700), (636, 686), (13, 13)),
              9: ((724, 776), (718, 762), (15, 15)), 10: ((776, 821), (784, 844), (16, 16))},
    "multi": {1: ((396, 440), (225, 385), (10, 10)), 2: ((425, 470), (305, 505), (12, 12)),
              3: ((495, 545), (390, 645), (14, 14)), 4: ((509, 570), (470, 765), (14, 14)),
              5: ((562, 617), (550, 890), (15, 15)), 6: ((587, 632), (620, 1010), (15, 15)),
              # 7-10 re-measured 2026-11-14: out-turns no longer script user lines naming the absent
              7: ((666, 732), (495, 992), (18, 18)), 8: ((790, 850), (470, 955), (21, 21)),
              9: ((872, 925), (580, 1085), (23, 23)), 10: ((940, 990), (595, 1215), (25, 25))},
}
CASES = [(v, lv) for v in ("state", "multi") for lv in range(1, 11)]
TURNS = {1: 4, 2: 5, 3: 6, 4: 7, 5: 8, 6: 9, 7: 9, 8: 10, 9: 11, 10: 12}


def gen_of(variant, level, **kw):
    return {"type": "session_state", "level": level, "variant": variant, **kw}


def mat(variant, level, cid):
    return S.materialize(gen_of(variant, level), release_seed="r", case_id=cid)


@pytest.mark.parametrize("variant,level", CASES)
def test_deterministic(variant, level):
    a, b = mat(variant, level, "D1"), mat(variant, level, "D1")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    g = gen_of(variant, level)
    assert S.reference(g, release_seed="r", case_id="D1") == S.reference(g, release_seed="r", case_id="D1")
    assert S.mutants(g, release_seed="r", case_id="D1") == S.mutants(g, release_seed="r", case_id="D1")


@pytest.mark.parametrize("variant", ["state", "multi"])
def test_seeds_differ(variant):
    outs = {json.dumps([mat(variant, 3, c)["system"], mat(variant, 3, c)["turns"]]) for c in SEEDS[:10]}
    assert len(outs) == 10
    base = mat(variant, 3, "S")
    assert base["turns"] != S.materialize(gen_of(variant, 3, key="other"), release_seed="r", case_id="S")["turns"]
    assert base["turns"] != S.materialize(gen_of(variant, 3), release_seed="r2", case_id="S")["turns"]


@pytest.mark.parametrize("variant,level", CASES)
def test_reference_mutants_lazy_and_sizes(variant, level):
    (plo, phi), (rlo, rhi), (clo, chi) = SIZES[variant][level]
    ps, rs, cs = [], [], []
    for cid in SEEDS:
        g = gen_of(variant, level)
        m = S.materialize(g, release_seed="r", case_id=cid)
        assert len(m["turns"]) == TURNS[level]
        ref = S.reference(g, release_seed="r", case_id=cid)
        assert S.grade(ref, m) == 1.0, cid
        ms = S.mutants(g, release_seed="r", case_id=cid)
        assert len(ms) >= 5, cid
        for x in ms:
            assert S.grade(x, m) < 1.0, (cid, x[-120:])
        assert S.grade("", m) == 0.0
        assert S.grade("Okay.", m) == 0.0
        assert S.grade(S.TURN_SEP.join(["Okay."] * len(m["turns"])), m) == 0.0
        ps.append(m["meta"]["prompt_tokens_est"])
        rs.append(m["meta"]["reference_tokens_est"])
        cs.append(m["meta"]["n_checks"])
    assert plo <= min(ps) and max(ps) <= phi, (min(ps), max(ps))
    assert rlo <= min(rs) and max(rs) <= rhi, (min(rs), max(rs))
    assert clo <= min(cs) and max(cs) <= chi, (min(cs), max(cs))


@pytest.mark.parametrize("variant", ["state", "multi"])
def test_engine_shape_and_agreement(variant):
    m = mat(variant, 4, "E1")
    assert m["category"] == "rpdeep" and m["programmatic"] is True and m["user_name"]
    ids = [c["id"] for c in m["checks"]]
    assert len(ids) == len(set(ids))
    assert all(c["fact"] == c["id"] and c["type"] in session_checks.CHECKS for c in m["checks"])
    w = [c["weight"] for c in m["checks"]]
    assert max(w) == 2 and min(w) > 0                       # answer lines x2; "never do X" format rules share 15%
    passive = sum(c["weight"] for c in m["checks"] if c["id"] in ("no-puppet", "reply-length", "no-untagged-speech", "no-user-line"))
    assert passive / sum(w) == pytest.approx(0.15, abs=1e-3)
    ref = S.reference(gen_of(variant, 4), release_seed="r", case_id="E1")
    run = session_checks.run_checks(m, S.split_turns(ref))
    assert all(r["pass"] for r in run["results"]) and run["rate"] == 1.0


def test_key_depends_only_on_user_turns_and_card():
    """The expected answers are recomputed from the scripted user turns alone."""
    import re
    for cid in SEEDS[:15]:
        m = mat("state", 5, cid)
        card = m["system"]
        coins = int(re.search(r"with (\d+) ", card).group(1))
        for t in m["turns"][:-1]:
            for mm in re.finditer(r"I pay \w+ (\d+) ", t):
                coins -= int(mm.group(1))
            for mm in re.finditer(r"I earn (\d+) ", t):
                coins += int(mm.group(1))
        ref = S.reference(gen_of("state", 5), release_seed="r", case_id=cid).split(S.TURN_SEP)[-1]
        assert f"A2: {coins}" in ref


def test_false_memory_is_planted_and_denied():
    for cid in SEEDS[:15]:
        m = mat("state", 4, cid)
        ref = S.reference(gen_of("state", 4), release_seed="r", case_id=cid).split(S.TURN_SEP)[-1]
        assert "no, " in ref
        assert any("didn't you?" in t for t in m["turns"][:-1])      # level >= 3 plants it in an earlier turn
        assert any(c["id"].startswith("A-false-") for c in m["checks"])


def test_multi_requires_tags_and_minimum_lines():
    m = mat("multi", 2, "M1")
    names = {c["id"] for c in m["checks"]}
    assert {"no-untagged-speech", "no-user-line"} <= names
    assert sum(i.startswith("speaks-") for i in names) == 3
    ref = S.reference(gen_of("multi", 2), release_seed="r", case_id="M1")
    assert S.grade(ref, m) == 1.0
    first = ref.split(S.TURN_SEP)[0]
    untagged = first + '\n"Hello," he says.'
    assert S.grade(untagged + ref[len(first):], m) < 1.0


def test_module_main_prints_one_item(capsys):
    S._main([])
    out = capsys.readouterr().out
    assert "USER TURN 1" in out and "grade(reference) = 1.0" in out


# ---------------------------------------------------------------- 4.5 Core levels 7-10 (A4)
import re  # noqa: E402

HI = [(v, lv) for v in ("state", "multi") for lv in (7, 8, 9, 10)]
KINDS_BY_LEVEL = {7: ["coins_at", "elapsed"], 8: ["coins_at", "elapsed", "where_when"],
                  9: ["coins_at", "elapsed", "where_when", "held_then"],
                  10: ["coins_at", "elapsed", "where_when", "held_then", "order"]}


def _indep_sim(m):
    """A second simulator: replays the ledger from the card and the scripted USER turns only (regexes on the
    rendered text), returning the state after every action in order, plus the action list."""
    card = m["system"]
    st = re.search(r"START: \S+ is in the (.+?) with (\d+) \w+; the clock reads (\d\d):(\d\d); the pack holds: (.+)\.", card)
    room, coins, hh, mm, pack = st.group(1), int(st.group(2)), int(st.group(3)), int(st.group(4)), st.group(5).split(", ")
    clock = hh * 60 + mm
    pat = re.compile(r"I pay (\w[\w ]*?) (\d+) \w+ for the (.+?) and put it in my pack\.|I earn (\d+) \w+ helping|"
                     r"I spend (\d+) minutes|I walk over to the (.+?)\.|I head for the (.+?)\.|I leave and go to the (.+?)\.|"
                     r"I give the (.+?) to (\w+)\.")
    acts, snaps = [], []
    for t in m["turns"][:-1]:
        for x in pat.finditer(t):
            if x.group(3):
                coins -= int(x.group(2)); pack.append(x.group(3)); acts.append(("buy", x.group(3), x.group(1)))
            elif x.group(4):
                coins += int(x.group(4)); acts.append(("earn",))
            elif x.group(5):
                clock += int(x.group(5)); acts.append(("wait",))
            elif x.group(9):
                pack.remove(x.group(9)); acts.append(("give", x.group(9), x.group(10)))
            else:
                room = x.group(6) or x.group(7) or x.group(8); acts.append(("move", room))
            snaps.append({"room": room, "coins": coins, "time": clock, "pack": list(pack)})
    return acts, snaps


def _phrase_idx(acts, ph):
    hits = []
    for i, a in enumerate(acts):
        if a[0] == "buy" and ph == f"bought the {a[1]}":
            hits.append(i)
        if a[0] == "give" and ph == f"gave the {a[1]} to {a[2]}":
            hits.append(i)
    return hits


def _indep_answer(cb_stem, acts, snaps):
    """The expected answer for a callback, from the question text and the second simulator alone."""
    q = cb_stem
    if x := re.match(r"Where was I when I bought the (.+?)\?", q):
        (i,) = _phrase_idx(acts, f"bought the {x.group(1)}")
        return snaps[i]["room"]
    if x := re.match(r"How many \w+ did I have right after I (.+?)\?", q):
        (i,) = _phrase_idx(acts, x.group(1))
        return str(snaps[i]["coins"])
    if x := re.match(r"Who did I give the (.+?) to\?", q):
        (i,) = [i for i, a in enumerate(acts) if a[0] == "give" and a[1] == x.group(1)]
        return acts[i][2]
    if x := re.match(r"How many minutes passed between when I (.+?) and when I (.+?)\?", q):
        (i,), (j,) = _phrase_idx(acts, x.group(1)), _phrase_idx(acts, x.group(2))
        return str(snaps[j]["time"] - snaps[i]["time"])
    if x := re.match(r"Was the (.+?) in my pack when I went to the (.+?)\?", q):
        (i,) = [i for i, a in enumerate(acts) if a[0] == "move" and a[1] == x.group(2)]
        return "yes" if x.group(1) in snaps[i]["pack"] else "no"
    if x := re.match(r"Which came first: \(1\) I (.+?), or \(2\) I (.+?)\?", q):
        (i,), (j,) = _phrase_idx(acts, x.group(1)), _phrase_idx(acts, x.group(2))
        # the order question is about the actions, not turns: earlier action index = first
        return "1" if i < j else "2"
    raise AssertionError(q)


@pytest.mark.parametrize("variant,level", HI)
def test_callbacks_follow_level_and_max_tokens(variant, level):
    for cid in SEEDS[:6]:
        m = mat(variant, level, cid)
        assert m["meta"]["callbacks"] == KINDS_BY_LEVEL[level], cid
        assert m["max_tokens"] == 6144
        assert any(c["id"] == "A-false-fcb" for c in m["checks"]) == (level >= 8)
        # false callback is planted in a turn AFTER the purchase it names, and denied with the true room
        if level >= 8:
            ref = S.reference(gen_of(variant, level), release_seed="r", case_id=cid).split(S.TURN_SEP)[-1]
            assert re.search(r"A\d+: no, ", ref) and sum("when I bought" in t and "didn't you?" in t for t in m["turns"][:-1]) == 1


@pytest.mark.parametrize("variant", ["state", "multi"])
def test_callback_key_comes_from_user_turns_and_phrases_are_unique(variant):
    """Every callback: one valid answer, recomputed by a second simulator from the user turns alone; its referring
    phrase matches exactly one action (the `(i,) =` unpacking asserts that); model replies cannot change it."""
    for lv in (8, 10):
        for cid in SEEDS[:12]:
            g = gen_of(variant, lv)
            m = mat(variant, lv, cid)
            acts, snaps = _indep_sim(m)
            plan = S._plan(g, "r", cid)
            assert len(acts) == len(plan["sim"]["acts"])
            for c in plan["cbs"]:
                assert _indep_answer(c["stem"], acts, snaps) == c["ans"], (cid, c)
                assert c["ans"] != c["wrong"]


def test_every_callback_kind_is_available_and_drawn():
    for kind in S.CB_KINDS:
        g = gen_of("state", 8, callbacks=[kind])
        for cid in SEEDS[:8]:
            m = S.materialize(g, release_seed="r", case_id=cid)
            assert m["meta"]["callbacks"] == [kind]
            ref = S.reference(g, release_seed="r", case_id=cid)
            assert S.grade(ref, m) == 1.0
            acts, snaps = _indep_sim(m)
            plan = S._plan(g, "r", cid)
            assert _indep_answer(plan["cbs"][0]["stem"], acts, snaps) == plan["cbs"][0]["ans"]
    with pytest.raises(ValueError):
        S.materialize(gen_of("state", 8, callbacks=["nope"]), release_seed="r", case_id="Z")


def test_callback_weights_put_the_weight_on_derived_numbers():
    m = mat("multi", 8, "W1")
    w = {c["id"]: c["weight"] for c in m["checks"]}
    assert w["A-cb-coins_at"] == w["A-cb-elapsed"] == w["A-false-fcb"] == 3
    assert w["A-cb-where_when"] == 2
    assert w["presence-in"] == w["presence-out"] == w["exit-before"] == w["exit-after"] == 2
    styles = [k for k in w if k.startswith("style-")]
    assert len(styles) == 1 and w[styles[0]] == 1           # one style rule per case, they only floor Haiku


@pytest.mark.parametrize("level", [7, 8, 9, 10])
def test_speaker_rules_are_state_coupled(level):
    for cid in SEEDS[:20]:
        plan = S._plan(gen_of("multi", level), "r", cid)
        R, n_scene = plan["rules"], len(plan["turns"]) - 1
        p = R["presence"]
        assert len(p["in"]) >= 2 and len(p["out"]) >= 2 and sorted(p["in"] + p["out"]) == list(range(1, n_scene + 1))
        # the in-turns are exactly the turns after which the user is in the room (second simulator)
        acts, snaps = _indep_sim(S.materialize(gen_of("multi", level), release_seed="r", case_id=cid))
        room, per_turn, ai = None, [], 0
        for ti, t in enumerate(plan["turns"][:-1], 1):
            n = len(re.findall(r"I pay |I earn |I spend \d+ minutes|I walk over|I head for|I leave and go|I give the ", t))
            ai += n
            per_turn.append(snaps[ai - 1]["room"] if ai else plan["sim"]["start"]["room"])
        assert [t for t, r in enumerate(per_turn, 1) if r == p["room"]] == p["in"], cid
        if level >= 8:
            e = R["exit"]
            assert 0.3 * n_scene <= e["t"] <= 0.7 * n_scene
            assert f"I give the {e['item']} to" in plan["turns"][e["t"] - 1]
        assert ("names_user" in R) == (level == 10)
        assert plan["system"].count("Character rules:") == 1


@pytest.mark.parametrize("style", ["opener", "ends", "short", "asks"])
def test_each_style_rule_reference_obeys_and_mutant_breaks(style):
    for cid in SEEDS[:6]:
        g = gen_of("multi", 8, style=style)
        m = S.materialize(g, release_seed="r", case_id=cid)
        assert S.grade(S.reference(g, release_seed="r", case_id=cid), m) == 1.0
        assert any(c["id"] == f"style-{style}" for c in m["checks"])
        assert S.grade(S.mutants_named(g, release_seed="r", case_id=cid)["style-broken"], m) < 1.0


@pytest.mark.parametrize("level", [8, 9, 10])
def test_named_mutants_cost_only_their_own_check(level):
    g = gen_of("multi", level)
    for cid in SEEDS[:10]:
        m = S.materialize(g, release_seed="r", case_id=cid)
        named = S.mutants_named(g, release_seed="r", case_id=cid)
        want = {"speaks-out-of-room": {"presence-out"}, "speaks-after-exit": {"exit-after"},
                "accepts-false-callback": {"A-false-fcb"}}
        for name, ans in named.items():
            if name.startswith("m"):
                continue
            run = session_checks.run_checks(m, S.split_turns(ans))
            failed = {r["id"] for r in run["results"] if not r["pass"]}
            if name in want:
                assert failed == want[name], (cid, name, failed)
            elif name == "style-broken":
                assert len(failed) == 1 and next(iter(failed)).startswith("style-"), (cid, failed)
            elif name.startswith("callback-"):
                assert failed == {"A-cb-" + name[len("callback-"):-len("-wrong")]}, (cid, name, failed)
            else:                                           # final-state-for-callback: every callback line, nothing else
                assert failed == {c["id"] for c in m["checks"] if c["id"].startswith("A-cb-")}, (cid, failed)
        assert set(want) <= set(named) or level < 8


def test_levels_1_to_6_are_unchanged_by_the_new_machinery():
    """Frozen digest of materialized items for levels 1-6 (computed on the pre-4.5 generator)."""
    import hashlib
    h = hashlib.sha256()
    for v in ("state", "multi"):
        for lv in range(1, 7):
            for cid in ("X0", "X1", "X2"):
                h.update(json.dumps(mat(v, lv, cid), sort_keys=True).encode())
                h.update(S.reference(gen_of(v, lv), release_seed="r", case_id=cid).encode())
    assert h.hexdigest() == LOW_LEVEL_DIGEST


# 2026-11-14: re-pinned for the speaker-tag regex only (a leading quote/bullet is accepted); the
# levels 1-6 turns, system prompts and references are byte-identical to the previous digest's
LOW_LEVEL_DIGEST = "e27d6b6515c903039c2ce1858f87246c54c2602fa05b6583f628b88fffde3d0d"
