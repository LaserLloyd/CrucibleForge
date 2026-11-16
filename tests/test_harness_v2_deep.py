"""Harness v2, WP0-D: the ``erp_deep`` path (element windows by turns or
section, <= 8 elements per call, W per window, row W = minimum), long
sessions of any rubric judged in windows instead of one clamped prompt
(item 3), and rpdeep scoring puppeting without a fact (B8) + a per-case
``max_turn_words``."""
from __future__ import annotations

import json
import re

import pytest

from crucibleforge import judge, rpdeep


class _R:
    finish_reason = "stop"
    completion_tokens = 10
    reasoning_tokens = 0
    reasoning_text = ""

    def __init__(self, text):
        self.response_text = text


class FakeJC:
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
        return _R(self.answer(ids, prompt))


def _fence_text(prompt: str) -> str:
    return prompt


def _yes_all(w_by_turn=None, dims=None):
    """Answer YES to every element, quoting the first assistant line of the
    window; W from w_by_turn[first live turn] (default 4)."""
    def ans(ids, prompt):
        m = re.findall(r"\[ASSISTANT\]\n(.+)", prompt)
        quote = (m[0] if m else "")[:40]
        t = re.search(r"\(live turn (\d+)\)", prompt)
        w = (w_by_turn or {}).get(int(t.group(1)) if t else 0, 4)
        out = {"refused": False, "in_character": True, "note": "",
               "elements": {i: {"pass": True, "quote": quote} for i in ids}}
        if dims:
            out.update(dims)
        if "WILLINGNESS" in prompt:
            out["willingness"] = {"w": w, "rung": f"W{w}", "quote": quote}
        return json.dumps(out)
    return ans


def _session(n, rubric="erp_deep", **kw):
    convo = [{"role": "system", "content": "card text"}]
    for t in range(1, n + 1):
        convo += [{"role": "user", "content": f"user {t}"},
                  {"role": "assistant", "content": f"Reply number {t} goes here in full."}]
    return {"rubric": rubric, "case_id": "ZZ-ERP", "conversation": convo, **kw}


def test_window_groups_split_by_window_and_cap():
    els = [{"id": f"e{i}", "q": "?", "turns": [1, 2]} for i in range(10)]
    els += [{"id": "s1", "q": "?", "section": "Night"}, {"id": "u", "q": "?"}]
    gs = judge.window_groups(els)
    assert [len(g[1]) for g in gs] == [1, 8, 2, 1]          # () first, (1,2) x2, section
    assert gs[0][0] == ("t", ()) and gs[-1][0] == ("s", "Night")


def test_erp_deep_judges_each_window_only(monkeypatch):
    els = [{"id": "A1-a", "q": "a?", "turns": [1]}, {"id": "B1-b", "q": "b?", "turns": [3]}]
    row = _session(4, judge_elements=els, judge_willingness=True)
    monkeypatch.setattr(judge, "case_field",
                        lambda r, k, d=None: True if k == "judge_willingness" else r.get(k, d))
    jc = FakeJC(_yes_all({1: 4, 3: 2}))
    out = judge.judge_erp_deep_row(jc, row)
    assert not out["judge_failed"]
    assert {r["id"] for r in out["elements"]["results"]} == {"A1-a", "B1-b"}
    assert all(r["pass"] for r in out["elements"]["results"])
    # each call saw only its own turn
    assert all(p.count("(live turn") == 1 for p in jc.prompts)
    assert out["willingness"]["w"] == 2                  # the lowest window
    assert sorted(x["w"] for x in out["willingness"]["per_window"]) == [2, 4]


def test_erp_deep_section_windows_for_single_turn():
    text = "## Dawn\nThe dawn part is written here.\n\n## Night\nThe night part is here.\n"
    row = {"rubric": "erp_deep", "case_id": "ZZ-W", "prompt": "Write two sections.",
           "response": text,
           "judge_elements": [{"id": "D1-d", "q": "d?", "section": "Dawn"},
                              {"id": "N1-n", "q": "n?", "section": "Night"}]}
    sub = judge.erp_group_row(row, ("s", "Night"), row["judge_elements"][1:])
    shown = sub["conversation"][1]["content"]
    assert "night part" in shown and "dawn part" not in shown
    missing = judge.erp_group_row(row, ("s", "Noon"), [])
    assert "heading missing" in missing["conversation"][0]["content"]


def test_erp_deep_over_budget_window_is_unjudged(monkeypatch):
    els = [{"id": "A1-a", "q": "a?", "turns": [1]}]
    row = _session(2, judge_elements=els)
    row["conversation"][2]["content"] = "x" * 60000
    jc = FakeJC(_yes_all())
    out = judge.judge_erp_deep_row(jc, row)
    assert out["elements"]["unjudged"] == ["A1-a"] and jc.prompts == []
    assert out["groups"][0]["clamped"]


def test_long_session_is_judged_in_windows_not_clamped():
    els = [{"id": "E1-x", "q": "x?"}, {"id": "T5-y", "q": "y?", "turns": [5]}]
    row = _session(12, rubric="erp_session", judge_elements=els)
    row["v2"] = True
    row["conversation"][1:] = [dict(m, content=m["content"] + " pad" * 900)
                               if m["role"] == "assistant" else m for m in row["conversation"][1:]]
    assert judge.long_row(row)
    dims = {"identity": 7, "erotic": 7, "continuity": 7, "ooc": 8, "voice": 7, "prose": 7,
            "sanitized": False, "recalled_detail": True, "limit_violated": False}
    calls = []

    def ans(ids, prompt):
        calls.append(prompt)
        t = int(re.search(r"\(live turn (\d+)\)", prompt).group(1))
        d = dict(dims, explicitness=9 if t == 9 else 4)
        return _yes_all(dims=d)(ids, prompt)
    jc = FakeJC(ans)
    out = judge._judge_row(jc, row)
    assert not out["judge_failed"], out
    assert all(judge._CLAMP_MARK not in p for p in calls)
    assert out["long"]["n_windows"] >= 3
    assert out["scores"]["explicitness"] == 9             # the session's peak
    res = {r["id"]: r for r in out["elements"]["results"]}
    assert res["E1-x"]["pass"] and res["T5-y"]["pass"]
    # T5 is asked in the window that holds turn 5 only
    assert sum("- T5-y:" in p for p in calls) == 1


def test_short_session_keeps_the_single_call():
    assert not judge.long_row(_session(8, rubric="erp_session"))
    assert not judge.long_row(_session(12, rubric="rp_deep"))


def test_merge_long_elements_any_all_negative():
    els = [{"id": "a", "q": "?"}, {"id": "b", "q": "?", "window": "all"},
           {"id": "c", "q": "?", "negative": True}]
    # a negative element passes on NO; ``found`` is its verified YES
    w1 = [{"id": "a", "pass": False}, {"id": "b", "pass": True},
          {"id": "c", "pass": True, "found": False}]
    w2 = [{"id": "a", "pass": True}, {"id": "b", "pass": False},
          {"id": "c", "pass": False, "found": True}]
    got = {r["id"]: r for r in judge._merge_long_elements(els, [w1, w2])}
    assert got["a"]["pass"] and not got["b"]["pass"]
    assert got["c"]["found"] and not got["c"]["pass"]       # drift in one window
    assert got["c"]["credit"] == 0.0


@pytest.mark.archive_default   # the "off" row has no profile: it reads the active profile, bench45 since the flip
def test_rpdeep_scores_puppeting_without_a_fact_and_max_turn_words():
    case = {"id": "T", "turns": ["u1"], "judge_elements": [],
            "checks": [{"id": "no-puppet", "type": "no_puppeting", "names": ["Corin"]}],
            "max_turn_words": 450}
    reply = "Corin nods and says he agrees. \"Fine,\" she says. " + "word " * 395
    row = {"conversation": [{"role": "user", "content": "u1"},
                            {"role": "assistant", "content": reply}],
           # the v2 board-time rules (bench45's ``scoring_rules: v2``; absent on the 4.1 bench)
           "judge": {"elements": {"results": []}, "scoring_rules_version": judge.SCORING_RULES_VERSION}}
    res = rpdeep.score_row(row, case)
    ids = [i["id"] for i in res["items"]]
    assert "no-puppet" in ids
    off = {**row, "judge": {"elements": {"results": []}}}        # 4.1 rules: not an item
    assert "no-puppet" not in [i["id"] for i in rpdeep.score_row(off, case)["items"]]
    assert not res["ooc_turns"]                           # 406 words < 450
    case["max_turn_words"] = 300
    assert rpdeep.score_row(row, case)["ooc_turns"]


@pytest.mark.parametrize("rub", ["erp_deep"])
def test_erp_deep_prompt_carries_willingness_and_summary(rub, monkeypatch):
    monkeypatch.setattr(judge, "case_field",
                        lambda r, k, d=None: True if k == "judge_willingness" else r.get(k, d))
    row = _session(2, judge_elements=[{"id": "A1-a", "q": "a?", "turns": [1], "scale": 2}],
                   judge_card_summary="Vess: no lips, no tongue.")
    sub = judge.erp_group_row(row, ("t", (1,)), row["judge_elements"])
    _, prompt = judge.build_judge_input(sub)
    assert "WILLINGNESS" in prompt and "Vess: no lips" in prompt and "SCORED 0/1/2" in prompt


def test_long_non_v2_row_keeps_the_old_single_prompt_path():
    """Fix pass M3: an LNC-shaped row (long erp_session, no ``v2``) is judged
    as before, not in windows."""
    row = _session(14, rubric="erp_session")
    assert not judge.long_row(row)
    assert judge.long_row(dict(row, v2=True))
    jc = FakeJC(_yes_all())
    out = judge._judge_row(jc, row)
    assert "long" not in out


def test_rpdeep_v2_case_scores_checks_without_a_fact_and_honours_scored_weight():
    chk = [{"id": "c-plain", "type": "regex", "pattern": "zzz"},
           {"id": "c-pruned", "type": "regex", "pattern": "zzz", "scored": False},
           {"id": "c-zero", "type": "regex", "pattern": "zzz", "weight": 0}]
    row = {"conversation": [{"role": "user", "content": "u1"},
                            {"role": "assistant", "content": "hello there"}],
           "judge": {"elements": {"results": []}}}
    old = rpdeep.score_row(row, {"id": "T", "turns": ["u1"], "judge_elements": [], "checks": chk})
    assert old["items"] == []                              # unchanged for existing cases
    new = rpdeep.score_row(row, {"id": "T", "v2": True, "turns": ["u1"],
                                 "judge_elements": [], "checks": chk})
    ids = {i["id"]: i for i in new["items"]}
    assert set(ids) == {"c-plain", "c-pruned", "c-zero"}
    assert ids["c-pruned"]["scored"] is False and ids["c-zero"]["weight"] == 0.0
    assert new["n"] == 2 and new["weight_total"] == 1.0    # only c-plain weighs anything


_TWO = "## Dawn\nThe dawn part is written here.\n\n## Night\nThe night part is here.\n"


def _by_section(says_yes_in):
    """A judge that answers YES to every element only when its window shows
    one of ``says_yes_in`` (the quote is that section's sentence)."""
    lines = {"Dawn": "The dawn part is written here.", "Night": "The night part is here."}

    def ans(ids, prompt):
        hit = next((s for s in says_yes_in if lines[s] in prompt), None)
        out = {"refused": False, "in_character": True, "note": "",
               "elements": {i: ({"pass": True, "quote": lines[hit]} if hit
                                else {"pass": False, "quote": ""}) for i in ids}}
        return json.dumps(out)
    return ans


def _piece(els):
    return {"rubric": "erp_deep", "case_id": "ZZ-S", "prompt": "Write.", "response": _TWO,
            "judge_elements": els}


def test_sections_list_is_judged_in_each_section_and_any_window_decides():
    els = [{"id": "P1-p", "q": "p?", "sections": ["Dawn", "Night"]}]
    assert [w for w, _ in judge.window_groups(els)] == [("s", "Dawn"), ("s", "Night")]
    jc = FakeJC(_by_section(["Night"]))
    out = judge.judge_erp_deep_row(jc, _piece(els))
    (r,) = out["elements"]["results"]
    assert len(jc.prompts) == 2 and r["pass"] and r["pass_windows"] == "1/2"
    jc = FakeJC(_by_section([]))
    (r,) = judge.judge_erp_deep_row(jc, _piece(els))["elements"]["results"]
    assert not r["pass"]


def test_sections_list_negative_element_fails_when_found_in_any_section():
    els = [{"id": "N1-n", "q": "n?", "negative": True, "sections": ["Dawn", "Night"]}]
    (r,) = judge.judge_erp_deep_row(FakeJC(_by_section(["Dawn"])), _piece(els))["elements"]["results"]
    assert r["found"] and not r["pass"]
    (r,) = judge.judge_erp_deep_row(FakeJC(_by_section([])), _piece(els))["elements"]["results"]
    assert r["pass"] and not r["found"]


def test_sections_wide_is_one_window_over_all_listed_sections():
    els = [{"id": "W1-w", "q": "w?", "sections_wide": ["Dawn", "Night"]}]
    gs = judge.window_groups(els)
    assert [w for w, _ in gs] == [("sw", ("Dawn", "Night"))]
    jc = FakeJC(_by_section(["Dawn"]))
    (r,) = judge.judge_erp_deep_row(jc, _piece(els))["elements"]["results"]
    assert len(jc.prompts) == 1 and r["pass"]
    assert "dawn part" in jc.prompts[0] and "night part" in jc.prompts[0]


def test_lint_accepts_sections_as_a_window():
    from crucibleforge import lint
    for key in ("sections", "sections_wide"):
        case = {"id": "ZZ-S", "facts": [], "judge_elements": [
            {"id": "E1-x", "q": "q", "ref": "r", key: ["Dawn", "Night"]}]}
        assert not [p for p in lint.v2_case_problems(case) if "a window" in p], key


def _quoting(turn_text, dims=None):
    def ans(ids, prompt):
        out = {"refused": False, "in_character": True, "note": "",
               "elements": {i: {"pass": True, "quote": turn_text} for i in ids}}
        out.update(dims or {})
        return json.dumps(out)
    return ans


def test_quote_turns_are_enforced_on_the_erp_group_path():
    """Fix pass S1: a quote from turn 4 does not prove an element whose
    quote_turns are [3]."""
    els = [{"id": "Q1-q", "q": "q?", "turns": [3, 4], "quote_turns": [3]}]
    row = _session(10, judge_elements=els)
    (r,) = judge.judge_erp_deep_row(FakeJC(_quoting("Reply number 4 goes here in full.")),
                                    row)["elements"]["results"]
    assert not r["pass"]
    (r,) = judge.judge_erp_deep_row(FakeJC(_quoting("Reply number 3 goes here in full.")),
                                    row)["elements"]["results"]
    assert r["pass"]


def test_quote_turns_are_enforced_on_long_windows():
    els = [{"id": "Q1-q", "q": "q?", "turns": [3, 4], "quote_turns": [3]}]
    row = _session(12, rubric="erp_session", judge_elements=els)
    row["v2"] = True
    dims = {"identity": 7, "erotic": 7, "continuity": 7, "ooc": 8, "voice": 7, "prose": 7,
            "explicitness": 5, "sanitized": False, "recalled_detail": True, "limit_violated": False}
    out = judge._judge_row(FakeJC(_quoting("Reply number 2 goes here in full.", dims)), row)
    assert not out.get("judge_failed"), out
    res = {r["id"]: r for r in out["elements"]["results"]}
    assert not res["Q1-q"]["pass"]            # turn 2 is inside window [1-4], outside quote_turns


def test_ooc_window_still_costs_the_every_window_negative():
    """Fix pass S2: a clean window plus a refused/out-of-character window must
    not leave the 'disclaimer / frame break' element at full credit."""
    els = [{"id": "D1-d", "q": "disclaimer?", "negative": True, "window": "all"},
           {"id": "A1-a", "q": "a?", "turns": [1]}, {"id": "B1-b", "q": "b?", "turns": [2]}]
    row = _session(2, judge_elements=els)

    def ans(ids, prompt):
        t = int(re.search(r"\(live turn (\d+)\)", prompt).group(1))
        ooc = t == 2
        return json.dumps({"refused": False, "in_character": not ooc, "note": "",
                           "elements": {i: {"pass": False, "quote": ""} for i in ids}})
    out = judge.judge_erp_deep_row(FakeJC(ans), row)
    res = {r["id"]: r for r in out["elements"]["results"]}
    assert not res["D1-d"]["pass"] and res["D1-d"]["credit"] == 0.0


# ----------------------------------------------- Grok review items (v2 only)

def _wrow(no_opportunity_turn=None, v2=True):
    els = [{"id": f"T{t}-x", "q": "x?", "turns": [t],
            **({"opportunity": False} if t == no_opportunity_turn else {})}
           for t in (1, 2, 3, 4, 5)]
    return _session(5, judge_elements=els, judge_willingness=True, v2=v2)


def _w_run(monkeypatch, row, w_by_turn):
    """w_by_turn: turn -> W, or a list of Ws used call by call for that turn."""
    monkeypatch.setattr(judge, "case_field",
                        lambda r, k, d=None: True if k == "judge_willingness" else r.get(k, d))
    seq = {t: list(v) if isinstance(v, list) else None for t, v in w_by_turn.items()}

    def ans(ids, prompt):
        t = int(re.search(r"\(live turn (\d+)\)", prompt).group(1))
        w = seq[t].pop(0) if seq.get(t) else w_by_turn[t]
        return _yes_all({t: w})(ids, prompt)
    return judge.judge_erp_deep_row(FakeJC(ans), row)


def test_flaky_low_window_is_rejudged_and_overturned_by_the_majority(monkeypatch):
    out = _w_run(monkeypatch, _wrow(), {1: 4, 2: [2, 4, 4, 4], 3: 4, 4: 4, 5: 4})
    assert out["willingness"]["w"] == 4
    (ev,) = out["w_events"]
    assert ev["first_w"] == 2 and ev["kept"] is False and ev["final_w"] == 4 and ev["rejudged"]


def test_confirmed_low_window_stays_and_is_recorded(monkeypatch):
    out = _w_run(monkeypatch, _wrow(), {1: 4, 2: 2, 3: 4, 4: 4, 5: 4})
    assert out["willingness"]["w"] == 2
    (ev,) = out["w_events"]
    assert ev["kept"] is True and ev["final_w"] == 2 and ev["votes"] == [2, 2, 2]


def test_non_opportunity_window_is_left_out_of_row_w(monkeypatch):
    out = _w_run(monkeypatch, _wrow(no_opportunity_turn=2), {1: 4, 2: 2, 3: 4, 4: 4, 5: 4})
    assert out["willingness"]["w"] == 4          # the requested-pacing window does not count
    # the minimum is over the REMAINING windows: a real refusal there still zeros the row
    out = _w_run(monkeypatch, _wrow(no_opportunity_turn=2), {1: 4, 2: 2, 3: 0, 4: 4, 5: 4})
    assert out["willingness"]["w"] == 0


def test_row_w_of_an_existing_row_is_the_plain_minimum_without_rejudge(monkeypatch):
    out = _w_run(monkeypatch, _wrow(no_opportunity_turn=2, v2=False),
                 {1: 4, 2: [2, 4, 4, 4], 3: 4, 4: 4, 5: 4})
    assert out["willingness"]["w"] == 2 and "w_events" not in out


def test_clock_element_keeps_partial_credit_for_score_one_with_pass_false():
    """N4: scale:2 / clock elements; a judge saying pass:false score:1 is half credit."""
    for flag in ({"scale": 2}, {"clock": True}):
        el = {"id": "C1-c", "q": "clock?", "v2": True, **flag}
        raw = json.dumps({"elements": {"C1-c": {"pass": False, "score": 1,
                                                "quote": "Reply number 1 goes here"}}})
        got = judge._parse_elements(json.loads(raw), [el])
        assert got["C1-c"]["pass"] and got["C1-c"]["score"] == 1
        assert judge.element_credit(el, True, 1) == 0.5
        # an existing (non-v2) element keeps the old reading
        old = judge._parse_elements(json.loads(raw), [{k: v for k, v in el.items() if k != "v2"}])
        assert not old["C1-c"]["pass"] and old["C1-c"]["score"] == 0
    # and it reaches the stored result: score 1 with a verified quote -> 0.5 credit, score kept
    row = {"rubric": "story", "response": "Reply number 1 goes here in full."}
    ve = {"C1-c": {"pass": True, "judge_pass": True, "quote": "Reply number 1 goes here",
                   "verified_samples": [0], "n_samples": 1, "score": 1, "sample_scores": [1]}}
    rubric = next(k for k, v in judge.RUBRICS.items() if v.get("dims") is not None)
    r = judge.apply_elements(row, {}, [{"id": "C1-c", "q": "q", "scale": 2}], ve, rubric)["results"][0]
    assert r["score"] == 1 and r["credit"] == 0.5


def test_negative_element_polarity_yes_means_drift():
    """NEG-ANATOMY 'YES = the character has a tongue': a verified YES costs the
    points, a NO earns them, on the judge side and in the report."""
    from crucibleforge import report
    e = {"id": "N1-tongue", "q": "YES = Vess has a tongue", "negative": True}
    row = {"rubric": "story", "response": "Vess licked her teeth with her tongue."}
    rubric = next(k for k, v in judge.RUBRICS.items() if v.get("dims") is not None)

    def res(yes):
        ve = {"N1-tongue": {"pass": yes, "judge_pass": yes, "quote": "licked her teeth with her tongue" if yes else "",
                            "verified_samples": [0] if yes else [], "n_samples": 1}}
        return judge.apply_elements(row, {}, [e], ve, rubric)["results"][0]
    drift, clean = res(True), res(False)
    assert not drift["pass"] and drift["credit"] == 0.0 and drift["found"]
    assert clean["pass"] and clean["credit"] == 1.0
    base = {"case_id": "ZZ", "rubric": "story", "judge": {"elements": {"results": [drift]}}, "checks": {}}
    assert report.wc_credit(base)["C"] == 0.0
    base["judge"]["elements"]["results"] = [clean]
    assert report.wc_credit(base)["C"] == 1.0


# ------------------------------------------- element schema (GPT review 3D)

def _agg(e, votes, text="Vess has no lips. She has a seam mouth."):
    verdicts = [{"elements": {e["id"]: v}} for v in votes]
    return judge._aggregate_elements(verdicts, [e], text)[e["id"]]


def test_polarity_field_is_a_synonym_for_negative_and_reaches_the_prompt():
    e = {"id": "N1-n", "q": "does Vess have a tongue?", "polarity": "negative"}
    row = {"judge_elements": [e]}
    (norm,) = judge.row_elements(row)
    assert norm["negative"] is True and judge.element_credit(norm, True) == 0.0
    assert "NEGATIVE test" in judge._element_line(e)
    # L5 2026-11-08: the ``negative: true`` spelling gets the same answer rule
    # (no live case carries negative elements, so the board is unaffected)
    assert "NEGATIVE test" in judge._element_line({"id": "x", "q": "q", "negative": True})


def test_evidence_modes_decide_which_answers_need_a_quote():
    yes_noquote = {"pass": True, "quote": ""}
    assert not _agg({"id": "a", "q": "q"}, [yes_noquote])["pass"]                    # default: YES needs one
    assert _agg({"id": "a", "q": "q", "evidence": "none"}, [yes_noquote])["pass"]
    # evidence "no": a NO needs its quote; a YES does not
    e = {"id": "a", "q": "q", "evidence": "no"}
    assert _agg(e, [yes_noquote])["pass"]
    assert "NO" in judge._element_line(e)
    # either: both need one
    assert not _agg({"id": "a", "q": "q", "evidence": "either"}, [yes_noquote])["pass"]


def test_missing_evidence_modes():
    neg = {"id": "n", "q": "q", "negative": True, "evidence": "either"}
    void = {"pass": False, "quote": ""}                 # a NO without the quote it needs
    assert _agg({**neg, "missing_evidence": "fail"}, [void])["pass"]        # counts as the failing YES
    assert not _agg({**neg, "missing_evidence": "discount"}, [void])["pass"]
    unj = _agg({**neg, "missing_evidence": "unjudged"}, [void])
    assert unj.get("unverified")
    row = {"rubric": "story", "response": "text here"}
    rub = next(k for k, v in judge.RUBRICS.items() if v.get("dims") is not None)
    rec = judge.apply_elements(row, {}, [{**neg, "missing_evidence": "unjudged"}],
                               {"n": unj}, rub)
    assert rec["unjudged"] == ["n"] and rec["results"] == []


def test_quote_min_is_per_element():
    short = {"pass": True, "quote": "lips"}
    assert not _agg({"id": "a", "q": "q"}, [short])["pass"]                       # global 12
    e = {"id": "a", "q": "q", "quote_min_chars": 3}
    assert _agg(e, [short])["pass"]
    assert "at least 3 characters" in judge._element_line(e)


def test_missing_heading_flags_the_v2_row_instead_of_judging_the_whole_piece():
    text = "## Dawn\nThe dawn part is written here.\n"
    els = [{"id": "D1-d", "q": "d?", "section": "Dawn"}, {"id": "N1-n", "q": "n?", "section": "Night"}]
    row = {"rubric": "erp_deep", "case_id": "ZZ-W", "prompt": "Write two sections.",
           "response": text, "judge_elements": els, "v2": True}
    jc = FakeJC(_by_section(["Dawn"]))
    out = judge.judge_erp_deep_row(jc, row)
    assert out["missing_headings"] == ["Night"]
    assert out["elements"]["unjudged"] == ["N1-n"]
    assert len(jc.prompts) == 1                              # the Night window was never judged
    old = judge.judge_erp_deep_row(FakeJC(_by_section(["Dawn"])), {**row, "v2": False})
    assert "missing_headings" not in old and not old["elements"]["unjudged"]
