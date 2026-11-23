"""4.5 grading of degenerate and empty-stop replies (2026-11-14, lead's ruling).

- A degenerate reply (degenerate.py) in a run that did not trip counts as a FAILURE, like a
  blank: graded on an empty answer, never on the loop's text (a "no user line" check would
  otherwise pass on "════").
- A degenerate reply is never regenerated (X's on_empty_stop: regenerate): the order in
  ``_Ctx.call`` is degenerate first, then the empty-stop retry. A row can therefore carry
  ``empty_stop_retry`` together with ``degenerate`` only when the FIRST attempt was a plain
  empty stop and the RETRY looped (the retry is what was graded).
- the owner's "grade only the response given": a row stamped ``empty_stop`` is graded on an empty
  answer in the live verdict and on the chains board (``api.row_answer`` already reads it so).
4.1 (no ``empty_stop_rule``) reads exactly as before.
"""
from __future__ import annotations

from crucibleforge import chains, runner
from crucibleforge.api import ChatResult

LOOP = ("", "answer 42 " * 400, "stop", 16000)              # a finished reply whose think looped
CUT = ("", "The answer is 42 because the ledger says so and the", "stop", 636)
OK = ("42", "short", "stop", 20)


class _P:
    name, type = "fake", "openai"
    extra_body, reasoning_replay = {}, True

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def chat(self, model_id, messages, **kw):
        self.sent.append([dict(m) for m in messages])
        c, r, f, n = self.replies.pop(0)
        return ChatResult(response_text=c, reasoning_text=r, finish_reason=f, completion_tokens=n,
                          prompt_tokens=100, served_model="m")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(p, rule=True, regen=True):
    d = {"on_empty_stop": "regenerate"} if regen else {}
    ctx = runner._Ctx({"defaults": d}, {"name": "m", "model_id": "m", "thinking": True}, p, 32768)
    ctx.recovery_enabled = False
    ctx.thinking_fallback = False
    ctx.empty_stop_rule = rule
    ctx.reasoning_replay = True
    return ctx


CASE = {"id": "c", "category": "general", "prompt": "q", "max_tokens": 64,
        "grader": "contains", "grader_config": {"needles": ["42"]}}


def _single(replies, **kw):
    p = _P(replies)
    row = runner._run_single(dict(CASE), {}, _ctx(p, **kw), 64, 0.6, 0.95, 1)
    runner.stamp_degenerate(row)
    return p, row


# ------------------------------------------------------------ regenerate vs degenerate


def test_a_degenerate_first_attempt_is_not_regenerated_and_fails():
    p, row = _single([LOOP, OK])
    assert len(p.sent) == 1                                   # no regenerate
    assert "empty_stop_retry" not in row
    # still empty, so also an empty stop: grade only the response given; the loop is in the reasoning
    assert row["empty_stop"] and row["degenerate"]["channel"] == "reasoning"
    assert row["grade"] == "fail"                             # graded as a blank, never on "answer 42 ..."


def test_an_empty_first_attempt_then_a_degenerate_retry_carries_both_and_fails():
    p, row = _single([CUT, LOOP])
    assert len(p.sent) == 2
    assert row["empty_stop_retry"]["mode"] == "regenerate" and row["empty_stop_retry"]["retry_visible"] is False
    assert row["degenerate"] and row["grade"] == "fail"


def test_an_empty_first_attempt_then_a_good_retry_passes():
    p, row = _single([CUT, OK])
    assert row["grade"] == "pass" and "degenerate" not in row and "empty_stop" not in row


# ------------------------------------------------------------ grade only the response given


def test_an_empty_stop_row_is_graded_on_nothing_under_the_rule():
    p, row = _single([CUT, CUT])
    assert row["empty_stop"] and row["grade"] == "fail"       # "42" sits in the reasoning only


def test_4_1_reads_the_reasoning_channel_as_before():
    p, row = _single([CUT, CUT], rule=False, regen=False)
    assert "empty_stop" not in row and row["grade"] == "pass"
    p, row = _single([LOOP, LOOP], rule=False, regen=False)                 # 4.1 re-asks an empty stop once
    assert row["grade"] == "pass"                             # 4.1 grading untouched (row still stamped)


# ------------------------------------------------------------ the chains board


def test_the_board_reads_stamped_rows_as_blank():
    base = {"finish_reason": "stop", "response": "", "reasoning": "The answer is 42."}
    assert chains._row_text(base) == "The answer is 42."
    assert chains._row_text({**base, "empty_stop": {"history": "empty"}}) == ""
    assert chains._row_text({**base, "degenerate": {"channel": "answer"}}) == ""
    assert chains._row_text({"finish_reason": "stop", "response": "═" * 3000}) == ""   # unstamped yet


def test_session_replies_blank_the_stamped_turns():
    rows = [{"turn": 1, "response": "Hello.", "finish_reason": "stop"},
            {"turn": 2, "response": "", "reasoning": "draft", "empty_stop": {"history": "empty"},
             "finish_reason": "stop"},
            {"turn": 3, "response": "═" * 3000, "finish_reason": "stop",
             "conversation": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "Hello."},
                              {"role": "user", "content": "b"}, {"role": "assistant", "content": ""},
                              {"role": "user", "content": "c"}, {"role": "assistant", "content": "═" * 3000}]}]
    assert chains._replies(rows) == ["Hello.", "", ""]


# ------------------------------------------------------------ counted per set, dropped only on a trip


def test_scale_counts_degenerate_units_per_cell_and_renders_the_flag():
    from crucibleforge import scale
    us = [{"cell": "c1", "rule": "zero", "n": 3, "ran_out": 0, "degenerate": 1, "empty_stop": 0},
          {"cell": "c1", "rule": "zero", "n": 3, "ran_out": 0, "degenerate": 0, "empty_stop": 1},
          {"cell": "c2", "rule": "zero", "n": 3, "ran_out": 0}]
    rep = scale.ran_out_report(us)
    assert rep["c1"]["degenerate"] == 1 and rep["c1"]["empty_stop"] == 1 and rep["c2"]["degenerate"] == 0


def test_elemstats_table_has_a_degenerate_column_before_empty_stop():
    from crucibleforge import elemstats
    base = {"stratum": "s", "unit": "u1", "think": 1.0, "answer": 0.0, "cell": "s", "concept": "-", "band": "-",
            "rule": "zero", "unit_prompt": 10.0, "unit_cached": 0.0, "unit_cache_known": 0.0,
            "unit_empty_stop": 2, "unit_degenerate": 1}
    els = {"e1": {**base, "score": 0.0, "outcome": "fail"}, "e2": {**base, "score": 1.0, "outcome": "pass"}}
    rows = elemstats.table({"m": els})
    assert rows[0]["degenerate"] == 1 and rows[0]["empty_stop"] == 2
    line = elemstats.render(rows).splitlines()[2]
    assert line.endswith("| 1 | 2 |")


def test_the_report_notes_degenerate_replies():
    from crucibleforge import report
    assert any("2 reply(ies) degenerate" in x for x in report._model_notes("m", {"degenerate": 2}))


def test_rows_are_dropped_only_for_the_run_that_tripped():
    from crucibleforge import scale
    rows = [{"bench_run_id": "a", "x": 1}, {"bench_run_id": "b", "x": 2}, {"bench_run_id": "b", "x": 3}]
    kept, d = scale.drop_tripped(rows, {"early_stop": {"signal": "probe", "bench_run_id": "b"}})
    assert kept == rows[:1] and d == {"bench_run_id": "b", "signal": "probe", "rows": 2}
    # a run that did not trip keeps everything, degenerate rows included (scored as failures)
    kept, d = scale.drop_tripped(rows, {"early_stop": {"tripped": False, "counts": {"degenerate": 1}}})
    assert kept == rows and d is None
    assert scale.drop_tripped(rows, None) == (rows, None)
