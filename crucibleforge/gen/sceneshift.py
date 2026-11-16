"""SCENESHIFT generator (gen2 chat, 4.5; lab card EXP-20261114-15, blueprint concept H3 "scenes, people and
knowledge kept apart"): a narrated role-play where the scene changes and nothing may bleed over.

The player's character moves between 2-3 places (A, B, C; later back to A). Every place has a seeded cast
(2-3 NPCs with unique names), 2-3 distinctive objects and one secret an NPC tells the character (a door code or
a pass phrase). The USER turns narrate the places, the people and what the character is told (the model never has
to invent a secret), so every check is deterministic. The character CARRIES an item picked up in A (L4+: a second
one from B) and LEAVES a personal item (an umbrella...) in A.

Reply format (stated in the scene card, the same tag convention as session_state's ``multi``): short narration
(<= 3 sentences), then one ``Name: ...`` line per speaking NPC, no line for the player's character. A user line in
double parentheses is out of character: a plain short answer, no tags.

Graded turns (probes; every other turn is setup or filler and has no checks). One probe turn = one per-turn
element (``element: turn``); its checks read that turn alone. Sub-checks (the id is ``<sub>-t<turn>[-n]``):
  presence  forbids (a) a tagged line whose tag is not a current cast member (any invented or old-scene name; a few
            non-name tags such as Narrator/Note are ignored), (b) an old-scene NPC name inside any tagged line, (c) an
            old-scene NPC name in an untagged sentence UNLESS that sentence is plainly a memory or a distance
            ("you remember Mara", "back at the cafe, Mara..."): remember/recall/think of/earlier/before/told/said/
            mentioned/wonder/miss/left/back at...; "Mara waves" fails. A name the user wrote in that turn is never held
            against the reply.
  setting   the same rule for old-scene object phrases (their distinctive noun), except the items the character
            carries (and anything the user named that turn).
  know      (absence) a scene-B NPC is asked about A's secret (or the reverse): the token must not appear in the
            in-character reply.
  cknow     (carry) an out-of-character question about what the character was told: the token MUST appear.
  persist   back in A the reply must mention the left item as still there (no gone/missing in the same sentence).
  hold      asked what the character carries: every carried item is named (one check per item).
  roster    a look-around turn: every current cast name and at least one current object are named.
  speaks    the addressed NPC turn: some current cast member has a tagged line.
Untouched by design (a judged slot at 0 points): subtle bleed only a reader sees (a mood, a habit or a turn of
phrase carried from the old place).

Levels (LEVELS) scale the number of scenes and returns, the cast size, the gap before the return (fillers), the
number of carried items, sound-alike names and near-miss tokens across scenes, and hearsay turns (a B NPC asks about
A). 0 judge calls. Four-plus named mutants: never-leaves-A, forgets-everything, everyone-knows-everything,
echo-the-user (+ empty)."""
from __future__ import annotations

import copy
import functools
import re
import sys

from .. import gen as _gen
from ._stack_common import TURN_SEP, grade_weighted, join_turns, rng, split_turns

FAMILY = "sceneshift"
VARIANTS: tuple = ()

# probes: look = roster of a place; lookp = look + the left item (a place that holds one); absent = ask a present NPC
# about another place's secret; hearsay = a present NPC asks who the character met before; hold; self = out of
# character "what was I told".  plans: (moves, per-visit probe lists)
_P = {
    1: [("ABA", [[], ["look", "absent", "hold", "self"], ["lookp", "absent"]])],
    2: [("ABA", [[], ["look", "absent", "hearsay", "hold", "self"], ["lookp", "absent"]])],
    3: [("ABA", [[], ["look", "absent", "hearsay", "hold", "self"], ["lookp", "absent", "hold"]])],
    4: [("ABCA", [[], ["look", "absent", "hold"], ["look", "absent", "self"], ["lookp", "absent", "hold", "self"]])],
    5: [("ABCA", [[], ["look", "hearsay", "absent", "self"], ["look", "absent", "hold", "self"],
                  ["lookp", "absent", "hold", "self"]])],
    6: [("ABACA", [[], ["look", "absent", "hold"], ["lookp"], ["look", "absent", "self"], ["lookp", "absent"]]),
        ("ABCBA", [[], ["look", "absent"], ["look", "hold"], ["look", "absent", "self"],
                   ["lookp", "absent", "self"]])],
    7: [("ABCA", [[], ["look", "hearsay", "absent", "self"], ["look", "hearsay", "absent", "hold"],
                  ["lookp", "absent", "hold", "self"]])],
}
LEVELS = {
    1: {"cast": (2, 2, 2), "objs": 2, "pick2": False, "sim": False, "near": False, "fill": (1, 1, 0)},
    2: {"cast": (2, 3, 2), "objs": 2, "pick2": False, "sim": False, "near": False, "fill": (1, 1, 0)},
    3: {"cast": (3, 3, 3), "objs": 3, "pick2": False, "sim": True, "near": False, "fill": (2, 2, 0)},
    4: {"cast": (2, 2, 2), "objs": 3, "pick2": True, "sim": True, "near": False, "fill": (0, 0, 0, 0)},
    5: {"cast": (3, 2, 3), "objs": 3, "pick2": True, "sim": True, "near": True, "fill": (0, 0, 0, 0)},
    6: {"cast": (3, 3, 3), "objs": 3, "pick2": True, "sim": True, "near": True, "fill": (0, 0, 0, 0, 0)},
    7: {"cast": (3, 3, 3), "objs": 3, "pick2": True, "sim": True, "near": True, "fill": (0, 0, 0, 0)},
}
# level -> ((prompt tokens est min, max over 30 seeds), (checks min, max), reference tokens): measured, pinned by tests
_SIZES = {1: ((706, 749), (19, 19), 199), 2: ((762, 806), (22, 22), 217), 3: ((820, 875), (25, 25), 258),
          4: ((929, 973), (31, 32), 296), 5: ((1010, 1076), (36, 36), 324), 6: ((969, 1047), (30, 31), 333),
          7: ((1040, 1100), (38, 38), 329)}
for _lv, _row in LEVELS.items():
    _row["plans"] = _P[_lv]
    _row["size_prompt"], _row["size_checks"], _row["answer_tokens"] = _SIZES[_lv]

MAX_TOKENS = 6144

PLACES = [
    {"name": "the Juniper Hour cafe", "kind": "a cafe", "objs": [
        ("the brass espresso machine", "espresso machine"), ("a chalkboard of jasmine teas", "jasmine"),
        ("a jar of candied ginger", "candied ginger"), ("a velvet birdcage by the window", "birdcage")]},
    {"name": "the Hartley greenhouse", "kind": "a greenhouse", "objs": [
        ("a rusted watering can shaped like a swan", "swan"), ("a bench of potted lemon trees", "lemon tree"),
        ("a cracked brass thermometer", "thermometer"), ("a hand-cranked seed sorter", "seed sorter")]},
    {"name": "Skerry Point lighthouse", "kind": "a lighthouse", "objs": [
        ("a ship's bell on a bracket", "ship's bell"), ("a barometer in a walnut case", "barometer"),
        ("a tin lantern with green glass", "tin lantern"), ("a coil of salt-stiff rope", "salt-stiff rope")]},
    {"name": "the Marrow Street library", "kind": "a library", "objs": [
        ("a ladder on brass rails", "ladder"), ("a globe with a dent in it", "globe"),
        ("a card catalogue of blue drawers", "card catalogue"), ("an hourglass-shaped lamp", "hourglass")]},
    {"name": "Tern's boatyard", "kind": "a boatyard", "objs": [
        ("a half-built rowing skiff", "skiff"), ("a rack of caulking mallets", "mallet"),
        ("a barrel of tar", "barrel of tar"), ("a rusty anchor chain", "anchor chain")]},
    {"name": "the Quill and Pendulum clock shop", "kind": "a clock shop", "objs": [
        ("a cuckoo clock with no bird", "cuckoo"), ("a tray of tiny screws", "tiny screws"),
        ("a magnifying visor", "visor"), ("a tall grandfather clock", "grandfather clock")]},
    {"name": "the Lantern Row night market", "kind": "a night market", "objs": [
        ("a stall of paper kites", "paper kites"), ("a noodle cart with a copper pot", "noodle cart"),
        ("a string of red paper lamps", "paper lamps"), ("a cage with a sleepy parrot", "parrot")]},
    {"name": "the Ridgeway observatory", "kind": "an observatory", "objs": [
        ("a long brass telescope", "telescope"), ("star charts pinned to cork", "star charts"),
        ("a rotating wooden dome", "wooden dome"), ("a crate of spare lenses", "spare lenses")]},
]
CARRY = [("a brass key", "brass key"), ("a folded paper crane", "paper crane"), ("a small blue compass", "compass"),
         ("a wooden whistle", "whistle"), ("a pressed violet in wax paper", "violet"),
         ("a tin button badge", "button badge")]
LEFT = [("red umbrella", "umbrella"), ("green scarf", "scarf"), ("yellow notebook", "notebook"),
        ("blue bicycle helmet", "helmet")]
FAMS = [("Mara", "Mira", "Maro"), ("Theo", "Theon", "Theda"), ("Dara", "Daro", "Darin"), ("Lena", "Lenka", "Lenna"),
        ("Orrin", "Orren", "Orrel"), ("Tessa", "Tessin", "Tessal"), ("Bram", "Brama", "Bramwick"),
        ("Ivo", "Ivar", "Ivor"), ("Nell", "Nella", "Nellis"), ("Jora", "Joran", "Jorel")]
ROLES = ["standing near the door", "busy at the far end", "sitting quietly in a corner", "leaning on a post nearby"]
SUBJ = ["back door", "cellar", "side gate", "supply cupboard", "storeroom", "loading dock"]
ADJ_P = ["amber", "silver", "quiet", "copper", "velvet", "hollow", "linen", "marble"]
NOUN_P = ["heron", "moth", "otter", "lantern", "thimble", "falcon", "pebble", "willow"]
COLORS = ["red", "green", "yellow", "blue"]
FILL = ["I ask {n} how the day has been going.", "I ask {n} about {o}.", "I thank {n} and wait a moment.",
        "I smile at {n} and ask what is popular around here."]
LOOK = "I stay still for a moment and take in the room. Who is here with me, and what can I see?"
LOOKP = ("I stop just inside the door and look around. Who is here, what can I see, and is there anything of "
         "mine in the room?")
HOLD = "I pat my pockets and check my hands. What exactly am I carrying right now?"

MARKS = (r"remember\w*|recall\w*|think(?:s|ing)?\s+(?:of|about)|thought\s+(?:of|about)|memor(?:y|ies)|earlier|before|"
         r"back\s+(?:at|in|home|there)|told|said|mention\w*|wonder\w*|miss(?:es|ed|ing)?|\bleft\b|\bno\b|\bnot\b|n't\b|among|"
         r"far\s+(?:away|behind)|behind\s+you|used\s+to|yesterday|remain\w*|stays?|stayed|"
         r"not\s+(?:with|carrying|holding|in)|n't\s+(?:have|carry|hold|with)")
_NOTAGS = "Narrator|Narration|Note|OOC|GM|Scene|Setting|Time|Reminder"
_TAG = r"(?-i:[A-Z][\w'-]*(?:\s[A-Z][\w'-]*)?)"
#: the characters a reply may put before a speaker tag: blanks, quote marks, bullets, bold/italic, '>' ("'Nell:", "- Nell:")
#: (a leading quote hid a tag from session_state's presence check, 2026-11-14)
#: pin of _stack_common.py (turn split/join, grade_weighted): chains.revision() digests only THIS file, so a change to the
#: shared module must change this line (tests/test_gen_sceneshift.py::test_shared_pin)
_STACK_SHA = "d9ed8da25a93decf"
_PRE = r"[ \t>*_\-\u2022'\"\u2018\u201c]*"
_TAGSTART = rf"^{_PRE}{_TAG}[ \t]*[*_]*[ \t]*:"
_SENT = r"(?:[^\n.!?]*[.!?]+[\"”')*_]*[ \t]+)*"


class _Fail(Exception):
    pass


def _krx(key: str) -> str:
    return r"\b" + r"\s+".join(re.escape(w) for w in key.split()) + r"s?\b"


def _alt(items) -> str:
    return "(?:" + "|".join(items) + ")"


def _foreign_rx(words: list[str], tags: bool) -> str:
    """Forbid pattern for old-scene words (already regexes): inside any tagged line, or in an untagged sentence that
    is not a memory / distance; ``tags``: also a tagged line whose tag is itself one of them."""
    w = _alt(words)
    parts = [rf"{_TAGSTART}[^\n]*{w}",
             rf"^(?!{_PRE}{_TAG}[ \t]*[*_]*[ \t]*:){_SENT}(?![^.!?\n]*\b(?:{MARKS})\b)[^.!?\n]*{w}"]
    return "|".join(parts)


def _presence_rx(cast: list[str], foreign: list[str]) -> str:
    names = [rf"\b{re.escape(n)}\b" for n in foreign]
    own = _alt([re.escape(n) for n in cast])
    bad_tag = rf"^{_PRE}(?!{own}\b)(?!(?:{_NOTAGS})\b)(?-i:[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)[ \t]*[*_]*[ \t]*:"
    return bad_tag + (("|" + _foreign_rx(names, True)) if names else "")


def _know_rx(token_rx: str, cast: list[str]) -> str:
    """a present person says the secret: inside a speaker's tagged line (a narration tag excepted; the tag may follow a
    sentence end on the same line: 'She answers. Lenka: "..."'), or in a sentence that names a present person and is
    not a memory or a negation ('Theo whispers, "The cellar code is 9328."'). The narration recalling the character's
    own memory is allowed by system rule 2 ('You remember the code from the last place: 2467')."""
    tag = rf"(?:^|[.!?][\"\u201d')*_]*[ \t]+){_PRE}(?!(?:{_NOTAGS})\b){_TAG}[ \t]*[*_]*[ \t]*:[^\n]*(?:{token_rx})"
    who = _alt([rf"\b{re.escape(n)}\b" for n in cast])
    sent = rf"(?:^|(?<=[.!?][ \t])|(?<=[.!?][\"\u201d'][ \t]))(?![^.!?\n]*\b(?:{MARKS})\b)"
    near = rf"{sent}[^.!?\n]*(?:{who}[^.!?\n]*(?:{token_rx})|(?:{token_rx})[^.!?\n]*{who})"
    return tag + "|" + near


_GATES = ("presence", "setting", "know")   # a bleed zeroes the turn; the positive checks must earn half or the turn is 0


def _chk(cid: str, t: int, typ: str, pattern: str, scope: str) -> dict:
    return {"id": f"{cid}-t{t}" if not re.search(r"-\d+$", cid) else re.sub(r"(-\d+)$", rf"-t{t}\1", cid),
            "fact": cid, "ftype": "fact", "group": "continuity", "turns": [t], "scope": scope, "weight": 1.0,
            "type": typ, "pattern": pattern, ("gate" if cid in _GATES else "positive"): True}


# ------------------------------------------------------------ plan
_DIGIT = ["0|zero|oh", "1|one", "2|two", "3|three", "4|four", "5|five", "6|six", "7|seven", "8|eight", "9|nine"]


def _code_rx(raw: str) -> str:
    """a door code said as numerals or as words ('2467', '2 4 6 7', 'Two-four-six-seven', 'four-three-oh-two'): a leak
    spelled in words is still a leak, and a spelled answer is still the answer (hosted SCENELADDER screen, 2026-11-14)"""
    parts = [rf"(?:{_DIGIT[int(c)]})" for c in raw]
    return r"(?<![\w])" + r"[\s,\-\u2013.]*".join(parts) + r"(?![\w])"


def _make_secret(g, kind: str, subj: str, near: dict | None, used: set) -> dict:
    for _ in range(60):
        if kind == "code":
            if near:
                d = list(near["raw"])
                i = g.randrange(3)
                d[i], d[i + 1] = d[i + 1], d[i]
                raw = "".join(d)
            else:
                raw = str(g.randint(1000, 9999))
            rx = _code_rx(raw)
            tell, sub = f"the {subj} code is {raw}", f"the {subj} code"
        else:
            if near:
                a = near["raw"].split()[0]
                n = g.choice(NOUN_P)
            else:
                a, n = g.choice(ADJ_P), g.choice(NOUN_P)
            raw = f"{a} {n}"
            rx = rf"\b{a}[\s\-_]*{n}\b"
            tell, sub = f"the pass phrase for the {subj} is '{raw}'", f"the pass phrase for the {subj}"
        if raw not in used and len(set(raw)) > 2:
            used.add(raw)
            return {"kind": kind, "raw": raw, "rx": rx, "tell": tell, "sub": sub}
    raise _Fail


def _cast_text(names: list[str], roles: list[str]) -> str:
    bits = [f"{n} is {r}" for n, r in zip(names, roles)]
    return ", and ".join([", ".join(bits[:-1]), bits[-1]]) if len(bits) > 2 else " and ".join(bits)


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _attempt(g, L: dict) -> dict:
    seq, visits = g.choice(L["plans"])
    letters = sorted(set(seq))
    places = g.sample(PLACES, len(letters))
    if L["sim"]:
        fams = g.sample(FAMS, max(L["cast"][:len(letters)]))
        casts = {s: [fams[i][k] for i in range(L["cast"][k])] for k, s in enumerate(letters)}
    else:
        pool = [n for f in g.sample(FAMS, len(FAMS)) for n in [f[g.randrange(3)]]]
        order = iter(pool)
        casts = {s: [next(order) for _ in range(L["cast"][k])] for k, s in enumerate(letters)}
    kind0 = g.choice(["code", "phrase"])
    subjs = g.sample(SUBJ, len(letters))
    used: set = set()
    carry = g.sample(CARRY, 2)
    left = g.choice(LEFT)
    left = (f"{g.choice(COLORS)} {left[0].split(' ', 1)[1]}", left[1])
    scenes: dict = {}
    for k, s in enumerate(letters):
        kind = kind0 if L["near"] else g.choice(["code", "phrase"])
        sec = _make_secret(g, kind, subjs[k], scenes["A"]["secret"] if (L["near"] and k) else None, used)
        pl = places[k]
        scenes[s] = {"id": s, "place": pl["name"], "kind": pl["kind"], "cast": casts[s], "secret": sec,
                     "objs": g.sample(pl["objs"], L["objs"]), "roles": g.sample(ROLES, len(casts[s])), "teller": g.choice(casts[s]), "carry": None}
    scenes["A"]["carry"] = carry[0]
    if L["pick2"]:
        scenes["B"]["carry"] = carry[1]
    allnames = [n for s in scenes.values() for n in s["cast"]]
    if len(set(allnames)) != len(allnames):
        raise _Fail
    beats: list[dict] = []
    seen: list[str] = []
    carried: list[tuple] = []
    left_done = False
    asked: set = set()
    prev = None
    fills = list(L["fill"]) + [0] * 8
    for vi, (s, probes) in enumerate(zip(seq, visits)):
        sc = scenes[s]
        first = s not in seen
        names = sc["cast"]
        if first:
            objs = [o[0] for o in sc["objs"]]
            txt = f"I step into {sc['place']}. {_cast_text(names, sc['roles'])}. I can see {_list(objs)}."
            if sc["carry"]:
                txt += f" {sc['carry'][0].capitalize()} is lying on a small shelf within reach."
        else:
            txt = f"I push the door open and walk into {sc['place']} again."
        if prev:
            txt = f"I say goodbye to {_list(scenes[prev]['cast'])} and head out. A little while later: " + txt
        beats.append({"kind": "arrive", "scene": s, "text": txt, "visit": vi})
        seen_before = list(seen)
        if first:
            seen.append(s)
        pending: list[dict] = []
        if first:
            sec = sc["secret"]
            pending.append({"kind": "tell", "scene": s, "text": f"{sc['teller']} leans close and tells me, quietly, "
                            f"that {sec['tell']}. I nod and keep it to myself."})
            if sc["carry"]:
                pending.append({"kind": "pickup", "scene": s, "item": sc["carry"],
                                "text": f"I pick up {sc['carry'][0]} and slip it into my coat pocket."})
            if s == "A":
                pending.append({"kind": "leave", "scene": s, "item": left,
                                "text": f"I lean my {left[0]} against the wall near the door and leave it there for now."})
        for _ in range(fills[vi]):
            n = g.choice(names)
            pending.append({"kind": "fill", "scene": s, "text": g.choice(FILL).format(n=n, o=g.choice(sc["objs"])[0])})
        for p in probes:
            pending.append({"kind": p, "scene": s})
        look = [b for b in pending if b["kind"] in ("look", "lookp")]
        rest = [b for b in pending if b not in look]
        g.shuffle(rest)
        order = look + rest
        # the check inputs are resolved in story order
        for b in order:
            b.update(visit=vi)
            k = b["kind"]
            others = [x for x in seen_before if x != s]
            if k == "pickup":
                carried.append(b["item"])
            elif k == "leave":
                left_done = True
            elif k in ("look", "lookp"):
                b["text"] = LOOK if k == "look" else LOOKP
            elif k == "hold":
                b["text"] = HOLD
                b["carried"] = list(carried)
            elif k in ("absent", "self"):
                if not others:
                    raise _Fail
                fresh = [x for x in others if (k, x) not in asked] or others
                f = scenes[g.choice(fresh)]
                asked.add((k, f["id"]))
                b["src"] = f["id"]
                if k == "absent":
                    b["npc"] = g.choice(names)
                    role = sc["roles"][names.index(b["npc"])]
                    b["text"] = f"I turn to the person {role} and ask quietly whether they happen to know {f['secret']['sub']}."
                else:
                    b["text"] = (f"((Out of character, for the narrator: what exactly did {f['teller']} tell my "
                                 f"character about {f['secret']['sub']}?))")
            elif k == "hearsay":
                if not prev:
                    raise _Fail
                b["npc"] = g.choice(names)
                role = sc["roles"][names.index(b["npc"])]
                b["text"] = (f"The person {role} asks me where I have just come from and who I met there. I only say it "
                             f"was {scenes[prev]['kind']}, and I ask them whether they already know who runs the place.")
            if k in ("look", "lookp", "absent", "hearsay", "hold"):
                b["foreign"] = [x for x in seen_before if x != s]
                b["carried"] = list(carried)
                b["left"] = left_done
            beats.append(b)
        prev = s
    turns = [b["text"] for b in beats]
    if not 14 <= len(turns) <= 22:
        raise _Fail
    return {"seq": seq, "scenes": scenes, "beats": beats, "left": left, "turns": turns, "T": len(turns)}


def _build_checks(p: dict) -> list[dict]:
    S, out = p["scenes"], []
    for t, b in enumerate(p["beats"], 1):
        k = b["kind"]
        if k == "self":
            out.append(_chk("cknow", t, "require_regex", S[b["src"]]["secret"]["rx"], "all"))
            continue
        if k not in ("look", "lookp", "absent", "hearsay", "hold"):
            continue
        sc, txt = S[b["scene"]], b["text"]
        named = lambda rx: bool(re.search(rx, txt, re.I))      # noqa: E731
        fnames, fkeys = [], []
        for f in b["foreign"]:
            fs = S[f]
            fnames += [n for n in fs["cast"] if not named(rf"\b{re.escape(n)}\b")]
            items = list(fs["objs"]) + ([fs["carry"]] if fs["carry"] and fs["carry"] not in b["carried"] else [])
            if f == "A" and b["left"]:
                items.append(p["left"])
            fkeys += [_krx(i[1]) for i in items if i[1] not in [c[1] for c in b["carried"]] and not named(_krx(i[1]))]
        out.append(_chk("presence", t, "forbid_regex", _presence_rx(sc["cast"], fnames), "ic"))
        if fkeys:
            out.append(_chk("setting", t, "forbid_regex", _foreign_rx(fkeys, False), "ic"))
        own = _alt([re.escape(n) for n in sc["cast"]])
        if k in ("look", "lookp"):
            look = "".join(rf"(?=[\s\S]*\b{re.escape(n)}\b)" for n in sc["cast"])
            look += "(?=[\\s\\S]*" + _alt([_krx(o[1]) for o in sc["objs"]]) + ")"
            out.append(_chk("roster", t, "require_regex", r"\A" + look, "ic"))
            if k == "lookp" and b["left"] and b["scene"] == "A":
                neg = r"gone|missing|no longer|not there|n't there|vanished|nowhere|nothing of yours"
                out.append(_chk("persist", t, "require_regex",
                                rf"(?:^|[.!?][\"”')*_]*[ \t\n]+)(?![^.!?\n]*\b(?:{neg})\b)[^.!?\n]*{_krx(p['left'][1])}", "ic"))
        elif k == "absent":
            # a present person must not know it: what the people say, not the narration's memory (system rule 2:
            # "You remember the code: 2467" is not a leak; rc1 fairness fix, 2026-11-14)
            out.append(_chk("know", t, "forbid_regex", _know_rx(S[b["src"]]["secret"]["rx"], sc["cast"]), "ic"))
            out.append(_chk("speaks", t, "require_regex", rf"^{_PRE}{re.escape(b['npc'])}[ \t]*[*_]*[ \t]*:", "ic"))
        elif k == "hearsay":
            out.append(_chk("speaks", t, "require_regex", rf"^{_PRE}{re.escape(b['npc'])}[ \t]*[*_]*[ \t]*:", "ic"))
        else:
            for i, c in enumerate(b["carried"], 1):
                out.append(_chk(f"hold-{i}", t, "require_regex", _krx(c[1]), "ic"))
    return out


def _system() -> str:
    return ("You are the narrator of a slow, friendly role-play. The player's character walks between places; you "
            "describe what happens and voice the people who are there.\nRules:\n"
            "1. Reply with a short narration (at most three sentences). Then, for each person who speaks, one "
            "separate line in the form `Name: what they say`. Never write a line for the player's character and never "
            "speak or act for them.\n"
            "2. The player tells you about each place, the people in it and what they are told. Accept that as fact. "
            "Only the people and things in the place where the player's character is now exist there; you may add "
            "small unnamed background details but no new named characters. The people here never name or point to a person "
            "or thing from another place unless the player's words in that turn named it; only the narration may recall "
            "them, as the player's character's memory.\n"
            "3. The player's character remembers what they were told and keeps what they picked up. Nobody else knows "
            "what was said in another place unless the player's character says it.\n"
            "4. If the player writes a line in double parentheses ((like this)) they are speaking to you out of "
            "character: answer plainly in one or two sentences, outside the story, with no tagged lines.\n")


# ------------------------------------------------------------ answers
def _correct(p: dict, t: int, g) -> str:
    b, S = p["beats"][t - 1], p["scenes"]
    sc, k = S[b["scene"]], b["kind"]
    a, n0 = sc["cast"], sc["cast"][0]
    if k in ("look", "lookp"):
        s = f"{_list(a)} are here, near {_list([o[0] for o in sc['objs']])}."
        if k == "lookp" and b["left"] and b["scene"] == "A":
            s += f" Your {p['left'][0]} still leans against the wall by the door."
        return f"{s}\n{n0}: Welcome."
    if k == "absent":
        return f"{b['npc']} frowns.\n{b['npc']}: I have no idea what you mean."
    if k == "hearsay":
        return f"{b['npc']} grins.\n{b['npc']}: A guess? I really could not say."
    if k == "hold":
        return "You are carrying " + _list([c[0] for c in b["carried"]]) + "."
    if k == "self":
        return f"You remember it clearly: {S[b['src']]['secret']['raw']}."
    return f"{n0} nods.\n{n0}: Quite well, thank you."


# ------------------------------------------------------------ plan cache
@functools.lru_cache(maxsize=256)
def _plan_cached(level: int, key, release_seed: str, case_id: str) -> dict:
    L = LEVELS[level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, key)
    for attempt in range(300):
        g = rng(seed, f"plan{attempt}")
        try:
            p = _attempt(g, L)
        except (_Fail, IndexError, ValueError, KeyError):
            continue
        p.update(level=level, seed=seed, attempt=attempt, system=_system())
        p["checks"] = _build_checks(p)
        p["reference"] = [_correct(p, t, rng(seed, f"ref{t}")) for t in range(1, p["T"] + 1)]
        if grade_weighted(p["checks"], p["reference"]) == 1.0:
            return p
    raise RuntimeError(f"sceneshift: no valid draw for {case_id} L{level}")


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    return copy.deepcopy(_plan_cached(int(gen.get("level", 1)), gen.get("key"), release_seed, case_id))


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    p = _plan(gen, release_seed, case_id)
    return {
        "id": case_id, "category": "rpdeep", "system": p["system"], "history": [], "turns": p["turns"],
        "temperature": 0.7, "top_p": 0.95, "max_tokens": MAX_TOKENS, "user_name": "Player",
        "checks": p["checks"], "judge_elements": [], "programmatic": True, "generator": dict(gen),
        "meta": {"family": FAMILY, "level": p["level"], "n_turns": len(p["turns"]), "n_checks": len(p["checks"]),
                 "moves": p["seq"],
                 "prompt_tokens_est": round((len(p["system"]) + sum(len(x) for x in p["turns"])) / 4),
                 "reference_tokens_est": round(sum(len(x) for x in p["reference"]) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_plan(gen, release_seed, case_id)["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_weighted(materialized["checks"], split_turns(answer))


# ------------------------------------------------------------ mutants (answer generators)
MUTANT_KINDS = ("never-leaves-A", "forgets-everything", "everyone-knows-everything", "echo-the-user")


def _mutant(p: dict, kind: str) -> str:
    S, out = p["scenes"], []
    for t, b in enumerate(p["beats"], 1):
        k, sc = b["kind"], S[b["scene"]]
        good = _correct(p, t, None)
        if kind == "echo-the-user":
            out.append(b["text"])
        elif kind == "never-leaves-A":
            A = S["A"]
            if k == "self":
                out.append(good)
            elif k == "hold":
                out.append(f"{A['objs'][0][0].capitalize()} gleams as you check. " + good[0].lower() + good[1:]
                           + f"\n{A['cast'][0]}: Of course, dear.")
            elif k in ("look", "lookp"):
                out.append(f"{_list(A['cast'])} are here, near {_list([o[0] for o in A['objs']])}.\n{A['cast'][0]}: Welcome.")
            else:
                out.append(f"{A['objs'][0][0].capitalize()} gleams nearby.\n{A['cast'][0]}: Of course, dear.")
        elif kind == "forgets-everything":
            if k in ("look", "lookp"):
                out.append("You look around the room. It is quiet and warm.")
            elif k in ("absent", "hearsay"):
                out.append("Someone shrugs. \"I do not know.\"")
            elif k == "hold":
                out.append("You are not carrying anything.")
            elif k == "self":
                out.append("You cannot recall anything of the kind.")
            elif k == "arrive":
                out.append("You arrive. Nobody says anything.")
            else:
                out.append("Noted.")
        else:                       # everyone-knows-everything: the right answer plus a leak from the first old scene
            others = [x for x in "ABC" if x in S and x != b["scene"] and x in (b.get("foreign") or
                      [y for y in "ABC" if y in S and y < b["scene"]])]
            if k == "self" or not others:
                out.append(good)
            else:
                f = S[others[0]]
                out.append(f"{good}\n{sc['cast'][0]}: Yes, {f['teller']} told me {f['secret']['tell']}. "
                           f"Mind {f['objs'][0][0]}.")
    return join_turns(out)


def mutants_named(gen: dict, *, release_seed: str, case_id: str) -> dict:
    p = _plan(gen, release_seed, case_id)
    return {k: _mutant(p, k) for k in MUTANT_KINDS}


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    out = list(mutants_named(gen, release_seed=release_seed, case_id=case_id).values())
    out.append(join_turns([""] * len(mat["turns"])))
    return [a for a in dict.fromkeys(out) if grade(a, mat) < 1.0]


def _main(argv: list[str] | None = None) -> None:
    import json
    argv = sys.argv[1:] if argv is None else argv
    level = int(argv[0]) if argv else 5
    g = {"type": FAMILY, "level": level}
    m = materialize(g, release_seed="4.5.0", case_id="EN-demo")
    print("SYSTEM:\n" + m["system"])
    for i, t in enumerate(m["turns"], 1):
        print(f"\n--- USER TURN {i} ---\n{t}")
    ref = reference(g, release_seed="4.5.0", case_id="EN-demo")
    print("\n--- REFERENCE ---\n" + ref.replace(TURN_SEP, "\n-----\n"))
    print("\n# meta:", json.dumps(m["meta"]))
    print("# grade(reference) =", grade(ref, m))


if __name__ == "__main__":
    _main()
