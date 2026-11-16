"""RPSESSION: the six long role-play session cards (CHAT-RP, lead 2026-11-14 night; EXP-20261114-35 section 4) and
the screen-chatrp-pilot composition. Each card's hand-written reference must pass every code check and its
hand-written foil must fail EXACTLY the checks it names, so the checks are proven to fire both ways. The recipe is
held structurally: 8-12 turns, a restrictive voice, two canon misstatements, two withheld facts pushed twice each,
late recall of details planted before turn 4, one scene change with a bleed check (+ Tally / Know items for the
packs, lead + PACKS2)."""
import copy
import os
import re

import pytest

from crucibleforge import chains, gen, judge as J, profiles, session_checks as S
from crucibleforge.config import ConfigError
from crucibleforge.gen import rpsession as R
from crucibleforge.gen._stack_common import join_turns

CARDS = R.cards()
IDS = [c["id"] for c in CARDS]


def _kind(card, kind):
    return [c for c in card["checks"] if c["kind"] == kind]


def _rx(c):
    return re.compile(c["pattern"], re.I | re.M)


def _fails(card, replies, variant="scene"):
    out = S.run_checks({"checks": R.checks_of(card, variant)}, replies)
    return sorted(r["id"] for r in out["results"] if not r["pass"])


# --------------------------------------------------------------------------------------------- the recipe
def test_six_cards_six_genres_safe_for_work():
    assert len(CARDS) == 6 and len({c["genre"] for c in CARDS}) == 6
    for c in CARDS:
        text = c["system"] + " ".join(c["turns"]) + " ".join(c["reference"])
        assert not re.search(r"\b(?:sex|sexual|naked|nude|kiss(?:es|ed|ing)?|bed\b)", text, re.I), c["id"]
        assert not re.search(r"\b(?:child|children|kid|kids|girl|teen\w*|schoolboy)\b", text, re.I), c["id"]


@pytest.mark.parametrize("cid", IDS)
def test_card_follows_the_recipe(cid):
    c = next(x for x in CARDS if x["id"] == cid)
    n = len(c["turns"])
    assert 8 <= n <= 12 and len(c["reference"]) == n
    ids = {k["id"] for k in c["checks"]}
    assert {"voice-cap", "voice-banned", "voice-habit", "canon1-held", "nopuppet", "bleed"} <= ids
    # two withheld facts, each pushed twice (two checks on two different turns)
    for r in ("ruleA", "ruleB"):
        turns = sorted(k["turns"][0] for k in _kind(c, "rule") if k["id"].startswith(r + "@"))
        assert len(turns) == 2 and turns[0] < turns[1], (r, turns)
    # canon: the first misstatement at turn 4-5, the subtler one later
    c1 = next(k for k in _kind(c, "canon") if k["id"].startswith("canon1@"))
    c2 = next(k for k in _kind(c, "canon") if k["id"].startswith("canon2@"))
    assert 4 <= c1["turns"][0] <= 5 and c2["turns"][0] > c1["turns"][0] + 1
    # recall: 2-3 details asked at turn 8+, each planted in a user turn before turn 4 and not restated after
    rec = _kind(c, "recall")
    assert 2 <= len(rec) <= 3
    for k in rec:
        t = k["turns"][0]
        assert t >= 8
        assert any(_rx(k).search(u) for u in c["turns"][:3]), (k["id"], "not planted before turn 4")
        assert not _rx(k).search(c["turns"][t - 1]), (k["id"], "restated in the recall turn")
    # the scene change: bleed reads from the change on; its names are in the card and never in the user's later turns
    sc = c["scene_change_turn"]
    b = next(k for k in c["checks"] if k["id"] == "bleed")
    assert b["turns"][0] == sc and b["turns"][-1] == n and 4 <= sc <= 7
    assert _rx(b).search(c["system"])
    assert not any(_rx(b).search(u) for u in c["turns"][sc - 1:])


@pytest.mark.parametrize("cid", IDS)
def test_withheld_facts_can_never_be_echoed_from_the_user(cid):
    """A rule check fails on the secret itself, which only the character card holds: the user never says it, so an
    in-character reply that quotes the user cannot fail a rule by accident."""
    c = next(x for x in CARDS if x["id"] == cid)
    for k in _kind(c, "rule"):
        assert not any(_rx(k).search(u) for u in c["turns"]), k["id"]
    # the canon correction is not handed over by the misstating turn itself
    for k in _kind(c, "canon"):
        if "@" in k["id"]:
            assert not _rx(k).search(c["turns"][k["turns"][0] - 1]), k["id"]


def test_tally_and_know_items_for_the_packs():
    tal = [c for c in CARDS if _kind(c, "tally")]
    kno = [c for c in CARDS if _kind(c, "know")]
    assert len(tal) >= 3 and len(kno) >= 3
    for c in tal:
        k = _kind(c, "tally")[0]
        assert k["turns"][0] >= 9
        # the counted quantity changes in at least four user turns before it is asked for
        word = {"RPS3-bakery": r"\brolls?\b", "RPS4-ferry": r"\bdollars?\b", "RPS6-arctic": r"\bsacks?\b"}[c["id"]]
        moves = [i for i, u in enumerate(c["turns"][:k["turns"][0] - 1], 1) if re.search(word, u, re.I)]
        assert len(moves) >= 4, (c["id"], moves)
    for c in kno:
        k = _kind(c, "know")[0]
        assert re.search(r"\b(?:agreed|said|told)\b", c["turns"][k["turns"][0] - 1])


# ------------------------------------------------------------------------------ the checks fire both ways
@pytest.mark.parametrize("cid", IDS)
def test_reference_transcript_passes_every_check(cid):
    c = next(x for x in CARDS if x["id"] == cid)
    assert _fails(c, R.reference_replies(c)) == []
    mat = R.materialize({"type": "rpsession", "variant": "scene", "card": cid}, release_seed="r", case_id="X")
    assert R.grade(join_turns(R.reference_replies(c)), mat) == 1.0


@pytest.mark.parametrize("cid", IDS)
def test_foil_transcript_breaks_exactly_the_named_checks(cid):
    c = next(x for x in CARDS if x["id"] == cid)
    assert _fails(c, R.foil_replies(c)) == sorted(c["foil"]["breaks"])
    # every family of check is proven to fire at least once across the foil
    kinds = {k["kind"] for k in c["checks"] if k["id"] in c["foil"]["breaks"]}
    assert {"rule", "canon", "recall", "bleed", "voice"} <= kinds


@pytest.mark.parametrize("cid", IDS)
def test_forgetful_recall_fails_only_the_recall_checks(cid):
    c = next(x for x in CARDS if x["id"] == cid)
    reps = R.reference_replies(c)
    rt = _kind(c, "recall")[0]["turns"][0]
    reps[rt - 1] = c["forgetful_recall"]
    assert _fails(c, reps) == sorted(k["id"] for k in _kind(c, "recall"))


@pytest.mark.parametrize("cid", IDS)
def test_board_code_share_equals_the_family_grade(cid):
    """chains.check_results (the board's check elements: the block score is their mean) == grade()."""
    c = next(x for x in CARDS if x["id"] == cid)
    mat = R.materialize({"type": "rpsession", "variant": "scene", "card": cid}, release_seed="r", case_id="X")
    for reps in (R.reference_replies(c), R.foil_replies(c)):
        res = chains.check_results(mat, reps)
        assert len(res) == len(mat["checks"])
        assert sum(v for _, v, _, _ in res) / len(res) == pytest.approx(R.grade(join_turns(reps), mat))


# ------------------------------------------------------------------------- pack variants (judge off, PACKS2)
@pytest.mark.parametrize("variant,kinds", [("state", {"rule", "recall", "canon", "bleed", "nopuppet"}),
                                           ("tally", {"tally"}), ("know", {"know"})])
def test_pack_variants_are_checks_only_and_drawable_by_a_set_file(variant, kinds):
    cs = R.cards(variant)
    assert len(cs) >= (6 if variant == "state" else 3)
    for c in cs:
        case = {"id": f"PK-{c['id']}", "category": "rpdeep",
                "generator": {"type": "rpsession", "variant": variant, "level": 1, "card": c["id"]}}
        m = gen.materialize_case(case)
        assert "rubric" not in m and m["programmatic"] is True and "judge_key" not in m
        assert {k["id"] for k in m["checks"]} == {k["id"] for k in c["checks"] if k["kind"] in kinds}
        assert gen.verify_case(case) == []
        fam = gen.get("rpsession")
        g = case["generator"]
        for ans in [fam.reference(g, release_seed="r", case_id=case["id"])] + \
                fam.mutants(g, release_seed="r", case_id=case["id"]):
            assert gen.engine_score({**case, **m}, ans) == pytest.approx(fam.grade(ans, m), abs=1e-3)
    if variant != "state":
        other = next(c for c in CARDS if c not in cs)
        with pytest.raises(gen.GeneratorError):
            gen.materialize_case({"id": "x", "category": "rpdeep",
                                  "generator": {"type": "rpsession", "variant": variant, "level": 1,
                                                "card": other["id"]}})


# ----------------------------------------------------------------------------------------- judge + scoring
def _scene_row(card):
    mat = R.materialize({"type": "rpsession", "variant": "scene", "card": card["id"]}, release_seed="r",
                        case_id="X")
    conv = [{"role": "system", "content": mat["system"]}]
    for u, a in zip(mat["turns"], R.reference_replies(card)):
        conv += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
    return {"case_id": "FLAT-chatrp_pilot_s1-001-r1-a1", "rubric": mat["rubric"], "conversation": conv,
            "judge_key": mat["judge_key"],
            "chain": {"judge_mode": {"kind": "rubric", "axes": ["prose", "dialogue", "emotion", "atmosphere"],
                                     "combine": "weakest", "code_weight": 0.5, "max_tokens": 1024}}}


def test_rp_long_rubric_is_new_and_leaves_rp_scene_alone():
    assert J.RUBRICS["rp_long"]["dims"] == ["prose", "dialogue", "emotion", "atmosphere"]
    assert J.RUBRICS["rp_scene"]["schema"]["json_schema"]["name"] == "rp_scene_verdict"     # the 4.x rubric
    assert "careful editor" in J.RUBRICS["rp_long"]["text"]


@pytest.mark.parametrize("cid", IDS)
def test_judge_prompt_fits_and_is_rubric_mode(cid):
    row = _scene_row(next(x for x in CARDS if x["id"] == cid))
    rubric, prompt = J.build_judge_input(row)
    assert rubric == "rp_long" and J._CLAMP_MARK not in prompt
    assert len(J.judge_system(row)) + len(prompt) <= J.judge_input_budget_chars(16384, 1024)
    assert "ANSWER KEY" in prompt and row["judge_key"] in prompt
    pol = J.judge_policy(row)
    assert pol["kind"] == "rubric" and pol["thinking"] is False and pol["max_tokens"] == 1024
    v = J.parse_verdict('{"prose": 8, "dialogue": 7, "emotion": 6, "atmosphere": 9, "refused": false, '
                        '"note": "ok"}', "rp_long")
    assert v["emotion"] == 6


def test_code_weight_mixes_the_weakest_mark_with_the_code_share():
    jm = {"kind": "rubric", "axes": ["prose", "dialogue", "emotion", "atmosphere"], "sexual_axes": [],
          "combine": "weakest", "dead_axes": [], "code_weight": 0.5}
    blk = {"id": "U", "score": 0.75}
    el = chains.rubric_element(blk, {"scores": {"prose": 9, "dialogue": 8, "emotion": 6, "atmosphere": 9}}, {}, jm)
    assert el["score"] == pytest.approx(0.5 * 0.6 + 0.5 * 0.75)
    assert el["judge_share"] == pytest.approx(0.6) and el["code_share"] == pytest.approx(0.75)
    el = chains.rubric_element(blk, {"scores": {"prose": 9, "dialogue": 8, "emotion": 6, "atmosphere": 9,
                                                "refused": True}}, {}, jm)
    assert el["score"] == 0.0
    # without code_weight nothing changes (rc3-main)
    jm0 = {k: v for k, v in jm.items() if k != "code_weight"}
    assert chains.rubric_element(blk, {"scores": {"prose": 9, "dialogue": 8, "emotion": 6, "atmosphere": 9}},
                                 {}, jm0)["score"] == pytest.approx(0.6)


def _sec(jm):
    return {"name": "t", "release_seed": "r", "cases": {"rpdeep": []},
            "scores": {"chat": {"label": "Chat", "parts": {"j": {"label": "J", "max": 5, "scoring": ["rpdeep"],
                                                                  "cases": []}}}},
            "chains": {"chat/j": {"judged": True, "judge_mode": jm, "average": "unit", "se_unit": "prompt",
                                  "flat": [{"name": "t_s1", "family": "rpsession", "variant": "scene", "level": 1,
                                            "count": 2, "deal": True, "element": "check", "gen_seed": 1}]}}}


def test_code_weight_config_rules():
    ok = {"kind": "rubric", "axes": ["prose"], "code_weight": 0.5}
    assert chains.sections(_sec(ok))[0]["judge_mode"]["code_weight"] == 0.5
    for bad in (1, 1.5, -0.1, True, "half"):
        with pytest.raises(ConfigError, match="code_weight"):
            chains.sections(_sec({**ok, "code_weight": bad}))


def _pilot_rows(p, verdicts, code):
    rows = []
    for c in chains.chain_cases(p):
        inst = chains.instance(c["chain_spec"], 1, 1, release_seed=p["release_seed"])
        blk = dict(inst["chain"], score=code, passed=True, outcome="pass", judged=True)
        rows.append({"case_id": inst["id"], "chain": blk, "ts": "2026-11-15T01:00", "response": "a reply",
                     "finish_reason": "stop", "judge": {"scores": verdicts, "policy": {"kind": "rubric"}}})
    return rows


def test_pilot_composition(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "screen-chatrp-pilot")
    p = profiles.load_profile("bench45")
    secs = {s["part"]: s for s in chains.sections(p)}
    assert list(secs) == ["chat/j_rpsession"]
    s = secs["chat/j_rpsession"]
    assert s["judge_mode"] == {"kind": "rubric", "axes": ["prose", "dialogue", "emotion", "atmosphere"],
                               "sexual_axes": [], "combine": "weakest", "dead_axes": [], "code_weight": 0.5,
                               "max_tokens": 1024}
    assert s["se_cluster"] == "card" and sorted({f["item"]["gen_seed"] for f in s["flat"]}) == [1, 2]
    chains.check_gradeable(p)
    cards = set()
    for c in chains.chain_cases(p):
        inst = chains.instance(c["chain_spec"], 1, 1, release_seed=p["release_seed"])
        case = {"id": inst["id"], "category": "rpdeep", "generator": inst["generator"]}
        m = gen.materialize_case(case)
        cards.add(m["meta"]["card"])
        assert m["rubric"] == "rp_long" and gen.verify_case(case) == []
    assert cards == set(IDS)
    # the pilot's part: 0.5 x the weakest mark + 0.5 x the code share, both seeds averaged in a card
    rows = _pilot_rows(p, {"prose": 9, "dialogue": 8, "emotion": 6, "atmosphere": 7}, 0.8)
    st = chains.section_stats(rows, p)["chat/j_rpsession"]
    assert st["complete"] and st["unit_classes"]["judged"] == 12
    assert st["points"] == pytest.approx(5 * (0.5 * 0.6 + 0.5 * 0.8))
    # not in the shipped composition
    monkeypatch.setenv("CF_COMPOSITION", "rc3-main")
    assert not any(f["item"]["family"] == "rpsession" for s in chains.sections(profiles.load_profile("bench45"))
                   for f in s["flat"])
