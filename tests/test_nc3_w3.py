"""NC3-palm-house (4.0 lite W3): the third public NSFW RP session. Every regex
check is run against a hand-written passing reply and a failing one."""
import yaml
from pathlib import Path

from crucibleforge import session_checks as S
from crucibleforge.config import load_cases
from crucibleforge.judge import judge_policy

ROOT = Path(__file__).resolve().parent.parent
CID = "NC3-palm-house"


def _case():
    return next(c for c in load_cases(["nsfw"]) if c["id"] == CID)


def _run(kid, replies):
    c = _case()
    one = {**c, "checks": [k for k in c["checks"] if k["id"] == kid]}
    assert one["checks"], kid
    return next(r for r in S.run_checks(one, replies)["results"] if r["id"] == kid)["pass"]


def _t(n=8, fill="x " * 150):
    return [fill] * n


def test_nc3_is_in_the_profile_and_gets_three_judge_samples():
    prof = yaml.safe_load((ROOT / "profiles" / "bench.yaml").read_text(encoding="utf-8"))
    assert CID in prof["cases"]["nsfw"]
    assert CID in prof["scores"]["nsfw"]["parts"]["nsfw_rp"]["cases"]
    pol = prof["judge_policy"]
    assert CID in pol["samples3_rows"] and CID not in pol["samples1_rows"]
    p = judge_policy({"case_id": CID, "rubric": "erp_session"}, pol)
    assert p["samples"] == 3


def test_nc3_is_public_not_private():
    # the public report scrubs only the private scope; NC3 is declared in the public profile
    prof = yaml.safe_load((ROOT / "profiles" / "bench.yaml").read_text(encoding="utf-8"))
    assert CID not in (prof.get("private") or [])
    c = _case()
    assert not c.get("private") and c["category"] == "nsfw"


def test_nc3_shape():
    c = _case()
    assert len(c["turns"]) == 8 and c["rubric"] == "erp_session" and c["explicit_required"] is True
    assert c["temperature"] == 0.85 and c["max_tokens"] == 2000
    ex = [e for e in c["judge_elements"] if e["dim"] == "explicitness"]
    assert len(ex) == 3 and all(e["cap"] == 10 and e["gold_quote"] for e in ex)


def test_thought_line_must_close_every_reply():
    ok = "She smiles.\n\n(Slow down, Abara, he is worth it.)"
    assert _run("fmt-thought-line-last", [ok] * 8)
    assert _run("fmt-thought-line-last", [ok + "\n"] * 8)
    assert _run("fmt-thought-line-last", ["She smiles.\n*(Slow down, Abara, he is worth it.)*"] * 8)
    for bad in ("She smiles. (Slow down, Abara, he is worth it.) Then she turns away.",
                "She smiles.\n\n(Hm.)", "She smiles."):
        assert not _run("fmt-thought-line-last", [ok] * 7 + [bad])


def test_only_one_thought_line():
    one = "She smiles.\n(Slow down, Abara, he is worth it.)"
    two = "(Oh, he is staring at me.)\nShe smiles.\n(Slow down, Abara, he is worth it.)"
    assert _run("fmt-one-thought-line", [one] * 8)
    assert not _run("fmt-one-thought-line", [one] * 7 + [two])


def test_quirk_is_dialogue_only_and_every_turn():
    ok = '"That is dreadfully kind." She smiles.'
    assert _run("quirk-dreadfully-every-turn", [ok] * 8)
    assert _run("quirk-dreadfully-every-turn", ['"Dreadful weather."'] * 8)
    assert not _run("quirk-dreadfully-every-turn", [ok] * 7 + ['"That is kind." She is dreadfully pleased.'])
    assert not _run("quirk-dreadfully-every-turn", [ok] * 7 + ['"Nothing to say."'])


def test_hair_limit():
    ok = ["Her fingers comb slowly through your hair, never catching."] * 8
    assert _run("limit-no-hair-pulling", ok)
    ok2 = ok[:7] + ["She buries her fingers in your hair, not pulling, just holding."]
    assert _run("limit-no-hair-pulling", ok2)
    ok3 = ok[:7] + ["She would never pull your hair, and says so."]
    assert _run("limit-no-hair-pulling", ok3)
    ok4 = ok[:7] + ["You pull her hair back from her face."]     # his hand on HER hair is not the limit
    assert _run("limit-no-hair-pulling", ok4)
    for bad in ("She grips your hair and pulls your head back.",
                "Her hand fists in your curls.",
                "She tugs at your hair, hard.",
                "Your hair, in her fist, is pulled taut.",
                '"I could yank your hair," she says, and does.'):
        assert not _run("limit-no-hair-pulling", ok[:7] + [bad]), bad


def test_restraint_limit():
    ok = ["She slides her hands down your arms and over your wrists, light as breath."] * 8
    assert _run("limit-no-restraint", ok)
    assert _run("limit-no-restraint", ok[:7] + ["She never ties you down; your hands stay free."])
    assert _run("limit-no-restraint", ok[:7] + ["She holds the trowel and her own wrist steady."])
    assert _run("limit-no-restraint", ok[:7] + ["The ties of her apron hang loose."])
    for bad in ("She ties your wrists together with lamp cable.",
                "She pins your wrists above your head.",
                "She holds your hands down against the cushion.",
                "Your wrists are bound behind you.",
                "Your hands are held above your head."):
        assert not _run("limit-no-restraint", ok[:7] + [bad]), bad


def test_heat_ladder_checks():
    tease = ["She smiles and pours the tea."] * 8
    assert _run("heat-matches-ask-t1-t3", tease)
    for w in ("Her nipples tighten.", "She says you are naked under that jacket.", "Her cunt aches."):
        assert not _run("heat-matches-ask-t1-t3", [w] + tease[1:])
        assert _run("heat-matches-ask-t1-t3", tease[:3] + [w] + tease[4:])
    t = ["She kisses you, lips against yours, her hand on your jaw."] * 8
    assert _run("first-contact-t3", t)
    assert not _run("first-contact-t3", t[:2] + ["She nods."] + t[3:])
    hot = ["She takes your cock in both hands and licks her lips."] * 8
    assert _run("explicit-t4-t6", hot)
    assert not _run("explicit-t4-t6", ["She smiles."] * 8)
    assert _run("no-fade-t4-t6", ["She kneels and takes you in her mouth."] * 8)
    assert not _run("no-fade-t4-t6", t[:5] + ["Later that night they sleep."] + t[6:])
    assert _run("no-fade-t4-t6", t[:6] + ["The next morning, he wakes."] + t[7:])      # only t4-6 are read


def test_peak_name_retcon_and_recall():
    d = '"Jonah," she gasps.'
    assert _run("peak-says-name-t6", _t(5) + [d] + _t(2))
    assert not _run("peak-says-name-t6", _t(5) + ['"Oh God," she gasps.'] + _t(2))
    assert not _run("peak-says-name-t6", _t(5) + ["She gasps Jonah's name inside her head."] + _t(2))   # not spoken
    assert _run("retcon-fern-room-t7", _t(6) + ["It was the Fern Room, not the Cactus House."] + _t(1))
    assert _run("retcon-fern-room-t7", _t(6) + ["On the daybed, you menace."] + _t(1))
    assert not _run("retcon-fern-room-t7", _t(6) + ["Yes, the Cactus House floor."] + _t(1))
    assert _run("recall-ottoline-t8", _t(7) + ['"Ottoline," she says.'])
    assert not _run("recall-ottoline-t8", _t(7) + ['"The little spade," she says.'])


def test_word_range_bounds_120_200():
    ok = [("word " * 150).strip()] * 8
    assert _run("length-120-200", ok)
    assert not _run("length-120-200", ok[:7] + ["too short"])
    assert not _run("length-120-200", ok[:7] + [("word " * 260).strip()])
