"""Tool-use revision (lead 2026-11-15 08:27): one revision for two fixes, so tool rows are re-made once.

1. A tool call at a prose step is fed back to the model with TOOLS_UNAVAILABLE for EVERY provider and the rig,
   regardless of reasoning_replay (before: only reasoning-replay providers were told the call was refused; the
   others saw an empty assistant turn). The step still fails. It changes what is sent, so on the 4.5 profile
   (top-level ``tool_call_feedback: all``) a stored tool-use row without the stamp reads STALE, never regraded.
   4.1 (profile bench) is untouched. LAG1/LAG2 run in the code-agent loop, which has no prose steps: unchanged.
2. The "task done right" half (part unit score ``half_task``): an EXTRA read-only lookup beside a correct step
   does not dock the pass. Still a fail: a write call that was not due, a wrong argument on a due call, a prose
   step where the model called a tool and wrote NO text (the user got no answer), a wrong or missing answer.
   Each tool's effect comes from the case: ``meta.tool_effects`` {tool: read|write} (``meta`` is outside every
   content and grading hash, so no 4.1 stamp moves). An undeclared tool counts as write. The step-share half is
   unchanged."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from crucibleforge import profiles, report, runner, tooltask, version
from crucibleforge.api import ChatResult

FIX = Path(__file__).parent / "fixtures" / "tool_rows_d2_20261115.json"
TZ = ("TZ16-pickup-dst-delay", "TZ19-hotel-cot-deadline", "TZ20-swim-saturday-age-day")


def _tool_cases():
    cs = json.loads((Path(__file__).parents[1] / "cases" / "tooluse.json").read_text(encoding="utf-8"))
    cs = cs if isinstance(cs, list) else cs["cases"]
    return {c["id"]: c for c in cs}


# ---------------------------------------------------------------- effects in the cases

def test_every_tool_of_the_c5_cases_declares_read_or_write():
    cs = _tool_cases()
    for cid in TZ:
        assert tooltask.effects_problems(cs[cid]) == [], cid
        eff = tooltask.tool_effects(cs[cid])
        assert set(eff) == {t["function"]["name"] for t in cs[cid]["tools"]}
    eff = {k: v for cid in TZ for k, v in tooltask.tool_effects(cs[cid]).items()}
    assert {k for k, v in eff.items() if v == "read"} == {"get_flight", "search_rooms", "get_booking",
                                                         "list_classes"}


def test_the_effect_markers_move_no_stamp():
    # the stamps the stored 4.1 and 4.5 rows carry for these cases (D2's rows, 2026-11-15)
    rows = json.loads(FIX.read_text(encoding="utf-8"))
    stored = {r["case_id"]: r["case_revision"] for r in rows}
    for cid in TZ:
        assert version.case_hash(cid) == stored[cid], cid


def test_a_missing_or_bad_effect_is_reported():
    c = {"id": "X", "tools": [{"type": "function", "function": {"name": "a"}},
                              {"type": "function", "function": {"name": "b"}}],
         "meta": {"tool_effects": {"a": "read", "b": "delete"}}}
    probs = tooltask.effects_problems(c)
    assert any("b" in p for p in probs)
    c["meta"]["tool_effects"] = {"a": "read"}
    assert any("b" in p for p in tooltask.effects_problems(c))
    assert tooltask.tool_effects(c)["b"] == "write"          # undeclared counts as write


# ---------------------------------------------------------------- the generation change

class _Ctx:
    def __init__(self, replies, replay=False):
        self.replies = list(replies)
        self.sent = []
        self.reasoning_replay = replay
        self.ctx_len = None

    def recover_for(self, case):
        return False

    def budget(self, n):
        return n

    def call(self, messages, **kw):
        self.sent.append([dict(m) for m in messages])
        return self.replies.pop(0)


def _case():
    return {"id": "T1", "category": "tooluse", "max_tokens": 64, "system": "s",
            "tools": [{"type": "function", "function": {"name": "book", "parameters": {"type": "object"}}},
                      {"type": "function", "function": {"name": "look", "parameters": {"type": "object"}}}],
            "meta": {"tool_effects": {"book": "write", "look": "read"}},
            "tool_script": [
                {"user": "book it", "expect_tool": "book", "tool_result": "{\"ok\": true}"},
                {"user": "and Joao too?", "expect_no_tool": True, "answer_contains": ["phone"]},
                {"user": "thanks", "expect_no_tool": True, "answer_contains": ["welcome"]},
            ]}


def _replies():
    return [ChatResult(response_text="", tool_calls=[{"id": "c1", "name": "book", "arguments": "{}"}],
                       finish_reason="tool_calls", served_model="m"),
            ChatResult(response_text="", tool_calls=[{"id": "c2", "name": "book", "arguments": "{\"n\": \"J\"}"}],
                       finish_reason="tool_calls", served_model="m"),
            ChatResult(response_text="You are welcome.", finish_reason="stop", served_model="m")]


@pytest.mark.parametrize("replay", [False, True])
def test_under_the_revision_a_prose_step_call_is_fed_back_for_every_provider(replay):
    ctx = _Ctx(_replies(), replay=replay)
    row = runner._run_tool_loop(_case(), {"profile": "bench45"}, ctx, 64, 0.0, 42)
    last = ctx.sent[-1]
    asst = [m for m in last if m["role"] == "assistant"]
    assert asst[1].get("tool_calls"), "the call at the prose step is on the turn sent back"
    tool_msgs = [m for m in last if m["role"] == "tool"]
    assert tool_msgs[-1]["content"] == runner.TOOLS_UNAVAILABLE
    assert [e["pass"] for e in row["elements"]["results"]] == [True, False, True]   # the step still fails
    assert row["tool_call_feedback"] == "all"
    els = row["elements"]["results"]
    assert els[1]["calls"] == [{"name": "book", "arguments": "{\"n\": \"J\"}"}] and els[1]["said"] is False
    assert els[2]["calls"] == [] and els[2]["said"] is True
    assert els[0]["calls"] == [{"name": "book", "arguments": "{}"}]


def test_outside_the_revision_nothing_changes():
    """4.1 (profile bench): a non-replay provider's call is NOT fed back, as before, no stamp, no new keys."""
    ctx = _Ctx(_replies(), replay=False)
    row = runner._run_tool_loop(_case(), {"profile": "bench"}, ctx, 64, 0.0, 42)
    last = ctx.sent[-1]
    assert not any(m["role"] == "tool" and m["content"] == runner.TOOLS_UNAVAILABLE for m in last)
    assert "tool_call_feedback" not in row
    assert all("said" not in e for e in row["elements"]["results"])
    assert row["tool_conversation"][5].get("tool_calls_not_sent")    # the old record, unchanged


def test_the_4_5_profile_declares_the_revision_and_4_1_does_not():
    assert profiles.profile_switch("bench45", "tool_call_feedback") == "all"
    assert profiles.profile_switch("bench", "tool_call_feedback") is None


def test_an_old_4_5_tool_row_reads_stale_and_a_4_1_row_does_not():
    rows = json.loads(FIX.read_text(encoding="utf-8"))
    r45 = next(r for r in rows if r["case_id"] == TZ[0])
    assert r45["profile"] == "bench45"
    # the revision's own rule, read directly (row_current's other inputs, the case stamp and regime, are
    # process-wide caches an earlier test file can leave patched in a long single-process run)
    assert version.tool_revision_current(r45) is False             # made before the revision: stale
    assert version.tool_revision_current({**r45, "tool_call_feedback": "all"}) is True
    assert version.tool_revision_current({**r45, "profile": "bench"}) is True   # 4.1: unchanged
    assert version.row_current(r45) is False                       # and the currency rule applies it


# ---------------------------------------------------------------- the task-done rule

def _row(steps, conv_turns, grade="fail"):
    """A stored-shape row: ``steps`` = element pass flags; ``conv_turns`` = per step (calls, text)."""
    conv = [{"role": "system", "content": "s"}]
    for k, (calls, text) in enumerate(conv_turns):
        conv.append({"role": "user", "content": f"u{k}"})
        m = {"role": "assistant", "content": text}
        if calls:
            m["tool_calls"] = [{"id": f"c{k}{j}", "type": "function",
                                "function": {"name": n, "arguments": a}} for j, (n, a) in enumerate(calls)]
        conv.append(m)
    els = [{"id": f"step{k + 1}", "type": "x", "pass": p, "detail": ""} for k, p in enumerate(steps)]
    return {"grade": grade, "elements": {"results": els, "rate": sum(steps) / len(steps)},
            "tool_conversation": conv}


def _tcase():
    return {"id": "T", "tools": [{"type": "function", "function": {"name": n}} for n in ("look", "book")],
            "meta": {"tool_effects": {"look": "read", "book": "write"}},
            "tool_script": [
                {"user": "u0", "expect_tool": "book", "required_args": {"who": ["ana"]}, "tool_result": "{}"},
                {"user": "u1", "expect_no_tool": True, "answer_contains": ["cannot"]}]}


def test_an_extra_lookup_beside_the_right_call_is_done():
    r = _row([False, True], [([("look", "{}"), ("book", '{"who": "Ana"}')], ""), ([], "I cannot.")])
    assert tooltask.task_done(r, _tcase()) is True


def test_a_wrong_argument_or_an_extra_write_is_not_done():
    wrong = _row([False, True], [([("book", '{"who": "Bob"}')], ""), ([], "I cannot.")])
    extra = _row([False, True], [([("book", '{"who": "Ana"}'), ("book", '{"who": "Ana"}')], ""), ([], "I cannot.")])
    assert tooltask.task_done(wrong, _tcase()) is False
    assert tooltask.task_done(extra, _tcase()) is False


def test_a_lookup_at_a_prose_step_is_done_only_with_the_right_text():
    ok = _row([True, False], [([("book", '{"who": "Ana"}')], ""), ([("look", "{}")], "Sorry, I cannot do that.")])
    silent = _row([True, False], [([("book", '{"who": "Ana"}')], ""), ([("look", "{}")], "")])
    wrong = _row([True, False], [([("book", '{"who": "Ana"}')], ""), ([("look", "{}")], "Done!")])
    write = _row([True, False], [([("book", '{"who": "Ana"}')], ""), ([("book", "{}")], "I cannot.")])
    assert tooltask.task_done(ok, _tcase()) is True
    assert tooltask.task_done(silent, _tcase()) is False
    assert tooltask.task_done(wrong, _tcase()) is False
    assert tooltask.task_done(write, _tcase()) is False


def test_a_new_row_is_read_from_its_elements_and_a_capped_row_is_never_done():
    r = _row([True, False], [([("book", '{"who": "Ana"}')], ""), ([], "")])
    r["tool_conversation"] = []                                   # the new keys alone decide
    r["elements"]["results"][0].update(calls=[{"name": "book", "arguments": '{"who": "Ana"}'}], said=False)
    r["elements"]["results"][1].update(calls=[{"name": "look", "arguments": "{}"}], said=True)
    r["elements"]["results"][1]["text"] = "I cannot."
    assert tooltask.task_done(r, _tcase()) is True
    assert tooltask.task_done({**r, "end_reason": "context cap"}, _tcase()) is False
    assert tooltask.task_done({**r, "grade": "pass"}, _tcase()) is True


def test_an_unreadable_row_falls_back_to_its_grade():
    r = _row([True, False], [([("book", '{"who": "Ana"}')], "")])     # one turn short
    assert tooltask.task_done(r, _tcase()) is False
    assert tooltask.task_done({**r, "grade": "pass"}, _tcase()) is True


# ---------------------------------------------------------------- the 18 stored rows (the table)

#: (model, case, repeat) -> (stored grade, task done under the new rule)
EXPECTED = {
    ("deepseek-flash", "TZ16", 1): ("pass", True), ("deepseek-flash", "TZ16", 2): ("pass", True),
    ("deepseek-flash", "TZ19", 1): ("pass", True), ("deepseek-flash", "TZ19", 2): ("pass", True),
    ("deepseek-flash", "TZ20", 1): ("pass", True), ("deepseek-flash", "TZ20", 2): ("pass", True),
    ("grok-4-7", "TZ16", 1): ("fail", False), ("grok-4-7", "TZ16", 2): ("fail", False),
    ("grok-4-7", "TZ19", 1): ("fail", False), ("grok-4-7", "TZ19", 2): ("fail", False),
    ("grok-4-7", "TZ20", 1): ("fail", False), ("grok-4-7", "TZ20", 2): ("fail", False),
    ("minimax-m3-1-flash", "TZ16", 1): ("pass", True), ("minimax-m3-1-flash", "TZ16", 2): ("fail", False),
    ("minimax-m3-1-flash", "TZ19", 1): ("fail", False), ("minimax-m3-1-flash", "TZ19", 2): ("fail", False),
    ("minimax-m3-1-flash", "TZ20", 1): ("fail", False), ("minimax-m3-1-flash", "TZ20", 2): ("pass", True),
}


def test_the_18_stored_rows_under_the_new_rule():
    cs = _tool_cases()
    got = {}
    for r in json.loads(FIX.read_text(encoding="utf-8")):
        k = (r["model_label"], r["case_id"][:4], r["repeat"])
        got[k] = (r["grade"], tooltask.task_done(r, cs[r["case_id"]]))
    assert got == EXPECTED


# ---------------------------------------------------------------- the report rule

def test_half_task_is_a_tool_use_unit_score_and_c5_uses_it():
    assert "half_task" in report.UNIT_SCORES
    assert report.part_unit_score({"key": "c5", "scoring": ["tooluse"], "unit_score": "half_task"}) == "half_task"
    prof = profiles.load_profile("bench45")
    parts = [p for comp in [prof] + list((prof.get("compositions") or {}).values())
             for sec in ((comp.get("scores") or {}).values()) for k, p in ((sec or {}).get("parts") or {}).items()
             if k == "c5_tools" and isinstance(p, dict) and p.get("unit_score")]
    assert parts and all(p["unit_score"] == "half_task" for p in parts)
    assert all("read-only" in p["note"] for p in parts)


def test_half_task_scores_the_task_half_from_task_done():
    cs = _tool_cases()
    rows = [copy.deepcopy(r) for r in json.loads(FIX.read_text(encoding="utf-8")) if r["model_label"] == "minimax-m3-1-flash"]
    tr = report.task_rate(rows, cs)
    assert tr == pytest.approx(2 / 6)
