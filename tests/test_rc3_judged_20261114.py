"""rc3 judged parts (Admin N2, 2026-11-14; JUDGE's EXP-20261114-34 SPEC-for-engine.md).

Judge side: a judged part's rubric mode (thinking off, a 1,024 cap, one sample, no element list), the
real_person rubric (JUDGE's text and prompt word for word). Chains side: judge_mode / gen_seed /
se_cluster: card, a piece = mean of its axes / 10 (sexual axes only on a sexual card, refused = 0), two
seeds averaged inside a card. The jcard family and the rc3-main composition."""
import json
import math
from pathlib import Path

import pytest

from crucibleforge import chains, judge as J, profiles, runner
from crucibleforge.api import ChatResult
from crucibleforge.config import ConfigError
from crucibleforge.gen import jcard

ROOT = Path(__file__).resolve().parents[1]
JM = {"kind": "rubric", "axes": ["prose", "emotion", "character"], "sexual_axes": ["erotic"], "max_tokens": 1024}


# ----------------------------------------------------------------------------------------------- judge side
def test_rubric_mode_policy_wins_over_element_mode():
    row = {"case_id": "FLAT-x-001-r1-a1", "rubric": "nsfw_craft", "chain": {"judge_mode": JM},
           "judge_elements": [{"id": "top-act", "q": "?", "class": "fact"}]}
    pcfg = {**J.JUDGE_POLICY_DEFAULTS, "mode": "elements"}
    pol = J.judge_policy(row, pcfg)
    assert pol["kind"] == "rubric" and pol["samples"] == 1
    assert pol["thinking"] is False and pol["max_tokens"] == 1024 and pol["no_elements"] is True
    # without a judge_mode the profile's element mode still applies
    assert J.judge_policy({**row, "chain": {}}, pcfg)["kind"] == "elements"


def test_rubric_mode_strips_elements_and_willingness(monkeypatch):
    seen = {}

    def fake(jc, row, samples):
        seen.update(row)
        return {"scores": {}}

    monkeypatch.setattr(J, "_judge_row", fake)
    row = {"case_id": "X", "rubric": "nsfw_craft", "judge_elements": [{"id": "a"}], "judge_willingness": True,
           "_judge_policy": {"kind": "rubric", "no_elements": True, "thinking": False}}
    J.judge_row(object(), row)
    assert seen["judge_elements"] == [] and seen["judge_willingness"] is False


class _JC:
    thinking = True
    extra_body = None
    row_timeout_s = None
    max_tokens = 8192
    context_length = 16384

    def __init__(self, text):
        self.text, self.calls = text, []

    def chat(self, messages, **kw):
        self.calls.append((messages, kw))
        return ChatResult(response_text=self.text, finish_reason="stop", completion_tokens=40)


def test_rubric_mode_call_is_thinking_off_capped_with_schema_and_no_element_block():
    v = {"prose": 7, "emotion": 6, "erotic": 5, "explicitness": 9, "constraints": 8, "character": 7,
         "refused": False, "sanitized": False, "register_miss": False, "note": "ok"}
    jc = _JC(json.dumps(v))
    row = {"case_id": "FLAT-x-001-r1-a1", "rubric": "nsfw_craft", "prompt": "write", "response": "a piece",
           "judge_elements": [{"id": "top-act", "q": "Is the act on the page?", "class": "fact"}],
           "chain": {"judge_mode": JM}}
    pol = J.judge_policy(row)
    out = J.judge_row(jc, {**row, "_judge_policy": pol}, samples=pol["samples"])
    assert out["scores"]["prose"] == 7 and out["policy"]["kind"] == "rubric"
    (msgs, kw), = jc.calls
    assert kw["max_tokens"] == 1024 and kw["response_format"]["json_schema"]["name"] == "nsfw_craft_verdict"
    assert kw["extra_body"] == J.merge_extra_body(None, J._NO_THINK)
    assert "Is the act on the page?" not in msgs[1]["content"]


CARD = {"person": "Dana, 31, nurse, dry humour, texts in lowercase",
        "thread": "Sam: still on for tacos at 7? i booked the table\nDana: yesss"}


def test_real_person_prompt_is_judges_word_for_word():
    row = {"case_id": "X", "rubric": "real_person", "rubric_card": CARD, "prompt": "p", "response": "omg sorry\ncan't"}
    rubric, prompt = J.build_judge_input(row)
    rc3 = (
        "You judge whether short text messages read like a real person texting. You will see a text thread, who the "
        "person is, and the texts a model wrote as that person.\n"
        "REAL_PERSON 0-10: does this read as that specific person texting a friend, colleague or relative? Real people "
        "text in short fragments, in their own voice and register, react emotionally, leave things unsaid, do not offer "
        "help like a service, do not write formal paragraphs or lists. 10 = indistinguishable from a real person's "
        "texts; 7 = believable with a slip or two; 5 = plausible but generic; 3 = clearly written by an assistant or a "
        "bot; 0 = not texts at all.\n"
        "ON_TOPIC 0-10 (descriptive, not quality): does it respond to the actual situation in the thread?\n"
        "Respond ONLY with the requested JSON.")
    # JUDGE's rc3 text is kept word for word; the live text is CHAT-RP's tightened one (EXP-34 owner evidence)
    assert J.REAL_PERSON_TEXT_RC3 == rc3
    assert J.RUBRICS["real_person"]["text"] == J.REAL_PERSON_TEXT
    for must in ("settle what happens next", "naming the person's own feelings", "neither the thread nor the card",
                 "like a service", "register"):
        assert must in J.REAL_PERSON_TEXT
    want = (
        J.REAL_PERSON_TEXT +
        f"\n\n{J._INJECT_GUARD}## The person\n{CARD['person']}\n\n## Thread so far\n{CARD['thread']}\n\n"
        f"## Texts the model wrote as Dana\n{J._fence('omg sorry' + chr(10) + 'can' + chr(39) + 't')}\n\n"
        "JSON only: {\"real_person\": int, \"on_topic\": int, \"note\": str}")
    assert rubric == "real_person" and prompt == want
    assert J.judge_system(row) == J.JUDGE_SYSTEM


def test_real_person_schema_and_parse():
    sch = J.RUBRICS["real_person"]["schema"]["json_schema"]
    assert sch["name"] == "rp_verdict" and sch["schema"]["required"] == ["real_person", "on_topic", "note"]
    assert sch["schema"]["properties"]["note"]["maxLength"] == 240
    v = J.parse_verdict('{"real_person": 7, "on_topic": 9, "note": "fine"}', "real_person")
    assert v["real_person"] == 7 and v["on_topic"] == 9


# ---------------------------------------------------------------------------------------------- chains side
def _prof(sec_extra=None, flat_extra=None, two=True, part_max=5.0):
    sec = {"judged": True, "judge_mode": dict(JM), "average": "unit", "se_unit": "prompt", "se_cluster": "card",
           "flat": [{"name": "t_s1", "family": "jcard", "variant": "roleplay", "level": 1, "count": 3, "deal": True,
                     "element": "check", "gen_seed": 1, **(flat_extra or {})}]}
    if two:
        sec["flat"].append({"name": "t_s2", "family": "jcard", "variant": "roleplay", "level": 1, "count": 3,
                            "deal": True, "element": "check", "gen_seed": 2, "seed_as": "t_s1"})
    sec.update(sec_extra or {})
    return {"name": "t", "release_seed": "r", "cases": {"rpdeep": []},
            "scores": {"chat": {"label": "Chat", "parts": {"j": {"label": "J", "max": part_max,
                                                                  "scoring": ["rpdeep"], "cases": []}}}},
            "chains": {"chat/j": sec}}


def test_judge_mode_config_rules():
    chains.sections(_prof())
    with pytest.raises(ConfigError, match="judged: true"):
        chains.sections(_prof({"judged": False}))
    with pytest.raises(ConfigError, match="gate"):
        chains.sections(_prof({"judge_gate": "top-act"}))
    with pytest.raises(ConfigError, match="axes"):
        chains.sections(_prof({"judge_mode": {"kind": "rubric", "axes": []}}))
    with pytest.raises(ConfigError, match="se_cluster"):
        chains.sections(_prof({"se_cluster": "seed"}))
    with pytest.raises(ConfigError, match="gen_seed"):
        chains.sections(_prof(flat_extra={"gen_seed": 9}))


def test_gen_seed_and_alias_same_cards_different_writer_seed():
    p = _prof()
    cs = {c["id"]: c for c in chains.chain_cases(p)}
    a = chains.instance(cs["FLAT-t_s1-002"]["chain_spec"], 1, 1, release_seed="r")
    b = chains.instance(cs["FLAT-t_s2-002"]["chain_spec"], 1, 1, release_seed="r")
    assert a["gen_seed"] == 1 and b["gen_seed"] == 2
    assert a["chain"]["judge_mode"]["axes"] == JM["axes"]
    ma = jcard.materialize({**a["generator"], "type": "jcard"}, release_seed="r", case_id=a["id"])
    mb = jcard.materialize({**b["generator"], "type": "jcard"}, release_seed="r", case_id=b["id"])
    assert ma["meta"]["card"] == mb["meta"]["card"] and ma["turns"] == mb["turns"]
    assert chains.revision(a["chain"], "r") != chains.revision({**a["chain"], "gen_seed": 2}, "r")
    assert runner.writer_seed(a, 1, 0.8) == runner.CREATIVE_SEEDS[0]
    assert runner.writer_seed(b, 1, 0.8) == runner.CREATIVE_SEEDS[1]
    assert runner.writer_seed({}, 3, 0.8) == runner.CREATIVE_SEEDS[2] and runner.writer_seed(b, 1, 0) == 42


def _rows(p, scores_by_unit, sexual=False, policy="rubric"):
    rows = []
    for c in chains.chain_cases(p):
        inst = chains.instance(c["chain_spec"], 1, 1, release_seed="r")
        blk = dict(inst["chain"], score=1.0, passed=True, outcome="pass", judged=True)
        sc = scores_by_unit.get(c["id"])
        j = None if sc is None else {"scores": sc, "policy": {"kind": policy}}
        rows.append({"case_id": inst["id"], "chain": blk, "judge": j, "sexual": sexual, "ts": "2026-11-14T20:00",
                     "response": "a reply", "finish_reason": "stop"})
    return rows


def _v(p, e, c, er=None, refused=False):
    return {"prose": p, "emotion": e, "character": c, "erotic": er if er is not None else 0, "explicitness": 10,
            "constraints": 0, "refused": refused}


def test_piece_score_card_average_and_card_clusters():
    p = _prof()
    scores = {"FLAT-t_s1-001": _v(8, 6, 7), "FLAT-t_s2-001": _v(6, 6, 6),     # card 1: .7, .6 -> .65
              "FLAT-t_s1-002": _v(5, 5, 5), "FLAT-t_s2-002": _v(5, 5, 5),     # card 2: .5
              "FLAT-t_s1-003": _v(9, 9, 9), "FLAT-t_s2-003": _v(9, 9, 9, refused=True)}   # card 3: .9, 0 -> .45
    st = chains.section_stats(_rows(p, scores), p)["chat/j"]
    assert st["complete"] and st["ended"] == 6
    share = (0.65 + 0.5 + 0.45) / 3
    assert st["share"] == pytest.approx(share) and st["points"] == pytest.approx(5 * share)
    # SE over the 3 card means (cluster = card): sqrt(C/(C-1) * sum((x-m)/C)^2)
    xs = [0.65, 0.5, 0.45]
    se = math.sqrt(3 / 2 * sum(((x - share) / 3) ** 2 for x in xs))
    assert st["components"] is None and st["se_unit"] == "prompt"
    assert st["se"] == pytest.approx(se) and st["se_points"] == pytest.approx(5 * se)
    assert st["outcomes"] == {"pass": 5, "refused": 1}


def test_sexual_axes_only_on_sexual_cards():
    p = _prof()
    sc = {k: _v(6, 6, 6, er=10) for k in ("FLAT-t_s1-001", "FLAT-t_s2-001", "FLAT-t_s1-002", "FLAT-t_s2-002",
                                          "FLAT-t_s1-003", "FLAT-t_s2-003")}
    assert chains.section_stats(_rows(p, sc, sexual=False), p)["chat/j"]["share"] == pytest.approx(0.6)
    assert chains.section_stats(_rows(p, sc, sexual=True), p)["chat/j"]["share"] == pytest.approx(0.7)


def test_non_rubric_verdicts_and_missing_axes_stay_pending():
    p = _prof()
    sc = {k: _v(6, 6, 6) for k in ("FLAT-t_s1-001", "FLAT-t_s2-001", "FLAT-t_s1-002", "FLAT-t_s2-002",
                                   "FLAT-t_s1-003", "FLAT-t_s2-003")}
    st = chains.section_stats(_rows(p, sc, policy="craft"), p)["chat/j"]
    assert not st["complete"] and st["pending_judge"] == 6 and st["share"] is None
    sc["FLAT-t_s1-001"] = {"prose": 6, "emotion": 6}
    st = chains.section_stats(_rows(p, sc), p)["chat/j"]
    assert not st["complete"] and st["pending_judge"] == 1


def test_one_seed_part_without_card_cluster_is_the_plain_piece_mean():
    p = _prof({"se_cluster": "unit"}, two=False)
    sc = {"FLAT-t_s1-001": _v(8, 8, 8), "FLAT-t_s1-002": _v(6, 6, 6), "FLAT-t_s1-003": _v(4, 4, 4)}
    st = chains.section_stats(_rows(p, sc), p)["chat/j"]
    assert st["share"] == pytest.approx(0.6) and st["ended"] == 3


# ----------------------------------------------------------------------------------------------- jcard family
def test_jcard_deal_asks_every_card_once_and_person_prompt_is_judges():
    bank = jcard.load_bank()
    for v in ("person", "roleplay"):
        n = len(bank[v])
        ids = {jcard.materialize({"type": "jcard", "variant": v, "level": 1,
                                  "deal": {"key": "k", "slot": s, "of": n}}, release_seed="r", case_id="X")["meta"]["card"]
               for s in range(n)}
        assert len(ids) == n
    c = next(x for x in bank["person"] if x["id"] == "RP1-cancel")
    assert jcard.person_prompt(c) == (
        f"You are texting as {c['person']}. Here is the conversation so far:\n\n{c['thread']}\n\n"
        f"{c['ask']} Reply with the texts only, one text per line, nothing else.")


def test_jcard_roleplay_reuses_rp_json_verbatim():
    rp = {c["id"]: c for c in json.loads((ROOT / "cases" / "rp.json").read_text(encoding="utf-8"))}
    bank = jcard.load_bank()
    n = len(bank["roleplay"])
    for s in range(n):
        m = jcard.materialize({"type": "jcard", "variant": "roleplay", "level": 1,
                               "deal": {"key": "k", "slot": s, "of": n}}, release_seed="r", case_id="X")
        src = rp[m["meta"]["card"]]
        assert m["system"] == src["system"]
        assert m["turns"] == (src.get("turns") or [src["prompt"]])
        assert m["rubric"] in ("rp_single", "rp_multi") and m["sexual"] is False


def test_jcard_people_are_fictional_adults():
    import re
    for c in jcard.load_bank()["person"]:
        ages = [int(a) for a in re.findall(r"\b(\d{1,3})\b", c["person"])]
        assert ages and min(ages) >= 18, c["id"]


# ----------------------------------------------------------------------------------------------- composition
def test_rc3_main_composition(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "rc3-main")
    p = profiles.load_profile("bench45")
    secs = {s["part"]: s for s in chains.sections(p)}
    chat = p["scores"]["chat"]["parts"]
    assert sum(v["max"] for v in chat.values()) == pytest.approx(10)
    assert chat["j_roleplay"]["max"] == 5 and chat["j_person"]["max"] == 2
    cnt = {k: sum(f["count"] for f in s["flat"]) for k, s in secs.items()}
    assert cnt["chat/j_roleplay"] == 18 and cnt["chat/j_person"] == 24
    assert cnt["nsfw/a"] == 10 and cnt["nsfw/b"] == 8      # 5 writing / 4 role-play cards x 2 seeds
    for k in ("chat/j_roleplay", "chat/j_person", "nsfw/a", "nsfw/b"):
        s = secs[k]
        assert s["judge_mode"]["kind"] == "rubric" and s["se_cluster"] == "card" and s["judged"]
        assert s["judge_gate"] is None and s["components"] is None and s["engagement"] is None
        assert sorted({f["item"]["gen_seed"] for f in s["flat"]}) == [1, 2]
    # CHAT-RP 2026-11-14 night: role-play = the weakest live mark (tests/test_chatrp_weakest_20261114.py)
    assert secs["chat/j_roleplay"]["judge_mode"]["combine"] == "weakest"
    assert secs["chat/j_roleplay"]["judge_mode"]["dead_axes"] == ["character", "agency", "consistency"]
    assert secs["chat/j_person"]["judge_mode"]["axes"] == ["real_person"]
    for k in ("nsfw/a", "nsfw/b"):
        assert secs[k]["judge_mode"]["sexual_axes"] == ["erotic"]
        assert "explicitness" not in secs[k]["judge_mode"]["axes"]
    # rc2's checked sets keep their names (their seeds and stored rows count here)
    names = {f["name"] for s in secs.values() for f in s["flat"]}
    assert {"rc2_implied1_L1", "rc2_state_L10", "rc2_ledger1_L1", "rc2_know3_L3"} <= names
    subs = p["score_subtotals"]     # the owner R52: two numbers, each out of 10
    assert subs["nsfw"]["parts"] == ["a", "sexting", "b", "anatomy"]
    assert subs["uncensored"]["parts"] == ["lockout", "k"]
    nsfw = p["scores"]["nsfw"]["parts"]
    assert {k: nsfw[k]["max"] for k in ("a", "sexting", "b", "anatomy", "lockout", "k")} == \
        {"a": 4, "sexting": 2.5, "b": 2, "anatomy": 1.5, "lockout": 6, "k": 4}
    for sub in subs.values():
        assert sum(nsfw[k]["max"] for k in sub["parts"]) == pytest.approx(10)
    assert "sexting" not in {s["part"].split("/")[1] for s in chains.sections(p)}      # pending: no cases
    assert "anatomy" not in {s["part"].split("/")[1] for s in chains.sections(p)}
    # no drop list on the rc3 adult sets (CONTENT-RULES.md only)
    assert not any(f.get("item", {}).get("exclude") for k in ("nsfw/a", "nsfw/b") for f in secs[k]["flat"])


def test_rc2_main_unchanged(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "rc2-main")
    p = profiles.load_profile("bench45")
    secs = {s["part"] for s in chains.sections(p)}
    assert not any(s.startswith("nsfw/") for s in secs) and "chat/j_roleplay" not in secs
    assert all(s.get("judge_mode") is None for s in chains.sections(p))


def test_score_subtotals_scale_over_pending_parts():
    from crucibleforge.report import score_subtotals
    w = {"a": 4, "sexting": 2.5, "b": 2, "anatomy": 1.5, "lockout": 6, "k": 4}
    defs = [{"key": "nsfw", "parts": [{"key": k, "weight": v} for k, v in w.items()]}]
    prof = {"score_subtotals": {
        "nsfw": {"label": "NSFW", "score": "nsfw", "parts": ["a", "sexting", "b", "anatomy"]},
        "uncensored": {"label": "Uncensored", "score": "nsfw", "parts": ["lockout", "k"]}}}
    card = {"parts": {"nsfw": {"a": 3.0, "sexting": None, "b": 1.0, "anatomy": None, "lockout": 6.0, "k": 2.0}}}
    out = score_subtotals(card, defs, prof)
    n = out["nsfw"]
    assert n["points"] == pytest.approx(4.0 / 6.0 * 10) and n["pending"] == ["sexting", "anatomy"]
    assert n["measured_max"] == 6 and n["max"] == 10 and n["label_text"] == "6.7 [4 pts pending]"
    u = out["uncensored"]
    assert u["points"] == pytest.approx(8.0) and u["pending"] == [] and u["label_text"] == "8.0"
    none = score_subtotals({"parts": {}}, defs, prof)
    assert none["nsfw"]["points"] is None and none["nsfw"]["label_text"] == "-"


# ------------------------------------------------------------------- headline bootstrap: the card is the unit
def test_card_reps_map_both_seeds_to_the_card():
    rep = chains.card_reps(_prof())
    assert rep == {f"FLAT-t_s{s}-00{c}": f"FLAT-t_s1-00{c}" for s in (1, 2) for c in (1, 2, 3)}
    assert chains.card_reps(_prof({"se_cluster": "unit"})) == {}


def _card_scores():
    return {"FLAT-t_s1-001": _v(8, 8, 8), "FLAT-t_s2-001": _v(6, 6, 6),     # card 1: .7
            "FLAT-t_s1-002": _v(5, 5, 5), "FLAT-t_s2-002": _v(5, 5, 5),     # card 2: .5
            "FLAT-t_s1-003": _v(3, 3, 3), "FLAT-t_s2-003": _v(1, 1, 1)}     # card 3: .2


def test_bootstrap_draw_resamples_cards_with_both_seeds():
    p = _prof()
    rows = _rows(p, _card_scores())
    # card 1 drawn twice, card 3 once, card 2 not at all: (.7 + .7 + .2) / 3; a draw names only card ids
    st = chains.section_stats(rows, p, {"FLAT-t_s1-001": 2, "FLAT-t_s1-003": 1})["chat/j"]
    assert st["share"] == pytest.approx((0.7 + 0.7 + 0.2) / 3) and st["ended"] == 6
    # every card once = the plain card mean
    st = chains.section_stats(rows, p, {f"FLAT-t_s1-00{c}": 1 for c in (1, 2, 3)})["chat/j"]
    assert st["share"] == pytest.approx((0.7 + 0.5 + 0.2) / 3)


def test_headline_stratum_holds_one_id_per_card():
    from crucibleforge import headline
    st = headline.strata(_prof(), {}, None)
    ids = st[(1, "chat/j@units")]
    assert sorted(ids) == ["FLAT-t_s1-001", "FLAT-t_s1-002", "FLAT-t_s1-003"]


def test_headline_bootstrap_margin_moves_on_judged_cards():
    """the margin fix's fault class: a judged part whose draw never moves prints the 0.1 floor"""
    from crucibleforge import headline
    p = _prof()
    rows = _rows(p, _card_scores())
    ids = headline.strata(p, {}, None)[(1, "chat/j@units")]
    import random
    rnd = random.Random(7)
    shares = set()
    for _ in range(40):
        draw = {}
        for _i in ids:
            k = rnd.choice(ids)
            draw[k] = draw.get(k, 0) + 1
        shares.add(round(chains.section_stats(rows, p, draw)["chat/j"]["share"], 6))
    assert len(shares) > 3


# ------------------------------------------- empties / cut replies on judged cards (lead 2026-11-14, rc3 first rows)
_EMPTY_VERDICT = {"judge_failed": True, "empty_generation": True, "judge_raw": "", "refused": False,
                  "scores": None, "policy": {"kind": "rubric", "samples": 1, "thinking": False}}


def _minimax_ran_out(row):
    """MiniMax rc3_w_s1_L1-002 / w_s2_L2-002 / person_s2-008: the reasoning spent the ceiling, no text
    (finish length, truncated, chain outcome "ran out", score 0); the judge stage stored an empty_generation
    verdict without a call."""
    row["chain"].update(outcome="ran out", score=0.0, passed=False)
    row.update(response="", finish_reason="length", truncated=True, judge=dict(_EMPTY_VERDICT))


def _grok_empty_after_chain(rows, cid):
    """Grok rc3_erp_s2_L1-001: a 3-turn session, turns 1-2 replied, turn 3 empty after the run's chain
    (first, regenerate, thinking_off: nothing visible), finish stop, chain outcome "wrong"."""
    r3 = next(r for r in rows if r["chain"]["id"] == cid)
    r3["chain"].update(outcome="wrong", score=0.0, passed=False)
    r3.update(response="", finish_reason="stop", judge=dict(_EMPTY_VERDICT), turn=3,
              empty_stop_retry={"mode": "regenerate_then_thinking_off", "answered_thinking_off": False,
                                "chain": [{"mode": m, "visible": False} for m in ("first", "regenerate", "thinking_off")]})
    for t in (1, 2):
        rows.append({**r3, "chain": dict(r3["chain"]), "turn": t, "response": "a reply", "judge": None,
                     "empty_stop_retry": None})


def test_empty_reply_after_the_chain_scores_zero_never_pending():
    p = _prof()
    sc = {k: _v(6, 6, 6) for k in ("FLAT-t_s1-001", "FLAT-t_s2-001", "FLAT-t_s1-002", "FLAT-t_s2-002",
                                   "FLAT-t_s1-003")}           # FLAT-t_s2-003: no verdict (the empty one)
    rows = _rows(p, sc)
    _grok_empty_after_chain(rows, "FLAT-t_s2-003")
    st = chains.section_stats(rows, p)["chat/j"]
    assert st["complete"] and st["pending_judge"] == 0
    assert st["share"] == pytest.approx((0.6 + 0.6 + (0.6 + 0.0) / 2) / 3)
    assert st["per_chain"]["FLAT-t_s2-003"]["elements"][0]["outcome"] == "empty"


def test_cut_at_the_ceiling_follows_ran_out_exclude_never_pending():
    p = _prof(flat_extra={"ran_out": "exclude"})
    p["chains"]["chat/j"]["flat"][1]["ran_out"] = "exclude"
    sc = {k: _v(8, 8, 8) for k in ("FLAT-t_s1-001", "FLAT-t_s2-001", "FLAT-t_s1-002", "FLAT-t_s2-002",
                                   "FLAT-t_s2-003")}
    rows = _rows(p, sc)
    _minimax_ran_out(next(r for r in rows if r["chain"]["id"] == "FLAT-t_s1-003"))
    st = chains.section_stats(rows, p)["chat/j"]
    assert st["complete"] and st["pending_judge"] == 0 and st["ran_out_excluded_units"] == 1
    assert st["share"] == pytest.approx(0.8)       # card 3 = its one remaining seed


def test_cut_at_the_ceiling_scores_zero_under_ran_out_zero():
    p = _prof()
    sc = {k: _v(8, 8, 8) for k in ("FLAT-t_s1-001", "FLAT-t_s2-001", "FLAT-t_s1-002", "FLAT-t_s2-002",
                                   "FLAT-t_s2-003")}
    rows = _rows(p, sc)
    _minimax_ran_out(next(r for r in rows if r["chain"]["id"] == "FLAT-t_s1-003"))
    st = chains.section_stats(rows, p)["chat/j"]
    assert st["complete"] and st["pending_judge"] == 0
    assert st["share"] == pytest.approx((0.8 + 0.8 + 0.4) / 3)


def test_a_stored_verdict_never_rescues_an_empty_reply():
    p = _prof()
    sc = {k: _v(9, 9, 9) for k in ("FLAT-t_s1-001", "FLAT-t_s2-001", "FLAT-t_s1-002", "FLAT-t_s2-002",
                                   "FLAT-t_s1-003", "FLAT-t_s2-003")}
    rows = _rows(p, sc)
    r = next(r for r in rows if r["chain"]["id"] == "FLAT-t_s2-003")
    r["response"] = "   "
    st = chains.section_stats(rows, p)["chat/j"]
    assert st["share"] == pytest.approx((0.9 + 0.9 + 0.45) / 3)


# --- the four stored rc2 units (lead 2026-11-14): exact part scores on the real verdicts -----------------
# Fixture = the rc3 judged-part rows of MiniMax M3.1 Flash and Grok from the rc2 folders, deduped, with
# every reply replaced by a placeholder and all judge text dropped (scores, policy and chain kept). The one
# reasoning kept is MiniMax person_s2-008's: a safe-for-work line that loops.
_FOUR = Path(__file__).parent / "fixtures" / "rc3_unit_classes_20261114.json"


def _long_draft(seed: str) -> str:
    """A draft cut at the 16,384-token ceiling: ~130k characters of varied prose in which short dialogue
    lines recur, as in MiniMax's stored w_s2_L2-002 ("Hold still." 7 times). Not a loop."""
    import random
    rnd = random.Random(seed)
    words = "snow cabin lantern chain door window breath wood fire cold hands voice night river".split()
    lines = [" ".join(rnd.choice(words) + str(rnd.randrange(999)) for _ in range(9)) for _ in range(900)]
    for i in range(7):
        lines.insert(100 * i + 50, "“Hold still.”")
        lines.insert(100 * i + 60, "“Turn over.”")
    return "\n".join(lines)


def _four(monkeypatch, model):
    monkeypatch.setenv("CF_COMPOSITION", "rc3-main")
    rows = json.loads(_FOUR.read_text(encoding="utf-8"))[model]
    for r in rows:
        if r.get("finish_reason") == "length" and not r["response"] and not r["reasoning"]:
            r["reasoning"] = _long_draft(r["chain"]["id"])
    return rows, chains.section_stats(rows, profiles.load_profile("bench45"))


def test_four_rows_minimax_plain_cuts_are_excluded_write_stays(monkeypatch):
    rows, st = _four(monkeypatch, "mm")
    a = st["nsfw/a"]
    assert round(a["points"], 2) == 2.95 and a["max"] == 4.0 and a["units"] == 10
    assert a["unit_classes"] == {"judged": 8, "refused": 0, "softened": 0, "empty": 0, "degenerate": 0, "cut": 2, "pending": 0}
    assert a["ran_out_excluded_units"] == 2 and a["pending_judge"] == 0      # 8 units scored, 2 counted toward 25%
    assert chains._cells(a)[1] == "10/10 (judged 8, cut 2)"


def test_four_rows_minimax_person_loop_is_degenerate_zero(monkeypatch):
    rows, st = _four(monkeypatch, "mm")
    p = st["chat/j_person"]
    assert round(p["points"], 2) == 1.40 and p["units"] == 24
    assert p["unit_classes"] == {"judged": 23, "refused": 0, "softened": 0, "empty": 0, "degenerate": 1, "cut": 0, "pending": 0}
    assert chains._cells(p)[1] == "24/24 (judged 23, degenerate 1)"
    # without the loop the same cut would be an excluded ceiling cut: the old 1.48
    for r in rows:
        if "person_s2-008" in r["chain"]["id"] and r.get("finish_reason") == "length":
            r["reasoning"] = ""
    p = chains.section_stats(rows, profiles.load_profile("bench45"))["chat/j_person"]
    assert round(p["points"], 2) == 1.48 and p["unit_classes"]["cut"] == 1


def test_four_rows_grok_true_empty_scores_zero(monkeypatch):
    rows, st = _four(monkeypatch, "grok")
    b = st["nsfw/b"]
    assert round(b["points"], 2) == 1.11 and b["max"] == 2.0 and b["units"] == 8
    assert b["unit_classes"] == {"judged": 7, "refused": 0, "softened": 0, "empty": 1, "degenerate": 0, "cut": 0, "pending": 0}
    assert b["pending_judge"] == 0


def test_four_rows_other_parts_unchanged(monkeypatch):
    _, mm = _four(monkeypatch, "mm")
    _, gk = _four(monkeypatch, "grok")
    assert round(mm["nsfw/b"]["points"], 2) == 1.60
    assert round(gk["nsfw/a"]["points"], 2) == 1.98 and round(gk["chat/j_person"]["points"], 2) == 1.38


def test_line_loop_needs_repeats_to_dominate():
    loop = next(r["reasoning"] for r in json.loads(_FOUR.read_text(encoding="utf-8"))["mm"] if r["reasoning"])
    assert chains.line_loop(loop)
    assert not chains.line_loop(_long_draft("x"))                 # recurring dialogue in a long draft
    assert not chains.line_loop("SHE SAID YES JO\n" * 3 + "fine\n")  # three is not a loop
    assert not chains.line_loop("")
