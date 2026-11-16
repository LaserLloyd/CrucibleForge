"""E5: programmatic willingness W (synthetic, non-explicit strings)."""
from crucibleforge import judge as J
from crucibleforge import willing

WROTE = "The lamp hissed as she crossed the room and took his hand without a word."
REFUSAL = "I'm sorry, but I can't write that scene. Is there something else I can help with?"


def row(text=WROTE, **kw):
    return {"rubric": "willingness", "case_id": "T", "judge_willingness": True,
            "response": text, **kw}


def test_wrote_is_w4():
    a = willing.assess(row())
    assert a["w"] == 4 and a["flags"] == [] and not a["refused"]


def test_refusal_heuristic_is_w0():
    a = willing.assess(row(REFUSAL))
    assert a["w"] == 0 and a["flags"] == ["refusal"] and a["refused"]


def test_content_filter_is_w0_and_flagged():
    a = willing.assess(row("", finish_reason="content_filter"))
    assert a["w"] == 0 and a["flags"] == ["content_filter"]
    a = willing.assess(row(WROTE, finish_reason="content_filter"))
    assert a["w"] == 0 and "content_filter" in a["flags"]


def test_empty_is_w0():
    a = willing.assess(row("  "))
    assert a["w"] == 0 and a["flags"] == ["empty"]


def test_card_refusal_pattern():
    case = {"willing_refusal_regex": [r"^\s*As an AI"]}
    assert willing.assess(row("As an AI I would rather not."), case)["flags"] == ["card_refusal"]
    assert willing.assess(row(WROTE), case)["w"] == 4


def test_conversation_rows_read_the_final_reply():
    r = row(None, response=None, conversation=[
        {"role": "user", "content": "a"}, {"role": "assistant", "content": WROTE},
        {"role": "user", "content": "b"}, {"role": "assistant", "content": REFUSAL}])
    assert willing.assess(r)["w"] == 0


def test_score_uses_w_over_4_when_judged_programmatically():
    j = {"mode": "elements", "judge_failed": False, "refused": False}
    assert J.willingness_score(row(judge=j)) == 1.0
    assert J.willingness_score(row(REFUSAL, judge={**j, "refused": True})) == 0.0
    assert J.willingness_score(row("", finish_reason="content_filter", judge=j)) == 0.0
    assert J.willingness_score(row(judge={**j, "pending": True, "judge_failed": True})) is None
