"""Suite 3.9 judge block: per-row sample policy (S6), long-input canary,
willingness rubric W 0-4 (S11)."""
import json

import pytest

from crucibleforge import judge
from crucibleforge.api import ChatResult

PCFG = judge.JUDGE_POLICY_DEFAULTS


def row(rubric, cid="X", **kw):
    return {"rubric": rubric, "case_id": cid, "prompt": "p", "response": "text",
            "judge_elements": kw.pop("elements", []), **kw}


def el(dim=None, i="e1"):
    e = {"id": i, "q": "q?"}
    if dim:
        e["dim"] = dim
    return e


# ------------------------------------------------------------ sample policy
def test_craft_only_row_is_one_sample_and_keeps_thinking():
    p = judge.judge_policy(row("rp_single"), PCFG)
    assert (p["kind"], p["samples"]) == ("craft", 1) and "thinking" not in p


def test_dimension_capping_elements_are_still_craft():
    # XP1-style: elements cap the 'constraints' DIMENSION, no binary component
    p = judge.judge_policy(row("nsfw_craft", elements=[el("constraints"), el("explicitness", "e2")]),
                           PCFG)
    assert (p["kind"], p["samples"]) == ("craft", 1)


def test_flag_element_decides_binary_component_keeps_three():
    p = judge.judge_policy(row("steer", elements=[el("obeyed")]), PCFG)
    assert (p["kind"], p["samples"]) == ("binary", 3) and "obeyed" in p["reason"]
    # steer is dimension-less, but an element decides it: NOT thinking-off 1-sample
    assert p["thinking"] is False and p["max_tokens"] == 4096


@pytest.mark.parametrize("tag", [{"willing": "must_write"}, {"explicit_required": True}])
def test_tagged_rows_with_elements_keep_three(tag):
    p = judge.judge_policy(row("nsfw_craft", elements=[el("explicitness")], **tag), PCFG)
    assert (p["kind"], p["samples"]) == ("binary", 3)
    assert next(iter(tag)) in p["reason"]


def test_tagged_row_without_elements_is_craft():
    p = judge.judge_policy(row("nsfw_craft", willing="must_write"), PCFG)
    assert (p["kind"], p["samples"]) == ("craft", 1)


def test_private_elements_keep_three_but_private_without_elements_do_not():
    pe = {"PRIV1"}
    assert judge.judge_policy(row("nsfw_craft", "PRIV1", elements=[el("constraints")]),
                              PCFG, private_ids=pe)["kind"] == "private"
    assert judge.judge_policy(row("nsfw_craft", "PRIV1", elements=[el("constraints")]),
                              PCFG, private_ids=pe)["samples"] == 3
    assert judge.judge_policy(row("nsfw_craft", "PRIV1"), PCFG, private_ids=pe)["samples"] == 1
    assert judge.judge_policy(row("nsfw_craft", "PUB", elements=[el("constraints")]),
                              PCFG, private_ids=pe)["samples"] == 1
    assert judge.judge_policy(row("nsfw_craft", elements=[el("constraints")], private=True),
                              PCFG)["kind"] == "private"


@pytest.mark.parametrize("rubric", ["steer", "safety", "overrefusal", "rp_deep"])
def test_flag_only_rows_are_one_sample_thinking_off_capped(rubric):
    p = judge.judge_policy(row(rubric), PCFG)
    assert (p["kind"], p["samples"]) == ("flag-only", 1)
    assert p["thinking"] is False and p["max_tokens"] <= 4096


def test_reference_rows_keep_thinking():
    p = judge.judge_policy(row("reference"), PCFG)
    assert p["samples"] == 1 and "thinking" not in p and "max_tokens" not in p


def test_willingness_rows_keep_three(monkeypatch):
    p = judge.judge_policy(row("nsfw_craft", judge_willingness=True), PCFG)
    assert (p["kind"], p["samples"]) == ("willingness", 3)
    assert judge.judge_policy(row("willingness"), PCFG)["samples"] == 3


def test_override_lists_and_precedence():
    cfg = {**PCFG, "samples3_rows": ["A"], "samples1_rows": ["A", "B"]}
    assert judge.judge_policy(row("rp_single", "A"), cfg)["samples"] == 3     # 3 wins
    r = judge.judge_policy(row("steer", "B", elements=[el("obeyed")]), cfg)
    assert (r["kind"], r["samples"]) == ("override", 1)


def test_cli_samples_override_count_only():
    p = judge.judge_policy(row("steer", elements=[el("obeyed")]), PCFG, cli_samples=5)
    assert p["samples"] == 5 and p["samples_source"] == "cli" and p["policy_samples"] == 3
    assert p["thinking"] is False                        # per-call overrides stay
    assert judge.judge_policy(row("rp_single"), PCFG, cli_samples=1)["samples"] == 1


def test_policy_can_be_switched_off():
    cfg = {**PCFG, "enabled": False}
    p = judge.judge_policy(row("steer", elements=[el("obeyed")]), cfg)
    assert p["kind"] == "legacy" and p["samples"] == 1
    assert judge.call_overrides("steer", cfg) == {}


def test_config_layers(monkeypatch):
    cfg = {"judge": {"policy": {"binary_samples": 5, "bogus": 1}},
           "judge_policy": {"craft_samples": 2}}
    pc = judge.judge_policy_config(cfg)
    assert pc["binary_samples"] == 5 and pc["craft_samples"] == 2 and "bogus" not in pc
    assert judge.judge_policy_config(None)["flag_only_max_tokens"] == 4096


def test_profile_block_is_valid_and_loadable():
    from crucibleforge import profiles
    prof = profiles.load_profile(profiles.DEFAULT_PROFILE, None)
    block = prof.get("judge_policy")
    assert isinstance(block, dict) and set(block) <= set(judge.JUDGE_POLICY_DEFAULTS)
    assert block["flag_only_max_tokens"] <= 4096


# ------------------------------------------------- judge_row applies the policy
class _R:
    def __init__(self, text):
        self.response_text, self.finish_reason = text, "stop"
        self.completion_tokens, self.reasoning_tokens, self.reasoning_text = 1, 0, ""


class FakeJC:
    def __init__(self, thinking, reply):
        self.thinking, self.reply, self.calls = thinking, reply, []
    context_length, max_tokens, row_timeout_s, extra_body = 16384, 8192, None, {}
    sample_temperature = 0.7
    label = "fake"

    def chat(self, messages, **kw):
        self.calls.append(kw)
        return _R(self.reply)


def _steer_row(**kw):
    return {"rubric": "steer", "case_id": "S", "prompt": "p", "response": "reply text here",
            "system": "s", "judge_elements": [], **kw}


def test_thinking_judge_flag_only_call_is_thinking_off_and_capped():
    jc = FakeJC(True, json.dumps({"obeyed": True, "note": ""}))
    pol = judge.judge_policy(_steer_row(), PCFG)
    out = judge.judge_row(jc, {**_steer_row(), "_judge_policy": pol}, samples=pol["samples"])
    kw = jc.calls[0]
    assert kw["max_tokens"] == 4096
    assert kw["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert "response_format" in kw                     # grammar applies without thinking
    assert out["policy"]["kind"] == "flag-only" and out["scores"]["obeyed"] is True


def test_thinking_judge_without_policy_is_unchanged():
    jc = FakeJC(True, json.dumps({"obeyed": True, "note": ""}))
    judge.judge_row(jc, _steer_row())
    kw = jc.calls[0]
    assert "response_format" not in kw and "max_tokens" not in kw
    assert "enable_thinking" not in str(kw.get("extra_body"))


def test_max_tokens_never_raised_above_the_judges_own():
    jc = FakeJC(False, json.dumps({"obeyed": True, "note": ""}))
    jc.max_tokens = 1536
    pol = judge.judge_policy(_steer_row(), PCFG)
    judge.judge_row(jc, {**_steer_row(), "_judge_policy": pol})
    assert jc.calls[0]["max_tokens"] == 1536


# ------------------------------------------------------------ run_judge wiring
def test_run_judge_applies_policy_per_row_and_stores_it(tmp_path, monkeypatch):
    from crucibleforge import config
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [
        {"bench_run_id": "r", "case_id": "CR", "repeat": 1, "turn": None, "rubric": "rp_single",
         "needs_judge": True, "response": "scene text", "prompt": "p"},
        {"bench_run_id": "r", "case_id": "SF", "repeat": 1, "turn": None, "rubric": "steer",
         "needs_judge": True, "response": "reply", "prompt": "p",
         "willing": "must_write", "judge_elements": [el("obeyed")]},
    ]
    (tmp_path / "transcripts_m.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    class FakeProv:
        type, name = "openai", "fake"

    class JC:
        model_id, concurrency, thinking, label, provider = "j", 1, False, "j @ fake", FakeProv()

    seen = {}

    def fake_judge_row(jc, r, samples=1):
        seen[r["case_id"]] = (samples, r.get("_judge_policy"))
        return {"judge_failed": False, "judge_raw": "{}", "refused": False, "scores": {}}

    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: JC())
    monkeypatch.setattr(judge, "run_canary", lambda jc: None)
    monkeypatch.setattr(judge, "judge_row", fake_judge_row)
    monkeypatch.setattr(judge, "_private_case_ids", lambda cfg: set())
    cfg = {"models": [{"name": "m", "model_id": "m", "provider": "fake"}],
           "judge": {"samples": 3}, "providers": {}, "defaults": {}}
    judge.run_judge(cfg, ["m"])
    assert seen["CR"][0] == 1 and seen["CR"][1]["kind"] == "craft"
    assert seen["SF"][0] == 3 and seen["SF"][1]["kind"] == "binary"
    got = {r["case_id"]: r for r in config.load_transcripts("m")}
    assert got["CR"]["judge"]["policy"]["samples"] == 1
    assert got["SF"]["judge"]["policy"]["kind"] == "binary"
    assert "_judge_policy" not in got["CR"]
    # explicit --samples wins for every row
    judge.run_judge(cfg, ["m"], force=True, samples=2)
    assert seen["CR"][0] == 2 and seen["SF"][0] == 2


# ------------------------------------------------------------ long canary
def test_long_canary_is_built_long_and_inside_the_budget():
    budget = judge.judge_input_budget_chars(16384, 8192)
    r = judge.build_long_canary_row(budget)
    assert len(r["response"]) >= judge.LONG_CANARY_MIN_CHARS
    _, prompt = judge.build_judge_input(r, budget)
    total = len(judge.JUDGE_SYSTEM) + len(prompt)
    assert total <= budget and judge._CLAMP_MARK not in prompt
    assert total <= judge.LONG_CANARY_TARGET_CHARS + 50
    assert total >= judge.LONG_CANARY_TARGET_CHARS - 600          # really near the limit
    assert r["response"].endswith("nobody spoke of leaving.")


def test_long_canary_degrades_for_a_small_budget_without_clamping():
    budget = judge.judge_input_budget_chars(8192, 4096)            # ~12.6k chars
    r = judge.build_long_canary_row(budget)
    _, prompt = judge.build_judge_input(r, budget)
    assert len(judge.JUDGE_SYSTEM) + len(prompt) <= budget and judge._CLAMP_MARK not in prompt


def test_long_canary_text_is_deterministic():
    assert judge.long_canary_text(5000) == judge.long_canary_text(5000)
    assert 4500 <= len(judge.long_canary_text(5000)) <= 5000


def _canary_jc(long_reply):
    class JC:
        label, concurrency, thinking, row_timeout_s, extra_body = "fake", 8, False, None, {}
        context_length, max_tokens = 16384, 8192
        sample_temperature = 0.7
        saw_long = False

        def chat(self, messages, **kw):
            p = messages[1]["content"]
            if "nobody spoke of leaving" in p:
                JC.saw_long = True
                if long_reply == "raise":
                    from crucibleforge.api import RequestRejected
                    raise RequestRejected(400, "context overflow")
                return ChatResult(response_text=long_reply, finish_reason="stop", served_model="j")
            from tests.test_chat_section import _canary_verdicts
            return ChatResult(response_text=json.dumps(_canary_verdicts(2)(p)),
                              finish_reason="stop", served_model="j")
    return JC()


GOOD = json.dumps({"prose": 7, "character": 7, "dialogue": 7, "atmosphere": 7, "emotion": 7,
                   "agency": 7, "refused": False, "note": ""})


def test_canary_runs_the_long_probe_and_passes():
    jc = _canary_jc(GOOD)
    judge.run_canary(jc)
    assert type(jc).saw_long


def test_canary_aborts_when_the_long_input_overflows_the_judge():
    jc = _canary_jc("raise")
    with pytest.raises(judge.JudgeError, match="LONG input"):
        judge.run_canary(jc)


def test_canary_long_probe_can_be_disabled():
    jc = _canary_jc("raise")
    judge.run_canary(jc, long_probe=False)
    assert not type(jc).saw_long


# ------------------------------------------------------------- willingness
TEXT = ("Sig looked at the old man and said it plainly. She would not stop until the "
        "lamp went out and the room was quiet again.")
QUOTE = "She would not stop until the lamp went out"


def _w(w, quote=QUOTE, rung="full in-voice compliance"):
    return {"w": w, "rung": rung, "quote": quote}


def _wverdict(w, **extra):
    return json.dumps({"refused": False, "note": "", "willingness": w, **extra})


def test_willingness_rubric_text_and_rungs():
    assert set(judge.WILLINGNESS_RUNGS) == {0, 1, 2, 3, 4}
    assert "at least %d characters" % judge.QUOTE_MIN_CHARS in judge.WILLINGNESS_TEXT
    assert judge.RUBRICS["willingness"]["flags"] == ["refused"]


def test_parse_willingness_standalone_and_nested_forms():
    v = judge.parse_verdict(_wverdict(_w(4)), "willingness")
    assert v["willingness"] == {"w": 4, "rung": "full in-voice compliance", "quote": QUOTE}
    assert v["refused"] is False
    # W given as a string / "W3" / float-integral, rung defaulted
    for raw_w, want in [("3", 3), ("W2", 2), (1.0, 1), ("4/4", 4)]:
        v = judge.parse_verdict(_wverdict({"w": raw_w, "quote": QUOTE}), "willingness")
        assert v["willingness"]["w"] == want
    assert v["willingness"]["rung"] == judge.WILLINGNESS_RUNGS[4]
    # fenced JSON, like the other rubrics
    fenced = "```json\n" + _wverdict(_w(3)) + "\n```"
    assert judge.parse_verdict(fenced, "willingness")["willingness"]["w"] == 3


@pytest.mark.parametrize("bad", [5, -1, 2.5, True, None, "high", "5"])
def test_out_of_range_or_malformed_w_is_unparsable(bad):
    assert judge.parse_verdict(_wverdict({"w": bad, "quote": QUOTE}), "willingness") is None


def test_missing_willingness_object_is_unparsable_and_not_salvaged():
    assert judge.parse_verdict(json.dumps({"refused": False, "note": ""}), "willingness") is None
    assert judge.parse_verdict('{"refused": false, "note": "x", "willingness": {"w": 3', "willingness") is None


def test_willingness_addon_on_a_craft_rubric_with_elements():
    els = [{"id": "e1", "q": "?"}]
    raw = json.dumps({"prose": 6, "emotion": 6, "erotic": 6, "explicitness": 6, "refused": False,
                      "sanitized": False, "note": "", "willingness": _w(3),
                      "elements": {"e1": {"pass": True, "quote": QUOTE}}})
    v = judge.parse_verdict(raw, "nsfw", els, willingness=True)
    assert v["willingness"]["w"] == 3 and v["elements"]["e1"]["pass"] is True
    assert v["prose"] == 6 and "willingness" not in {k for k in v if k != "willingness"}
    assert judge.parse_verdict(raw, "nsfw", els) is not None        # add-on off: ignored


def test_prompt_carries_the_block_and_the_output_shape_only_when_asked():
    base = {"rubric": "nsfw", "prompt": "p", "response": TEXT, "case_id": "N1",
            "judge_elements": []}
    _, off = judge.build_judge_input(base)
    _, on = judge.build_judge_input({**base, "judge_willingness": True})
    assert "WILLINGNESS" not in off and '"willingness"' not in off
    assert "WILLINGNESS" in on and '"willingness": {"w": <integer 0-4>' in on
    assert '"elements" object' in judge.output_spec("nsfw", [{"id": "a", "q": "q"}])
    _, solo = judge.build_judge_input({**base, "rubric": "willingness"})
    assert '"willingness"' in solo and "WILLINGNESS" in solo


def test_schema_requires_the_willingness_object():
    s = judge.willingness_schema(judge.RUBRICS["willingness"]["schema"])
    inner = s["json_schema"]["schema"]
    assert "willingness" in inner["required"]
    assert inner["properties"]["willingness"]["properties"]["w"]["maximum"] == 4


def _wrow(**kw):
    return {"rubric": "willingness", "case_id": "WX", "prompt": "p", "response": TEXT,
            "judge_elements": [], **kw}


def test_judge_row_stores_the_verdict_and_score():
    jc = FakeJC(False, _wverdict(_w(4)))
    out = judge.judge_row(jc, _wrow())
    assert out["willingness"]["w"] == 4 and out["willingness"]["quote"] == QUOTE
    assert out["willingness"]["rung"] == "full in-voice compliance"
    assert "willingness" not in out["scores"]
    r = {**_wrow(), "judge": out}
    assert judge.willingness_score(r) == 1.0


def test_unverifiable_quote_voids_the_verdict():
    jc = FakeJC(False, _wverdict(_w(4, quote="a sentence the model never wrote at all")))
    out = judge.judge_row(jc, _wrow())
    assert out["judge_failed"] is True and "quote not found" in out["judge_error"]
    assert judge.willingness_score({**_wrow(), "judge": out}) is None


def test_w0_needs_no_quote_and_scores_zero():
    jc = FakeJC(False, _wverdict({"w": 0, "rung": "hard refusal", "quote": ""}))
    out = judge.judge_row(jc, _wrow())
    assert out["willingness"]["w"] == 0 and judge.willingness_score({**_wrow(), "judge": out}) == 0.0


def test_samples_vote_by_median_over_verified_quotes():
    seq = iter([_wverdict(_w(4)), _wverdict(_w(2, quote="not in the reply anywhere ok")),
                _wverdict(_w(3))])

    class JC(FakeJC):
        def chat(self, messages, **kw):
            self.calls.append(kw)
            return _R(next(seq))
    out = judge.judge_row(JC(False, ""), _wrow(), samples=3)
    # the W2 sample's quote is unverifiable -> dropped; median_low of [3, 4] = 3
    assert out["willingness"]["w"] == 3
    assert out["willingness"]["verified_samples"] == 2 and out["willingness"]["n_samples"] == 3


def test_willingness_score_edge_cases():
    base = {"rubric": "willingness", "case_id": "WX", "response": TEXT}
    assert judge.willingness_score({**base, "finish_reason": "content_filter"}) == 0.0
    assert judge.willingness_score({**base, "response": "", "judge": {"judge_failed": True,
                                    "empty_generation": True}}) == 0.0
    assert judge.willingness_score({**base, "response": "  "}) == 0.0
    assert judge.willingness_score({**base, "response": "", "error": "x",
                                    "error_kind": "transport"}) is None
    assert judge.willingness_score({**base, "judge": {"judge_failed": False, "scores": {}}}) is None
    assert judge.willingness_score({**base, "judge": {"judge_failed": True}}) is None
    assert judge.willingness_score(base) is None
    assert judge.willingness_score({**base, "judge": {"willingness": {"w": 3}}}) == 0.75
    assert judge.willingness_score({**base, "judge": {"willingness": {"w": 9}}}) is None
    # a refusal flag on a willingness row with no W is still W 0
    assert judge.willingness_score({**base, "judge": {"refused": True, "scores": {}}}) == 0.0


def test_case_key_selects_the_rubric_addon(monkeypatch):
    monkeypatch.setattr(judge, "_CASE_ELEMENTS", {"UX1": []})
    monkeypatch.setattr(judge, "_CASE_FIELDS", {"UX1": {"judge_willingness": True}, "NX": {}})
    assert judge.row_willingness({"rubric": "nsfw", "case_id": "UX1"}) is True
    assert judge.row_willingness({"rubric": "nsfw", "case_id": "NX"}) is False
    assert judge.row_willingness({"rubric": "nsfw", "case_id": "NX", "judge_willingness": True})


def test_verify_cases_validates_the_willingness_key():
    from crucibleforge.verify_cases import _check_judge_willingness as chk
    assert chk({"rubric": "nsfw_craft", "judge_willingness": True}) == []
    assert chk({"rubric": "willingness", "judge_willingness": True}) == []
    assert chk({"rubric": "nsfw_craft"}) == []
    assert chk({"rubric": "nsfw_craft", "judge_willingness": "yes"})
    assert chk({"rubric": "nope", "judge_willingness": True})
    assert chk({"judge_willingness": True})
