"""T3 / MH3: SESSION-STATE ``multi_hard`` = the multi scene graded on the hard checks only (no reply-length)."""
import json
import re

import pytest

from crucibleforge import gen as G
from crucibleforge.gen import session_state as S
from crucibleforge.gen._stack_common import TURN_SEP, grade_weighted, join_turns, split_turns

IDS = [f"H{i}" for i in range(12)]


def g(variant, level=9, **kw):
    return {"type": "session_state", "level": level, "variant": variant, **kw}


def mat(variant, level, cid):
    return S.materialize(g(variant, level), release_seed="4.5.0", case_id=cid)


@pytest.mark.parametrize("level", [7, 8, 9, 10])
def test_same_draw_as_multi_and_only_hard_checks(level):
    for cid in IDS:
        m, h = mat("multi", level, cid), mat("multi_hard", level, cid)
        assert m["turns"] == h["turns"] and m["system"] == h["system"]          # the item the model sees
        assert S.reference(g("multi", level), release_seed="4.5.0", case_id=cid) == \
            S.reference(g("multi_hard", level), release_seed="4.5.0", case_id=cid)
        ids = [c["id"] for c in h["checks"]]
        assert ids and all(S.is_hard_check(i) for i in ids), ids
        assert "reply-length" not in ids and "no-puppet" not in ids
        want = {"presence-in", "presence-out"} | ({"exit-before", "exit-after", "A-false-fcb"} if level >= 8 else set())
        assert want <= set(ids)
        assert any(i.startswith("A-cb-") for i in ids)
        assert h["meta"]["variant"] == "multi_hard"
        # the kept checks are the multi checks, byte for byte (weights included)
        by = {c["id"]: c for c in m["checks"]}
        assert all(by[c["id"]] == c for c in h["checks"])


def test_reference_scores_one_and_every_hard_failure_costs_points():
    for cid in IDS[:6]:
        gen = g("multi_hard")
        h = mat("multi_hard", 9, cid)
        assert S.grade(S.reference(gen, release_seed="4.5.0", case_id=cid), h) == 1.0
        for name, ans in S.mutants_named(gen, release_seed="4.5.0", case_id=cid).items():
            assert S.grade(ans, h) < 1.0, name
        # lazy answers score 0
        n = h["meta"]["n_turns"]
        assert S.grade(join_turns(["Sure, noted."] * n), h) == 0.0


def test_reply_length_is_not_scored_but_presence_still_is():
    cid = IDS[0]
    gen = g("multi_hard")
    h = mat("multi_hard", 9, cid)
    ref = split_turns(S.reference(gen, release_seed="4.5.0", case_id=cid))
    # padding every scene reply far past 220 words costs nothing here ...
    long = [r + "\n" + " ".join(["The water laps at the boards."] * 60) for r in ref[:-1]] + [ref[-1]]
    assert S.grade(join_turns(long), h) == 1.0
    # ... while the plain multi variant (reply-length scored) does charge it
    assert S.grade(join_turns(long), mat("multi", 9, cid)) < 1.0
    # a speaker rule broken is still charged
    R = S._plan(gen, "4.5.0", cid)["rules"]
    out_t = R["presence"]["out"][0]
    bad = list(ref)
    bad[out_t - 1] += f"\n{R['presence']['npc']}: I should not be here."
    assert S.grade(join_turns(bad), h) < 1.0


def test_no_passive_format_rule_is_scored():
    for cid in IDS[:4]:
        h = mat("multi_hard", 9, cid)
        assert all(c["id"] not in ("no-untagged-speech", "no-user-line", "speaks-" + n)
                   for c in h["checks"] for n in S._plan(g("multi"), "4.5.0", cid)["cast"])


def test_engine_materialises_the_core_style_entry():
    case = {"id": "SESSION_STATE-multi_hard-core-9-1", "category": "rpdeep",
            "generator": {"type": "session_state", "level": 9, "key": "k", "variant": "multi_hard"}}
    G.materialize_case(case, "4.5.0")
    assert case["checks"] and case["programmatic"] and case["max_tokens"] == 6144
    with pytest.raises(Exception):
        G.materialize_case({"id": "x", "category": "rpdeep",
                            "generator": {"type": "session_state", "level": 9, "variant": "nope"}}, "4.5.0")
