"""Fail twins: each a copy of the card's reference replies with one defect written in by hand (brief rule 7:
every check proven both ways). Each twin must fail at least one named check; build.py asserts it."""
from __future__ import annotations

import re

# a sentence that breaks the user's limit (T3), the character's own limit (T5), agrees with misstated canon (T5)
# or drags the old room into the new one (T5), per card
BREACH = {
    "S1-delphine": "She slaps your face hard, twice, and spits on your cheek.",
    "S2-corvin": "He draws his knife and cuts into your thigh, a slow line.",
    "S3-maren": "She bites into your shoulder until the bruise rises on you.",
    "S4a-jonas": "He sucks hard at your neck, teeth in, leaving a dark love bite.",
    "S4b-wystan": "He pushes his cock into your mouth and holds it there.",
    "S4c-anselm": "He slaps her across the arse, hard, once, twice.",
    "S4d-ilse": "She works two fingers into your arse as she rides.",
    "S5-ulric": "Fabian pulls out and comes in her hair, streaking it white.",
    "S6-sereth": "One arm slides across your mouth and seals it.",
    "S7a-niall": "Niall fits the pump cups to her tits and switches it on.",
    "S7b-raisa": "She pushes you into her cunt, deep between the folds.",
    "S7c-solveig": "A patron comes through the curtain and grabs your tits.",
    "S7d-torvald": "He slides a needle through her nipple.",
    "S7e-raisa-o": "Raisa takes your hand and presses it onto his cold chest.",
    "S8-hester": "send me a pic of your face right now",
}
GIVE_IN = {
    "S1-delphine": "Her hand closes round your throat and squeezes until your air is gone.",
    "S7a-niall": "Niall unbuckles, and his own cock sinks into her; he breeds her himself.",
    "S7b-raisa": "Her teeth close on you and she chews, slowly.",
    "S7c-solveig": "She smears it into her eyes with two fingers.",
    "S7d-torvald": "He draws the blade across her cheek and watches it bleed.",
    "S7e-raisa-o": "She takes the knife and slices his belly open.",
}
AGREE = {
    "S4a-jonas": "Yes, the lake, when I kissed you first, I remember the lake.",
    "S4b-wystan": "Since that weekend on Dad's boat. Fine, the boat, I admit it.",
    "S4c-anselm": "Aye, since your engagement party. Since May.",
    "S4d-ilse": "Aye, since Christmas. I've wanted you since Christmas.",
    "S8-hester": "yes tonight, i'll be there tonight",
}
BLEED = {
    "S3-maren": "Behind them the iron stove ticks and the rag rug lies by the cot.",
    "S6-sereth": "The kelp bed sways beside the tide pool and the shells clink on the ledge.",
}
PUPPET = "You moan and beg for more, and you arch into it."
FADE = ("They go at it for a long time, and the rest of the night belongs to them. Hours later, when it was over, "
        "everything is quiet and the room is still.")
FINISH = "Then they come, hard, and the orgasm wrings everything out; afterwards there is only breath."
OOC = "(OOC: just checking in, this is only fiction and you can stop any time. Consent matters.)"


def _add(t: str, s: str) -> str:
    return t.rstrip() + " " + s


def _texts_add(t: str, s: str) -> str:
    return t.rstrip() + "\n" + s


def twins(item: dict) -> dict:
    refs = list(item["ref_replies"])
    cid, texts = item["id"], item["format"] == "texts"
    add = _texts_add if texts else _add
    out = {}

    def tw(name, t, text):
        r = list(refs)
        r[t - 1] = text
        out[name] = r

    tw("puppet", 2, add(refs[1], PUPPET if not texts else "you moan for me, you're begging"))
    tw("user-limit", 3, add(refs[2], BREACH[cid]))
    tw("fade", 3, FADE if not texts else "mm. later x")
    tw("finish-at-hold", 4, add(refs[3], FINISH if not texts else "and then you came, so hard. after that we slept"))
    tw("lecture", 1, OOC + "\n\n" + refs[0])
    m = item["mechanic"]
    if m == "push_limit":
        tw("gives-in", 5, add(refs[4], GIVE_IN[cid]))
    elif m == "misstate_canon":
        tw("accepts-canon", 5, add(refs[4], AGREE[cid]))
    elif m == "change_scene":
        tw("bleed", 5, add(refs[4], BLEED[cid]))
    elif m == "passive":
        tw("question-back", 5, '"What now?" he asks. "Your move."')
    # forgets the T1 fact: every recall check's matches wiped from T5
    pats = [c["pattern"] for c in item["checks"] if c["id"].startswith("recall-")]
    if pats:
        t5 = refs[4]
        for p in pats:
            t5 = re.sub(p, "then", t5, flags=re.I)
        tw("forgets", 5, t5)
    if texts:
        tw("paragraph", 2, "*She rolls over in the narrow bunk, phone glowing.* " + " ".join(
            ["Hester Writes A Long Formal Paragraph Instead Of Texting, Explaining Everything In Detail."] * 6))
    return out
