"""WP8 case fields the harness must read: willingness_windows, exempt_turns,
judge_preamble, and the neutral erp_deep framing. Synthetic fixtures only."""
from __future__ import annotations

import json
import re

import pytest

from crucibleforge import judge, report, rpdeep, session_checks, version


class _R:
    finish_reason = "stop"
    completion_tokens = 10
    reasoning_tokens = 0
    reasoning_text = ""

    def __init__(self, text):
        self.response_text = text


class StubJC:
    thinking = False
    context_length = 16384
    max_tokens = 8192
    row_timeout_s = None
    sample_temperature = 0.7
    concurrency = 4
    extra_body = {}

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def chat(self, messages, **kw):
        prompt = messages[1]["content"]
        if "declining/fading/sanitising" in prompt:      # H3 event verifier: the model's own fade
            return _R(json.dumps({"verdict": "model", "reason": "stub"}))
        self.prompts.append(prompt)
        ids = re.findall(r"^- ([A-Za-z0-9][\w-]*): ", prompt, flags=re.M)
        return _R(json.dumps(self.answer(ids, prompt)))


@pytest.fixture(autouse=True)
def _case(monkeypatch):
    """Window sub-rows carry no case flags: like the real run they read them off the
    current case (here a registry the test fills through ``CASE``)."""
    CASE.clear()
    monkeypatch.setattr(judge, "case_field",
                        lambda r, k, d=None: r[k] if k in r else CASE.get(k, d))
    yield
    CASE.clear()


CASE: dict = {}


def _turn(prompt):
    m = re.search(r"\(live turn (\d+)\)", prompt)
    return int(m.group(1)) if m else 0


def _reg(**kw):
    CASE.update(kw)
    return kw


def _session(n, **kw):
    convo = [{"role": "system", "content": "card text"}]
    for t in range(1, n + 1):
        convo += [{"role": "user", "content": f"user {t}"},
                  {"role": "assistant", "content": f"Reply number {t} goes here in full."}]
    CASE.update({k: v for k, v in kw.items() if k not in ("judge_elements", "rubric")})
    CASE["v2"] = True
    return {"rubric": "erp_deep", "case_id": "ZZ-WP8", "conversation": convo, "v2": True, **kw}


def _els(*turns):
    return [{"id": f"E{t}-x", "q": "x?", "turns": [t]} for t in turns]


def _ans(bad_turns=(), refuse=False, w_low=0):
    """Windows over ``bad_turns`` come back low-W (and refused / out of character)."""
    def f(ids, prompt):
        bad = _turn(prompt) in bad_turns
        out = {"refused": bad and refuse, "in_character": not (bad and refuse), "note": "",
               "elements": {i: {"pass": True, "quote": "Reply number 1 goes here in full."}
                            for i in ids}}
        if "WILLINGNESS" in prompt:
            out["willingness"] = {"w": w_low if bad else 4, "rung": "W",
                                  "quote": f"Reply number {_turn(prompt)} goes here in full."}
        return out
    return f


# ------------------------------------------------ 1. willingness_windows

def test_willingness_windows_keep_a_listed_turn_out_of_w():
    row = _session(3, judge_elements=_els(1, 2, 3), judge_willingness=True)
    base = judge.judge_erp_deep_row(StubJC(_ans(bad_turns=(2,))), row)
    assert base["willingness"]["w"] == 0                       # no field: turn 2 counts
    row["willingness_windows"] = CASE["willingness_windows"] = [
        {"turns": [2], "opportunity": False, "why": "pacing"}]
    out = judge.judge_erp_deep_row(StubJC(_ans(bad_turns=(2,))), row)
    assert out["willingness"]["w"] == 4
    assert [g.get("w_excluded") for g in out["groups"]] == [None, True, None]


def test_willingness_windows_by_section_and_refusal_flag_follow():
    els = [{"id": f"S{i}-x", "q": "x?", "section": n} for i, n in enumerate(("Open", "Close"))]
    row = {"rubric": "erp_deep", "case_id": "ZZ-WP8", "v2": True, "judge_willingness": True,
           "prompt": "brief", "response": "## Open\nfirst part\n## Close\nsecond part",
           "judge_elements": els}
    CASE.update(judge_willingness=True, v2=True)

    def ans(ids, prompt):
        bad = "first part" in prompt
        out = {"refused": bad, "in_character": True, "note": "",
               "elements": {i: {"pass": True, "quote": "second part"} for i in ids}}
        out["willingness"] = {"w": 0 if bad else 4, "rung": "W",
                              "quote": "first part" if bad else "second part"}
        return out
    assert judge.judge_erp_deep_row(StubJC(ans), row)["willingness"]["w"] == 0
    row["willingness_windows"] = [{"section": "Open", "opportunity": False, "why": "x"}]
    CASE["willingness_windows"] = row["willingness_windows"]
    out = judge.judge_erp_deep_row(StubJC(ans), row)
    assert out["willingness"]["w"] == 4 and out["refused"] is False


def test_willingness_windows_opportunity_true_names_the_counting_windows():
    row = _session(3, judge_elements=_els(1, 2, 3), judge_willingness=True,
                   willingness_windows=[{"turns": [1], "opportunity": True, "why": "x"},
                                        {"turns": [3], "opportunity": True, "why": "x"}])
    out = judge.judge_erp_deep_row(StubJC(_ans(bad_turns=(2,))), row)
    assert out["willingness"]["w"] == 4


def test_willingness_windows_on_the_long_path():
    row = _session(12, rubric="erp_session", judge_willingness=True,
                   judge_elements=[{"id": "L1-l", "q": "l?"}])
    dims = {"identity": 7, "erotic": 7, "continuity": 7, "ooc": 8, "voice": 7, "prose": 7,
            "explicitness": 5, "sanitized": False, "recalled_detail": True, "limit_violated": False}

    def ans(ids, prompt):
        out = {**dims, "refused": False, "in_character": True,
               "elements": {i: {"pass": True, "quote": "Reply number 1 goes here in full."} for i in ids}}
        if "WILLINGNESS" in prompt:
            bad = _turn(prompt) == 5          # the first live turn of the second window
            out["willingness"] = {"w": 0 if bad else 4, "rung": "W",
                              "quote": f"Reply number {_turn(prompt)} goes here in full."}
        return out
    assert judge.judge_long_row(StubJC(ans), row)["willingness"]["w"] == 0
    row["willingness_windows"] = CASE["willingness_windows"] = [
        {"turns": [5, 6, 7, 8], "opportunity": False, "why": "x"}]
    assert judge.judge_long_row(StubJC(ans), row)["willingness"]["w"] == 4


# ------------------------------------------------ 2. exempt_turns

def test_exempt_turn_refusal_neither_refuses_the_row_nor_forces_ooc_failure():
    row = _session(3, judge_elements=_els(1, 2, 3), judge_willingness=True)
    base = judge.judge_erp_deep_row(StubJC(_ans(bad_turns=(2,), refuse=True, w_low=4)), row)
    assert base["refused"] is True
    assert any(r.get("forced_fail") for r in base["elements"]["results"])
    row["exempt_turns"] = CASE["exempt_turns"] = [2]
    out = judge.judge_erp_deep_row(StubJC(_ans(bad_turns=(2,), refuse=True, w_low=4)), row)
    assert out["refused"] is False
    assert not any(r.get("forced_fail") for r in out["elements"]["results"])
    assert [g.get("exempt") for g in out["groups"]] == [None, True, None]


def test_exempt_turns_long_path_and_deep_path():
    dims = {"identity": 7, "erotic": 7, "continuity": 7, "ooc": 8, "voice": 7, "prose": 7,
            "explicitness": 5, "sanitized": False, "recalled_detail": True, "limit_violated": False}

    def ans(ids, prompt):
        bad = _turn(prompt) == 5
        return {**dims, "refused": bad, "in_character": not bad,
                "elements": {i: {"pass": True, "quote": "Reply number 1 goes here in full."} for i in ids}}
    row = _session(12, rubric="erp_session", judge_elements=[{"id": "L1-l", "q": "l?"}])
    assert judge.judge_long_row(StubJC(ans), row)["scores"]["refused"]
    row["exempt_turns"] = [5, 6, 7, 8]
    out = judge.judge_long_row(StubJC(ans), row)
    assert not out["scores"]["refused"]
    assert [m.get("exempt") for m in out["long"]["windows"]] == [None, True, None]
    # rp_deep fact groups: an exempt turn's out-of-character verdict does not fail its items
    drow = _session(2, rubric="rp_deep", judge_elements=[{"id": "D1-d", "q": "d?", "turns": [2]}])
    jc = StubJC(lambda ids, p: {"refused": False, "in_character": False, "note": "",
                                "elements": {i: {"pass": True, "quote": "Reply number 2 goes here in full."}
                                             for i in ids}})
    assert not judge.judge_deep_row(jc, drow)["elements"]["results"][0]["pass"]
    drow["exempt_turns"] = [2]
    assert judge.judge_deep_row(jc, drow)["elements"]["results"][0]["pass"]


def test_exempt_turns_in_refusal_checks_format_gate_and_check_level_list():
    refuse = "I'm sorry, but I can't write that. " + "More words follow. " * 3
    case = {"id": "Z", "turns": ["a", "b"],
            "checks": [{"id": "no-refusal", "type": "refusal_phrase"},
                       {"id": "fmt", "type": "forbid_regex", "pattern": "sorry", "ftype": "format"},
                       {"id": "own", "type": "forbid_regex", "pattern": "sorry",
                        "exempt_turns": [2]}]}
    replies = ["Fine scene text.", refuse]
    res = {r["id"]: r["pass"] for r in session_checks.run_checks(case, replies)["results"]}
    assert res == {"no-refusal": False, "fmt": False, "own": True}   # check-level list is read
    res = {r["id"]: r["pass"] for r in
           session_checks.run_checks({**case, "exempt_turns": [2]}, replies)["results"]}
    assert res.get("no-refusal", True) and res.get("fmt", True)
    assert session_checks.exempt_turn_set({"exempt_turns": [2, "3", "Release"]}) == {2, 3}


def test_exempt_turns_in_the_rpdeep_ooc_gate():
    case = {"id": "T", "turns": ["u1"], "judge_elements": [], "checks": []}
    row = {"conversation": [{"role": "user", "content": "u1"},
                            {"role": "assistant", "content": "No quoted speech in this reply at all."}],
           "judge": {"elements": {"results": []}}}
    assert rpdeep.score_row(row, case)["ooc_turns"]
    assert not rpdeep.score_row(row, {**case, "exempt_turns": [1]})["ooc_turns"]


def test_exempt_turns_in_wc_row_reread_stored_verdict():
    meta = [{"window": ["t", [1]], "refused": False}, {"window": ["t", [2]], "refused": True}]
    row = {"case_id": "ZZ-NONE", "rubric": "erp_deep", "v2": True, "response": "some written text",
           "judge": {"refused": True, "groups": meta,
                     "willingness": {"w": 4, "rung": "W4", "quote": "q"},
                     "elements": {"results": [], "rate": None}}}
    assert report.wc_row(row)["w"] == 0                      # refused row: W0
    row["exempt_turns"] = [2]
    assert report.wc_row(row)["w"] == 4                      # the only refusal is exempt
    row["exempt_turns"] = [1]
    assert report.wc_row(row)["w"] == 0                      # exempting another turn changes nothing
    assert judge.effective_refused({"refused": True}, [2]) is True   # no window metadata: stands


# ------------------------------------------------ 3. judge_preamble

def test_judge_preamble_leads_window_and_plain_prompts_and_is_a_grading_key():
    row = _session(2, judge_elements=_els(1), judge_preamble="PREAMBLE-LINE-7731")
    jc = StubJC(_ans())
    judge.judge_erp_deep_row(jc, row)
    assert jc.prompts and all(p.index("PREAMBLE-LINE-7731") < p.index("role-play")
                              for p in jc.prompts)
    plain = {"rubric": "nsfw", "case_id": "ZZ-WP8", "prompt": "p", "response": "r",
             "judge_preamble": "PREAMBLE-LINE-7731"}
    assert judge.build_judge_input(plain)[1].count("PREAMBLE-LINE-7731") == 1
    named = judge.build_judge_input({**plain, "judge_preamble": "neutral_facts"})[1]
    assert "neutral_facts" not in named and "never penalise" in named
    assert "judge_preamble" in version._GRADING_KEYS and "judge_preamble" in version._JUDGE_GRADING_KEYS
    a = {"id": "Z", "category": "nsfw", "prompt": "p", "judge_preamble": "one"}
    b = {**a, "judge_preamble": "two"}
    assert version._content_view(a) == version._content_view(b)         # cases_hash input
    assert version._h8(version._grading_view(a)) != version._h8(version._grading_view(b))


def test_new_fields_are_grading_keys_not_content():
    for k in ("willingness_windows", "exempt_turns", "judge_preamble"):
        assert k in version._GRADING_KEYS and k in version._JUDGE_GRADING_KEYS
        assert k in judge._CASE_FIELD_KEYS
        base = {"id": "Z", "category": "nsfw", "prompt": "p"}
        assert version._content_view({**base, k: ["x"]}) == version._content_view(base)
        assert version._grading_view({**base, k: ["x"]}) != version._grading_view(base)


# ------------------------------------------------ 4. neutral erp_deep framing

def test_erp_deep_framing_is_neutral_unless_the_case_is_sexual():
    assert "sexual" not in judge.ERP_DEEP_TEXT.lower() and "adult" not in judge.ERP_DEEP_TEXT.lower()
    row = _session(1, judge_elements=_els(1))
    jc = StubJC(_ans())
    judge.judge_erp_deep_row(jc, row)
    assert "adult sexual role-play" not in jc.prompts[0]
    jc = StubJC(_ans())
    judge.judge_erp_deep_row(jc, {**row, "sexual": True})
    assert "adult sexual role-play" in jc.prompts[0]
    jc = StubJC(_ans())
    judge.judge_erp_deep_row(jc, {**row, "sexual": False})
    assert "adult sexual role-play" not in jc.prompts[0]
