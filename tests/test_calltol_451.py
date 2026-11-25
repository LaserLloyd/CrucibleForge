"""PLAN-451-LIMITS step 4b (lead ruling 2026-11-25): a tool call written mid-line (``…}CALL x {…}`` or
``…the room.CALL x {…}``; a raw stream capture showed two hosted models really write it so) is read as its own line
only when the tool is one the task offered and its JSON object parses. The frozen parser and its stamps are untouched;
an old tool row holding such a call is owed again by a plain resume."""
from __future__ import annotations

import pytest

from crucibleforge import attempts45, calltol, runner
from crucibleforge.api import ChatResult
from crucibleforge.gen import toolworld as tw

TOOLS = frozenset(("find_person", "get_person", "search_rooms"))


def test_a_glued_call_to_an_offered_tool_with_valid_json_is_split():
    s, n = calltol.split('CALL find_person {"name": "A"}CALL find_person {"name": "B"}', TOOLS)
    assert n == 1 and s == 'CALL find_person {"name": "A"}\nCALL find_person {"name": "B"}'
    s, n = calltol.split('We need his busy times.CALL get_person {"person_id": "P-1"}', TOOLS)
    assert n == 1 and [c[0] for c in tw.parse_calls(s)] == ["get_person"]


@pytest.mark.parametrize("text", [
    'done.CALL launch_rocket {"x": 1}',                 # not an offered tool
    'done.CALL get_person {"person_id": "P-1"',         # JSON does not parse
    'I will CALL get_person next, then book.',          # prose that mentions CALL
    'done.CALL get_person ["P-1"]',                     # not an object
])
def test_anything_else_is_left_as_written(text):
    assert calltol.split(text, TOOLS) == (text, 0)


# review 3 (2026-11-25): its adversarial replies (/var/tmp review scripts, call_adv.py and the stored-row contexts)
@pytest.mark.parametrize("text", [
    'Done. Earlier I ran CALL get_person {"person_id": "P-1"} and it worked.',     # narrated, after a letter
    'You could use `CALL get_person {"person_id": "P-1"}` for that, but I will not.',  # backticked example
    'I should not CALL get_person {"person_id": "P-1"} because nobody asked.',      # refused
    '```\nprint("x")  # CALL get_person {"person_id": "P-1"}\n```',               # inside a code fence
    '```python\nx = 1.CALL get_person {"person_id": "P-1"}\n```',                # fenced, even after a period
    'He said "ok.CALL get_person {"person_id": "P-1"}" twice.',                      # inside a quoted span
    'x CALL find_person {"name": {"first": "A"}}',                                  # after a letter
    '<|tool_call>CALL get_person {"person_id": "P-1"}',                             # leaked template token
    '</mm:think>CALL get_person {"person_id": "P-1"}',                               # a thinking join
    'He typed "CALL get_person {"person_id": "P-1"}',                               # after a quote
    'Turn 1: CALL find_person {"name": "A"} then Turn 2.',                           # narrated, after a colon
])
def test_quoted_narrated_refused_and_fenced_calls_are_not_split(text):
    assert calltol.split(text, TOOLS) == (text, 0)


@pytest.mark.parametrize("text", [
    'ok.CALL get_person ({"person_id": "P-1"})',
    'Found him! CALL get_person {"person_id": "P-1"}',
    '(see above) CALL get_person {"person_id": "P-1"}',
    'CALL find_person {"name": "A \\"Al\\" B"} CALL get_person {"person_id": "P-1"}',
])
def test_a_call_after_a_closing_brace_or_sentence_end_is_split(text):
    assert calltol.split(text, TOOLS)[1] == 1


def test_a_line_start_call_goes_to_the_parser_unchanged():
    text = 'CALL find_person {"name": "A"}\n- CALL get_person {"person_id": "P-1"}'
    assert calltol.split(text, TOOLS) == (text, 0)
    assert tw.parse_calls(text) == tw.parse_calls(calltol.split(text, TOOLS)[0])


def test_the_offered_tools_come_from_the_task_text():
    m = tw.materialize({"type": "toolworld", "level": 1, "key": "r1"}, release_seed="4.5.0", case_id="T-run")
    got = calltol.offered_tools(m["system"], "toolworld")
    assert got and got <= frozenset(tw.TOOLS_DOC)
    assert calltol.offered_tools("no list here", "toolworld") == frozenset(tw.TOOLS_DOC)


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(runner, "row_budget", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_prompt_tokens", lambda m: 10)
    g = {"type": "toolworld", "level": 3, "key": "r1"}                # level 3: a turn makes two calls
    m = tw.materialize(g, release_seed="4.5.0", case_id="T-run")
    c = {"id": "TOOLWORLD-r1-3", "category": "tooluse", "generator": g, **m}
    return c, tw.reference(g, release_seed="4.5.0", case_id="T-run").split("\n<<<TURN>>>\n")


def _ctx(texts):
    it = iter(texts)

    class Ctx:
        def budget(self, k):
            return k * 4

        def recover_for(self, c):
            return False

        def regen_for(self, c):
            return True

        def call(self, messages, **kw):
            t = next(it, "")
            return ChatResult(response_text=t, reasoning_text="", finish_reason="stop", tool_calls=[],
                              prompt_tokens=10, completion_tokens=5, reasoning_tokens=0)
    return Ctx()


def test_the_console_reads_glued_calls_and_keeps_the_model_reply(case):
    c, ref = case
    glued = [t.replace("\nCALL ", "CALL ") for t in ref]
    assert glued != ref                                               # the reference does put several calls in a turn
    plain = runner._run_console_loop(c, {"case_id": c["id"]}, _ctx(ref), 512, None, None)
    row = runner._run_console_loop(c, {"case_id": c["id"]}, _ctx(glued), 512, None, None)
    assert row["score"] == plain["score"] and row["grade"] == plain["grade"]
    assert row["console"]["calls_without_line_break"] >= 1 and row["calltol"] == calltol.REV
    stored = [m["content"] for m in row["tool_conversation"] if m["role"] == "assistant"]
    assert stored == glued[:len(stored)]                              # the model's own replies are stored
    assert not attempts45.superseded(row)


def test_an_old_row_with_a_glued_call_is_owed_and_still_scores(case):
    c, ref = case
    row = runner._run_console_loop(c, {"case_id": c["id"]}, _ctx(ref), 512, None, None)
    old = {**row, "tool_conversation": [dict(m) for m in row["tool_conversation"]]}
    old.pop("calltol"), old.pop("console_rev")
    assert attempts45.superseded(old) is None                         # nothing glued: kept
    k = next(i for i, m in enumerate(old["tool_conversation"]) if m["role"] == "assistant")
    old["tool_conversation"][k]["content"] = "Looking up.CALL find_person {\"name\": \"A\"}"
    assert attempts45.superseded(old) == "tool tolerance"
    assert not attempts45.is_infra(old)
