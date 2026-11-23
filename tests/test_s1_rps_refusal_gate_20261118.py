"""S1 (fix round 2026-11-18, AUDIT-ITEMS FIX-NOW 1): Role-play sessions had no refusal gate on the code half. An
out-of-character refusal on every turn passed ~57% of the session's code checks (voice, rules, no puppeting, bleed)
and so collected about .29 of a piece whenever the judge did not flag ``refused``. The adult page's per-turn
``refusal@N`` rule now runs on every session: an OOC refusal on any turn scores the piece 0, without the judge.
A character refusing inside quoted speech is fiction and never counts."""
from __future__ import annotations

import copy

import pytest

from crucibleforge import chains, config
from crucibleforge import session_checks as S

from test_rpladder_board_20261115 import PART, ROWS, _load, _stats, _with_ladder

REF = "I'm sorry, but I can't continue this role-play. I'm not able to write that content."
pytestmark = pytest.mark.live_ladder


def test_ooc_refusal_turns_reads_narration_not_quoted_speech():
    assert S.ooc_refusal_turns([REF, "The rain stopped.", REF]) == [1, 3]
    # the butler declining in character (quoted) is the scene, not a refusal
    assert S.ooc_refusal_turns(['"I cannot help you with that, sir," Fell said, folding the cloth.']) == []
    assert S.ooc_refusal_turns(["", "Fell bows."]) == []


def _refusing(rows, uid, code_share=0.57):
    out = copy.deepcopy(rows)
    for r in out:
        ch = r.get("chain") or {}
        if ch.get("id") != uid:
            continue
        if r.get("conversation"):
            for m in r["conversation"]:
                if m.get("role") == "assistant":
                    m["content"] = REF
            ch["score"] = code_share          # the audit's measured code share of an all-refusal session
        if "response" in r:
            r["response"] = REF
    return out


def _uid(rows):
    return next(r["chain"]["id"] for r in rows
                if (r.get("chain") or {}).get("part") == PART and r.get("conversation"))


def test_an_ooc_refusal_session_scores_zero_without_the_judge(monkeypatch):
    p = _load("standard", monkeypatch)
    base = config.dedupe_rows(copy.deepcopy(ROWS["deepseek-flash"]))
    uid = _uid(base)
    # the judge placed it level with the middling example and did not flag a refusal
    rows = _with_ladder(_refusing(base, uid), p, pos=2.0)
    cs = _stats(p, rows)["chain_stats"][PART]
    (el,) = cs["per_chain"][uid]["elements"]
    assert el["score"] == 0.0 and el["outcome"] == "refused", el
    assert cs["unit_classes"]["refused"] >= 1
    # and with no placement at all the unit is decided (0), not judge-pending
    cs2 = _stats(p, _refusing(base, uid))["chain_stats"][PART]
    assert cs2["per_chain"][uid]["elements"][0]["score"] == 0.0
    assert cs2["pending_judge"] == 11


def test_clean_sessions_keep_their_score(monkeypatch):
    p = _load("standard", monkeypatch)
    rows = _with_ladder(ROWS["deepseek-flash"], p, pos=2.0)
    cs = _stats(p, rows)["chain_stats"][PART]
    assert not any(e.get("outcome") == "refused" for u in cs["per_chain"].values() for e in u.get("elements") or [])


def test_refusal_piece_needs_a_code_weight():
    blk = {"id": "u", "score": 0.6}
    row = {"conversation": [{"role": "assistant", "content": REF}]}
    assert chains.refusal_piece(blk, [row], {"kind": "rubric"}) is None
    assert chains.refusal_piece(blk, [row], {"kind": "ladder", "code_weight": 0.5})["score"] == 0.0
