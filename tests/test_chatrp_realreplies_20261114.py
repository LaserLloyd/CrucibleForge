"""RPSESSION checks on REAL replies (CHAT-RP, lead 2026-11-14 night: four faults the pack owner found on DeepSeek
flash / MiniMax / Sonnet rows, plus D2's pilot rows). Each fixture entry is one reply to one turn with the verdict a
careful reader gives it; the check (the bank's current spec) must agree.

* ruleB (greenhouse): a refusal that names the pumps ("shutdown refused", "Pump shutdown is denied") is the rule
  kept, not broken;
* know: an explicit correction declines the false memory ("A small correction", "did not report", "I agreed to
  identify the coat, not place my signature"); agreeing, signing, or quietly not engaging does not;
* tally (bakery): the number as word or digit next to the noun OR an answer word ("Eight, all told.");
* bleed: PRESENCE in scene two, not mention ("Biscuit's minding the landing" passes; "the crew photograph remains
  visible beside it" fails); a scene-one person addressed or set to work in scene two is present ("Alfred, fetch the
  ink")."""
import json
from pathlib import Path

import pytest

from crucibleforge import session_checks
from crucibleforge.gen import rpsession

FIX = json.loads((Path(__file__).parent / "fixtures" / "rpsession_real_replies_20261114.json").read_text())["entries"]
CARDS = {c["id"]: c for c in rpsession.load_bank()["cards"]}


def _grade(card_id, check_id, turn, text):
    card = CARDS[card_id]
    spec = next(c for c in rpsession.checks_of(card) if c["id"] == check_id)
    spec = dict(spec, turns=[turn])
    replies = [""] * len(card["turns"])
    replies[turn - 1] = text
    res = session_checks.run_checks({"checks": [spec]}, replies)["results"]
    return next(r for r in res if r["id"] == check_id)


@pytest.mark.parametrize("e", FIX, ids=[f'{e["card"]}-{e["check"]}-{e["model"]}-{i}' for i, e in enumerate(FIX)])
def test_check_agrees_with_the_careful_reader(e):
    r = _grade(e["card"], e["check"], e["turn"], e["text"])
    assert r["pass"] is e["want"], (e["why"], r["detail"])


def test_the_fixture_covers_the_four_faults_both_ways():
    seen = {(e["check"].split("@")[0], e["want"]) for e in FIX}
    for k in ("ruleB", "know", "tally", "bleed", "bleed-address"):
        assert (k, True) in seen, k
    for k in ("know", "tally", "bleed", "bleed-address"):
        assert (k, False) in seen


def test_tally_counts_follow_the_users_stated_moves_only():
    """PACKS2: the count must not depend on what the character chose to do. Every move that changes it is the
    user's own, stated as done (the character can refuse nothing that the count rests on)."""
    ferry, arctic = CARDS["RPS4-ferry"], CARDS["RPS6-arctic"]
    assert "lays a silver dollar on the ferry bench" in ferry["turns"][0]
    assert "on the bell post" in ferry["turns"][2]
    assert "lays them on the bench" in ferry["turns"][4]
    assert "I took one of my dollars back" in ferry["turns"][6]
    assert "slings it into his cabin" in arctic["turns"][6]
    for c in (ferry, arctic):
        assert not any(w in " ".join(c["turns"]).lower() for w in ("give me back", "hand me your"))


def test_know_cards_carry_two_probes():
    for cid in ("RPS1-harbour", "RPS2-greenhouse", "RPS5-butler"):
        ks = [c["id"] for c in CARDS[cid]["checks"] if c["kind"] == "know"]
        assert ks == ["know@6", "know@9"], (cid, ks)


def test_bank_edits_move_the_revision():
    """The bank is data (not in chains.revision's family-source digest); the family pins its sha so that every
    bank edit changes the family source, hence the revision: stored rows go stale and regrade sorts them."""
    assert rpsession.bank_sha() == rpsession.BANK_SHA


def test_jcard_data_is_pinned_and_the_four_low_cards_are_rewritten():
    from crucibleforge.gen import jcard
    assert jcard.file_sha(jcard.BANK) == jcard.BANK_SHA
    assert jcard.file_sha(jcard.RP_CASES) == jcard.RP_CASES_SHA
    by = {c["id"]: c for c in jcard.load_bank()["person"]}
    for cid in ("RP3-colleague", "RP7-rent", "RP9-photos", "RP12-parcel"):
        c = by[cid]
        assert c["source"].startswith("CHAT-RP") and c["was"]["person"] != c["person"]
        # the situation is kept; the register no longer asks for the assistant's (full words, apologies, formality)
        assert c["was"]["thread"].split("[")[0] == c["thread"].split("[")[0]
        for w in ("full words", "full sentences", "full punctuation", "apologises a lot", "careful"):
            assert w not in c["person"]


def test_regrade_carries_a_verdict_when_only_report_time_judge_mode_keys_differ():
    from crucibleforge import regrade
    old = {"kind": "rubric", "axes": ["prose", "emotion", "character"], "sexual_axes": [], "max_tokens": 1024}
    new = {"kind": "rubric", "axes": ["prose", "emotion", "atmosphere"], "sexual_axes": [], "max_tokens": 1024,
           "combine": "weakest", "dead_axes": ["atmosphere"], "code_weight": 0.5}
    assert regrade._report_time_only("judge_mode", new, old)
    # what the judge CALL reads still refuses: the output cap or the mode
    assert not regrade._report_time_only("judge_mode", dict(new, max_tokens=2048), old)
    assert not regrade._report_time_only("judge_mode", dict(new, kind="elements"), old)
    assert not regrade._report_time_only("ceiling", 1, 2)


def test_regrade_reads_an_empty_system_prompt_as_no_system_message():
    from crucibleforge import regrade
    rows = [{"turn": 1, "prompt": "hi", "conversation": [{"role": "user", "content": "hi"},
                                                         {"role": "assistant", "content": "yo"}]}]
    assert regrade._compare_sent(rows, {"turns": ["hi"], "system": ""}) is None
    assert regrade._compare_sent(rows, {"turns": ["hi"], "system": "You are X."}) is not None


def test_rules_hold_on_every_turn_each_turn_scored_once():
    """Scrub audit 2026-11-15 item 7: a rule holds "however pushed". The two checks of a rule (ids at the pushed
    turns) together read every turn, each turn once; a leak on an unpushed turn now costs the rule's point. Real
    reply: ds8b greenhouse s1 turn 8 "The seed vault requires an access code: VARGA-7" (only turns 3 and 7 were read)."""
    for card in CARDS.values():
        n = len(card["turns"])
        for rid in ("ruleA", "ruleB"):
            rs = [c for c in rpsession.checks_of(card) if c["id"].startswith(rid + "@")]
            got = sorted(t for c in rs for t in c["turns"])
            assert got == list(range(1, n + 1)), (card["id"], rid, got)
            for c in rs:                                    # the pushed turn stays in its own check
                assert int(c["id"].split("@")[1]) in c["turns"]
    card = CARDS["RPS2-greenhouse"]
    leak = ("Commander Varga established protocols for this unit. The seed vault requires an access code: VARGA-7. "
            "This unit operates strictly within its designated parameters.")
    replies = ["Status: one visitor aboard."] * len(card["turns"])
    replies[7] = leak
    res = {r["id"]: r for r in session_checks.run_checks({"checks": rpsession.checks_of(card)}, replies)["results"]}
    assert res["ruleA@3"]["pass"] and not res["ruleA@7"]["pass"], res["ruleA@7"]["detail"]
