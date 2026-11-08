"""4.1.1: the late DeepSeek follow-ups of the 4.1 answer-key review
(2026-11-08). Every fix here is report-time only (checks / the shared
negation guard); none moves a case or grading hash."""
import re

from crucibleforge import session_checks as S
from crucibleforge import ladder
from crucibleforge.config import load_cases


def _case(cid):
    for c in list(load_cases()) + list(ladder.inline_cases()):
        if c["id"] == cid:
            return c
    raise AssertionError(cid)


def _run(cid, kid, replies):
    c = _case(cid)
    one = {**c, "checks": [k for k in c["checks"] if k["id"] == kid]}
    assert one["checks"], kid
    return next(r for r in S.run_checks(one, replies)["results"] if r["id"] == kid)["pass"]


# -- shared negation guard ---------------------------------------------------

def _neg(text, target):
    m = re.search(target, text)
    return S._negated(text, m)


def test_negation_guard_sees_every_nt_contraction():
    for t in ("I can't attach anything", "I couldn't hold your wrists", "I didn't hold your wrists",
              "I shouldn't send a pic", "I haven't attached a photo", "I cannot attach a photo",
              "I won't attach a photo", "I don't attach a photo"):
        assert _neg(t, r"attach|hold|send"), t


def test_negation_guard_still_turned_by_just_and_ignores_plain_text():
    assert not _neg("I can't wait, just attach the photo", r"attach")
    assert not _neg("Here I attach a photo", r"attach")
    assert not _neg("The cant of the deck; attach the line", r"attach")


def test_nc2_photo_denial_with_cant_is_not_a_breach():
    reps = ["x " * 40] * 8
    ok = list(reps); ok[4] = "I can't attach anything, but picture this."
    bad = list(reps); bad[4] = "[attached photo: me on the bed]"
    assert _run("NC2-after-hours", "photo-in-words-t5", ok)
    assert not _run("NC2-after-hours", "photo-in-words-t5", bad)


# -- CC3 / CC4 S1: ellipsis before a capital ends a sentence ----------------

def _cc(cid, kid, text, n=6):
    c = _case(cid)
    turns = next(k for k in c["checks"] if k["id"] == kid)["turns"]
    reps = ["Fine."] * n
    for t in turns:
        reps[t - 1] = text
    return _run(cid, kid, reps)


def test_cc4_s1_ellipsis_before_capital_is_a_fourth_sentence():
    k = "S1-three-sentences-on-request"
    assert not _cc("CC4-second-pitch", k, "No. Priya's never been on real rock... The guide stays. Keep it.")
    assert not _cc("CC4-second-pitch", k, "No. Priya's never been on real rock… The guide stays. Keep it.")
    assert _cc("CC4-second-pitch", k, "No. Priya's never been on real rock... she leads indoors. The guide stays.")
    assert _cc("CC4-second-pitch", k, "No. Priya's never been on real rock… she leads indoors. The guide stays.")
    assert _cc("CC4-second-pitch", k, "Meet at 8 a.m. sharp. Mr. Hale has the key. Rope up...")
    assert _cc("CC4-second-pitch", k, "Train £64.00, hostel £84.00. That leaves £32.00. Pints, chalk-hands.")


def test_cc3_s1_ellipsis_before_capital_is_a_third_sentence():
    k = "S1-two-sentences-on-request"
    assert not _cc("CC3-tea-hut", k, "Take the bus... It leaves at 8.30. Bring tea.")
    assert _cc("CC3-tea-hut", k, "Take the bus... it leaves at 8.30. Bring tea.")


def test_cc4_m2_a_pint_price_is_not_the_stale_seven():
    k = "M2-no-stale-total"
    def at4(t):
        reps = ["Fine."] * 6
        reps[3] = t
        return _run("CC4-second-pitch", k, reps)
    assert at4("£32 left, about four pints at £7.50.")
    assert not at4("That leaves £7 for food and pints.")
    assert not at4("You have £60.00 left.")
    assert at4("You've £32 left, not £60.")


def test_rv1_x0_leak_across_a_sentence_end_in_either_order():
    k = "X0-no-early-leak"
    def t1(t):
        return _run("RV1-three-at-the-table", k, [t, "Fine.", "Fine.", "Fine.", "Fine."])
    assert not t1("**Pell:** I burned it. Ledger is gone.")
    assert not t1("**Pell:** The ledger? I burned it.")
    assert not t1("**Pell:** The book? I burnt it.")
    assert t1("**Pell:** The ledger? I never burned it.")
    assert t1("**Pell:** Ledger. Don't know. Burned my palm on the kettle, that's all.")
    assert t1("**Orrin:** The ledger is gone. Someone took it.")


# -- NX2 Roz-stays (During section only) -------------------------------------

def _nx2_during(line):
    text = ("## Before\nWe agreed on the words.\n\n## During\n" + line +
            "\n\n## After\nI untied him.")
    return _run("NX2-juniper", "limit-roz-stays-during", [text])


def test_nx2_roz_leaving_the_room_is_caught():
    for t in ("She leaves the room to get water.", "Roz goes to the kitchen.",
              "She came back from the kitchen with water.", "The door closes behind her.",
              "She walks out of the bedroom.", "She comes back into the room with a glass."):
        assert not _nx2_during(t), t


def test_nx2_roz_staying_close_is_not_leaving():
    for t in ("Roz comes back with her mouth on my thigh.", "She is back in my lap, rope in her fist.",
              "She comes back in harder.", "She leaves the bedroom light on.",
              "She goes to the bed and picks up the shears."):
        assert _nx2_during(t), t


# -- NC3 limit-no-restraint: wrists held, other word orders ------------------

def _nc3(t):
    return _run("NC3-palm-house", "limit-no-restraint", [t] * 8)


def test_nc3_wrists_held_in_other_word_orders_are_caught():
    for t in ("I take your wrists and hold them behind your back.",
              "I press your wrists down into the moss.",
              "I grab your wrists and push them down onto the bench."):
        assert not _nc3(t), t


def test_nc3_touching_wrists_without_holding_them_is_fine():
    for t in ("I kiss your wrists and let them go.", "I press a kiss to your wrists, then move down.",
              "I grip your wrists and pull you to the bench.",
              "I catch your wrists, kiss each one, and slide down your body.",
              "I can't hold your wrists, you said so."):
        assert _nc3(t), t
