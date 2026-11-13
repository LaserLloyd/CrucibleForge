"""One policy for a FINISHED answer left in the reasoning channel (2026-11-13).

Nanbeige4.3-33B (27/122 rows), deepseek-r1-0528-8b (~12%) and Dark-Scarlett
v2/v3 (~6%) end replies with finish=stop, empty content, no tool call and the
whole answer in ``reasoning``. The single-turn objective path read that text
(``scoreable_text``) and stamped ``answered_in_reasoning``; the multi-turn
runner, the tool loop's prose steps and the judge did not, so a session such
as RD1-marisol was judged on twelve empty replies and scored 0.

Policy (api.reasoning_answer): finish=stop + empty content + no tool call +
non-empty reasoning -> the reasoning text (thinking tags stripped) IS the
answer, stamped answered_in_reasoning. finish=length is never an answer.
"""
from __future__ import annotations

import json

import pytest

from crucibleforge import api, judge, runner, stopflag
from crucibleforge.api import ChatResult, reasoning_answer, row_answer


@pytest.fixture(autouse=True)
def _clear_stop_flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    yield
    stopflag.ABORT.clear()
    runner.STOP.clear()


# ------------------------------------------------------------ the policy

def test_reasoning_answer_only_for_a_finished_empty_reply():
    assert reasoning_answer("", "Marisol turns.", "stop", None) == "Marisol turns."
    assert reasoning_answer("", "<think>x</think>Marisol turns.", "stop", []) == "Marisol turns."
    # content wins, a tool call is an answer, an overflow is never one
    assert reasoning_answer("shown", "hidden", "stop", None) == ""
    assert reasoning_answer("", "hidden", "stop", [{"name": "f"}]) == ""
    assert reasoning_answer("", "cut off chain", "length", None) == ""
    # a dropped stream / content filter is not a finished reply
    assert reasoning_answer("", "partial", None, None) == ""
    assert reasoning_answer("", "partial", "content_filter", None) == ""
    assert reasoning_answer("", "   ", "stop", None) == ""


def test_row_answer_reads_old_rows_from_the_raw_fields():
    # rows written before the stamp existed (no answered_in_reasoning key)
    assert row_answer({"response": "", "reasoning": "R", "finish_reason": "stop"}) == "R"
    assert row_answer({"response": "", "reasoning": "R", "finish_reason": "length"}) == ""
    assert row_answer({"response": "C", "reasoning": "R", "finish_reason": "stop"}) == "C"


def test_single_turn_stamp_formula_is_shared():
    assert api.in_reasoning_flag("", "r", None) is True
    assert api.in_reasoning_flag("", "r", [{"name": "f"}]) is False
    assert api.in_reasoning_flag("c", "r", None) is False


# ------------------------------------------------------------ multi-turn

class _P:
    """Replies as (content, reasoning, finish) triples."""
    name, type = "fake", "openai"

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def chat(self, model_id, messages, **kw):
        self.sent.append([dict(m) for m in messages])
        c, r, f = self.replies.pop(0)
        return ChatResult(response_text=c, reasoning_text=r, finish_reason=f, served_model="m")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(p, replay=False, recovery=False):
    """recovery=False: the 4.0 profile path (one empty-stop retry)."""
    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "thinking": True}, p, 8192)
    ctx.reasoning_replay = replay
    ctx.recovery_enabled = recovery
    return ctx


def _case(cat="rpdeep"):
    return {"id": "S", "category": cat, "turns": ["one", "two", "three"], "max_tokens": 64,
            "rubric": "rp_multi"}


@pytest.mark.parametrize("cat", ["rpdeep", "rp", "nsfw"])
def test_session_turn_answered_in_reasoning_is_the_answer(cat):
    # turn 2 finishes with its reply in the reasoning channel; the empty-stop
    # retry (4.0 profile path) gets the same thing back, so it stands
    p = _P([("first", "", "stop"),
            ("", "Marisol answers in reasoning.", "stop"),
            ("", "Marisol answers in reasoning.", "stop"),
            ("third", "", "stop")])
    rows = runner._run_multiturn(_case(cat), {}, _ctx(p), 64, 0.0, 1.0, 1)
    t2 = rows[1]
    assert t2["response"] == ""                       # the raw channel stays honest
    assert t2["reasoning"] == "Marisol answers in reasoning."
    assert t2["answered_in_reasoning"] is True
    assert rows[0]["answered_in_reasoning"] is False
    # the judged conversation carries it
    conv = rows[-1]["conversation"]
    assert [m["content"] for m in conv if m["role"] == "assistant"] == [
        "first", "Marisol answers in reasoning.", "third"]
    # and the next turn is SENT with it as the assistant's reply (no glued user turns)
    last = p.sent[-1]
    assert [m["role"] for m in last] == ["user", "assistant", "user", "assistant", "user"]
    assert last[3] == {"role": "assistant", "content": "Marisol answers in reasoning."}
    # the judge sees content on the final turn
    assert judge._has_content(rows[-1])


def test_session_overflow_turn_stays_empty():
    p = _P([("first", "", "stop"),
            ("", "a long cut-off chain", "length"),
            ("third", "", "stop")])
    rows = runner._run_multiturn(_case(), {}, _ctx(p), 64, 0.0, 1.0, 1)
    assert rows[1]["truncated"] is True
    conv = rows[-1]["conversation"]
    assert [m["content"] for m in conv if m["role"] == "assistant"] == ["first", "", "third"]


def test_recovery_ladder_runs_first_and_the_fallback_applies_only_when_it_fails():
    """With the recovery ladder on, a stop inside reasoning is asked for
    again (thinking off, then continuation); only when every attempt still
    leaves the reply in the reasoning channel is the FIRST reply's reasoning
    read as the answer — exactly the single-turn order."""
    class P(_P):
        def chat(self, model_id, messages, **kw):
            self.sent.append([dict(m) for m in messages])
            if messages[-1]["content"] == "b":
                return ChatResult(response_text="ok", finish_reason="stop", served_model="m")
            return ChatResult(response_text="", reasoning_text="the reply",
                              finish_reason="stop", served_model="m")
    p = P([])
    rows = runner._run_multiturn({**_case(), "turns": ["a", "b"]}, {}, _ctx(p, recovery=True),
                                 64, 0.0, 1.0, 1)
    assert len(p.sent) >= 3                       # the ladder ran on turn 1
    assert rows[0]["recovery"]["mode"] is None    # ...and did not recover it
    assert rows[0]["answered_in_reasoning"] is True
    assert p.sent[-1][1] == {"role": "assistant", "content": "the reply"}


def test_reasoning_answer_is_not_replayed_twice_on_a_replay_provider():
    p = _P([("", "the reply", "stop"), ("", "the reply", "stop"), ("ok", "think", "stop")])
    runner._run_multiturn({**_case(), "turns": ["a", "b"]}, {}, _ctx(p, replay=True),
                          64, 0.0, 1.0, 1)
    sent = p.sent[-1][1]
    assert sent == {"role": "assistant", "content": "the reply"}


def test_assistant_turn_keeps_reasoning_replay_for_a_normal_reply():
    r = ChatResult(response_text="answer", reasoning_text="chain", finish_reason="stop")
    assert runner.assistant_turn(r, True) == {"role": "assistant", "content": "answer",
                                             "reasoning_content": "chain"}
    assert runner.assistant_turn(r, False) == {"role": "assistant", "content": "answer"}


# ------------------------------------------------------------ tool loop prose step

def test_tool_loop_prose_step_carries_the_reasoning_answer_forward():
    case = {"id": "T", "category": "agentic", "tools": [],
            "tool_script": [{"user": "q1", "expect_answer_contains": ["42"]},
                            {"user": "q2", "expect_answer_contains": ["ok"]}]}
    p = _P([("", "The answer is 42.", "stop"), ("", "The answer is 42.", "stop"),
            ("ok", "", "stop")])
    row = runner._run_tool_loop(case, {}, _ctx(p), 64, 0.0, 1)
    assert p.sent[-1][1] == {"role": "assistant", "content": "The answer is 42."}
    texts = [m["content"] for m in row["tool_conversation"] if m["role"] == "assistant"]
    assert texts[0] == "The answer is 42."


# ------------------------------------------------------------ the judge

def _single(**kw):
    return {"case_id": "C", "rubric": "rp_single", "needs_judge": True, "prompt": "p",
            "response": "", "reasoning": "The scene, written.", "finish_reason": "stop",
            "tool_calls": None, **kw}


def test_single_turn_judged_row_reads_the_reasoning_answer():
    row = _single()
    assert judge._has_content(row)
    _, prompt = judge.build_judge_input(row)
    assert "The scene, written." in prompt
    assert judge.assistant_text(row) == "The scene, written."


def test_single_turn_overflow_stays_an_empty_generation():
    row = _single(finish_reason="length")
    assert not judge._has_content(row)

    class _JC:
        pass
    v = judge._judge_row(_JC(), row)
    assert v["empty_generation"] is True


def test_reference_rubric_reads_the_reasoning_answer():
    row = _single(rubric="reference", reference="42", reasoning="So it is 42.")
    _, prompt = judge.build_judge_input(row)
    assert "So it is 42." in prompt


# ------------------------------------------------------------ re-judge tool

def _write(tmp_path, rows):
    from crucibleforge.config import set_results_dir
    set_results_dir(tmp_path)
    (tmp_path / "transcripts_m.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _session_rows(final_judge, last_finish="stop", last_content="three", rubric="rp_multi"):
    base = {"bench_run_id": "b1", "case_id": "S", "repeat": 1, "category": "rp"}
    t = [
        {**base, "turn": 1, "response": "one-r", "reasoning": "", "finish_reason": "stop"},
        {**base, "turn": 2, "response": "", "reasoning": "two-r", "finish_reason": "stop"},
        {**base, "turn": 3, "response": "", "reasoning": "chain", "finish_reason": "length"},
    ]
    final = {**base, "turn": 4, "response": last_content, "reasoning": "",
             "finish_reason": last_finish, "needs_judge": True, "rubric": rubric,
             "conversation": [{"role": "system", "content": "card"},
                              {"role": "user", "content": "u1"},
                              {"role": "assistant", "content": "one-r"},
                              {"role": "user", "content": "u2"},
                              {"role": "assistant", "content": ""},
                              {"role": "user", "content": "u3"},
                              {"role": "assistant", "content": ""},
                              {"role": "user", "content": "u4"},
                              {"role": "assistant", "content": last_content}],
             "judge": final_judge}
    return t + [final]


def test_rejudge_dry_run_lists_and_writes_nothing(tmp_path):
    from crucibleforge import reasoning_repair as rr
    _write(tmp_path, _session_rows({"judge_failed": True, "empty_generation": False,
                                    "scores": {"x": 1}}))
    before = (tmp_path / "transcripts_m.jsonl").read_bytes()
    rep = rr.run(["m"])
    assert (tmp_path / "transcripts_m.jsonl").read_bytes() == before
    rows = rep["m"]["rows"]
    assert len(rows) == 1 and rows[0]["turns"] == [2]     # turn 3 overflowed: untouched
    assert rows[0]["verdict"] == "judged"


def test_rejudge_apply_makes_the_row_pending_with_the_repaired_conversation(tmp_path):
    from crucibleforge import reasoning_repair as rr
    from crucibleforge.config import load_transcripts
    _write(tmp_path, _session_rows({"empty_generation": True, "judge_failed": True}))
    rr.run(["m"], apply=True)
    final = next(r for r in load_transcripts("m") if r.get("conversation"))
    assert judge.needs_verdict(final)
    assert final["judge_before_reasoning_repair"]["empty_generation"] is True
    assert [m["content"] for m in final["conversation"] if m["role"] == "assistant"] == [
        "one-r", "two-r", "", "three"]
    assert final["answered_in_reasoning_turns"] == [2]
    # idempotent: a second pass finds nothing
    assert rr.run(["m"])["m"]["rows"] == []


def test_rejudge_skips_a_session_whose_final_turn_stays_empty(tmp_path):
    from crucibleforge import reasoning_repair as rr
    _write(tmp_path, _session_rows({"empty_generation": True}, last_finish="length",
                                   last_content=""))
    rep = rr.run(["m"])["m"]
    assert rep["rows"] == [] and rep["skipped"][0]["turns"] == [2]
    # rp_deep judges per fact group: the repair still matters there
    _write(tmp_path, _session_rows({"items": []}, last_finish="length", last_content="",
                                   rubric="rp_deep"))
    assert len(rr.run(["m"])["m"]["rows"]) == 1


def test_rejudge_single_turn_row(tmp_path):
    from crucibleforge import reasoning_repair as rr
    _write(tmp_path, [{"bench_run_id": "b", "case_id": "C", "repeat": 1, "turn": None,
                       "needs_judge": True, "rubric": "rp_single", "response": "",
                       "reasoning": "R", "finish_reason": "stop",
                       "judge": {"empty_generation": True}},
                      {"bench_run_id": "b", "case_id": "D", "repeat": 1, "turn": None,
                       "needs_judge": True, "rubric": "rp_single", "response": "",
                       "reasoning": "R", "finish_reason": "length",
                       "judge": {"empty_generation": True}}])
    rows = rr.run(["m"])["m"]["rows"]
    assert [r["case_id"] for r in rows] == ["C"]


def test_rejudge_hint_drops_the_case_filter_for_ladder_pack_cases():
    """2026-11-13: the printed `judge --cases` command died on "unknown case id(s)" when a repaired row belonged to
    a ladder pack (LNC1-glasshouse-keys), leaving every repaired row of the batch pending."""
    from crucibleforge.cli import _rejudge_hint
    known = {"RD1-marisol", "NC2-after-hours"}
    assert _rejudge_hint("m", {"NC2-after-hours", "RD1-marisol"}, known) == \
        "crucibleforge judge --models m --cases NC2-after-hours,RD1-marisol"
    h = _rejudge_hint("m", {"RD1-marisol", "LNC1-glasshouse-keys"}, known)
    assert h.startswith("crucibleforge judge --models m ") and "--cases " not in h and "LNC1-glasshouse-keys" in h
    assert "--cases RD1-marisol" in _rejudge_hint("m", {"RD1-marisol"}, None)   # profile unreadable: old hint
