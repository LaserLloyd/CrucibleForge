"""Fix pass C: NSFW+ takes the SAME cut wrapper as Code and Chat (report.unit_cut) for single replies and sessions,
and the board says what happened to each cut unit ("left out of the mean" or "did not finish ..., scored 0")."""
from __future__ import annotations

import pytest

from crucibleforge import gen
from crucibleforge import nsfwplus45 as N
from crucibleforge import report as R
from crucibleforge import segments45 as S
from crucibleforge.gen import adult45

pytestmark = pytest.mark.live_ladder
ZERO = {"cut_at_full_budget": "zero", "thinking_max_tokens_cap": 1000}


@pytest.fixture(autouse=True, scope="module")
def _discover():
    gen.discover()


def _single(text, finish="stop", sent=1000, used=10, **kw):
    return {"case_id": "c", "repeat": 1, "response": text, "finish_reason": finish, "max_tokens_sent": sent,
            "metrics": {"completion_tokens": used}, **kw}


@pytest.mark.parametrize("row,expect", [
    (_single("x " * 30, "length", used=999), "did_not_finish"),        # full budget cut
    (_single("x " * 30, "length", used=400), "left_out"),              # cut below the full budget
    (_single("ok"), "scored"),
])
def test_code_and_nsfw_plus_get_the_same_disposition_for_a_single_reply(row, expect):
    assert R.unit_cut([row], "zero", 1000) == expect
    kept, rec = R.case_part_rows([row, _single("fine", case_id="d")], "exclude", "zero", 1000)
    assert (row in kept) == (expect != "left_out")
    (u,) = N.apply_cut_rule([{"segment": "range", "rows": [row], "numbered": False, "status": {}}], ZERO)
    assert u["status"]["disposition"] == expect
    assert u["status"]["cut"] == (expect == "left_out") and u["status"]["dnf"] == (expect == "did_not_finish")


def test_a_session_with_a_cut_turn_is_left_out_whole_in_both():
    turns = [{**_single(f"t{i}"), "turn": i} for i in (1, 2, 3)]
    turns[1] = {**turns[1], "finish_reason": "length", "metrics": {"completion_tokens": 1000}}
    kept, rec = R.case_part_rows(turns, "exclude", "zero", 1000)
    assert kept == [] or not any(t in kept for t in turns)
    assert rec["ran_out"] == 1 and not rec.get("did_not_finish")
    (u,) = N.apply_cut_rule([{"segment": "roleplay_session", "rows": turns, "numbered": True, "status": {}}], ZERO)
    assert u["status"]["disposition"] == "left_out"


def _taboo(n_full):
    out = []
    for i, it in enumerate(adult45.items("taboo")):
        row = _single(it["ref_reply"], "length" if i < n_full else "stop", used=999 if i < n_full else 10)
        out.append({"id": f"range-{i}", "segment": "range", "card": it["id"], "replies": [it["ref_reply"]],
                    "rows": [row], "numbered": False, "status": {}})
    return out


def test_the_board_says_what_happened_to_each_cut_unit():
    units = _taboo(2)
    ver = {u["id"]: {"cls": "EXPLICIT"} for u in units}
    nums = N.numbers(units, ver, ZERO)
    blk = S.nsfw_marks({"numbers": nums}, {k: {"state": "applied"} for k in ()})
    g = nums["nsfw"]["segments"]["range"]
    assert S.cut_record(g["cut"]) == {"left_out": 0, "did_not_finish": 2, "of": 21, "dnf_zero": True}
    line = R.cut_words(S.cut_record(g["cut"]))
    assert line == "2 of 21 did not finish at the full budget, scored 0" and "left out" not in line
    # below the full budget: left out, never "scored 0"
    units = [{**u, "rows": [{**u["rows"][0], "metrics": {"completion_tokens": 300}}]} for u in _taboo(2)]
    g = N.numbers(units, ver, ZERO)["nsfw"]["segments"]["range"]
    assert R.cut_words(S.cut_record(g["cut"])) == "2 of 21 cut, left out of the mean"
    assert blk is not None


def test_both_docs_state_the_rule_in_the_same_words():
    from pathlib import Path
    root = Path(R.__file__).resolve().parents[1] / "docs"
    key = "One cut rule for Code, Chat and NSFW+, applied by the same code to single replies and to multi-turn"
    a = " ".join((root / "SCORING.md").read_text(encoding="utf-8").split())
    b = " ".join((root / "SCORING-RULES-45.md").read_text(encoding="utf-8").split())
    i, j = a.index(key), b.index(key)
    assert a[i:i + 600] == b[j:j + 600]
