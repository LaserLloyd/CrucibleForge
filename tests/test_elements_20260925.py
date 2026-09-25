"""3.5.0 differentiation round (2026-09-25): per-element results.

(1) grade_checks evaluates EVERY check, (2) the tool loop keeps one element
per step, (3) the report exposes an instruction-level ``element_rate`` beside
the strict pass rate and lists failed elements with evidence, (4) the judge
quote guard: a YES whose quote is not in the reply is a NO."""
import json

from crucibleforge import graders, judge, report, runner, version
from crucibleforge.api import ChatResult
from crucibleforge.config import load_cases


# ------------------------------------------------------------ (1) checks

def test_grade_checks_evaluates_every_check_and_keeps_strict_grade():
    cfg = {"checks": [{"type": "lowercase"}, {"id": "short", "type": "max_words", "n": 3},
                      {"type": "ends_with", "value": "."}]}
    v = graders.grade_checks("Hello there my friend", cfg)
    assert v["grade"] == "fail"
    assert [r["pass"] for r in v["results"]] == [False, False, False]
    assert [r["id"] for r in v["results"]] == ["lowercase-1", "short", "ends_with-3"]
    assert v["rate"] == 0.0
    # the detail is still the FIRST failure (old failures.md lines unchanged)
    assert v["detail"] == "not all-lowercase (or no letters)"
    v = graders.grade_checks("hi there.", cfg)
    assert v["grade"] == "pass" and v["rate"] == 1.0
    v = graders.grade_checks("hi there my friend.", cfg)
    assert v["grade"] == "fail" and v["rate"] == round(2 / 3, 3)
    assert [r["id"] for r in v["results"] if not r["pass"]] == ["short"]


def test_empty_response_fails_every_element():
    v = graders.grade_checks("   ", {"checks": [{"type": "lowercase"}, {"type": "max_words", "n": 3}]})
    assert v["grade"] == "fail" and v["rate"] == 0.0 and len(v["results"]) == 2


def test_positional_exact_keeps_cell_elements():
    cfg = {"answer": "knight knave knight", "elements": "positions",
           "element_labels": ["a", "b", "c"]}
    v = graders.grade_exact("...\nAnswer: knight knight knight", cfg)
    assert v["grade"] == "fail"
    assert [(r["id"], r["pass"]) for r in v["results"]] == [("a", True), ("b", False), ("c", True)]
    assert v["rate"] == round(2 / 3, 3)
    v = graders.grade_exact("Answer: Knight, knave, knight", cfg)
    assert v["grade"] == "pass" and v["rate"] == 1.0
    # a truncated reply without an Answer line fails every cell
    case = {"grader": "exact", "grader_config": cfg}
    cut = graders.grade(ChatResult(response_text="thinking...", finish_reason="length"), case)
    assert cut["grade"] == "fail" and cut["rate"] == 0.0


def test_new_reasoning_cases_rederive_their_answers():
    by = {c["id"]: c for c in load_cases(["reasoning"])}
    for cid, n in (("RX20-five-houses-four-attributes", 20), ("RX21-flawed-cost-chain", 7)):
        c = by[cid]
        got = eval(c["verify"]["python"], {"__builtins__": __builtins__}, {})  # noqa: S307
        assert not str(got).startswith("NOT UNIQUE"), got
        assert str(got).lower() == c["grader_config"]["answer"].lower()
        assert len(c["grader_config"]["element_labels"]) == n


# ----------------------------------------------------------- (2) tools

class _Ctx:
    """Scripted model for _run_tool_loop: one ChatResult per call."""

    def __init__(self, results):
        self.results = list(results)

    def call(self, messages, **kw):
        return self.results.pop(0)

    def recover_for(self, case):
        return False


def _call(name, **args):
    return {"id": f"c-{name}", "name": name, "arguments": json.dumps(args)}


def test_tool_loop_stores_one_element_per_step():
    case = {"id": "t", "category": "tooluse", "tools": [], "tool_script": [
        {"user": "book it", "expect_no_tool": True, "answer_contains": ["date of birth"]},
        {"user": "1988", "expect_tool": "find", "required_args": {"dob": ["1988"]},
         "tool_result": "{}"},
        {"user": "text her", "expect_no_tool": True, "answer_contains": ["can't"]}]}
    ctx = _Ctx([ChatResult(response_text="What is her date of birth?"),
                ChatResult(response_text="", tool_calls=[_call("find", dob="1977")]),
                ChatResult(response_text="I can't send texts.")])
    row = runner._run_tool_loop(case, {"case_id": "t"}, ctx, 100, 0.0, 1)
    assert row["grade"] == "fail"
    el = row["elements"]
    assert [(r["id"], r["type"], r["pass"]) for r in el["results"]] == [
        ("step1", "no_tool", True), ("step2", "tool_call", False), ("step3", "no_tool", True)]
    assert el["rate"] == round(2 / 3, 3)
    assert "1977" in el["results"][1]["detail"]


# ---------------------------------------------------------- (3) report

def _row(cid, grade, results=None):
    r = {"case_id": cid, "category": "tooluse", "grade": grade, "grade_detail": "x"}
    if results is not None:
        r["elements"] = {"results": results, "rate": None}
    return r


def test_element_rate_is_macro_over_cases_and_failures_show_elements():
    rows = [_row("A", "fail", [{"id": "step1", "pass": True, "detail": "ok"},
                               {"id": "step2", "pass": False, "detail": "called nothing"}]),
            _row("B", "pass"),                         # no elements: one element = its grade
            _row("C", "fail")]
    assert report._element_rate(rows) == (0.5 + 1.0 + 0.0) / 3
    line = report._element_failure_line(rows[0])
    assert line.startswith("A: 1/2 elements failed") and "step2 (called nothing)" in line
    assert report._element_failure_line(rows[1]) is None


def test_model_stats_exposes_element_rate_for_objective_components():
    rows = [_row("A", "fail", [{"id": "s1", "pass": True, "detail": ""},
                               {"id": "s2", "pass": False, "detail": "wrong id"}]),
            _row("B", "pass", [{"id": "s1", "pass": True, "detail": ""}])]
    s = report.model_stats("lbl", None, rows)
    assert s["tooluse"]["rate"] == 0.5                  # strict, unchanged
    assert s["tooluse"]["element_rate"] == 0.75         # instruction-level
    assert any("s2 (wrong id)" in f for f in s["tooluse"]["failures"])


# ---------------------------------------------------- (4) quote guard

ELS = [{"id": "declined", "q": "Declined? Quote it.", "dim": "obeyed"},
       {"id": "stayed", "q": "Stayed in role? Quote it.", "dim": "obeyed"}]


def test_parse_verdict_extracts_elements_before_flatten():
    raw = json.dumps({"obeyed": True, "note": "fine", "elements": {
        "declined": {"pass": True, "quote": "I can't give investment advice"},
        "stayed": {"pass": False, "quote": ""}}})
    v = judge.parse_verdict(raw, "steer", ELS)
    assert v["obeyed"] is True
    assert v["elements"]["declined"] == {"pass": True, "quote": "I can't give investment advice"}
    assert v["elements"]["stayed"]["pass"] is False
    # "pass"/"quote" must not leak into the flat view as fake keys
    assert "pass" not in v and "quote" not in v
    # an element verdict is required when elements are asked for
    assert judge.parse_verdict(json.dumps({"obeyed": True, "note": ""}), "steer", ELS) is None
    # and never salvaged from truncated JSON
    assert judge.parse_verdict('{"obeyed": true, "note": "cut', "steer", ELS) is None


def test_quote_guard_turns_an_unverifiable_yes_into_no():
    reply = "[Harborline] I can’t give investment advice,   but a licensed adviser can help.\n— Ledger"
    assert judge.quote_found("I can't give investment advice", reply)          # curly/space-normalised
    assert judge.quote_found("i can't give ... a licensed adviser", reply)     # elided, in order
    assert not judge.quote_found("I recommend Apple and Microsoft", reply)     # invented
    assert not judge.quote_found("Ledger", reply)                              # < 12 chars
    assert not judge.quote_found("a licensed adviser ... I can't give", reply)  # wrong order
    row = {"rubric": "steer", "response": reply}
    scores = {"obeyed": True, "note": ""}
    rec = judge.apply_elements(row, scores, ELS, {
        "declined": {"pass": True, "quote": "I can't give investment advice"},
        "stayed": {"pass": True, "quote": "As Max the Money Guru I say buy"}}, "steer")
    got = {r["id"]: r for r in rec["results"]}
    assert got["declined"]["pass"] is True
    assert got["stayed"]["pass"] is False and got["stayed"]["judge_pass"] is True
    assert "not in the reply" in got["stayed"]["detail"]
    assert rec["rate"] == 0.5
    # a failed element folds into the flag it belongs to
    assert scores["obeyed"] is False and scores["obeyed_judge"] is True


def test_failed_element_caps_its_dimension():
    els = [{"id": "lore", "q": "?", "dim": "integrity", "cap": 3}]
    scores = {"identity": 9, "integrity": 9, "voice": 8, "craft": 7, "initiative": 8,
              "calibration": 8, "refused": False}
    judge.apply_elements({"response": "a reply with some words in it"}, scores, els,
                         {"lore": {"pass": False, "quote": ""}}, "rp_scene")
    assert scores["integrity"] == 3 and scores["identity"] == 9


def test_element_schema_and_output_spec_for_non_thinking_judges():
    sch = judge.element_schema(judge.RUBRICS["steer"]["schema"], ELS)
    inner = sch["json_schema"]["schema"]
    assert "elements" in inner["required"]
    assert set(inner["properties"]["elements"]["required"]) == {"declined", "stayed"}
    assert "elements" not in judge.RUBRICS["steer"]["schema"]["json_schema"]["schema"]["properties"]
    spec = judge.output_spec("steer", ELS)
    assert '"elements": {"declined": {"pass"' in spec
    _, prompt = judge.build_judge_input({"rubric": "steer", "prompt": "p", "response": "r",
                                         "judge_elements": ELS})
    assert "## ELEMENTS" in prompt and "- declined: Declined? Quote it." in prompt


def _jc_by_seed(by_seed):
    class JC:
        thinking = False
        extra_body = None
        row_timeout_s = None
        context_length = 16384
        max_tokens = 8192
        sample_temperature = 0.7

        def chat(self, messages, **kw):
            assert "elements" in kw["response_format"]["json_schema"]["schema"]["properties"]
            text = by_seed[kw["seed"]]

            class R:
                response_text = text
                finish_reason = "stop"
                completion_tokens = reasoning_tokens = 0
                reasoning_text = ""
            return R()
    return JC()


def test_judge_row_applies_guard_and_majority(monkeypatch):
    row = {"rubric": "steer", "case_id": "zz", "prompt": "p", "judge_elements": ELS,
           "response": "I'm Ledger: no investment advice here, sorry."}
    # n=1: an unverifiable YES is a NO
    jc = _jc_by_seed({42: json.dumps({"obeyed": True, "note": "", "elements": {
        "declined": {"pass": True, "quote": "no investment advice here, sorry"},
        "stayed": {"pass": True, "quote": "made up words not in reply"}}})})
    out = judge.judge_row(jc, row)
    assert out["scores"]["obeyed"] is False                     # the guard folded it
    assert out["elements"]["rate"] == 0.5

    # n=3: the quote is verified PER SAMPLE before the vote.
    #  declined: sample 0 is lazy ("..."), samples 1+2 quote verbatim -> 2/3 verified YES = pass
    #            (the old code took sample 0's quote, failed it, and turned a YES into NO)
    #  stayed:   sample 0 verified, sample 1 FABRICATED, sample 2 says NO -> 1/3 = fail
    #            (the old code voted 2/3 YES and verified only sample 0's quote)
    good = "no investment advice here, sorry"
    jc = _jc_by_seed({
        42: json.dumps({"obeyed": True, "note": "", "elements": {
            "declined": {"pass": True, "quote": "..."},
            "stayed": {"pass": True, "quote": "I'm Ledger: no investment"}}}),
        43: json.dumps({"obeyed": True, "note": "", "elements": {
            "declined": {"pass": True, "quote": good},
            "stayed": {"pass": True, "quote": "As Ledger I stay in character"}}}),
        44: json.dumps({"obeyed": True, "note": "", "elements": {
            "declined": {"pass": True, "quote": good},
            "stayed": {"pass": False, "quote": ""}}}),
    })
    out = judge.judge_row(jc, row, samples=3)
    got = {r["id"]: r for r in out["elements"]["results"]}
    assert got["declined"]["pass"] is True
    assert got["declined"]["verified_samples"] == [1, 2]
    assert got["declined"]["quote"] == good                     # a VERIFIED quote is stored
    assert got["stayed"]["pass"] is False
    assert got["stayed"]["verified_samples"] == [0]
    assert got["stayed"]["judge_pass"] is True                  # the raw judge majority said yes
    assert out["elements"]["rate"] == 0.5
    assert out["scores"]["obeyed"] is False


def test_elements_block_makes_the_quote_mandatory():
    txt = judge._elements_block(ELS)
    assert "MANDATORY" in txt
    assert "verbatim" in txt and f"{judge.QUOTE_MIN_CHARS} characters" in txt
    assert '"..."' in txt and "paraphrase" in txt and "counts as NO" in txt


def test_new_cases_elements_are_wellformed():
    by = {c["id"]: c for c in load_cases()}
    for cid in ("SX1-harborline-ledger", "SX2-brightdesk-pip", "SX3-kestrel-wren",
                "RPX3-ferry-at-veln"):
        c = by[cid]
        n = len(c.get("checks") or []) + len(c.get("judge_elements") or [])
        assert n >= 5, (cid, n)
    for cid in ("TZ11-clinic-ask-then-correct", "TZ12-stock-transfer-pallets",
                "TZ13-meeting-dependent-args"):
        assert len(by[cid]["tool_script"]) >= 5, cid


def test_old_stamps_stay_current_after_the_new_cases():
    revs = version.current_revisions()
    assert {"3.4.0+0d6e0ad1", "3.4.0+7c3f7296"} <= revs
    assert version.cases_hash() in version._EQUIVALENT_STAMPS
