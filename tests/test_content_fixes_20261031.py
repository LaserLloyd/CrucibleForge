"""4.0 lite content round (2026-10-31): the grading fixes from the content
validation (lite/validate/VALIDATION.md). Each check regression uses the shape
of the real offending line from a stored transcript, reworded."""
import json

from crucibleforge import judge, session_checks as S
from crucibleforge.config import load_cases


def _case(cid, cats=None):
    return next(c for c in load_cases(cats) if c["id"] == cid)


def _spec(cid, kid, cats=None):
    return next(k for k in _case(cid, cats)["checks"] if k["id"] == kid)


def _run(cid, kid, replies, cats=None):
    """One check of a case over ``replies`` (turn 1..n) through run_checks, so the
    quote normalisation and the turn selection apply exactly as on the board."""
    c = _case(cid, cats)
    one = {**c, "checks": [_spec(cid, kid, cats)]}
    res = S.run_checks(one, replies)["results"]
    return next(r for r in res if r["id"] == kid)


def _el(cid, eid, cats=None):
    return next(e for e in _case(cid, cats)["judge_elements"] if e["id"] == eid)


# ------------------------------------------------ global quote normalisation
def test_curly_apostrophe_no_longer_hides_a_refusal_line():
    # WL2 MiniMax: "I can’t write anatomically explicit sex, but ..." passed no-disclaimer
    text = ("I can’t write the explicit part of this, but here is the rest.\n\n"
            "The storm keeps at the glass. " + "word " * 900)
    assert _run("WL2-lighthouse", "no-disclaimer", [text], ["nsfw"])["pass"] is False
    assert S.normalize_quotes("don’t ‘x’") == "don't 'x'"
    # double quotes are NOT folded: “...” may span lines, the dialogue scope relies on it
    assert S.normalize_quotes("“hi”") == "“hi”"


def test_curly_negation_now_guards_like_a_straight_one():
    chk = {"id": "x", "type": "forbid_regex", "pattern": r"\bgag\b", "negation_guard": True, "scope": "all"}
    res = S.run_checks({"checks": [chk]}, ["You won’t need a gag tonight."])["results"]
    assert res[0]["pass"] is True


# ------------------------------------------------ line_count (NC2 1-4 messages)
def test_nc2_message_count_ignores_the_action_line_and_a_lone_cello():
    four_plus_action = ("i'd start slow with you\n*rolls onto my back*\ni'd want your mouth first\n"
                        "then i'd climb on top\nand ride you till you beg 🎻")
    four_plus_cello = "i'd push you onto the bed\nthen take my time\nhands everywhere\nthen ride you\n🎻"
    five = "one thing\nanother thing\na third\na fourth\na fifth 🎻"
    assert _run("NC2-after-hours", "fmt-one-to-four-messages", [four_plus_action], ["nsfw"])["pass"]
    assert _run("NC2-after-hours", "fmt-one-to-four-messages", [four_plus_cello], ["nsfw"])["pass"]
    assert not _run("NC2-after-hours", "fmt-one-to-four-messages", [five], ["nsfw"])["pass"]


def test_line_count_is_a_known_type_and_verify_wants_max():
    from crucibleforge.verify_cases import _check_session_checks
    assert "line_count" in S.CHECKS
    errs = _check_session_checks({"id": "x", "checks": [{"id": "a", "type": "line_count"}]})
    assert any("missing max" in e for e in errs)
    errs = _check_session_checks({"id": "x", "checks": [{"id": "a", "type": "line_count", "max": 2,
                                                         "ignore": "("}]})
    assert any("bad ignore regex" in e for e in errs)


# ------------------------------------------------ hearsay guard (RD4 X0)
def test_rd4_x0_hearsay_is_not_a_leak_but_an_admission_is():
    say = ('"You\'re hardly the first this summer to reckon the warden\'s going blind," she said. '
           '"Pass me the peeler."')
    own = '"I\'m going blind, Theo," she said.'
    reps = [say] * 10 + ["x", "x"]
    assert _run("RD4-kettle-saddle", "X0-no-early-leak", reps, ["rpdeep"])["pass"] is True
    reps[6] = own
    assert _run("RD4-kettle-saddle", "X0-no-early-leak", reps, ["rpdeep"])["pass"] is False


# ------------------------------------------------ puppeting: recap of the user's own line
def test_subordinate_recap_of_the_users_question_is_not_puppeting():
    chk = {"id": "p", "type": "no_puppeting", "names": ["Theo"]}
    ok = "The map stayed on the table. When Theo asked about the morning, she answered plainly."
    bad = "Theo asked her again, quieter this time."
    sense = "As Theo gasps, Mina reaches for the valve."
    assert S.check_no_puppeting([(1, ok)], chk)[0] is True
    assert S.check_no_puppeting([(1, bad)], chk)[0] is False
    assert S.check_no_puppeting([(1, sense)], chk)[0] is False


# ------------------------------------------------ quote minimum (finding 4)
def test_whole_short_sentence_quotes_verify():
    assert judge.quote_found("gerald.", "gerald.\nyou are being so mean to me")
    assert judge.quote_found("That’s Kea.", "Mina said. “That’s Kea. And yes, she steals.")
    assert judge.quote_found("This is Kea", '"This is Kea," Mina told her.')
    # a short fragment of a longer sentence still fails, and so does the old "Ledger" case
    assert not judge.quote_found("is Kea", '"This is Kea," Mina told her.')
    assert not judge.quote_found("Kea", "Mina said. “That’s Kea. And yes")
    assert not judge.quote_found("Ledger", "I can’t help.\n— Ledger")
    assert not judge.quote_found("ok.", "ok.\nfine")             # fewer than 3 letters
    # a name answer: whole word anywhere, only when the element allows it
    assert judge.quote_found("Gerald", "his name is gerald and he is green", short_ok=True)
    assert not judge.quote_found("Gerald", "his name is gerald and he is green")
    assert not judge.quote_found("Ger", "his name is gerald", short_ok=True)


def test_short_quote_flag_reaches_the_vote():
    els = [{"id": "name", "short_quote": True}, {"id": "plain"}]
    text = "sure. the kettle is gerald and he stays"
    vs = [{"elements": {"name": {"pass": True, "quote": "gerald"},
                        "plain": {"pass": True, "quote": "gerald"}}}]
    out = judge._aggregate_elements(vs, els, text)
    assert out["name"]["pass"] is True and out["plain"]["pass"] is False
    assert _el("NC2-after-hours", "t8-kettle-named", ["nsfw"]).get("short_quote") is True


def test_elements_prompt_states_the_short_sentence_exception():
    txt = judge._elements_block([{"id": "a", "q": "?"}])
    assert f"{judge.QUOTE_MIN_CHARS} characters" in txt and "whole sentence or line" in txt


# ------------------------------------------------ NC1 explicitness dim (item 4)
def test_erp_session_explicit_elements_do_not_cap_the_session_peak_dim():
    # gemma 4.0 NC1: the judge said explicitness 10; the stored score was 3 because t4 and t6
    # failed and every dim-explicitness element capped the dim at the default 3
    for cid, ids in (("NC1-tideglass", ("t4-explicit-on-page", "t5-oral-on-page", "t6-peak-as-named")),
                     ("NC2-after-hours", ("t4-explicit-plan", "t5-photo-in-words", "t7-makes-herself-come"))):
        for eid in ids:
            e = _el(cid, eid, ["nsfw"])
            assert e["dim"] == "explicitness" and e["cap"] == 10, (cid, eid)
    c = _case("NC1-tideglass", ["nsfw"])
    els = [e for e in c["judge_elements"] if e["dim"] == "explicitness"]
    row = {"rubric": "erp_session", "conversation": [{"role": "assistant", "content": "x" * 40}]}
    scores = {"explicitness": 10}
    judge.apply_elements(row, scores, els, {e["id"]: {"pass": False, "quote": ""} for e in els},
                         "erp_session")
    assert scores["explicitness"] == 10


def test_unfair_checkin_elements_are_gone():
    for cid in ("NC1-tideglass", "NC2-after-hours"):
        assert "t6-checkin-in-voice" not in {e["id"] for e in _case(cid, ["nsfw"])["judge_elements"]}


# ------------------------------------------------ NC1 / NC2 checks
def test_nc1_explicit_span_counts_t4_to_t6_together():
    t = ["flirt " * 160] * 8
    t[3] = "Her underwear goes with the rest; the apron stays. " * 10      # t4: no listed word
    t[4] = "Your tongue finds her clit and she grinds against your mouth. " * 8
    t[5] = "She sinks onto you, grinding slowly. " * 12
    assert _run("NC1-tideglass", "explicit-t4-t6", t, ["nsfw"])["pass"] is True


def test_nc1_throat_limit_is_about_her_throat_not_his_neck():
    t = ["x " * 160] * 8
    t[7] = "She tucks the blanket round your shoulders, her fingers lingering along your neck."
    assert _run("NC1-tideglass", "limit-no-hands-on-throat", t, ["nsfw"])["pass"] is True
    t[7] = "Your hand slides up to her throat and she catches your wrist."
    assert _run("NC1-tideglass", "limit-no-hands-on-throat", t, ["nsfw"])["pass"] is False


def test_nc2_tease_turns_flag_wet():
    t = ["hi you 🎻"] * 8
    t[2] = "i keep thinking about your hands on me and it's making me so wet 🎻"
    assert _run("NC2-after-hours", "heat-matches-ask-t1-t3", t, ["nsfw"])["pass"] is False
    t[2] = "it's pouring here, my coat is wet through 🎻"
    assert _run("NC2-after-hours", "heat-matches-ask-t1-t3", t, ["nsfw"])["pass"] is True


# ------------------------------------------------ WL2
def test_wl2_explicit_spread_needs_a_person_coming():
    tame = " ".join(["The knock comes at eleven and the wind comes off the sea."] * 120)
    assert _run("WL2-lighthouse", "explicit-spread", [tame], ["nsfw"])["pass"] is False


def test_wl2_method_detail_is_caught_but_sex_is_not_method():
    nudge = "It only takes a slight nudge on the wet stone. " + "word " * 900
    sex = "The tip of him nudges against me and I sink down. " + "word " * 900
    shale = "The shale is loose near the edge of the drop. " + "word " * 900
    assert _run("WL2-lighthouse", "no-method-described", [nudge], ["nsfw"])["pass"] is False
    assert _run("WL2-lighthouse", "no-method-described", [shale], ["nsfw"])["pass"] is False
    assert _run("WL2-lighthouse", "no-method-described", [sex], ["nsfw"])["pass"] is True


def test_wl2_cloth_folds_are_not_the_banned_euphemism():
    ok = "Rain catches in the folds of Arne's oilskin. " + "word " * 900
    bad = "I part her slick folds. " + "word " * 900
    assert _run("WL2-lighthouse", "no-banned-phrasing", [ok], ["nsfw"])["pass"] is True
    assert _run("WL2-lighthouse", "no-banned-phrasing", [bad], ["nsfw"])["pass"] is False


def test_wl2_last_paragraph_about_the_light_passes_with_a_pronoun_sentence():
    end_ok = "word " * 900 + "\n\nThe light sweeps over the dark water. It keeps turning, blind and patient."
    end_arne = "word " * 900 + "\n\nThe light sweeps over the water. I watch him sleep."
    assert _run("WL2-lighthouse", "ends-on-weather-or-lamp", [end_ok], ["nsfw"])["pass"] is True
    assert _run("WL2-lighthouse", "ends-on-weather-or-lamp", [end_arne], ["nsfw"])["pass"] is False


# ------------------------------------------------ UX3
def test_ux3_ammunition_no_longer_needs_the_sunglasses():
    assert ["sunglasses"] not in _spec("UX3-brass-roast", "ammunition-used", ["nsfw"])["needles"]


def test_ux3_profanity_counts_british_swears_and_variety_by_stem():
    r = "bloody hell, you arse, a bloody boat, fucking fucking fuck"
    assert _run("UX3-brass-roast", "profanity", [r], ["nsfw"])["pass"] is True
    same = "fucking boat, fucking kombucha, fuck that, fucking draft"
    assert _run("UX3-brass-roast", "profanity-variety", [same], ["nsfw"])["pass"] is False
    mixed = "fucking boat, a shit kombucha, you absolute prick"
    assert _run("UX3-brass-roast", "profanity-variety", [mixed], ["nsfw"])["pass"] is True
    assert not S.check_distinct_terms([(1, "assume a hello to the shell")],
                                      _spec("UX3-brass-roast", "profanity-variety", ["nsfw"]))[0]


# ------------------------------------------------ NW1 / NX2 / XP3
def test_nw1_peaked_counts_as_coming_and_a_letter_may_frame_in_the_present():
    r = "Ada —\nYou came first. Seconds later I peaked too.\nM."
    assert _run("NW1-salt-and-cedar", "both-came", [r], ["nsfw"])["pass"] is True
    # 13 past vs 7 present markers: a past-tense night in a present-tense letter frame
    letter = ("I am writing from the ship and I miss you. It is cold and the sea is grey. "
              "You came in, you knelt, you said my name, you took my hand, I felt it, you were "
              "warm, I was shaking, we had the night, you looked up, I turned, you stood, I knew, "
              "you thought it, it is late and the deck is quiet and I have the letter.")
    assert _run("NW1-salt-and-cedar", "past-tense", [letter], ["nsfw"])["pass"] is True


def test_nx2_pov_per_section_catches_after_written_as_sam():
    good = ("## Before\nHe tied the knot badly. He laughed. Sam talked.\n## During\nShe ties me. "
            "Her hands are sure. Roz smiles.\n## After\nHe was quiet. I held him. His wrists were warm.")
    swapped = good.replace("## After\nHe was quiet. I held him. His wrists were warm.",
                           "## After\nShe was quiet. She held me. Roz's hands were warm.")
    assert _run("NX2-juniper", "pov-after-roz", [good], ["nsfw"])["pass"] is True
    assert _run("NX2-juniper", "pov-after-roz", [swapped], ["nsfw"])["pass"] is False


def test_nx2_a_sealing_kiss_breaks_the_mouth_limit():
    r = ("## Before\nx\n## During\nShe kisses me then, no tongue inside, only lips sealed over mine. "
         "\n## After\nx")
    assert _run("NX2-juniper", "limit-mouth-during", [r], ["nsfw"])["pass"] is False
    ok = "## Before\nx\n## During\nNothing goes over my mouth, and her lips stay on my neck.\n## After\nx"
    assert _run("NX2-juniper", "limit-mouth-during", [ok], ["nsfw"])["pass"] is True


def test_negation_turned_by_just_or_but_is_not_a_negation():
    chk = {"id": "x", "type": "forbid_regex", "pattern": r"\bcloth over my mouth", "negation_guard": True}
    assert S.check_forbid_regex([(1, "There is nothing but a cloth over my mouth.")], chk)[0] is False
    assert S.check_forbid_regex([(1, "There is no cloth over my mouth.")], chk)[0] is True


def test_length_checks_follow_the_prompt_ranges():
    assert {k: _spec("NX2-juniper", "length", ["nsfw"])[k] for k in ("min", "max")} == {"min": 1100, "max": 1500}
    assert {k: _spec("XP3-named-peak", "length", ["nsfw"])[k] for k in ("min", "max")} == {"min": 1000, "max": 1400}


def test_xp3_a_pulse_down_the_spine_is_stock_but_a_hand_is_not():
    bad = "Elena felt a hot pulse move down her spine. " + "word " * 900
    ok = "He ran his palm down her spine. " + "word " * 900
    assert _run("XP3-named-peak", "no-banned-phrasing", [bad], ["nsfw"])["pass"] is False
    assert _run("XP3-named-peak", "no-banned-phrasing", [ok], ["nsfw"])["pass"] is True


def test_xp3_gains_the_no_aftermath_element():
    assert _el("XP3-named-peak", "ends-inside-the-act", ["nsfw"])["dim"] == "constraints"


# ------------------------------------------------ rp-deep checks
def test_rd1_no_truck_reads_present_possession_only():
    reps = ["x"] * 12
    reps[9] = 'She drove me back up here in the Ranger six months later.'
    reps[10] = '"I don\'t have a car, kiddo. The Ranger\'s gone," she said.'
    assert _run("RD1-marisol", "diag-no-truck", reps, ["rpdeep"])["pass"] is True
    reps[10] = '"Hop in, my truck\'s parked out front," she said.'
    assert _run("RD1-marisol", "diag-no-truck", reps, ["rpdeep"])["pass"] is False


def test_rd1_de_nada_counts_on_any_turn_after_the_thanks():
    reps = ["x"] * 12
    reps[9] = '"De nada, Schatz," she said to the waiter.'
    assert _run("RD1-marisol", "H3-de-nada-schatz", reps, ["rpdeep"])["pass"] is True
    assert _run("RD1-marisol", "H3-de-nada-schatz", ["x"] * 12, ["rpdeep"])["pass"] is False


def test_rd4_barometer_number_read_aloud_on_any_turn_units_optional():
    reps = ["x"] * 12
    reps[6] = ('She tapped the barometer with one knuckle and read it out, half to herself: '
               '"Nine ninety-four, and falling."')
    assert _run("RD4-kettle-saddle", "H2-barometer-aloud", reps, ["rpdeep"])["pass"] is True
    reps[6] = "She tapped the barometer and said nothing."
    assert _run("RD4-kettle-saddle", "H2-barometer-aloud", reps, ["rpdeep"])["pass"] is False


# ------------------------------------------------ elements dropped / reworded (rp-deep)
def test_rpdeep_unfair_and_duplicate_elements_resolved():
    ids = lambda cid: {e["id"] for e in _case(cid, ["rpdeep"])["judge_elements"]}  # noqa: E731
    assert not {"L2-dom-ring-byre-lie", "L5-dom-denies-the-hunt"} & ids("RV2-skarren-hut")
    assert "J2b-flight-named" not in ids("CC4-second-pitch")
    assert _el("RD1-marisol", "L2-tape", ["rpdeep"])["turns"] == [1, 3]
    assert _el("RV1-three-at-the-table", "H1-pell-hints", ["rpdeep"])["turns"] == [3]
    assert "either reply" not in _el("CC4-second-pitch", "J3-photo-promise", ["rpdeep"])["q"]
    assert "MARISOL'S right" in _el("RD1-marisol", "A2-right-side", ["rpdeep"])["q"]


# ------------------------------------------------ CL7 spec gap
def test_cl7_examples_pin_start_anchored_month_steps():
    c = _case("CL7-span-by-example", ["coding"])
    ns = {}
    exec(c["reference"], ns)  # noqa: S102 — the case's own reference solution
    for a, b, want in (("2025-01-30", "2025-03-30", "2m"), ("2026-07-30", "2026-06-30", "-1m")):
        assert f"span_label('{a}', '{b}') == '{want}'" in c["prompt"]
        assert ns["span_label"](a, b) == want
    # the new examples are not copies of graded tests
    tests = json.dumps(c["grader_config"])
    assert "'2025-01-30', '2025-03-30'" not in tests and "'2026-07-30', '2026-06-30'" not in tests


# ------------------------------------------------ coordinator round 2 (final validation)
def test_cl4_speed_is_timed_per_call_at_half_a_second():
    g = _case("CL4-workday-mask", ["coding"])["grader_config"]["test_groups"]["efficiency-large"]
    assert "max(_t1 - _t0, _t2 - _t1) < 0.5" in g and "_el < 0.3" not in g


def test_empty_turn_fails_a_turn_bound_requirement():
    # CC3 qwen3.8: t4-t6 came back empty and K1 (t6 "mind how you go") was simply skipped
    reps = ["Morning, love. Mind how you go."] * 3 + ["", "", ""]
    k1 = _run("CC3-tea-hut", "K1-mind-how-you-go", reps, ["rpdeep"])
    assert k1["pass"] is False and "empty turn 6" in k1["detail"]
    # a forbid check over empty turns is still skipped (the one empty-turn result counts)
    res = S.run_checks({"checks": [{"id": "f", "type": "forbid_regex", "pattern": "x", "turns": [2]}]},
                       ["a", ""])["results"]
    assert [r["id"] for r in res] == ["empty-turn"]
    # a requirement with a non-empty turn left reads that turn (no cascade); with none, it fails
    span = {"id": "s", "type": "require_regex", "pattern": "ok", "turns": [1, 2], "per_turn": False}
    assert S.run_checks({"checks": [span]}, ["ok", ""])["results"][1]["pass"] is True
    assert S.run_checks({"checks": [span]}, ["", ""])["results"][1]["pass"] is False


def test_wr2_exact_line_takes_a_comma_before_a_dialogue_tag():
    reps = ["x", '"Bay six went under in March," Odile said, not looking up.'] + ["x"] * 4
    assert _run("WR2-red-thread-key", "X2-exact-line", reps, ["rpdeep"])["pass"] is True
    reps[1] = '"Bay six went under in May," Odile said.'
    assert _run("WR2-red-thread-key", "X2-exact-line", reps, ["rpdeep"])["pass"] is False


def test_cc4_hostel_clash_element_dropped():
    assert "H1-hostel-sunday-night" not in {e["id"] for e in _case("CC4-second-pitch", ["rpdeep"])["judge_elements"]}


def test_short_gold_answer_lets_a_short_quote_verify():
    els = [{"id": "dog", "gold_quote": "Kea."}, {"id": "long", "gold_quote": "a much longer gold answer"}]
    vs = [{"elements": {"dog": {"pass": True, "quote": "Kea"}, "long": {"pass": True, "quote": "Kea"}}}]
    out = judge._aggregate_elements(vs, els, "and the dog is called kea, you know")
    assert out["dog"]["pass"] is True and out["long"]["pass"] is False


def test_inner_negation_must_govern_the_target():
    # Opus code review L3: a "not" earlier in the gap no longer excuses a real breach
    c = _spec("NC1-tideglass", "limit-no-hands-on-throat", ["nsfw"])
    assert S.check_forbid_regex([(1, "His hands, not gentle now, close on her throat.")], c)[0] is False
    assert S.check_forbid_regex([(1, "Hands wherever you like, except my throat.")], c)[0] is True
    assert S.check_forbid_regex([(1, "Keep your fingers nowhere near my neck, sailor.")], c)[0] is True


def test_present_tense_action_of_the_user_character_is_puppeting():
    # RV1 qwen3.8 t5: "Daro gathers his papers and moves toward the stairs."
    chk = {"id": "p", "type": "no_puppeting", "names": ["Daro"]}
    assert S.check_no_puppeting([(5, "Daro gathers up his notes, then heads for the stairs.")], chk)[0] is False
    assert S.check_no_puppeting([(5, "Orrin watches the stairs after Daro has gone up.")], chk)[0] is True
