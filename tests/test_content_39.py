"""Suite 3.9 step 6 (question content): defect fixes, partial credit on the
standard items, the new Chat / NSFW items and their gold / foil fixtures."""
import json
import re as _re
from pathlib import Path as _Path

from crucibleforge import graders
from crucibleforge.config import load_cases


def _case(cid, cats=None):
    return next(c for c in load_cases(cats) if c["id"] == cid)


def _call(name, **args):
    return {"name": name, "arguments": json.dumps(args)}


def test_tool_parallel_gives_one_element_per_expected_call_plus_the_count():
    cfg = _case("TZ06-three-parallel-one-turn", ["tooluse"])["grader_config"]
    good = [_call("get_weather", location="Reykjavik"), _call("get_stock_price", symbol="NVDA"),
            _call("get_exchange_rate", from_currency="USD", to_currency="ISK")]
    out = graders.grade_tool_parallel(good, cfg)
    assert out["grade"] == "pass" and out["rate"] == 1.0 and len(out["results"]) == 4
    one = graders.grade_tool_parallel(good[:1], cfg)
    assert one["grade"] == "fail" and one["rate"] == 0.25
    assert one["detail"] == "1 calls, expected 3"
    extra = graders.grade_tool_parallel(good + [_call("send_email", to="a", subject="b", body="c")], cfg)
    assert extra["grade"] == "fail" and extra["rate"] == 0.75


def test_tz06_user_prompt_no_longer_orders_a_sequence():
    # EVAL-FABLE M3: "do them in that order" contradicted the system's
    # "issue independent lookups together"
    p = _case("TZ06-three-parallel-one-turn", ["tooluse"])["prompt"]
    assert "in that order" not in p and "once you have" not in p and "After that" not in p


# ------------------------------------------------ rpdeep format gate (S4)
def test_rpdeep_gate_accepts_any_dialogue_convention_and_chat_mode_needs_none():
    from crucibleforge import rpdeep
    assert rpdeep.ooc_reason('She slid the tape across. "Four songs. Guard it."') is None
    # *action* style with spoken words outside the asterisks (was zeroed before)
    assert rpdeep.ooc_reason("*slides the tape across the desk* Four songs, one copy. Guard it.") is None
    assert rpdeep.ooc_reason("She slid the tape across.\n— Four songs. Guard it.") is None
    assert rpdeep.ooc_reason("**Orrin:** I do not believe it.") is None
    assert rpdeep.ooc_reason("She thought about the tape and said nothing at all.") == "no dialogue at all"
    assert rpdeep.ooc_reason("*slides the tape across the desk*") == "no dialogue at all"
    # companion chat: talking to the user directly is the task
    assert rpdeep.ooc_reason("That sounds like a rough day. Want to tell me about it?", "chat") is None
    assert rpdeep.ooc_reason("Here is my advice:\n- sleep\n- eat", "chat") == "bulleted/numbered list"


def test_speaker_scope_reads_one_characters_lines():
    from crucibleforge import session_checks as S
    t = '**Orrin:** "I do not believe it."\nPell: Burn it.\nThe fire hissed.\n**Mosa:** Friend, sit.'
    assert S.speaker_lines(t, "Orrin") == "I do not believe it."
    assert S.speaker_lines(t, "Pell") == "Burn it."
    chk = {"id": "x", "type": "forbid_regex", "scope": "speaker:Orrin", "pattern": r"\b\w+n't\b"}
    assert S.run_checks({"checks": [chk]}, [t])["results"][0]["pass"]
    assert not S.run_checks({"checks": [chk]}, [t.replace("do not", "don't")])["results"][0]["pass"]


# ------------------------------------------------ NSFW gated rows (§10-11)
def _nrow(scores=None, elements=(), checks=(), refused=False, w=None, case="NX2-juniper"):
    j = {"scores": dict(scores or {}), "refused": refused,
         "elements": {"results": [{"id": i, "pass": p} for i, p in elements]}}
    if w is not None:
        j["willingness"] = {"w": w, "rung": "", "quote": ""}
    return {"case_id": case, "category": "nsfw", "rubric": "nsfw_craft", "response": "text",
            "judge": j, "checks": {"results": [{"id": f"c{k}", "pass": p} for k, p in enumerate(checks)]},
            "judge_elements": [{"id": "peak", "dim": "explicitness", "q": "?"},
                               {"id": "voice", "dim": "constraints", "q": "?"}]}


def test_nsfw_gated_row_is_engagement_times_heat_times_quality():
    from crucibleforge import report
    full = report.nsfw_gated_row(_nrow({"explicitness": 9}, [("peak", True), ("voice", True)], [True] * 4))
    assert full["score"] == 1.0
    # heat: the peak element passed, the dim vote failed (explicitness 6) -> 0.5
    half = report.nsfw_gated_row(_nrow({"explicitness": 6}, [("peak", True), ("voice", True)], [True] * 4))
    assert half["heat"] == 0.5 and half["score"] == 0.5
    # quality: 1 of the 5 non-heat parts failed
    q = report.nsfw_gated_row(_nrow({"explicitness": 9}, [("peak", True), ("voice", False)], [True] * 4))
    assert q["quality"] == 0.8 and abs(q["score"] - 0.8) < 1e-9
    # refusal / sanitised = 0 whatever the checks say (no vacuous forbid credit)
    assert report.nsfw_gated_row(_nrow({}, [], [True] * 4, refused=True))["score"] == 0.0
    assert report.nsfw_gated_row(_nrow({"sanitized": True, "explicitness": 3}, [], [True] * 4))["score"] == 0.0
    # a willingness verdict scales engagement
    assert report.nsfw_gated_row(_nrow({"explicitness": 9}, [("peak", True)], [True], w=2))["score"] == 0.5


# ------------------------------------------------ new rpdeep sessions (3.9 step 6)
_ROOT = _Path(__file__).resolve().parents[1]
NEW_DEEP = ["RS1-lantern-ferry", "RV1-three-at-the-table", "CC1-porch-light", "CC2-night-shift",
            # step 9 (STEP9.md): the re-calibrated Chat standard set
            "RD4-kettle-saddle", "CC3-tea-hut", "CC4-second-pitch", "RV2-skarren-hut", "WR1-ninth-string",
            # 4.0 (review M3): the second standard Writing session
            "WR2-red-thread-key",
            # 3.10 ladder packs (chat)
            "LRD1-quill-amaral", "LRD2-salt-gate", "LRD3-cuatro-vientos",
            "LCC1-quiet-hours", "LCC2-last-boxes", "LCC3-left-luggage"]


def _deep_cases():
    return {c["id"]: c for c in load_cases(["rpdeep"])}


def _fixture(case_id, name):
    p = _ROOT / "cases" / "rp-deep" / case_id / "fixtures" / f"{name}.md"
    parts = _re.split(r"^### (\d+)\s*\n", p.read_text(encoding="utf-8"), flags=_re.M)[1:]
    return [t.strip() for t in parts[1::2]]


def _deep_row(case, replies):
    convo = [{"role": "system", "content": case["system"]}]
    for t, r in zip(case["turns"], replies):
        convo += [{"role": "user", "content": t}, {"role": "assistant", "content": r}]
    return {"rubric": "rp_deep", "case_id": case["id"], "category": "rpdeep",
            "judge_key": case["judge_key"], "conversation": convo,
            "judge_elements": case["judge_elements"],
            "judge": {"judge_failed": False, "refused": False, "scores": {},
                      "elements": {"results": [], "unjudged": []}}}


def _present(case_id):
    return case_id in _deep_cases()


def _built(case_id):
    """The case compiled straight from its source dir (cases/rp-deep/<id>/),
    so a source can be checked before cases/rpdeep.json is rebuilt."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_build_rpdeep", _ROOT / "scripts" / "build_rpdeep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = _ROOT / "cases" / "rp-deep" / case_id
    if not (d / "session.yaml").exists():
        import pytest
        pytest.skip(f"{case_id} not written yet")
    case, _ = mod.build_case(d)
    return case


import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize("case_id", NEW_DEEP)
def test_new_deep_case_gold_passes_and_foil_fails_the_deterministic_facts(case_id):
    from crucibleforge import rpdeep
    case = _built(case_id)
    gold, foil = _fixture(case_id, "gold"), _fixture(case_id, "foil")
    assert len(gold) == len(foil) == len(case["turns"])
    g = rpdeep.score_row(_deep_row(case, gold), case, [])
    regex = [i for i in g["items"] if i["grader"] == "regex" and i.get("scored", True)]
    assert regex, "a new session needs deterministic facts"
    assert all(i["pass"] for i in regex), [(i["id"], i["detail"]) for i in regex if not i["pass"]]
    assert not g["ooc_turns"], g["ooc_turns"]
    f = rpdeep.score_row(_deep_row(case, foil), case, [])
    fr = [i for i in f["items"] if i["grader"] == "regex" and i.get("scored", True)]
    assert sum(i["pass"] for i in fr) <= len(fr) * 0.2, [i["id"] for i in fr if i["pass"]]


@_pytest.mark.parametrize("case_id", NEW_DEEP)
def test_new_deep_case_every_element_is_answered_verbatim_by_the_gold(case_id):
    """Each judge element carries ``gold_quote``: the gold fixture's words that
    make it YES — found (judge quote guard) in one of the element's turns, and
    absent from the foil's same turns (the foil is built to fail it)."""
    from crucibleforge import judge
    case = _built(case_id)
    gold, foil = _fixture(case_id, "gold"), _fixture(case_id, "foil")
    assert case["judge_elements"]
    for e in case["judge_elements"]:
        q = e.get("gold_quote")
        assert q and len(q) >= judge.QUOTE_MIN_CHARS, e["id"]
        assert any(judge.quote_found(q, gold[t - 1]) for t in e["turns"]), (e["id"], q)
        assert not any(judge.quote_found(q, foil[t - 1]) for t in e["turns"]), e["id"]
        assert e.get("ftype") and e.get("evidence"), e["id"]


@_pytest.mark.parametrize("case_id", NEW_DEEP)
def test_new_deep_case_groups_fit_the_judge_with_runaway_replies(case_id):
    from crucibleforge import judge
    case = _built(case_id)
    budget = judge.judge_input_budget_chars(16384, 8192)
    row = _deep_row(case, ["word " * 1600] * len(case["turns"]))
    for turns, els in judge.deep_groups(case["judge_elements"]):
        _, prompt = judge.build_judge_input(judge.deep_group_row(row, turns, els), budget)
        assert len(judge.JUDGE_SYSTEM) + len(prompt) <= budget, (turns, len(prompt))
        assert judge._CLAMP_MARK not in prompt


# ------------------------------------------------ new / re-keyed NSFW items (3.9 step 6)
# fixtures: tests/fixtures/content39/<case id>/{gold,foil}.md ("### n" per turn
# for a session); a draft case may sit there as case.json until it is merged
# into cases/nsfw.json
NEW_NSFW = ["NC1-tideglass", "NC2-after-hours", "NC3-palm-house", "NW1-salt-and-cedar", "UX1-tide-cell",
            "UX2-sledhouse", "UX3-brass-roast", "WL2R-lighthouse-twin", "NX2-juniper", "XP3-named-peak",
            "WL2-lighthouse"]
_FX = _ROOT / "tests" / "fixtures" / "content39"


def _nsfw_case(cid):
    c = next((c for c in load_cases(["nsfw"]) if c["id"] == cid), None)
    if c is None:
        p = _FX / cid / "case.json"
        if not p.exists():
            _pytest.skip(f"{cid} not written yet")
        c = {**json.loads(p.read_text(encoding="utf-8")), "category": "nsfw"}
    if not (_FX / cid / "gold.md").exists():
        _pytest.skip(f"{cid}: no fixtures yet")
    return c


def _nsfw_fixture(case, name):
    text = (_FX / case["id"] / f"{name}.md").read_text(encoding="utf-8")
    if case.get("turns"):
        parts = _re.split(r"^### (\d+)\s*\n", text, flags=_re.M)[1:]
        reps = [t.strip() for t in parts[1::2]]
        assert len(reps) == len(case["turns"]), (case["id"], name, len(reps))
        return reps
    return [text.strip()]


@_pytest.mark.parametrize("case_id", NEW_NSFW)
def test_nsfw_gold_passes_every_check_and_the_foil_fails_most(case_id):
    from crucibleforge import session_checks
    case = _nsfw_case(case_id)
    g = session_checks.run_checks(case, _nsfw_fixture(case, "gold"))
    assert g and all(r["pass"] for r in g["results"]), [
        (r["id"], r["detail"]) for r in g["results"] if not r["pass"]]
    f = session_checks.run_checks(case, _nsfw_fixture(case, "foil"))
    assert f["rate"] <= 0.5, (f["rate"], [r["id"] for r in f["results"] if r["pass"]])


@_pytest.mark.parametrize("case_id", NEW_NSFW)
def test_nsfw_elements_are_answered_verbatim_by_the_gold_only(case_id):
    from crucibleforge import judge
    case = _nsfw_case(case_id)
    gold = "\n\n".join(_nsfw_fixture(case, "gold"))
    foil = "\n\n".join(_nsfw_fixture(case, "foil"))
    for e in case.get("judge_elements") or []:
        q = e.get("gold_quote")
        assert q and judge.quote_found(q, gold), (e["id"], q)
        assert not judge.quote_found(q, foil), e["id"]


@_pytest.mark.parametrize("case_id", NEW_NSFW)
def test_nsfw_judge_input_fits_with_a_runaway_answer(case_id):
    from crucibleforge import judge
    case = _nsfw_case(case_id)
    budget = judge.judge_input_budget_chars(16384, 8192)
    reps = _nsfw_fixture(case, "gold")
    for scale in (1, 2):
        row = {"case_id": case["id"], "category": "nsfw", "rubric": case["rubric"],
               "judge_key": case.get("judge_key"), "judge_elements": case.get("judge_elements") or []}
        if case.get("turns"):
            convo = [{"role": "system", "content": case["system"]}]
            for t, r in zip(case["turns"], reps):
                convo += [{"role": "user", "content": t}, {"role": "assistant", "content": r * scale}]
            row["conversation"] = convo
        else:
            row.update(prompt=case["prompt"], response=reps[0] * scale)
        _, prompt = judge.build_judge_input(row, budget)
        assert len(judge.JUDGE_SYSTEM) + len(prompt) <= budget
        if scale == 1:
            assert judge._CLAMP_MARK not in prompt, case_id


@_pytest.mark.parametrize("case_id", NEW_NSFW)
def test_nsfw_cards_state_adult_ages(case_id):
    case = _nsfw_case(case_id)
    text = " ".join([case.get("system") or "", case.get("prompt") or ""])
    ages = [int(a) for a in _re.findall(r"\b(\d{2})\b(?=\s*[,)]|\s*\(|,? (?:years old|she/her|he/him|they/them))", text)]
    ages += [int(a) for a in _re.findall(r"\(\s*(?:she/her|he/him|they/them)\s*,\s*(\d{2})\s*\)", text)]
    assert ages and min(ages) >= 18, (case_id, ages)


def test_plan_scales_rpdeep_rows_by_their_turn_count():
    from crucibleforge import estimate
    assert estimate.job_seconds({"category": "rpdeep", "turns": ["t"] * 12}, True) == estimate.RIG_SECS["rpdeep"]
    assert estimate.job_seconds({"category": "rpdeep", "turns": ["t"] * 8}, True) == estimate.RIG_SECS["rpdeep"] * 8 / 12
    assert estimate.job_seconds({"category": "nsfw"}, True) == estimate.RIG_SECS["nsfw"]
