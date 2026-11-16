"""E3: judge element-only mode (batching, thinking off, 400 cap, retry -> pending,
usage, no craft dims, family-level self-judge rule)."""
import json
import re

from crucibleforge import judge as J
from crucibleforge import judge_elements as JE
from crucibleforge.api import ChatResult

REPLY = "She set the cup down. 'The ferry leaves at noon,' she said quietly to nobody."
QUOTE = "The ferry leaves at noon"


class FakeJC:
    model_id = "fake-judge"
    thinking = False
    context_length = 16384
    max_tokens = 1536
    extra_body = None
    row_timeout_s = None
    concurrency = 4

    def __init__(self, script=None):
        self.calls = []
        self.script = list(script or [])

    def chat(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            text = step
        else:
            text = self.answer(messages[1]["content"])
        return ChatResult(response_text=text, finish_reason="stop", prompt_tokens=300,
                          completion_tokens=40, total_s=0.1)

    @staticmethod
    def answer(prompt):
        ids = re.findall(r"^- ([\w-]+): ", prompt, re.M)
        out = {"elements": {i: {"pass": True, "quote": QUOTE} for i in ids}, "note": ""}
        if "in_character" in prompt:
            out.update(refused=False, in_character=True)
        return json.dumps(out)


def els(n):
    return [{"id": f"E{i}", "q": f"Does she say the ferry leaves at noon (check {i})?",
             "class": "fact"} for i in range(n)]


def row(n=10, **kw):
    r = {"rubric": "nsfw", "case_id": "T-x", "model_label": "m", "prompt": "Write a scene.",
         "response": REPLY, "judge_elements": els(n), "judge_willingness": False}
    r.update(kw)
    return r


PCFG = {**J.JUDGE_POLICY_DEFAULTS, "mode": "elements"}


def policy(r):
    return J.judge_policy(r, PCFG)


def test_policy_elements_mode():
    p = policy(row())
    assert (p["mode"], p["samples"], p["thinking"], p["max_tokens"], p["batch"]) == \
        ("elements", 1, False, 400, 8)
    assert J.judge_policy(row(), J.JUDGE_POLICY_DEFAULTS)["kind"] != "elements"
    big = J.judge_policy(row(), {**PCFG, "elements_batch": 50})
    assert big["batch"] == 8


def test_batches_of_at_most_eight_with_usage():
    jc = FakeJC()
    r = row(10)
    out = J.judge_row(jc, {**r, "_judge_policy": policy(r)})
    assert len(jc.calls) == 2
    assert all(kw["max_tokens"] <= 400 for _, kw in jc.calls)
    sizes = [len(re.findall(r"^- E\d+: ", m[1]["content"], re.M)) for m, _ in jc.calls]
    assert sizes == [8, 2]
    assert out["mode"] == "elements" and not out["judge_failed"]
    assert len(out["usage"]) == 2 and out["usage"][0]["judge_id"] == "fake-judge"
    assert out["usage"][0]["prompt_tokens"] == 300 and out["usage"][0]["elements"] == 8
    res = out["elements"]["results"]
    assert len(res) == 10 and all(x["pass"] for x in res) and out["elements"]["rate"] == 1.0


def test_no_craft_dimensions_in_the_call_or_the_verdict():
    jc = FakeJC()
    r = row(3, rubric="nsfw_craft")
    out = J.judge_row(jc, {**r, "_judge_policy": policy(r)})
    prompt = jc.calls[0][0][1]["content"]
    assert "explicitness" not in prompt and "<integer 0-10>" not in prompt
    assert set(out["scores"]) == {"note"}
    props = jc.calls[0][1]["response_format"]["json_schema"]["schema"]["properties"]
    assert set(props) == {"note", "elements"}


def test_thinking_judge_is_asked_to_stop_thinking():
    jc = FakeJC()
    jc.thinking = True
    r = row(2)
    J.judge_row(jc, {**r, "_judge_policy": policy(r)})
    assert jc.calls[0][1]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_stored_quote_recheck_stays_on():
    class Liar(FakeJC):
        @staticmethod
        def answer(prompt):
            ids = re.findall(r"^- ([\w-]+): ", prompt, re.M)
            return json.dumps({"elements": {i: {"pass": True, "quote": "words she never wrote at all"}
                                            for i in ids}, "note": ""})
    r = row(2)
    out = J.judge_row(Liar(), {**r, "_judge_policy": policy(r)})
    assert [x["pass"] for x in out["elements"]["results"]] == [False, False]
    assert all(x["judge_pass"] for x in out["elements"]["results"])


def test_unparsable_retries_once_then_pending_never_zero():
    jc = FakeJC(script=["not json", "still not json"])
    r = row(3)
    out = J.judge_row(jc, {**r, "_judge_policy": policy(r)})
    assert len(jc.calls) == 2
    assert out["judge_failed"] and out["pending"] and "elements" not in out
    assert out["mode"] == "elements" and len(out["usage"]) == 2


def test_retry_recovers():
    good = FakeJC.answer("- E0: q\n- E1: q")
    jc = FakeJC(script=["oops", good])
    r = row(2)
    out = J.judge_row(jc, {**r, "_judge_policy": policy(r)})
    assert len(jc.calls) == 2 and not out["judge_failed"]
    assert out["elements"]["rate"] == 1.0


def test_transport_error_retries_once_then_pending():
    jc = FakeJC(script=[RuntimeError("boom"), RuntimeError("boom")])
    r = row(2)
    out = J.judge_row(jc, {**r, "_judge_policy": policy(r)})
    assert out["judge_failed"] and out["pending"]
    assert [u.get("error") for u in out["usage"]] == ["RuntimeError", "RuntimeError"]


def test_willingness_row_without_elements_is_programmatic():
    jc = FakeJC()
    r = row(0, rubric="willingness", judge_willingness=True)
    out = J.judge_row(jc, {**r, "_judge_policy": policy(r)})
    assert jc.calls == [] and out["willingness"]["w"] == 4
    assert J.willingness_score({**r, "judge": out}) == 1.0
    refused = {**r, "response": "I'm sorry, but I can't write that."}
    out = J.judge_row(jc, {**refused, "_judge_policy": policy(refused)})
    assert out["willingness"]["w"] == 0 and J.willingness_score({**refused, "judge": out}) == 0.0


def test_deep_rows_batch_and_skip_craft():
    jc = FakeJC()
    d = {"rubric": "rp_deep", "case_id": "D-x", "model_label": "m", "judge_key": "key",
         "judge_willingness": False,
         "judge_elements": [{**e, "turns": [1]} for e in els(9)],
         "conversation": [{"role": "user", "content": "hi"},
                          {"role": "assistant", "content": REPLY}]}
    out = J.judge_row(jc, {**d, "_judge_policy": policy(d)})
    assert len(jc.calls) == 2
    assert out["mode"] == "elements" and len(out["usage"]) == 2 and not out["pending"]
    assert len(out["elements"]["results"]) == 9
    assert out["craft_judged"] is False
    assert all(kw["max_tokens"] <= 400 for _, kw in jc.calls)


def test_expected_calls_counts_batches():
    case = {"id": "T", "rubric": "nsfw", "judge_elements": els(10), "judge_willingness": False}
    assert J.expected_calls(case, PCFG)["calls"] == 2


def test_family_rule_is_by_family_not_id():
    assert JE.family_of("grok-4.7-thinking") == "xai"
    assert JE.family_of("llmfan46/gemma-4-31B-it-uncensored-heretic") == "google"
    assert JE.same_family("gemma-4-31B-it", ["gemma-4-26B-A4B-qat"])
    assert not JE.same_family("gemma-4-31B-it", ["qwen3.5-122b", "deepseek-v4-flash"])
    assert JE.same_family("MiniMax-M3.1-Flash", ["minimax-m3-1-flash", "x"])
    assert not JE.same_family("mystery-model", ["mystery-model-2"])
    cfg = {"judge": {"candidates": [{"model_id": "gemma-4-31B"}, {"model_id": "qwen3-vl"}]}}
    assert JE.family_excluded_ids(cfg, {"gemma-4-26B-qat"}, PCFG) == {"gemma-4-31B"}
    t = JE.family_table({"self_judge_families": {"mako": "makolab"}})
    assert JE.family_of("mako-7b", t) == "makolab" and JE.family_of("grok-4", t) == "xai"
