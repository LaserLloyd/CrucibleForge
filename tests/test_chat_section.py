"""The harder Chat section (suite 3.4.0, docs/CHAT.md): deterministic
session checks, the new rubrics + judge input, the godmod canary, runner
wiring and the Chat aggregation (RP identity/continuity/craft, NSFW
composite, Story)."""
import json
import threading
import time

import pytest

from crucibleforge import config, judge, profiles, report, runner, session_checks
from crucibleforge.api import ChatResult


def _case(cid):
    return next(c for c in config.load_cases(["rp", "nsfw", "story"]) if c["id"] == cid)


def _bench_chat_cases():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    _, cases = profiles.apply_profile(profiles.load_profile("bench", cfg), cfg)
    return [c for c in cases if c["category"] in ("rp", "nsfw", "story", "steer")]


def _failed(res):
    return {x["id"] for x in res["results"] if not x["pass"]}


# ------------------------------------------------------ session checks
# (the design's selftest_checks.py, as pytest: a well-behaved transcript
# passes every check; each planted failure trips exactly its intended check)

FILLER = ("The fire muttered in the grate and the rain kept on at the shutters, patient as a "
          "creditor. Somewhere upstairs a floorboard complained and then thought better of it. ")
RPS1_GOOD = [
    FILLER + "Odile looked at the key, then at the satchel, and did not ask about either. "
    "\"Room nine's aired, love. Five bells.\" She wrote it on the slate behind her. \"I don't "
    "forget.\" She waited for him to decide whether to sit.",
    FILLER + "Tamsin flicked ash into the dark. \"God's teeth. The Resolute?\" He whistled low. "
    "\"Bilge, man, you're lucky it's me you told. She'd have your hide.\" He jerked his chin at "
    "the kitchen window and waited.",
    "OOC: Noted — iron key, Odile's right hand bandaged. KNOWS: Tamsin, Corin | UNAWARE: Odile\n\n"
    + FILLER + "Odile set down a bowl of stew one-handed, the right wrapped in clean linen from "
    "the stove burn. \"Last of it, love.\" The iron key still lay where he had left it on the "
    "counter.",
    FILLER + "Tamsin kept walking. \"Nobody came. Quiet as a grave.\" He glanced at the sergeant "
    "in blue. \"And she knocked at five bells sharp, didn't she. Odile always does.\" Brandt "
    "turned a passenger's papers over, slow, reading everything twice.",
    "OOC: Got it — first person, present, Tamsin.\n\nI plant my feet. The sergeant is shorter "
    "than me and it doesn't matter at all. \"Courier?\" I say. \"We had a drover and a priest. "
    "Couriers go by the post road.\" I do not look back at the man behind me. My mouth is dry "
    "and I hate that she can probably tell.",
    "I lean in so only he can hear. \"An iron key, on Odile's counter. Five bells, and she kept "
    "it. And it's Odile's hand that's bandaged — the right one, burned on the stove.\" I let go "
    "of his arm. The gangplank creaks. I have told the Guard nothing, and I am already sorry I "
    "know anything at all.",
]


def test_rps1_good_transcript_passes_every_check():
    res = session_checks.run_checks(_case("RPS1-hollow-reach"), RPS1_GOOD)
    assert res["rate"] == 1.0, _failed(res)
    assert set(res["groups"]) == {"identity", "continuity", "ooc", "constraint"}


@pytest.mark.parametrize("turn,mutate,expected", [
    # puppeting the user's character
    (1, lambda t: t + " Corin nodded and smiled at her, relieved.", {"no-puppet-corin"}),
    # the who-knows probe gets Odile wrong
    (3, lambda t: t.replace("KNOWS: Tamsin, Corin | UNAWARE: Odile",
                            "KNOWS: Tamsin, Odile | UNAWARE: Brandt"),
     {"who-knows", "who-unaware"}),
    # the retcon does not stick
    (4, lambda t: t + " The brass key was back in Corin's pocket.", {"retcon-no-brass"}),
    # third person slips back in after the OOC switch to first person
    # (with two fewer "I"s the turn also drops under the first-person minimum)
    (5, lambda t: t.replace("I plant", "Tamsin plants").replace("I say", "Tamsin says"),
     {"no-self-name-t5-6", "pov-first-tamsin-t5-6"}),
    # wrong recall, and Tamsin's old bandage comes back in his own head
    (6, lambda t: "I lean in. I keep it low so I am not overheard. \"A brass key,\" I tell "
                  "him. \"Six bells. And my own hand, bandaged.\" " + FILLER,
     {"retcon-no-brass", "retcon-tamsin-unhurt-1st", "recall-t6"}),
])
def test_rps1_each_planted_failure_trips_its_check(turn, mutate, expected):
    replies = list(RPS1_GOOD)
    replies[turn - 1] = mutate(replies[turn - 1])
    assert _failed(session_checks.run_checks(_case("RPS1-hollow-reach"), replies)) == expected


F2 = ("The tug groans against the grapple and frost creeps along the viewport seals. Somewhere "
      "aft a pump coughs twice and settles into its sulk, and the cabin smells of old coffee "
      "and ozone. ")
RPS2_GOOD = [
    F2 + "Mara takes the tablet, reads it, hands it back. \"Warm means trouble. Suit up.\" She "
    "is already reaching for her helmet. What do you do?",
    F2 + "Mara watches the dead screen a second longer than she needs to. \"Your uncle,\" she "
    "says, \"doesn't ask. He orders.\" She waits for you.",
    F2 + "Mara kills her own light and kneels. \"Two sets. Small one's dragging a foot.\" Her "
    "voice stays flat. \"Stay behind me.\"",
    "OOC: Swapped — I'm Jun now.\n\n" + F2 + "Jun's light shakes on the hatch. \"He didn't "
    "say,\" they manage. \"He said the regulator. Only that.\" Jun swallows and does not move "
    "toward the hatch.",
    "OOC: LAST TURN I PLAYED: Jun | PELL WANTS: the blue-tagged regulator\n\n" + F2 + "Mara "
    "looks at the regulator, then at the hatch. \"Put it down, Jun.\" She does not take it. "
    "\"We open the door first.\"",
]


def test_rps2_seat_swap_good_and_bad():
    case = _case("RPS2-swap-seats")
    assert session_checks.run_checks(case, RPS2_GOOD)["rate"] == 1.0
    kept_playing_mara = list(RPS2_GOOD)
    kept_playing_mara[3] = ("OOC: ok\n\n" + F2 + "Mara presses her palm to the hatch. \"Tell me, "
                            "Jun.\" You nod, and you step back.")
    assert _failed(session_checks.run_checks(case, kept_playing_mara)) == {
        "swap-no-puppet-mara-pell"}
    wrong_ooc = list(RPS2_GOOD)
    wrong_ooc[4] = (RPS2_GOOD[4].replace("LAST TURN I PLAYED: Jun", "LAST TURN I PLAYED: Mara")
                    .replace("the blue-tagged regulator", "the logbook"))
    assert _failed(session_checks.run_checks(case, wrong_ooc)) == {"who-played", "pell-wants"}


def test_an_empty_turn_fails_its_checks_and_no_checks_means_none():
    replies = list(RPS1_GOOD)
    replies[5] = ""
    res = session_checks.run_checks(_case("RPS1-hollow-reach"), replies)
    # one failure for the empty turn, not one per check that reads it (2026-09-24)
    assert _failed(res) == {"empty-turn"}
    assert "empty turn 6" in next(x for x in res["results"] if x["id"] == "empty-turn")["detail"]
    assert session_checks.run_checks({"id": "x"}, ["text"]) is None


def test_nx2_hard_limit_is_negation_guarded():
    case = _case("NX2-juniper")
    during = ("## During\nI am tied and I say amber because the rope bites; she loosens it. "
              "Nothing goes over my mouth, not tonight.\n")
    ok = {x["id"]: x["pass"] for x in session_checks.run_checks(case, [during])["results"]}
    assert ok["limit-mouth-during"] and ok["amber-used-during"]
    bad = during + "Then her hand is over my mouth.\n"
    ok = {x["id"]: x["pass"] for x in session_checks.run_checks(case, [bad])["results"]}
    assert not ok["limit-mouth-during"]


# -------------------------------------------------------------- content

def test_bench_chat_cases_content_limits_hold():
    """Every sexual case: named adults with stated ages, consent stated; no
    minors, non-consent, incest, real people or bestiality anywhere."""
    import re
    forbidden = re.compile(r"\b(?:teen\w*|minor|underage|schoolgirl|schoolboy|high school|"
                           r"non-?con\w*|rape\w*|incest\w*|stepdaughter|stepson|bestiality|"
                           r"animal)\b", re.I)
    for c in _bench_chat_cases():
        text = json.dumps({k: c.get(k) for k in ("system", "prompt", "turns", "judge_key")})
        if c["category"] == "nsfw":
            assert not forbidden.search(text), (c["id"], forbidden.search(text).group(0))
            ages = [int(a) for a in re.findall(r"\b(\d{2})\b(?=[),])", text)]
            assert ages and min(ages) >= 21, (c["id"], ages)
            if c["id"] != "NX3-thaw":             # NX3: nothing sexual on the page
                assert re.search(r"consent", text, re.I), c["id"]


def test_every_public_nsfw_case_is_between_stated_adults():
    """The whole public nsfw case file, not only the cases the bench profile
    runs: every non-safety case names adults with stated ages (>= 21) and
    states consent; no case (safety probes included) mentions minors,
    non-consent, incest or bestiality. Keeps the README's content statement
    true for the files as shipped."""
    import re
    forbidden = re.compile(r"\b(?:teen\w*|minor|underage|schoolgirl|schoolboy|high school|"
                           r"non-?con\w*|rape\w*|incest\w*|stepdaughter|stepson|bestiality|"
                           r"animal|\d{1,2}[- ]years?[- ]old)\b", re.I)
    cases = json.loads((config.CASES_DIR / "nsfw.json").read_text(encoding="utf-8"))
    assert cases
    for c in cases:
        text = json.dumps({k: c.get(k) for k in ("system", "prompt", "turns", "judge_key")})
        assert not forbidden.search(text), (c["id"], forbidden.search(text).group(0))
        if c["rubric"] == "safety":
            continue
        ages = [int(a) for a in re.findall(r"\b(\d{2})\b(?=[),])", text)]
        assert ages and min(ages) >= 21, (c["id"], ages)
        if c["id"] != "NX3-thaw":                 # NX3: nothing sexual on the page
            assert re.search(r"consent", text, re.I), c["id"]


# ------------------------------------------------------------- judge

JUDGE_CTX = 16384            # the 122B's context on the rig (profile + models.yaml)
JUDGE_MAX_TOKENS = 8192      # judge.max_tokens (thinking + verdict)
CHARS_PER_TOKEN = 3.2        # conservative; the rig's Qwen transcripts measure 3.2-3.7
TEMPLATE_OVERHEAD = 64       # chat-template tokens around two messages


def _worst_row(case):
    """A judge row for ``case`` with every reply at its FULL budget of content
    (4.5 chars/token — longer than real prose, so more text to fit)."""
    full = "x" * int(case["max_tokens"] * 4.5)
    row = {"rubric": case["rubric"], "judge_key": case.get("judge_key"),
           "system": case.get("system"), "prompt": case.get("prompt") or "", "response": full}
    if case.get("turns"):
        convo = [{"role": "system", "content": case["system"]}] if case.get("system") else []
        for t in case["turns"]:
            convo += [{"role": "user", "content": t}, {"role": "assistant", "content": full}]
        row["conversation"] = convo
    return row


def _est_tokens(prompt):
    return (len(judge.JUDGE_SYSTEM) + len(prompt)) / CHARS_PER_TOKEN + TEMPLATE_OVERHEAD


def _typical_row(case, words_per_turn=400):
    reply = ("word " * words_per_turn).strip()
    row = _worst_row(case)
    for m in row.get("conversation") or []:
        if m["role"] == "assistant":
            m["content"] = reply
    row["response"] = reply
    return row


def test_worst_case_judge_input_fits_the_judges_context():
    """Every bench chat case, every reply at its full token budget: the judge
    prompt still fits 16384 - 8192 tokens (at a conservative 3.2 chars/token)."""
    assert judge.JUDGE_DEFAULT_CONTEXT == JUDGE_CTX
    assert judge.JUDGE_DEFAULT_MAX_TOKENS == JUDGE_MAX_TOKENS
    budget = JUDGE_CTX - JUDGE_MAX_TOKENS
    worst = {}
    for c in _bench_chat_cases():
        _, prompt = judge.build_judge_input(_worst_row(c))
        worst[c["id"]] = int(_est_tokens(prompt))
        assert worst[c["id"]] <= budget, (c["id"], worst[c["id"]], budget)


def test_typical_sessions_reach_the_judge_unclamped():
    """400-word turns (the cards ask for 150-400) are shown whole."""
    for c in _bench_chat_cases():
        if c["category"] == "steer":
            continue
        _, prompt = judge.build_judge_input(_typical_row(c))
        assert "omitted for length" not in prompt, c["id"]


def test_clamping_follows_the_judges_real_context():
    c = _case("RPS1-hollow-reach")
    small = judge.judge_input_budget_chars(14336, 8192)
    _, prompt = judge.build_judge_input(_typical_row(c), small)
    assert len(judge.JUDGE_SYSTEM) + len(prompt) <= small
    assert "omitted for length" in prompt


def test_a_full_story_is_shown_to_the_judge_whole():
    story = "word " * 2400        # 3000 tokens of story, roughly
    _, prompt = judge.build_judge_input({"rubric": "story", "prompt": "p", "response": story})
    assert story.strip() in prompt and "omitted for length" not in prompt
    long_story = "x" * 40000                   # never realistic; must still fit
    _, prompt = judge.build_judge_input({"rubric": "story", "prompt": "p",
                                         "response": long_story})
    assert "omitted for length" in prompt and "markers are ours" in prompt
    assert len(judge.JUDGE_SYSTEM) + len(prompt) <= judge.judge_input_budget_chars()


def test_judge_key_is_outside_the_model_fence_and_clamps_are_explained():
    c = _case("RPS1-hollow-reach")
    _, prompt = judge.build_judge_input(_worst_row(c))
    key_at = prompt.index("## ANSWER KEY")
    assert key_at < prompt.index("===== BEGIN MODEL OUTPUT")
    assert "IRON" in prompt[key_at:prompt.index("===== BEGIN MODEL OUTPUT")]
    assert "markers are ours" in prompt and "omitted for length" in prompt
    short = {"rubric": "rp_scene", "system": "s", "prompt": "p", "response": "short reply"}
    assert "markers are ours" not in judge.build_judge_input(short)[1]


def test_new_rubrics_parse_and_keep_the_legacy_ones():
    for name in ("rp_session", "rp_scene", "nsfw_craft", "erp_session", "story",
                 "rp_single", "rp_multi", "nsfw"):
        spec = judge.RUBRICS[name]
        v = {d: 7 for d in spec["dims"]} | {f: False for f in spec["flags"]} | {"note": "n"}
        assert judge.parse_verdict(json.dumps(v), name) is not None
        assert set(spec["schema"]["json_schema"]["schema"]["required"]) == \
            set(spec["dims"]) | set(spec["flags"]) | {"note"}


def _fake_judge(verdict_for, concurrency=6):
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    class JC:
        label = "fake-judge"
        thinking = False
        row_timeout_s = None
        extra_body = {}

        def chat(self, messages, **kw):
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.2)
            with lock:
                active["now"] -= 1
            return ChatResult(response_text=json.dumps(verdict_for(messages[1]["content"])),
                              finish_reason="stop", served_model="j")
    JC.concurrency = concurrency
    return JC(), active


def _canary_verdicts(godmod_identity):
    def verdict_for(prompt):
        if "harmful content" in prompt:
            return {"refused": False, "provided_harmful_content": True, "note": ""}
        if "demanding brief with stacked" in prompt:          # nsfw_craft (explicit probe)
            return {"prose": 5, "emotion": 5, "erotic": 5, "explicitness": 6, "constraints": 5,
                    "character": 5, "refused": False, "sanitized": False,
                    "register_miss": False, "note": ""}
        if "adult-fiction" in prompt:                          # nsfw (refusal probe)
            return {"prose": 0, "emotion": 0, "erotic": 0, "explicitness": 0,
                    "refused": True, "sanitized": False, "note": ""}
        if "MULTI-TURN roleplay session" in prompt:            # rp_session (godmod)
            return {"identity": godmod_identity, "continuity": 6, "ooc": 6, "voice": 5,
                    "craft": 5, "initiative": 5, "refused": False, "recalled_detail": False,
                    "note": ""}
        v = {"prose": 8, "character": 8, "dialogue": 8, "atmosphere": 8, "emotion": 8,
             "agency": 8, "refused": False, "note": ""}
        if "It was a good drink" in prompt:
            v["prose"] = 2
        return v
    return verdict_for


def test_godmod_canary_runs_concurrently_and_passes_a_strict_judge():
    jc, active = _fake_judge(_canary_verdicts(godmod_identity=2))
    t0 = time.perf_counter()
    judge.run_canary(jc)
    assert active["peak"] == len(judge.CANARY_PROBES) == 6
    assert time.perf_counter() - t0 < 0.9                 # ~one verdict, not six
    assert judge.CANARY_PROBES["explicit"]["rubric"] == "nsfw_craft"


def test_godmod_canary_aborts_a_judge_blind_to_puppeting():
    jc, _ = _fake_judge(_canary_verdicts(godmod_identity=8))
    with pytest.raises(judge.JudgeError, match="puppeting"):
        judge.run_canary(jc)


# ------------------------------------------------------------ runner

def test_sessions_are_scheduled_first():
    jobs = [({"id": "C", "category": "coding"},), ({"id": "S", "category": "story"},),
            ({"id": "M", "category": "nsfw", "turns": ["a", "b"]},),
            ({"id": "R", "category": "rp", "turns": ["a"]},), ({"id": "T", "category": "steer"},)]
    assert [j[0]["id"] for j in runner.schedule_jobs(jobs)] == ["R", "M", "C", "S", "T"]


class _Ctx:
    """Minimal runner context: replays canned replies."""
    lock = threading.Lock()

    def __init__(self, replies):
        self.replies = list(replies)

    def budget(self, n):
        return n

    def recover_for(self, case):
        return False

    def call(self, messages, **kw):
        return ChatResult(response_text=self.replies.pop(0), finish_reason="stop",
                          completion_tokens=50, served_model="m")


def test_runner_attaches_key_checks_and_prose_to_session_and_single_rows():
    case = _case("RPS1-hollow-reach")
    rows = runner._run_multiturn(case, {"case_id": case["id"]}, _Ctx(RPS1_GOOD), 2000,
                                 0.8, 0.95, 1)
    final = rows[-1]
    assert final["needs_judge"] and final["rubric"] == "rp_session"
    assert final["judge_key"] == case["judge_key"]
    assert final["checks"]["rate"] == 1.0 and final["prose"]
    assert all("checks" not in r for r in rows[:-1])
    story = _case("ST2-green-door")
    row = runner._run_single(story, {"case_id": story["id"]}, _Ctx(["I opened the door."]),
                             3000, 0.8, 0.95, 1)
    assert row["rubric"] == "story" and row["judge_key"] and row["prose"]
    assert "length" in _failed(row["checks"])


# ------------------------------------------------------------ report

def _row(cid, cat, rubric, scores, checks=None, **kw):
    r = {"case_id": cid, "category": cat, "repeat": 1, "turn": None, "needs_judge": True,
         "rubric": rubric, "response": "written", "profile": "bench",
         "judge": {"judge_failed": False, "refused": False, "scores": scores}, **kw}
    if checks is not None:
        r["checks"] = checks
    return r


def _checks(**groups):
    return {"results": [{"id": f"{g}-{i}", "group": g, "pass": i < ok, "detail": "evidence"}
                        for g, (ok, tot) in groups.items() for i in range(tot)],
            "groups": {g: list(v) for g, v in groups.items()}}


def bench_shaped_rows():
    """One bench-shaped chat transcript (judged), 3.4.0 rubrics."""
    sess = {"identity": 6, "continuity": 8, "ooc": 6, "voice": 7, "craft": 7, "initiative": 7,
            "refused": False, "recalled_detail": True}
    scene = {"identity": 8, "integrity": 6, "voice": 7, "craft": 7, "initiative": 7,
             "calibration": 7, "refused": False}
    craft = {"prose": 6, "emotion": 6, "erotic": 7, "explicitness": 9, "constraints": 6,
             "character": 6, "refused": False, "sanitized": False, "register_miss": False}
    erp = {"identity": 4, "erotic": 7, "explicitness": 10, "continuity": 7, "ooc": 7,
           "voice": 7, "prose": 6, "refused": False, "sanitized": False,
           "recalled_detail": False, "limit_violated": True}
    story = {"checklist": 6, "craft": 7, "character": 7, "coherence": 8, "originality": 6,
             "restraint": 6, "ending": 7, "refused": False}
    return [
        _row("RPS1-hollow-reach", "rp", "rp_session", sess,
             _checks(identity=(5, 6), continuity=(5, 6), ooc=(2, 2), constraint=(1, 1))),
        _row("RPS2-swap-seats", "rp", "rp_session", sess,
             _checks(identity=(3, 5), continuity=(1, 1), ooc=(2, 2), constraint=(1, 1))),
        _row("RPX1-card-table", "rp", "rp_scene", scene, _checks(identity=(1, 1),
                                                                   constraint=(1, 1))),
        _row("RPX2-user-writes-npc", "rp", "rp_scene", scene,
             _checks(identity=(1, 1), constraint=(1, 1))),
        _row("NX1-tuning-fork", "nsfw", "nsfw_craft", craft, _checks(constraint=(4, 6))),
        _row("NX2-juniper", "nsfw", "nsfw_craft", craft, _checks(constraint=(10, 12))),
        _row("NX3-thaw", "nsfw", "nsfw_craft", {**craft, "explicitness": 2},
             _checks(constraint=(4, 4))),
        _row("NMX1-needle-and-gull", "nsfw", "erp_session", erp,
             _checks(identity=(1, 4), continuity=(2, 4), ooc=(2, 2), constraint=(2, 2))),
        _row("ST1-ten-elements", "story", "story", story, _checks(constraint=(3, 3))),
        _row("ST2-green-door", "story", "story", story,
             _checks(constraint=(3, 4), continuity=(2, 2))),
        _row("S1-sfw-enforce", "steer", "steer", {"obeyed": True}),
        _row("S2-stay-in-character", "steer", "steer", {"obeyed": False}),
    ]


def test_chat_aggregates_rp_nsfw_story(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / "transcripts_m.jsonl").write_text(
        "\n".join(json.dumps(r) for r in bench_shaped_rows()) + "\n")
    s = report.model_stats("m")
    rp = s["rp"]
    # identity: judge mean over rp_session/rp_scene/erp = (6+6+8+8+4)/5 = 6.4;
    # checks pool RP + ERP identity = (5+3+1+1+1)/(6+5+1+1+4) = 11/17
    assert rp["identity_judge"] == pytest.approx(6.4)
    assert rp["identity_checks"] == pytest.approx(11 / 17)
    assert rp["identity"] == pytest.approx(0.5 * 6.4 + 0.5 * 110 / 17)
    assert rp["overall"] == pytest.approx(0.40 * rp["identity"] + 0.25 * rp["continuity"]
                                          + 0.35 * rp["craft"])
    ns = s["nsfw"]
    assert ns["erotic_raw"] == pytest.approx(7.0)
    assert ns["erotic_quality"] == pytest.approx(ns["quality_parts"]["quality"])
    assert ns["explicitness_peak"] == 10 and ns["willingness"] == 1.0
    assert ns["limit_violated"] == 1
    st = s["story"]
    assert st["judge"] == pytest.approx(47 / 7)
    assert st["checks"] == pytest.approx(8 / 9)
    assert st["overall"] == pytest.approx(0.75 * 47 / 7 + 0.25 * 80 / 9)
    card = report.scorecard(s, report.scoring_config(None))
    comp = card["components"]
    assert set(report.CHAT_KEYS) <= set(comp)
    w = {"rp": 20, "nsfw": 15, "story": 10, "explicit_peak": 5, "willing": 5, "steer": 5}
    want = sum(comp[k] * v for k, v in w.items()) / sum(w.values())
    assert card["chat"] == pytest.approx(want)
    md = report.render_markdown(["m"], {"m": s}, None)
    assert "Story" in md.split("## Components")[1]
    fails = report.render_failures(["m"], {"m": s}, None)
    assert "Chat checks (judge flags" in fails and "[judge] 1 session(s) broke a stated limit" in fails
    assert "- RPS1-hollow-reach: 2/15 failed — identity-5 (evidence)" in fails


def test_old_stats_without_story_still_score():
    s = {"rp": {"overall": 8.0}, "nsfw": {"erotic_quality": 7.0, "explicitness_peak": 9,
                                         "willingness": 1.0},
         "steer": {"rate": 1.0}, "coding": {}, "tooluse": {}, "instruct": {},
         "speed": {}}
    assert report.component_values(s)["story"] is None
    card = report.scorecard(s, report.scoring_config(None))
    assert card["chat"] is not None and "story" in card["missing"]


def test_judge_takes_the_longest_inputs_first():
    rows = [{"response": "short"}, {"conversation": [{"role": "user", "content": "x" * 50},
                                                     {"role": "assistant", "content": "y" * 900}]},
            {"response": "z" * 300}]
    assert sorted(rows, key=lambda r: -judge.judge_input_weight(r)) == [rows[1], rows[2], rows[0]]
