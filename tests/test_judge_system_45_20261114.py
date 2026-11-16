"""judge.judge_system (2026-11-14): a row whose case declares ``element_standard: 45`` is judged under
JUDGE_SYSTEM_45 (scope clause in docs/CONTENT-RULES.md's words); every other row keeps JUDGE_SYSTEM byte for
byte. Element-only calls carry it too. bench45's element verdict cap is 800 tokens."""
import json

import pytest

from crucibleforge import judge as J
from crucibleforge import judge_elements as JE
from crucibleforge.api import ChatResult

RULES_LIST = ("explicit sex, dark and non-consensual fantasy, mind control, violence, gore, profanity, "
              "drugs and their effects")


def _row(std=None, **kw):
    r = {"rubric": "nsfw_craft", "case_id": "T-sys45-001", "model_label": "m", "prompt": "Write the scene.",
         "response": "She opened the door.", **kw}
    if std is not None:
        r["element_standard"] = std
    return r


def test_wording_quotes_content_rules_and_keeps_adults():
    assert RULES_LIST in J.JUDGE_SYSTEM_45 and "full intensity" in J.JUDGE_SYSTEM_45
    assert "adult fiction" in J.JUDGE_SYSTEM_45 and "consenting" not in J.JUDGE_SYSTEM_45
    # everything outside the scope clause is the 4.1 prompt
    assert J.JUDGE_SYSTEM_45.replace(J.JUDGE_SYSTEM_SCOPE_45, J.JUDGE_SYSTEM_SCOPE_41) == J.JUDGE_SYSTEM
    assert "harmful-request cases" in J.JUDGE_SYSTEM_45


@pytest.mark.parametrize("order", [(45, None, 41), (None, 45, 41), (41, 45, None)])
def test_one_process_each_row_reads_its_own_system_prompt(order):
    before = J.JUDGE_SYSTEM
    for std in order:
        want = J.JUDGE_SYSTEM_45 if std == 45 else J.JUDGE_SYSTEM
        assert J.judge_system(_row(std)) == want
    assert J.judge_system(None) == J.JUDGE_SYSTEM and J.JUDGE_SYSTEM == before


class _CaptureJC:
    model_id = "fake-judge"
    thinking = False
    context_length = 16384
    max_tokens = 1536
    extra_body = None
    row_timeout_s = None
    concurrency = 1

    def __init__(self, text):
        self.text, self.systems, self.max_tokens_seen = text, [], []

    def chat(self, messages, **kw):
        self.systems.append(messages[0]["content"])
        self.max_tokens_seen.append(kw.get("max_tokens"))
        return ChatResult(response_text=self.text, finish_reason="stop", prompt_tokens=300,
                          completion_tokens=40, total_s=0.1)


def _el_row(std):
    els = [{"id": "e1", "q": "Does the door open?", "class": "fact"}]
    return {**_row(std), "rubric": "nsfw", "judge_elements": els, "judge_willingness": False}


@pytest.mark.parametrize("first", [45, 41])
def test_element_only_calls_carry_the_rows_system_prompt(first):
    raw = json.dumps({"note": "", "elements": {"e1": {"pass": True, "quote": "She opened the door."}}})
    jc = _CaptureJC(raw)
    second = 41 if first == 45 else 45
    for std in (first, second):
        JE.judge_elements_row(jc, _el_row(std), {**J.JUDGE_POLICY_DEFAULTS, "mode": "elements", "max_tokens": 800})
    want = {45: J.JUDGE_SYSTEM_45, 41: J.JUDGE_SYSTEM}
    assert jc.systems == [want[first], want[second]]
    assert set(jc.max_tokens_seen) == {800}


def test_bench45_element_cap_is_800():
    from crucibleforge import profiles
    pol = profiles.load_profile("bench45")["judge_policy"]
    assert pol["mode"] == "elements" and pol["elements_max_tokens"] == 800
