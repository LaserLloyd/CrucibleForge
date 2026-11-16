"""4.5 'grade only the response given': a row stamped ``empty_stop`` reads as an empty reply everywhere
api.row_answer is used (judge, early stop, regrade); 4.1 rows (no stamp) read exactly as before."""
from crucibleforge import api, earlystop

CUT = "The1 small2 clock3 ticks4 and the answer is Odo: hello there"


def _row(**kw):
    return {"response": "", "reasoning": CUT, "finish_reason": "stop", "tool_calls": [], **kw}


def test_unstamped_row_still_reads_the_reasoning_channel():
    assert api.row_answer(_row()) == CUT                         # 4.1 behaviour unchanged


def test_empty_stop_row_is_an_empty_reply():
    r = _row(empty_stop={"reasoning_chars": len(CUT), "completion_tokens": 40, "reasoning_tail": CUT[-60:]})
    assert api.row_answer(r) == ""
    assert api.row_answer(dict(r, empty_stop={**r["empty_stop"], "history": "empty"})) == ""


def test_visible_content_wins_even_with_a_stamp():
    assert api.row_answer(_row(response="Odo: hi", empty_stop={"reasoning_chars": 1})) == "Odo: hi"


def test_early_stop_reads_the_stamped_row_as_blank():
    r = _row(empty_stop={"reasoning_chars": len(CUT)})
    assert earlystop.effective_answer(r) == "" and earlystop.effective_answer(_row()) == CUT


def test_scale_counts_empty_stop_turns_per_cell():
    from crucibleforge import scale
    ch = {"id": "FLAT-s-001", "kind": "flat", "part": "work/main", "ladder": "s", "rung": 1, "attempt": 1,
          "family": "a", "level": 1}
    rows = [{"chain": dict(ch), "turn": 1, "empty_stop": {"reasoning_chars": 9}},
            {"chain": dict(ch), "turn": 2},
            {"chain": dict(ch, score=0.5, passed=False, elements=[{"id": "e", "score": 0.5}]), "turn": None}]
    us = scale.units(rows)
    assert us[0]["empty_stop"] == 1
    rep = scale.ran_out_report(us)["a:L1"]
    assert rep["empty_stop"] == 1
    cal = {"cells": {"a:L1": {"b": 0.0}}, "families": {}}
    assert "empty stop: a:L1 1" in scale.render({"m": scale.score_run(us, cal)}, [])
