"""gemma4's thinking-channel dialect is stripped/split at the API boundary.

2026-10-24: gemma4 models benched without ``reasoning_format`` leak their
thinking as literal ``<|channel>thought ... <channel|>`` text in ``content``
(rig capture, Omega-Arsenal-31B on llama.cpp b11718). ``enable_thinking:
false`` — the overflow-recovery rung — still emits an EMPTY channel
``<|channel>thought\\n<channel|>`` before the answer. Either way the tags must
never reach a grader, and leaked thinking must land in ``reasoning_text``.
"""
from __future__ import annotations

from crucibleforge.api import split_thinking, strip_thinking_tags

LEAK = ("<|channel>thought\nThe user wants 17*23+4.\n    *   391 + 4 = 395"
        "<channel|>17 * 23 = 391\n391 + 4 = 395\n\n395")
EMPTY_CHANNEL = "<|channel>thought\n<channel|>To work this out:\n\n395"
TRUNCATED = "<|channel>thought\nThe objective is to calculate $17 \\times 23 + 4$.\n    *   Step 1"


def test_a_closed_channel_is_split_into_reasoning():
    visible, think = split_thinking(LEAK)
    assert visible == "17 * 23 = 391\n391 + 4 = 395\n\n395"
    assert think.startswith("The user wants 17*23+4.")
    assert "<|channel>" not in think and "<channel|>" not in think


def test_the_empty_channel_from_enable_thinking_false_is_dropped():
    assert strip_thinking_tags(EMPTY_CHANNEL) == "To work this out:\n\n395"
    visible, think = split_thinking(EMPTY_CHANNEL)
    assert visible == "To work this out:\n\n395" and think == ""


def test_an_unclosed_channel_is_reasoning_never_an_answer():
    assert strip_thinking_tags(TRUNCATED) == ""
    visible, think = split_thinking(TRUNCATED)
    assert visible == ""
    assert think.startswith("The objective is")


def test_a_stray_close_tag_is_dropped():
    assert strip_thinking_tags("<channel|>395") == "395"


def test_strip_is_idempotent():
    once = strip_thinking_tags(LEAK)
    assert strip_thinking_tags(once) == once == "17 * 23 = 391\n391 + 4 = 395\n\n395"


def test_existing_dialects_unchanged():
    assert strip_thinking_tags("<think>x</think>ok") == "ok"
    assert strip_thinking_tags("(think)x(think)ok") == "ok"
    assert strip_thinking_tags("<|thinking|>x<|/thinking|>ok") == "ok"
