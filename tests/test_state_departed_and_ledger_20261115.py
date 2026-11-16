"""SESSION-STATE level 10 (scrub pass 1, lead 2026-11-15).

1. No scripted user line addresses a character the card has removed: once the user gives the exit item away, the
   exit character has no line, so no later action (a buy, a give, an earn) or distractor names them (rc3 001 turns
   9 and 11, 003 turn 11 did; MiniMax's only loss in 001, and the strong models' A2 in 001, came from it).
2. Every question the final turn asks is graded: ``multi_hard`` now keeps the ledger answers (A1-A4) and the plain
   false-memory probes next to the speaker rules, callbacks and the false callback; the reference still scores 1.0."""
from __future__ import annotations

import re

import pytest

from crucibleforge.gen import session_state as S
from crucibleforge.gen._stack_common import grade_weighted

IDS = [f"X{i}" for i in range(30)]


def _plan(variant, cid, level=10):
    return S._plan({"type": "session_state", "level": level, "variant": variant}, "4.5.0", cid)


@pytest.mark.parametrize("level", [8, 9, 10])
def test_no_user_line_after_the_exit_names_the_departed(level):
    seen = 0
    for cid in IDS:
        p = _plan("multi", cid, level)
        e = (p["rules"] or {}).get("exit")
        if not e:
            continue
        seen += 1
        word = re.compile(rf"\b{re.escape(e['npc'])}\b")
        give = f"I give the {e['item']} to "
        t0 = p["turns"][e["t"] - 1]
        after_give = t0[t0.index(give) + len(give):]
        after_give = after_give.split(".", 1)[1] if "." in after_give else ""
        assert not word.search(after_give), (cid, t0)
        for k in range(e["t"], len(p["turns"]) - 1):            # the scripted turns after the exit (not the OOC check)
            assert not word.search(p["turns"][k]), (cid, k + 1, p["turns"][k])
        assert grade_weighted(p["checks"], p["reference"]) == 1.0
    assert seen >= 20


@pytest.mark.live_ladder
def test_the_rc3_sessions_name_a_present_partner(monkeypatch):
    """The three rc3-main L10 sessions as the bench sends them: 001 turns 9 and 11 named Sable and 003 turn 11 named
    Ilse after they had left; 002 had no such line and is byte-identical."""
    from crucibleforge import chains, profiles
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    prof = profiles.load_profile("bench45")
    profiles.apply_profile(prof, {"defaults": {}, "models": [], "providers": {}})
    specs = {c["id"]: c for c in chains.chain_cases(prof)}
    gone = {}
    for n in ("001", "002", "003"):
        case = chains.materialize(chains.instance(specs[f"FLAT-rc2_state_L10-{n}"], 1, 1))
        ex = re.search(r"When \w+ gives away the ([^,]+), (\w+) leaves for good", case["system"])
        item, npc = ex.group(1), ex.group(2)
        gone[n] = npc
        k = next(i for i, t in enumerate(case["turns"]) if f"give the {item} to" in t)
        assert not any(re.search(rf"\b{npc}\b", t) for t in case["turns"][k + 1:-1]), (n, npc)
    assert gone["001"] == "Sable" and gone["003"] == "Ilse"


@pytest.mark.parametrize("level", [7, 8, 9, 10])
def test_multi_hard_grades_every_question_asked(level):
    for cid in IDS[:12]:
        h = _plan("multi_hard", cid, level)
        ids = {c["id"] for c in h["checks"]}
        assert {"A-room", "A-coins", "A-time", "A-pack-has", "A-pack-no-extra"} <= ids
        assert any(i.startswith("A-false-") and i != "A-false-fcb" for i in ids)
        assert not ids & {"reply-length", "no-puppet", "no-untagged-speech", "no-user-line"}
        asked = len(re.findall(r"^Q\d+\.", h["turns"][-1], re.M))
        answers = {c.get("fact") or c["id"] for c in h["checks"] if c["id"].startswith("A-")}
        # A3 (the pack) is two checks: what is there and nothing extra
        assert len({a for a in answers if a != "A-pack-no-extra"}) == asked, (cid, sorted(answers), asked)
        assert grade_weighted(h["checks"], h["reference"]) == 1.0
