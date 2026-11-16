"""PLANOPT (lab card EXP-20261114-20): exact optimum, reference 1.0, floors, grader edge cases."""
import itertools

import pytest

from crucibleforge import plansim as ps
from crucibleforge import planoptsim as pos
from crucibleforge.gen import planopt as P
from crucibleforge.gen._stack_common import join_turns

RS = "4.5.0"


def _p(lv, key):
    return P._plan({"type": "planopt", "level": lv, "key": key}, RS, f"T-planopt-L{lv}-{key}")


def _m(lv, key):
    return P.materialize({"type": "planopt", "level": lv, "key": key}, release_seed=RS, case_id=f"T-planopt-L{lv}-{key}")


@pytest.mark.parametrize("lv", [1, 2, 3])
@pytest.mark.parametrize("key", ["s1", "s2", "s3"])
def test_reference_is_one(lv, key):
    m = _m(lv, key)
    ref = P.reference({"type": "planopt", "level": lv, "key": key}, release_seed=RS, case_id=f"T-planopt-L{lv}-{key}")
    assert P.grade(ref, m) == 1.0
    assert m["meta"]["n_turns"] == P.LEVELS[lv]["T"]


@pytest.mark.parametrize("key", ["s1", "s2"])
def test_solver_matches_brute_force_level1(key):
    p = _p(1, key)
    for st in p["states"][:2]:
        pool = st["req"] + st["opt"]
        best = -1
        for r in range(len(pool) + 1):
            for perm in itertools.permutations(pool, r):
                if not set(st["req"]) <= set(perm):
                    continue
                ok, pt, _ = pos.plan_points(st, ps.render(ps.naive_schedule(st, list(perm))))
                if ok and pt > best:
                    best = pt
        assert best == st["best"]


@pytest.mark.parametrize("lv", [1, 2, 3])
def test_floors_low(lv):
    for key in ("s1", "s2", "s3"):
        p, m = _p(lv, key), _m(lv, key)
        for k in P.FLOOR_KINDS:
            assert P.grade(join_turns(P.floor_replies(p, k)), m) <= 0.25, (lv, key, k)
        assert P.grade(join_turns(["Sure, noted."] * len(m["turns"])), m) == 0.0


def test_grader_edges():
    p = _p(2, "s1")
    st = p["states"][0]
    ref = ps.render(p["refs"][0])
    ok, pts, _ = pos.plan_points(st, ref)
    assert ok and pts == st["best"]
    # tolerated decorations: bullets, numbering, bold
    deco = "\n".join(f"- **{ln}**" if i % 2 else f"{i + 1}. {ln}" for i, ln in enumerate(ref.splitlines()))
    assert pos.plan_points(st, "Here you go:\n" + deco)[:2] == (True, st["best"])
    # a duplicated stop is invalid
    first = ref.splitlines()[0]
    assert not pos.plan_points(st, ref + "\n" + first)[0]
    # an unknown place is invalid
    assert not pos.plan_points(st, ref + "\n23:00-23:10 Moon")[0]
    # dropping a required stop is invalid
    req = [r for r in st["req"] if r != ps.LUNCH]
    if req:
        kept = "\n".join(ln for ln in ref.splitlines() if not ln.endswith(" " + req[0]))
        assert not pos.plan_points(st, kept)[0]


def test_previous_plan_never_enough():
    for lv in (2, 3):
        p = _p(lv, "s2")
        for t in range(1, len(p["states"])):
            ok, pts, _ = pos.plan_points(p["states"][t], ps.render(p["refs"][t - 1]))
            assert not ok or pts < p["states"][t]["best"]


def test_revision_pins():
    """The family reads plansim and planoptsim: their source is pinned so a change re-seals rows."""
    import hashlib
    import pathlib
    for mod, pin in ((ps, P._PLANSIM_SHA), (pos, P._PLANOPTSIM_SHA)):
        assert hashlib.sha256(pathlib.Path(mod.__file__).read_bytes()).hexdigest()[:16] == pin, mod.__name__


def test_last_plan_block_is_graded():
    p = _p(2, "s1")
    st = p["states"][0]
    ref = ps.render(p["refs"][0])
    first = ref.splitlines()[0]
    draft = "\n".join([first] + ref.splitlines())        # a broken draft (duplicate line)
    txt = draft + "\n\nWait, let me fix that:\n\n" + ref + "\n\nThis is the best plan."
    assert pos.plan_points(st, txt)[:2] == (True, st["best"])
    # a broken FINAL draft is graded broken: a later-time repeat of a stop stays in its block (K's time-goes-back rule;
    # a same-start repeat reads as a restart, so the broken draft here repeats a stop later in the day)
    last = ref.splitlines()[-1]
    h, m = map(int, last.split()[0].split("-")[1].split(":"))
    broken = ref + f"\n{(h * 60 + m + 5) // 60:02d}:{(h * 60 + m + 5) % 60:02d}-23:59 " + first.split(" ", 1)[1]
    assert not pos.plan_points(st, ref + "\n\nOr maybe:\n" + broken)[0]


def test_notes_between_stops_keep_the_whole_plan():
    """Admin K (review of 683c7a4): a note under a stop must not split the plan; only time going back starts a draft."""
    txt = ("09:00-10:00 Museum\n  A great start.\n10:15-11:30 Park\n  Walk the gardens.\n"
           "12:00-12:45 Lunch\n13:00-14:00 Tower")
    assert pos.last_block(txt).splitlines() == ["09:00-10:00 Museum", "10:15-11:30 Park", "12:00-12:45 Lunch",
                                                "13:00-14:00 Tower"]
    p = _p(2, "s1")
    st = p["states"][0]
    lines = ps.render(p["refs"][0]).splitlines()
    noted = "\n".join(f"{ln}\n   (a note about this stop)" for ln in lines)
    assert pos.plan_points(st, noted)[:2] == (True, st["best"])
    # a later-time duplicate stays in the block, so the parser still catches it
    s, e = lines[0].split()[0].split("-")
    dup = "\n".join(lines + ["23:00-23:30 " + lines[0].split(" ", 1)[1]])
    assert pos.last_block(dup).count(lines[0].split(" ", 1)[1]) == 2


def test_t1_variant_is_the_sessions_first_turn():
    """variant t1 (rig screen): the same draw's turn 1 alone - one turn, its checks only, same optimum."""
    from crucibleforge.gen import planopt as P
    full = P._plan({"type": "planopt", "level": 2, "key": "s1"}, "4.5.0", "X")
    one = P._plan({"type": "planopt", "level": 2, "key": "s1", "variant": "t1"}, "4.5.0", "X")
    assert one["turns"] == full["turns"][:1] and one["bests"] == full["bests"][:1]
    assert one["checks"] and all(c["turns"] == [1] for c in one["checks"])
    assert P.grade_replies(one["checks"], [one["reference"][0]]) == 1.0
    with pytest.raises(Exception):
        P._plan({"type": "planopt", "level": 2, "key": "s1", "variant": "t2"}, "4.5.0", "X")
