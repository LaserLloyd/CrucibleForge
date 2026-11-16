"""H13 (verifier escape: a character declining in a NON-explicit window is the
scene stopping) and H14 (judge finish=length: output cap + split of big
groups). Synthetic neutral fixtures only."""
from __future__ import annotations

import json
import re

import pytest

from crucibleforge import judge
from tests.test_harness_w_rule_20261108 import CASE, StubJC, _R, _row, _run  # noqa: F401
from tests.test_harness_w_rule_20261108 import _case  # noqa: F401  (autouse fixture)


class HeatJC(StubJC):
    """Stub whose heat-element answer per turn can be 'no'."""
    def __init__(self, *a, heat_no=(), **kw):
        super().__init__(*a, **kw)
        self.heat_no = set(heat_no)

    def chat(self, messages, **kw):
        r = super().chat(messages, **kw)
        prompt = messages[1]["content"]
        m = re.search(r"\(live turn (\d+)\)", prompt)
        if m and int(m.group(1)) in self.heat_no and "declining/fading" not in prompt:
            d = json.loads(r.response_text)
            d["elements"] = {k: {"pass": False, "quote": ""} for k in d["elements"]}
            return _R(json.dumps(d))
        return r


def test_character_declines_and_window_non_explicit_is_w2():
    jc = HeatJC({1: 4, 2: 4, 3: 4, 4: 4, 5: 2, 6: 4}, verifier="character", heat_no={5})
    w = _run(jc, _row(peak_turn=5, heat=True))["willingness"]
    assert w["w"] == 2 and w["basis"] == "event"
    ev = w["events"][0]
    assert ev["decisive"] is True and ev["verifier"]["verdict"] == "character"
    assert ev["verifier"]["explicit_window"] is False
    assert not any(x.get("verifier_override") for x in w["windows"])


def test_character_declines_and_window_stays_explicit_is_compliant():
    jc = HeatJC({1: 4, 2: 4, 3: 4, 4: 4, 5: 2, 6: 4}, verifier="character")
    w = _run(jc, _row(peak_turn=5, heat=True))["willingness"]
    assert w["w"] == 4 and w["events"][0]["decisive"] is False
    assert w["events"][0]["verifier"]["explicit_window"] is True


def test_window_explicit_rules():
    heat = {"id": "H", "dim": "explicitness", "turns": [1]}
    other = {"id": "O", "turns": [1]}
    res = lambda c: {"elements": {"results": [{"id": "H", "credit": c}]}}  # noqa: E731
    assert judge.window_explicit([heat], res(1.0)) is True
    assert judge.window_explicit([heat], res(0.5)) is True
    assert judge.window_explicit([heat], res(0.0)) is False
    assert judge.window_explicit([{**other, "opportunity": False}], {}) is True
    assert judge.window_explicit([other], {"scores": {"explicitness": 6}}) is True
    assert judge.window_explicit([other], {"scores": {"explicitness": 1}}) is False
    assert judge.window_explicit([other], {}) is False


def test_verifier_prompt_has_the_clarifying_sentence():
    assert "ONLY if the window still carries explicit content" in judge.VERIFY_EVENT_QUESTION


# ---- H14 ------------------------------------------------------------------
class LenJC:
    thinking = False
    context_length = 16384
    max_tokens = 8192
    row_timeout_s = None
    sample_temperature = 0.7
    concurrency = 4
    extra_body = {}

    def __init__(self, big=5):
        self.calls = []
        self.big = big

    def chat(self, messages, **kw):
        prompt = messages[1]["content"]
        ids = re.findall(r"^- ([A-Za-z0-9][\w-]*): ", prompt, flags=re.M)
        self.calls.append((len(ids), kw.get("max_tokens")))
        if len(ids) > 4:
            r = _R("{\"elements\": {\"E1-")
            r.finish_reason = "length"
            return r
        return _R(json.dumps({"refused": False, "in_character": True, "note": "",
                              "elements": {i: {"pass": True, "quote": "Reply number 1 goes here in full."}
                                           for i in ids}}))


@pytest.fixture
def no_w():
    CASE["judge_willingness"] = False


def _sub(n):
    row = _row(n=1, judge_willingness=False)
    els = [{"id": f"E{i}-x", "q": "x?", "turns": [1]} for i in range(1, n + 1)]
    return judge.erp_group_row(row, ("t", (1,)), els)


def test_big_group_that_ends_length_is_split_into_halves(no_w):
    import threading
    jc = LenJC()
    out = judge._deep_call(jc, _sub(8), 1, threading.Semaphore(4), None)
    assert out["judge_failed"] is False and out["split"]["halves"] == [
        [f"E{i}-x" for i in range(1, 5)], [f"E{i}-x" for i in range(5, 9)]]
    assert [r["id"] for r in out["elements"]["results"]] == [f"E{i}-x" for i in range(1, 9)]
    # every call is capped at the v2 group cap, halves ask for 4 elements each
    assert all(mt is not None and mt <= 4096 for _n, mt in jc.calls)
    assert sorted({n for n, _ in jc.calls}) == [4, 8]


def test_group_cap_is_configurable_and_half_failing_fails_the_group(no_w):
    import threading
    jc = LenJC()
    jc.v2_group_max_tokens = 1000
    jc.chat_orig = jc.chat
    out = judge._deep_call(jc, _sub(8), 1, threading.Semaphore(4), None)
    assert all(mt <= 1000 for _n, mt in jc.calls)

    class AlwaysLen(LenJC):
        def chat(self, messages, **kw):
            r = _R("{\"elements\"")
            r.finish_reason = "length"
            return r
    out = judge._deep_call(AlwaysLen(), _sub(8), 1, threading.Semaphore(4), None)
    assert out["judge_failed"] is True and out["split"]["halves"]


def test_small_group_is_not_split_and_group_meta_logs_split_from(no_w):
    import threading
    jc = LenJC()
    out = judge._deep_call(jc, _sub(1), 1, threading.Semaphore(4), None)
    assert "split" not in out


def test_erp_row_logs_split_from_in_the_judge_block(no_w):
    row = _row(n=1)
    row["judge_willingness"] = False
    row["judge_elements"] = [{"id": f"E{i}-x", "q": "x?", "turns": [1]} for i in range(1, 9)]
    out = judge.judge_erp_deep_row(LenJC(), row, samples=1)
    g = out["groups"][0]
    assert g["split_from"] == [f"E{i}-x" for i in range(1, 9)] and len(g["split_halves"]) == 2
    assert out["judge_failed"] is False
