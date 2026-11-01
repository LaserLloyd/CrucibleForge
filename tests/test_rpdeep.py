"""rpdeep (deep-lore RP, suite 3.9 candidate): prior chat log injection, the
per-fact-group judge path (budget, quote guard scoped to the graded turns,
unjudged vs empty), and the report guard."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from crucibleforge import judge, report, runner
from crucibleforge.config import load_cases

ROOT = Path(__file__).resolve().parents[1]


def _cases():
    return load_cases(["rpdeep"])


def test_build_is_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_rpdeep.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "cases/rpdeep.json is stale: run scripts/build_rpdeep.py"


def test_history_alternates_and_ends_on_assistant():
    # 3.9 step 6: a short session may start cold (no earlier conversation)
    for c in _cases():
        h = c["history"]
        if not h:
            assert c["id"] in ("RS1-lantern-ferry", "RV1-three-at-the-table", "RD4-kettle-saddle",
                               "RV2-skarren-hut", "WR1-ninth-string", "WR2-red-thread-key",
                               "LRV1-pilots-mess", "LRV2-brannock-reading",
                               "LWR1-skerrow-bible", "LWR2-neap-seam", "LWR3-twelve-days"), c["id"]
            continue
        assert h[-1]["role"] == "assistant"
        for i, m in enumerate(h):
            assert m["role"] == ("user" if i % 2 == 0 else "assistant"), (c["id"], i)


def test_history_injected_between_card_and_live_turns():
    case = {"system": "CARD", "history": [{"role": "user", "content": "u0"},
                                          {"role": "assistant", "content": "a0"}]}
    msgs = runner._messages_for(case, [{"role": "user", "content": "live1"}])
    assert [m["content"] for m in msgs] == ["CARD", "u0", "a0", "live1"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]


def test_every_element_has_turns_evidence_and_ftype():
    for c in _cases():
        assert c["rubric"] == "rp_deep"
        for e in c["judge_elements"]:
            assert 1 <= len(e["turns"]) <= 2, e["id"]
            assert e["evidence"] and e["ftype"], e["id"]
        facts = [e["fact"] for e in c["judge_elements"]] + [
            x["fact"] for x in c["checks"] if x.get("fact")]
        assert len(facts) == len(set(facts)), "one grader per fact"


def _fake_row(case, reply="x"):
    convo = [{"role": "system", "content": case["system"]}]
    for t in case["turns"]:
        convo += [{"role": "user", "content": t}, {"role": "assistant", "content": reply}]
    return {"rubric": "rp_deep", "case_id": case["id"], "category": "rpdeep",
            "judge_key": case["judge_key"], "conversation": convo,
            "judge_elements": case["judge_elements"]}


def test_every_group_fits_the_16k_judge_with_runaway_replies():
    budget = judge.judge_input_budget_chars(16384, 8192)
    for c in _cases():
        row = _fake_row(c, reply="word " * 1600)          # 8,000 chars per reply
        for turns, els in judge.deep_groups(c["judge_elements"]):
            sub = judge.deep_group_row(row, turns, els)
            _, prompt = judge.build_judge_input(sub, budget)
            assert len(judge.JUDGE_SYSTEM) + len(prompt) <= budget, (c["id"], turns)
            # 3.10 ladder pack: LWR1's J1 reads two story sections; only an
            # 8,000-char runaway in BOTH clamps it (real sections are ~400 words)
            if (c["id"], turns) == ("LWR1-skerrow-bible", (1, 2)):
                continue
            assert judge._CLAMP_MARK not in prompt, (c["id"], turns)


class _R:
    finish_reason = "stop"
    completion_tokens = 1
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
        self.answer = answer        # fn(element ids, user prompt) -> raw text

    def chat(self, messages, **kw):
        prompt = messages[1]["content"]
        ids = re.findall(r"^- ([A-Za-z0-9]+-[\w-]+): ", prompt, flags=re.M)
        return _R(self.answer(ids, prompt))


def _mini_case():
    return {"id": "T", "system": "card", "judge_key": "k",
            "turns": ["u1", "u2"],
            "judge_elements": [
                {"id": "A1-x", "fact": "A1", "ftype": "applied", "turns": [1], "q": "q1?",
                 "evidence": [{"src": "card", "text": "e1"}]},
                {"id": "B1-y", "fact": "B1", "ftype": "direct", "turns": [2], "q": "q2?",
                 "evidence": [{"src": "card", "text": "e2"}]}]}


def _row(case, replies):
    convo = [{"role": "system", "content": "card"}]
    for t, r in zip(case["turns"], replies):
        convo += [{"role": "user", "content": t}, {"role": "assistant", "content": r}]
    return {"rubric": "rp_deep", "case_id": "T", "judge_key": "k", "conversation": convo,
            "judge_elements": case["judge_elements"], "judge_card_summary": "s"}


T1 = "She slid the cassette across the desk without a word."
T2 = "The house on Conti Street was where she grew up."


def _answer_quoting(quote_for):
    def ans(ids, prompt):
        if not ids:   # craft pass
            return json.dumps({"voice": 6, "craft": 6, "initiative": 6, "refused": False,
                               "note": ""})
        return json.dumps({"refused": False, "in_character": True, "note": "",
                           "elements": {i: {"pass": True, "quote": quote_for[i]} for i in ids}})
    return ans


def test_quote_from_another_turn_is_no():
    case = _mini_case()
    row = _row(case, [T1, T2])
    # B1 (graded on turn 2) quotes turn 1's line: must fail the quote guard
    jc = FakeJC(_answer_quoting({"A1-x": T1, "B1-y": T1}))
    v = judge.judge_deep_row(jc, row, samples=1)
    res = {r["id"]: r for r in v["elements"]["results"]}
    assert res["A1-x"]["pass"] is True
    assert res["B1-y"]["pass"] is False and res["B1-y"]["judge_pass"] is True


def test_group_parse_failure_is_unjudged_not_row_failure():
    case = _mini_case()
    row = _row(case, [T1, T2])

    def ans(ids, prompt):
        if ids == ["B1-y"]:
            return "not json at all"
        return _answer_quoting({"A1-x": T1})(ids, prompt)
    v = judge.judge_deep_row(FakeJC(ans), row, samples=1)
    assert v["judge_failed"] is False
    assert v["elements"]["unjudged"] == ["B1-y"]
    assert [r["id"] for r in v["elements"]["results"]] == ["A1-x"]


def test_empty_graded_reply_fails_its_items():
    case = _mini_case()
    row = _row(case, [T1, ""])
    v = judge.judge_deep_row(FakeJC(_answer_quoting({"A1-x": T1})), row, samples=1)
    res = {r["id"]: r for r in v["elements"]["results"]}
    assert res["B1-y"]["pass"] is False and "empty" in res["B1-y"]["detail"]
    assert v["elements"]["unjudged"] == []


def test_group_row_carries_only_graded_turns_and_evidence():
    case = _mini_case()
    row = _row(case, [T1, T2])
    sub = judge.deep_group_row(row, (2,), case["judge_elements"][1:])
    assert judge.assistant_text(sub) == T2
    assert "e2" in sub["judge_key"] and "e1" not in sub["judge_key"]


def test_rpdeep_never_reaches_the_generic_judged_path():
    # a dim-less rubric would score a refused row 1.0 there
    assert "rpdeep" in report._BUILTIN_JUDGED
    assert judge.RUBRICS["rp_deep"]["dims"] == []


def test_a_group_sub_row_is_not_dispatched_again():
    """2026-10-25: sub-rows kept rubric rp_deep, so judge_row sent each one back
    to judge_deep_row - unbounded recursion, a thread pool per level, 23 GB."""
    case = _mini_case()
    sub = judge.deep_group_row(_row(case, [T1, T2]), (1,), case["judge_elements"][:1])
    assert sub["deep_group"] is True
    calls = []
    real = judge.judge_deep_row
    try:
        judge.judge_deep_row = lambda *a, **k: calls.append(1) or real(*a, **k)
        judge.judge_row(FakeJC(_answer_quoting({"A1-x": T1})), sub, samples=1)
    finally:
        judge.judge_deep_row = real
    assert calls == []


def test_out_of_character_recital_fails_its_items():
    """A fact sheet ("As Marisol, here are the facts: - ...") must not score:
    the recital fixture passed 26/28 before the in-character gate."""
    case = _mini_case()
    row = _row(case, [T1, T2])

    def ans(ids, prompt):
        if not ids:
            return json.dumps({"voice": 2, "craft": 2, "initiative": 2, "refused": False,
                               "note": ""})
        q = {"A1-x": T1, "B1-y": T2}
        return json.dumps({"refused": False, "in_character": ids != ["B1-y"], "note": "",
                           "elements": {i: {"pass": True, "quote": q[i]} for i in ids}})
    v = judge.judge_deep_row(FakeJC(ans), row, samples=1)
    res = {r["id"]: r for r in v["elements"]["results"]}
    assert res["A1-x"]["pass"] is True
    assert res["B1-y"]["pass"] is False


def _score(row, case):
    sys.path.insert(0, str(ROOT / "scripts"))
    import rpdeep_score
    return rpdeep_score.score_row(row, case, [])


def test_scorer_never_scores_a_failed_or_partly_unjudged_row():
    case = next(c for c in _cases() if c["id"] == "RD1-marisol")
    row = _fake_row(case, reply="Conti house, Fuse Box Saints, Mackie 1604, eins zwei")
    row["judge"] = {"judge_failed": True, "scores": None, "refused": False}
    assert _score(row, case).get("rate") is None
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "elements": {"results": [], "unjudged": [e["id"] for e in
                                                             case["judge_elements"]]}}
    assert _score(row, case).get("rate") is None


def test_scorer_refused_row_scores_zero_including_regex():
    case = next(c for c in _cases() if c["id"] == "RD1-marisol")
    row = _fake_row(case, reply="I'm sorry, I can't continue this roleplay.")
    row["judge"] = {"judge_failed": False, "refused": True, "scores": {},
                    "elements": {"results": [], "unjudged": []}}
    assert _score(row, case)["passed"] == 0


def test_craft_prompt_clamping_is_recorded(monkeypatch):
    monkeypatch.setattr(judge, "DEEP_CRAFT", True)      # craft is opt-in since r3
    case = _mini_case()
    row = _row(case, [T1 * 400, T2 * 400])
    v = judge.judge_deep_row(FakeJC(_answer_quoting({"A1-x": T1, "B1-y": T2})), row, samples=1)
    # craft is judged in chunks; a chunk over budget is NOT judged clamped — it is
    # recorded as a craft error (review H4)
    assert any(m["clamped"] for m in v["craft_meta"])
    assert v["craft_error"] and v["craft_failed"] is True
    assert v["scores"] is None


# ---------------------------------------------------------------- review round 2
from crucibleforge import rpdeep  # noqa: E402

SPEECH = 'She slid the tape across. "Four songs, one copy. Guard it."'
RECITAL = "As Marisol, here are the facts:\n- I brought the tape.\n- I live in Tremont."


def test_ooc_gate_catches_fact_sheets_not_scenes():
    assert rpdeep.ooc_reason(RECITAL)
    assert rpdeep.ooc_reason("Facts:\n1. Conti house\n2. Fuse Box Saints")
    assert rpdeep.ooc_reason("She thought about the Conti house and said nothing at all.")
    assert rpdeep.ooc_reason("((OOC: sure!)) She nodded.")
    assert rpdeep.ooc_reason(SPEECH) is None
    assert rpdeep.ooc_reason("") is None          # empty is handled as missing


def test_ooc_turn_fails_regex_and_judge_facts_of_that_turn():
    case = next(c for c in _cases() if c["id"] == "RD1-marisol")
    good = 'She grinned. "The Conti house, kiddo. Fuse Box Saints. De nada, Schatz."'
    replies = [good] * len(case["turns"])
    replies[2] = "Facts:\n- the Conti house\n- Fuse Box Saints\n- De nada, Schatz"
    row = _fake_row(case)
    for i, m in enumerate([m for m in row["conversation"] if m["role"] == "assistant"]):
        m["content"] = replies[i]
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "elements": {"results": [{"id": e["id"], "pass": True, "quote": ""}
                                             for e in case["judge_elements"]], "unjudged": []}}
    sc = rpdeep.score_row(row, case, [])
    t3 = {i["id"]: i for i in sc["items"] if i["id"] in ("D1-conti-house", "D2-fuse-box-saints",
                                                         "H3-de-nada-schatz")}
    assert t3 and all(not i["pass"] and "not in character" in i["detail"] for i in t3.values())
    assert 3 in sc["ooc_turns"]


def test_weights_count_secret_phases_and_split_halves_half():
    case = next(c for c in _cases() if c["id"] == "RD1-marisol")
    w = {e.get("fact"): e.get("weight", 1) for e in case["judge_elements"]}
    w.update({c["fact"]: c.get("weight", 1) for c in case["checks"] if c.get("fact")})
    # 4.0 fix: items every model passed carry half weight again (0.5 -> 0.25)
    for k in ("X0", "X1", "X2", "X3", "I1a", "I1b"):
        assert w[k] in (0.5, 0.25), k
    row = _fake_row(case, reply=SPEECH)
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "elements": {"results": [{"id": e["id"], "pass": e.get("fact") == "X1",
                                              "quote": ""} for e in case["judge_elements"]],
                                 "unjudged": []}}
    sc = rpdeep.score_row(row, case, [])
    total = sum(i["weight"] for i in sc["items"] if i.get("scored", True))
    assert sc["weight_total"] == total


def test_source_half_needs_a_quote_from_her_spoken_words():
    case = _mini_case()
    case["judge_elements"][1]["quote_scope"] = "dialogue"
    narr = 'She remembered her father\'s shop. "Blue smoke means oil, kid."'
    row = _row(case, [T1, narr])
    jc = FakeJC(_answer_quoting({"A1-x": T1, "B1-y": "She remembered her father's shop."}))
    res = {r["id"]: r for r in judge.judge_deep_row(jc, row, samples=1)["elements"]["results"]}
    assert res["B1-y"]["pass"] is False and "spoken" in res["B1-y"]["detail"]
    jc = FakeJC(_answer_quoting({"A1-x": T1, "B1-y": "Blue smoke means oil, kid."}))
    res = {r["id"]: r for r in judge.judge_deep_row(jc, row, samples=1)["elements"]["results"]}
    assert res["B1-y"]["pass"] is True


def test_a_raising_group_is_retried_then_unjudged_not_a_row_failure():
    case = _mini_case()
    row = _row(case, [T1, T2])
    calls = {"n": 0}
    ok = _answer_quoting({"A1-x": T1, "B1-y": T2})

    class Boom(FakeJC):
        def chat(self, messages, **kw):
            if "- B1-y:" in messages[1]["content"]:
                calls["n"] += 1
                raise TimeoutError("row timeout")
            return super().chat(messages, **kw)
    v = judge.judge_deep_row(Boom(ok), row, samples=1)
    assert calls["n"] == 1 + judge.DEEP_GROUP_RETRIES
    assert v["judge_failed"] is False and v["elements"]["unjudged"] == ["B1-y"]
    assert v["judge_errors"]
    # and the scorer refuses to score that row
    sc = rpdeep.score_row({**row, "judge": v}, case, [])
    assert sc["rate"] is None and sc["status"] == "partial"


def test_judge_calls_in_flight_never_exceed_the_slot_cap():
    import threading
    import time
    case = _mini_case()
    row = _row(case, [T1, T2])
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()
    ok = _answer_quoting({"A1-x": T1, "B1-y": T2})

    class Slow(FakeJC):
        concurrency = 2

        def chat(self, messages, **kw):
            with lock:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
            time.sleep(0.05)
            with lock:
                live["now"] -= 1
            return super().chat(messages, **kw)
    jc = Slow(ok)
    ts = [threading.Thread(target=judge.judge_deep_row, args=(jc, row), kwargs={"samples": 1})
          for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert live["peak"] <= 2          # ceil(2 slots / 1 sample)


def test_every_rpdeep_case_declares_min_context_for_the_board_budget():
    # 3.8 bench regime: thinking floor 16384; the last-turn prompt reaches ~16.5k
    for c in _cases():
        assert c["min_context"] >= 40960


def _fixture_replies(case_id, name):
    p = ROOT / "cases" / "rp-deep" / case_id / "fixtures" / f"{name}.md"
    parts = re.split(r"^### (\d+)\s*\n", p.read_text(encoding="utf-8"), flags=re.M)[1:]
    return [t.strip() for t in parts[1::2]]


@pytest.mark.parametrize("case_id", ["RD1-marisol", "RD2-trieste", "RD3-granary"])
def test_regex_graders_against_the_fixtures(case_id):
    """gold and paraphrase-gold pass every scored regex fact, the foil passes none,
    and the out-of-character recital is gated to zero (review M8)."""
    case = next(c for c in _cases() if c["id"] == case_id)

    def regex_items(name):
        row = _fake_row(case)
        reps = _fixture_replies(case_id, name)
        assert len(reps) == len(case["turns"])
        for m, t in zip([m for m in row["conversation"] if m["role"] == "assistant"], reps):
            m["content"] = t
        row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                        "elements": {"results": [], "unjudged": []}}
        return [i for i in rpdeep.score_row(row, case, [])["items"] if i["grader"] == "regex"]
    assert all(i["pass"] for i in regex_items("gold"))
    assert all(i["pass"] for i in regex_items("paraphrase"))
    assert not any(i["pass"] for i in regex_items("foil"))
    assert not any(i["pass"] for i in regex_items("recital"))


def test_pruned_facts_carry_no_weight_and_a_failed_sanity_fact_is_flagged():
    case = _mini_case()
    case["judge_elements"][0]["scored"] = False
    case["judge_elements"][0]["sanity"] = True
    row = _row(case, [SPEECH, SPEECH])
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "elements": {"results": [{"id": "A1-x", "pass": False, "quote": ""},
                                             {"id": "B1-y", "pass": True, "quote": ""}],
                                 "unjudged": []}}
    sc = rpdeep.score_row(row, case, [])
    assert sc["rate"] == 1.0 and sc["weight_total"] == 1
    assert sc["sanity_failed"] == ["A1-x"]


def test_a_short_verbatim_quote_passes_when_the_judge_majority_said_yes():
    case = _mini_case()
    row = _row(case, [SPEECH, '"Ottavio." She said the name carefully.'])
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "elements": {"results": [
                        {"id": "A1-x", "pass": True, "judge_pass": True, "quote": ""},
                        {"id": "B1-y", "pass": False, "judge_pass": True, "quote": "Ottavio.",
                         "detail": "judge said yes but only 0/3 samples quoted the reply verbatim"}],
                        "unjudged": []}}
    assert rpdeep.score_row(row, case, [])["rate"] == 1.0
    row["judge"]["elements"]["results"][1]["quote"] = "Brisket."     # not in the reply
    assert rpdeep.score_row(row, case, [])["rate"] == 0.5


@pytest.mark.parametrize("case_id", ["RD1-marisol", "RD2-trieste", "RD3-granary"])
def test_keyword_gates_let_every_gold_and_paraphrase_answer_through(case_id):
    """require_any / veto floors (scoring time) must never fail a correct answer."""
    case = next(c for c in _cases() if c["id"] == case_id)
    for name in ("gold", "paraphrase"):
        row = _fake_row(case)
        for m, t in zip([m for m in row["conversation"] if m["role"] == "assistant"],
                        _fixture_replies(case_id, name)):
            m["content"] = t
        row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                        "elements": {"results": [{"id": e["id"], "pass": True, "judge_pass": True,
                                                  "quote": ""} for e in case["judge_elements"]],
                                     "unjudged": []}}
        bad = [i["id"] for i in rpdeep.score_row(row, case, [])["items"] if not i["pass"]]
        assert bad == [], (name, bad)


def test_colour_veto():
    case = next(c for c in _cases() if c["id"] == "RD3-granary")
    e = next(e for e in case["judge_elements"] if e["id"] == "L3-colourblind")
    assert re.search(e["veto"], '"The red one," she said.', re.I)
    assert not re.search(e["veto"], '"Not the red one - the left one, tag 7-C."', re.I)



def test_default_judge_config_is_one_sample_and_no_craft():
    """r3 (Fable final review): fact groups at samples=1, craft chunks opt-in."""
    case = _mini_case()
    row = _row(case, [SPEECH, SPEECH])
    seen = []

    class Count(FakeJC):
        def chat(self, messages, **kw):
            seen.append(messages[1]["content"][:40])
            return super().chat(messages, **kw)
    v = judge.judge_deep_row(Count(_answer_quoting({"A1-x": SPEECH[:30], "B1-y": SPEECH[:30]})),
                             row, samples=3)
    assert judge.DEEP_FACT_SAMPLES == 1 and judge.DEEP_CRAFT is False
    assert len(seen) == 2                     # two groups x one sample, no craft calls
    assert v["craft_judged"] is False and v["fact_samples"] == 1


def test_craft_gate_marks_low_craft_chunks_out_of_character():
    case = _mini_case()
    row = _row(case, [SPEECH, SPEECH])
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "craft_chunks": [{"turns": [1, 1], "scores": {"voice": 2, "craft": 3,
                                                                  "initiative": 2}},
                                     {"turns": [2, 2], "scores": {"voice": 8, "craft": 7,
                                                                  "initiative": 8}}],
                    "elements": {"results": [{"id": "A1-x", "pass": True, "quote": ""},
                                             {"id": "B1-y", "pass": True, "quote": ""}],
                                 "unjudged": []}}
    sc = rpdeep.score_row(row, case, [])
    assert {i["id"]: i["pass"] for i in sc["items"]} == {"A1-x": False, "B1-y": True}


def test_overlong_turn_fails_its_facts():
    case = _mini_case()
    long = SPEECH + " word" * (rpdeep.MAX_TURN_WORDS + 5)
    row = _row(case, [long, SPEECH])
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "elements": {"results": [{"id": "A1-x", "pass": True, "quote": ""},
                                             {"id": "B1-y", "pass": True, "quote": ""}],
                                 "unjudged": []}}
    sc = rpdeep.score_row(row, case, [])
    assert {i["id"]: i["pass"] for i in sc["items"]} == {"A1-x": False, "B1-y": True}
    assert sc["turns_over_350_words"] == 1


def test_floor_is_a_diagnostic_and_the_colour_veto_still_fails():
    for c in _cases():
        assert not any("require_any" in e for e in c["judge_elements"])
    case = next(c for c in _cases() if c["id"] == "RD3-granary")
    assert any(e.get("veto") for e in case["judge_elements"])


def test_rows_generated_with_the_old_knife_rule_are_not_graded_on_it():
    case = next(c for c in _cases() if c["id"] == "RD2-trieste")
    row = _fake_row(case, reply=SPEECH)
    row["conversation"][0]["content"] = "... she will not sell a knife to a captain on a Friday ..."
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "elements": {"results": [{"id": e["id"], "pass": False, "quote": ""}
                                             for e in case["judge_elements"]], "unjudged": []}}
    items = {i["id"]: i for i in rpdeep.score_row(row, case, [])["items"]}
    assert items["A2-no-knife-friday"]["scored"] is False


def test_regex_habits_weigh_half():
    for c in _cases():
        for x in c["checks"]:
            if x.get("fact") and x.get("ftype") == "habit":
                assert x["weight"] in (0.5, 0.25), x["id"]


@pytest.mark.parametrize("case_id", ["WR1-ninth-string", "WR2-red-thread-key"])
def test_gold_fixture_scores_one_under_the_40_weights(case_id):
    """W2: unscoring / capping / halving never leaves the gold session short of 1.0."""
    case = next(c for c in _cases() if c["id"] == case_id)
    row = _fake_row(case)
    reps = _fixture_replies(case_id, "gold")
    for m, t in zip([m for m in row["conversation"] if m["role"] == "assistant"], reps):
        m["content"] = t
    row["judge"] = {"judge_failed": False, "refused": False, "scores": {},
                    "elements": {"results": [{"id": e["id"], "pass": True, "quote": ""}
                                             for e in case["judge_elements"]], "unjudged": []}}
    sc = rpdeep.score_row(row, case, [])
    assert sc["rate"] == 1.0, [(i["id"], i["detail"]) for i in sc["items"] if i.get("scored", True) and not i["pass"]]
