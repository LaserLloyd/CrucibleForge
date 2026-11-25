"""PLAN-451-LIMITS step 5 (2026-11-25): the console loop on the tool families (toolworld, toolgap) regenerates an
empty stop like every other row, lets the session's own EMPTY reminder reach the model instead of ending the task,
reads a finished answer left in the reasoning channel as the reply (reply_of), and stamps the per-turn budget. Old
tool rows that ended on an empty reply are owed again by a plain resume (attempts45.superseded); they keep scoring
until a re-run replaces them, and the replacing run says why. COMPACTOR rows are unchanged."""
from __future__ import annotations

import pytest

from crucibleforge import attempts45, cli, report, runner
from crucibleforge.api import ChatResult
from crucibleforge.gen import toolworld as tw

SEED = "4.5.0"


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(runner, "row_budget", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_prompt_tokens", lambda m: 10)
    g = {"type": "toolworld", "level": 1, "key": "r1"}
    m = tw.materialize(g, release_seed=SEED, case_id="T-run")
    c = {"id": "TOOLWORLD-r1-1", "category": "tooluse", "generator": g, **m}
    assert c["console"]["family"] == "toolworld"
    return c, tw.reference(g, release_seed=SEED, case_id="T-run").split("\n<<<TURN>>>\n")


def _ctx(results):
    it = iter(results)

    class Ctx:
        sent: list = []
        kws: list = []

        def budget(self, k):
            return k * 4

        def recover_for(self, c):
            return False

        def regen_for(self, c):
            return True

        def call(self, messages, **kw):
            Ctx.sent.append(messages[-1]["content"])
            Ctx.kws.append(kw)
            return next(it, ChatResult(response_text="", finish_reason="stop"))
    return Ctx()


def _ok(text):
    return ChatResult(response_text=text, reasoning_text="", finish_reason="stop", tool_calls=[],
                      prompt_tokens=10, completion_tokens=5, reasoning_tokens=0)


def test_an_empty_stop_is_regenerated_and_the_empty_reminder_reaches_the_model(case):
    c, ref = case
    ctx = _ctx([ChatResult(response_text="", finish_reason="stop")] + [_ok(t) for t in ref])
    row = runner._run_console_loop(c, {"case_id": c["id"]}, ctx, 512, None, None)
    assert all(kw.get("regen") is True for kw in ctx.kws)                # an empty stop may be asked again
    assert ctx.sent[1].startswith("EMPTY:")                                # the session's reminder was sent
    assert row["console"]["end"] != "empty reply" and row["console_rev"] == runner.CONSOLE_REV
    assert row["max_tokens_sent"] == 512 * 4                                # the per-turn budget, stamped
    assert attempts45.superseded(row) is None


def test_a_finished_answer_in_the_reasoning_channel_is_the_reply(case):
    c, ref = case
    first = ChatResult(response_text="", reasoning_text=ref[0], finish_reason="stop", tool_calls=[])
    plain = runner._run_console_loop(c, {"case_id": c["id"]}, _ctx([_ok(t) for t in ref]), 512, None, None)
    row = runner._run_console_loop(c, {"case_id": c["id"]}, _ctx([first] + [_ok(t) for t in ref[1:]]), 512, None,
                                   None)
    assert row["answered_in_reasoning"] == 1
    assert row["score"] == plain["score"] and row["grade"] == plain["grade"]


def test_old_tool_rows_that_ended_empty_are_owed_but_still_score():
    old = {"case_id": "T", "repeat": 1, "category": "tooluse", "grade": "fail", "score": 0.0,
           "response": "", "reasoning": "r", "finish_reason": "stop",
           "console": {"family": "toolworld", "end": "empty reply"}}
    assert attempts45.superseded(old) == "empty stop"
    assert not attempts45.is_infra(old)                                     # the board keeps scoring it until a re-run
    assert attempts45.superseded({**old, "console_rev": 2}) is None
    assert attempts45.superseded({**old, "console": {"family": "toolworld", "end": "final answer"}}) is None
    assert attempts45.superseded({**old, "console": {"end": "empty reply"}}) is None        # COMPACTOR: untouched
    cases = [{"id": "T", "category": "tooluse"}]
    cfg = {"defaults": {"repeats": {"tooluse": 1}}}
    assert cli._first_reply_jobs("m", cfg, cases, rows=[old]) == {(None, "T", 1)}
    assert cli._first_reply_jobs("m", cfg, cases, rows=[{**old, "console_rev": 2}]) == set()


def test_the_replacing_run_says_why(monkeypatch):
    monkeypatch.setattr(report, "_job_complete", lambda rows: True)
    old = {"case_id": "T", "repeat": 1, "bench_run_id": "a", "ts": "2026-11-20T00:00:00", "grade": "fail",
           "console": {"family": "toolworld", "end": "empty reply"}}
    new = {"case_id": "T", "repeat": 1, "bench_run_id": "b", "ts": "2026-11-26T00:00:00", "grade": "pass",
           "console": {"family": "toolworld", "end": "final answer"}, "console_rev": 2}
    out = report._board_rows([old, new])
    assert [r["bench_run_id"] for r in out] == ["b"]
    assert out[0]["rerun"]["reason"] == "empty stop" and out[0]["rerun"]["kept"] == "b"
