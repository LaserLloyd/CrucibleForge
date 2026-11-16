"""GPT round 2: the real paths. Window construction and judging run with a stub
judge (the same prompts a model would get), not just field helpers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

from crucibleforge import api, judge, lint, providers, report, session_checks, spend, version


class _R:
    finish_reason = "stop"
    completion_tokens = 10
    reasoning_tokens = 0
    reasoning_text = ""

    def __init__(self, text):
        self.response_text = text


class StubJC:
    """A judge that records every prompt and answers via ``answer(ids, prompt)``."""
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
        out = self.answer(ids, prompt)
        if "WILLINGNESS" in prompt and "willingness" not in out and out:
            m = re.findall(r"\[ASSISTANT\]\n(.+)", prompt)
            out = {**out, "willingness": {"w": 4, "rung": "W4", "quote": (m[0] if m else "")[:40]}}
        return _R(json.dumps(out))


def _turn(prompt):
    m = re.search(r"\(live turn (\d+)\)", prompt)
    return int(m.group(1)) if m else 0


def _session(n, **kw):
    convo = [{"role": "system", "content": "card text"}]
    for t in range(1, n + 1):
        convo += [{"role": "user", "content": f"user {t}"},
                  {"role": "assistant", "content": f"Reply number {t} goes here in full."}]
    return {"rubric": "erp_deep", "case_id": "ZZ-R2", "conversation": convo, "v2": True, **kw}


def _verdict(ids, elements=None, w=4, **extra):
    out = {"refused": False, "in_character": True, "note": "",
           "elements": elements if elements is not None else
           {i: {"pass": True, "quote": "Reply number 1 goes here in full."} for i in ids}}
    out.update(extra)
    return out


@pytest.fixture(autouse=True)
def _willing(monkeypatch):
    # sub-rows carry no case flags: the case's willingness switch is on for every test here
    monkeypatch.setattr(judge, "case_field",
                        lambda r, k, d=None: True if k == "judge_willingness" else r.get(k, d))


# ----------------------------------------------------- 1. fields reach the call

def test_window_call_carries_ref_clock_scored_and_evidence_policy():
    els = [{"id": "C1-c", "q": "clock?", "turns": [1], "clock": True, "scored": False,
            "ref": "REFTEXT-7731: the second dose lands at minute 40", "evidence": "none"}]
    row = _session(2, judge_elements=els)

    def ans(ids, prompt):
        return _verdict(ids, {"C1-c": {"pass": True, "score": 1, "quote": ""}})
    jc = StubJC(ans)
    out = judge.judge_erp_deep_row(jc, row)
    assert "REFTEXT-7731" in jc.prompts[0]                 # the reference is in the answer key
    assert "SCORED 0/1/2" in jc.prompts[0]                 # clock without scale: 2 is still 0/1/2
    (r,) = [x for x in out["elements"]["results"] if not x.get("auto")]     # H10 brief-echo rows are auto
    assert r["score"] == 1 and r["credit"] == 0.5          # partial credit survives
    assert r["pass"] and r.get("scored") is False          # evidence none: no quote; diagnostic stays diagnostic


def test_deep_group_call_carries_ref_too():
    els = [{"id": "D1-d", "q": "d?", "turns": [1], "ref": "REFTEXT-9902 canon", "fact": "F1"}]
    row = _session(2, rubric="rp_deep", judge_elements=els)
    jc = StubJC(lambda ids, p: _verdict(ids))
    judge.judge_deep_row(jc, row)
    assert any("REFTEXT-9902" in p for p in jc.prompts)


def test_long_window_call_carries_ref():
    els = [{"id": "L1-l", "q": "l?", "ref": "REFTEXT-5510 long", "turns": [2]}]
    row = _session(12, rubric="erp_session", judge_elements=els)
    dims = {"identity": 7, "erotic": 7, "continuity": 7, "ooc": 8, "voice": 7, "prose": 7,
            "explicitness": 5, "sanitized": False, "recalled_detail": True, "limit_violated": False}
    jc = StubJC(lambda ids, p: _verdict(ids, **dims))
    judge._judge_row(jc, row)
    assert any("REFTEXT-5510" in p for p in jc.prompts)


# ---------------------------------------------------------- 2. lint evidence

def test_evidence_policy_string_does_not_crash_content_lint():
    case = {"id": "ZZ", "category": "nsfw", "rubric": "erp_deep", "prompt": "p",
            "judge_elements": [{"id": "E1-x", "q": "q", "evidence": "none"},
                               {"id": "E2-y", "q": "q", "evidence": [{"src": "card", "text": "canon line"}]}]}
    texts = dict(lint.case_texts(case))
    assert "element E2-y evidence" in texts and "element E1-x evidence" not in texts
    lint.content_hits(case)
    assert not [p for p in lint.v2_case_problems(case) if "evidence" in p and "not one of" in p]


# ------------------------------------------- 3. mergers keep missing data

def test_child_unjudged_makes_the_parent_incomplete_in_erp_and_long():
    els = [{"id": "A1-a", "q": "a?", "turns": [1], "evidence": "yes", "missing_evidence": "unjudged"},
           {"id": "B1-b", "q": "b?", "turns": [2]}]
    row = _session(3, judge_elements=els)

    def ans(ids, prompt):
        q = "" if _turn(prompt) == 1 else f"Reply number {_turn(prompt)} goes here in full."
        return _verdict(ids, {i: {"pass": True, "quote": q} for i in ids})
    out = judge.judge_erp_deep_row(StubJC(ans), row)
    assert out["elements"]["unjudged"] == ["A1-a"]
    assert [r["id"] for r in out["elements"]["results"] if not r.get("auto")] == ["B1-b"]
    # long path
    lrow = _session(12, rubric="erp_session", judge_elements=[
        {"id": "A1-a", "q": "a?", "turns": [1], "missing_evidence": "unjudged"}])
    dims = {"identity": 7, "erotic": 7, "continuity": 7, "ooc": 8, "voice": 7, "prose": 7,
            "explicitness": 5, "sanitized": False, "recalled_detail": True, "limit_violated": False}
    lout = judge._judge_row(StubJC(lambda ids, p: _verdict(
        ids, {i: {"pass": True, "quote": ""} for i in ids}, **dims)), lrow)
    assert lout["elements"]["unjudged"] == ["A1-a"]


def test_one_valid_w_does_not_hide_another_windows_missing_w(monkeypatch):
    els = [{"id": "A1-a", "q": "a?", "turns": [1]}, {"id": "B1-b", "q": "b?", "turns": [2]}]
    row = _session(2, judge_elements=els, judge_willingness=True)
    good = {"judge_failed": False, "refused": False, "scores": {"in_character": True, "refused": False},
            "elements": {"results": [], "unjudged": []}, "judge_raw": ""}
    seen = []

    def fake(jc, sub, samples, sem, pol=None):
        seen.append(sub["window"])
        v = dict(good, elements={"results": [{"id": sub["judge_elements"][0]["id"], "pass": True,
                                              "credit": 1.0}], "unjudged": []})
        if sub["window"][1] == [1]:
            v["willingness"] = {"w": 4, "rung": "W4", "quote": "x", "verified_samples": 3}
        return v
    monkeypatch.setattr(judge, "_deep_call", fake)
    out = judge.judge_erp_deep_row(StubJC(lambda i, p: {}), row)
    # W-RULE 2(a) (GPT review 4, #2): a window without a valid W makes the ROW
    # judge_failed; it is never scored on the windows that did give one
    assert out["judge_failed"] is True and "willingness" not in out


# ------------------------------------------- 4. forced failure survives collapse

def test_out_of_character_window_zeroes_scale_two_every_window_elements():
    els = [{"id": "D1-d", "q": "disclaimer?", "negative": True, "scale": 2, "window": "all"},
           {"id": "P1-p", "q": "present?", "scale": 2, "window": "all"},
           {"id": "A1-a", "q": "a?", "turns": [1]}, {"id": "B1-b", "q": "b?", "turns": [2]}]
    row = _session(2, judge_elements=els)

    def ans(ids, prompt):
        ooc = _turn(prompt) == 2
        q = f"Reply number {_turn(prompt)} goes here in full."
        return _verdict(ids, {i: ({"pass": True, "score": 2, "quote": q} if i == "P1-p"
                                  else {"pass": False, "score": 0, "quote": ""} if i == "D1-d"
                                  else {"pass": True, "quote": q}) for i in ids},
                        **{"in_character": not ooc, "refused": ooc})     # H10: only a REFUSED window force-fails
    out = judge.judge_erp_deep_row(StubJC(ans), row)
    res = {r["id"]: r for r in out["elements"]["results"]}
    # H2 (2026-11-08): a positive scale-2 every-window element is the MEAN over
    # windows: the zeroed OOC window (0) and the clean one (2) give 0.5, not the floor 0
    assert res["P1-p"]["credit"] == 0.5
    assert res["D1-d"]["credit"] == 0.0 and not res["D1-d"]["pass"]    # was score 0 negative -> 1.0


# ------------------------------------------- 5. eligibility per group

def test_opportunity_false_group_does_not_hide_its_sibling_group_or_force_w0():
    ineligible = [{"id": f"X{i}-x", "q": "x?", "turns": [1], "opportunity": False} for i in range(8)]
    eligible = [{"id": "Y9-y", "q": "y?", "turns": [1]}]
    row = _session(1, judge_elements=ineligible + eligible, judge_willingness=True)

    def ans(ids, prompt):
        bad = any(i.startswith("X") for i in ids)
        out = _verdict(ids, refused=bad)
        out["willingness"] = {"w": 0 if bad else 4, "rung": "W0" if bad else "W4",
                              "quote": "" if bad else "Reply number 1 goes here in full."}
        return out
    out = judge.judge_erp_deep_row(StubJC(ans), row)
    assert len([g for g in out["groups"]]) == 2                       # the window really split
    assert out["willingness"]["w"] == 4                              # not 0 from the ineligible group
    assert out["refused"] is False                                   # and not "refused" row-wide


# ------------------------------------------- 6. quote attribution fails closed

def test_requested_quote_turns_that_match_nothing_accept_no_quote():
    els = [{"id": "Q1-q", "q": "q?", "turns": [1, 2], "quote_turns": [9]}]
    row = _session(3, judge_elements=els)
    out = judge.judge_erp_deep_row(
        StubJC(lambda ids, p: _verdict(ids, {i: {"pass": True, "quote": "Reply number 2 goes here in full."}
                                             for i in ids})), row)
    (r,) = [x for x in out["elements"]["results"] if not x.get("auto")]
    assert not r["pass"]


# ------------------------------------------- 7. spend cap

@pytest.fixture
def capenv(monkeypatch, tmp_path):
    monkeypatch.delenv(spend.LEDGER_ENV, raising=False)
    monkeypatch.setenv(spend.CAP_ENV, "1")
    spend.reset()
    yield monkeypatch
    spend.reset()


def _provider():
    return providers.Provider(name="x", type="openai", base_url="http://127.0.0.1:9/v1",
                              prices={"m": {"input": 10.0, "output": 100.0}})


def test_first_billed_error_without_usage_is_charged_at_the_estimate(capenv):
    capenv.setattr(api, "stream_chat", lambda *a, **k: (_ for _ in ()).throw(api.StreamStalled("stall")))
    msgs = [{"role": "user", "content": "x" * 3000}]
    with pytest.raises(api.StreamStalled):
        _provider().chat("m", msgs, max_tokens=2000)
    # (1000+64) tokens x $10/M + 2000 x $100/M = ~0.21: a fresh run is not free
    assert spend.spent() == pytest.approx(0.21064, abs=1e-3)


def test_a_reply_without_usage_is_charged_at_the_estimate(capenv):
    capenv.setattr(api, "stream_chat",
                   lambda *a, **k: SimpleNamespace(prompt_tokens=None, completion_tokens=None,
                                                   response_text="ok", finish_reason="stop"))
    _provider().chat("m", [{"role": "user", "content": "hi"}], max_tokens=1000)
    assert spend.spent() > 0.09


def test_two_processes_cannot_both_pass_the_same_stale_total(tmp_path):
    ledger, go = tmp_path / "ledger.jsonl", tmp_path / "go"
    code = textwrap.dedent("""
        import sys, time, os
        from crucibleforge import spend
        ok = 0
        for _ in range(10):
            try:
                spend.reserve("openai", "m", 0.4); ok += 1
            except spend.SpendCapExceeded:
                pass
        print(ok, flush=True)
        while not os.path.exists(sys.argv[1]):
            time.sleep(0.05)
    """)
    env = {**os.environ, spend.CAP_ENV: "1", spend.LEDGER_ENV: str(ledger)}
    procs = [subprocess.Popen([sys.executable, "-c", code, str(go)], env=env, stdout=subprocess.PIPE,
                              text=True) for _ in range(2)]
    try:
        got = [int(p.stdout.readline()) for p in procs]
    finally:
        go.write_text("x", encoding="utf-8")
        for p in procs:
            p.wait(timeout=30)
    # 0.4 each: the INCOMING estimate counts (fix pass 3), so only 0.4 + 0.4 fit under 1.0
    assert sum(got) == 2


# ------------------------------------------- 10. diagnostic checks

def test_disabled_checks_neither_multiply_nor_score():
    r = {"judge": {"elements": {"results": [{"id": "a", "pass": True, "credit": 1.0}]}},
         "checks": {"results": [{"id": "td", "pass": False, "multiplier": 0.5, "scored": False},
                                {"id": "k", "pass": False, "weight": 0}]}}
    assert report.wc_credit(r)["C"] == 1.0 and report.wc_credit(r)["multiplier"] == 1.0
    case = {"id": "S", "turns": ["u"], "checks": [
        {"id": "good", "type": "require_regex", "pattern": "hello"},
        {"id": "diag", "type": "require_regex", "pattern": "zzz", "scored": False},
        {"id": "zero", "type": "require_regex", "pattern": "zzz", "weight": 0}]}
    out = session_checks.run_checks(case, ["hello there"])
    assert out["rate"] == 1.0


# ------------------------------------------- 11. grading-only fields

def test_v2_and_ooc_turns_are_grading_keys_and_summary_loads_from_the_case(monkeypatch, tmp_path):
    assert {"v2", "ooc_turns", "judge_card_summary"} <= set(version._GRADING_KEYS)
    base = {"id": "Z", "category": "nsfw", "prompt": "p"}
    h = []
    for extra in ({}, {"v2": True, "ooc_turns": [3]}):
        f = tmp_path / "x.json"
        f.write_text(json.dumps([{**base, **extra}]), encoding="utf-8")
        d = hashlib.sha256()
        version._hash_case_file(d, f)
        h.append(d.hexdigest())
    assert h[0] == h[1]
    monkeypatch.undo()                                    # the autouse patch of case_field
    monkeypatch.setattr(judge, "_CASE_ELEMENTS", {"Z": []})
    monkeypatch.setattr(judge, "_CASE_FIELDS", {"Z": {k: "SUMMARY!" for k in ("judge_card_summary",)}})
    assert judge._row_summary({"case_id": "Z"}) == "SUMMARY!"
    assert "judge_card_summary" in judge._CASE_FIELD_KEYS
