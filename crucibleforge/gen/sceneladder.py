"""SCENELADDER generator (gen2 chat, 4.5 rc2; lab card EXP-20261114-32, blueprint concept H3 "scenes, people and
knowledge kept apart"): the SCENESHIFT role-play rebuilt as a ladder ordered by construction.

Two parameters rise with the rung, everything else is held fixed:
  K  distinct knowledge states among the people met: (places they were at, secrets they heard). Built from scenes,
     an out-of-earshot person (R3+), a traveller who heard a secret in one place and turns up in a later one (R4+),
     and a traveller who left before the secret was told (R5).
  D  the most place visits between a secret being told and a probe about it.
Held fixed on every rung: 8 graded probe turns with the same mix (3 negative knowledge, 2 positive knowledge, 2 self
or 1 self + 1 attribution, 1 look), a greeting from a person in turn 2 (the `Name:` format is used before any probe),
2 people per place (+ travellers), no sound-alike names, no near-miss tokens. Who heard what is always stated in the
user's narration ("Rook is at the far end and hears none of it"), never implied.

Knowledge probes ask the addressed person, by name and in the same words every time, whether they know a secret and to
whisper it if they do. Negative probe: the addressed person must not say it (gate). Positive probe: the addressed
person must say it (the knowledge check). Both read what that person says, tagged or quoted, never the narration's own
memory. ``speaks`` (the addressed person has a `Name:` line) is a separate, non-gating check of its own weight, so a
format slip costs the format credit and never the knowledge credit. 0 judge calls.

Mutants: deny-all, tell-all, forgets-everything, echo-the-user (+ empty); deny-all and tell-all score below 1 on
every rung (the probe mix pairs positive and negative probes)."""
from __future__ import annotations

import copy
import functools
import re
import sys

from .. import gen as _gen
from ._stack_common import TURN_SEP, grade_weighted, join_turns, rng, split_turns
from .sceneshift import (FAMS, MARKS, PLACES, ROLES, SUBJ, ADJ_P, NOUN_P, LOOK, _NOTAGS, _PRE, _TAG, _alt,
                         _cast_text, _foreign_rx, _krx, _list)

FAMILY = "sceneladder"
VARIANTS: tuple = ()
#: pins of the shared modules: chains.revision() digests only THIS file, so a change to either must change its line
#: (tests/test_gen_sceneladder.py::test_shared_pins)
_STACK_SHA = "d9ed8da25a93decf"
_SCENESHIFT_SHA = "c07bf937afde18b8"

# probe = (kind, who, about); who: teller/other/earshot/trav/travleft/npc (any local); about: a place letter
_RUNGS = {
    1: {"seq": "ABA", "earshot": False, "trav": False, "travleft": False,
        "probes": [[], [("neg", "npc", "A"), ("pos", "teller", "B"), ("self", None, "A")],
                   [("look", None, None), ("pos", "teller", "A"), ("neg", "other", "B"), ("neg", "teller", "B"),
                    ("self", None, "B")]]},
    2: {"seq": "ABCA", "earshot": False, "trav": False, "travleft": False,
        "probes": [[], [("neg", "npc", "A"), ("self", None, "A")], [("neg", "npc", "B"), ("pos", "teller", "C")],
                   [("look", None, None), ("pos", "teller", "A"), ("neg", "other", "C"), ("self", None, "B")]]},
    3: {"seq": "ABCA", "earshot": True, "trav": False, "travleft": False,
        "probes": [[], [("neg", "npc", "A"), ("self", None, "A")], [("neg", "npc", "B"), ("pos", "teller", "C")],
                   [("look", None, None), ("pos", "teller", "A"), ("neg", "earshot", "A"), ("self", None, "B")]]},
    4: {"seq": "ABCDA", "earshot": True, "trav": True, "travleft": False,
        "probes": [[], [("neg", "npc", "A")], [("pos", "trav", "A"), ("self", None, "B")], [("neg", "npc", "C")],
                   [("look", None, None), ("pos", "teller", "A"), ("neg", "earshot", "A"), ("self", None, "C")]]},
    5: {"seq": "ABCDA", "earshot": True, "trav": True, "travleft": True,
        "probes": [[], [("neg", "npc", "A")], [("pos", "trav", "A"), ("self", None, "B")], [("neg", "travleft", "A")],
                   [("look", None, None), ("pos", "teller", "A"), ("neg", "earshot", "A"), ("attrib", None, "C")]]},
    # R6: A's secret changes after some people left: a third person in A (earshot), the other person hears the old
    # value and steps out before the change, the courier hears both. Probes ask what a named person believes.
    6: {"seq": "ABCDA", "earshot": True, "trav": True, "travleft": True, "change": True,
        "probes": [[], [("neg", "npc", "A")], [("pos", "trav", "A"), ("self", None, "B")], [("neg", "travleft", "A")],
                   [("look", None, None), ("pos", "other", "A"), ("neg", "earshot", "A"), ("now", None, "A")]]},
    # R7: R6 + news passed on inside the story: in C the courier tells C's other person the new value (stated in the
    # narration; C's teller does not hear it), and the player comes back to C.
    7: {"seq": "ABCDCA", "earshot": True, "trav": True, "travleft": True, "change": True, "retell": True,
        "probes": [[], [("neg", "npc", "A")], [("self", None, "B")], [("neg", "travleft", "A")],
                   [("pos", "other", "A"), ("neg", "teller", "A")],
                   [("look", None, None), ("pos", "other", "A"), ("now", None, "A")]]},
}
#: rung -> ((prompt tokens est min, max over 30 seeds), (checks min, max), reference answer tokens): measured, pinned
_SIZES = {1: ((762, 816), (25, 25), 172), 2: ((842, 902), (25, 25), 196), 3: ((866, 918), (25, 25), 198),
          4: ((982, 1048), (25, 25), 218), 5: ((1047, 1094), (25, 25), 218), 6: ((1128, 1196), (26, 26), 238),
          7: ((1202, 1283), (26, 26), 259)}
LEVELS = {lv: dict(r) for lv, r in _RUNGS.items()}
for _lv, _row in LEVELS.items():
    _row["size_prompt"], _row["size_checks"], _row["answer_tokens"] = _SIZES[_lv]

MAX_TOKENS = 6144
W_GATE, W_KNOW, W_SPEAKS = 0.25, 1.0, 0.5
TRAV_ROLE = "a courier waiting by the counter for a parcel"
TRAVL_ROLE = "a delivery rider finishing a cup of tea"
ASK = ("I turn to {n} and ask quietly whether they know {sub}, and if they do, to whisper it to me so I can check "
       "I have it right.")
SELF = "((Out of character, for the narrator: what exactly was my character told about {sub}?))"
ATTRIB = "((Out of character, for the narrator: who told my character {sub}?))"
NOW = "((Out of character, for the narrator: what is {sub} now, as far as my character knows?))"
STEP = "{n} goes out to the yard to fetch something and is gone for a while."
#: single names added for the rungs with a third person in A (11 people): none sounds like a FAMS name
EXTRA_FAMS = [("Wren", "Wren", "Wren"), ("Ansel", "Ansel", "Ansel"), ("Hollis", "Hollis", "Hollis")]
GREET = "{n} looks up and says hello to me. I say hello back and ask how they are."


class _Fail(Exception):
    pass


def _says_rx(npc: str, tok: str, memo: str) -> str:
    """the person ``npc`` says ``tok``: their tagged line (also mid-line after a sentence end) holding it, or a sentence
    naming them with it that is not narration memory (``memo`` = the words that make it one)"""
    n = re.escape(npc)
    tag = rf"(?:^|[.!?][\"”')*_]*[ \t]+){_PRE}{n}[ \t]*[*_]*[ \t]*:[^\n]*(?:{tok})"
    sent = rf"(?:^|(?<=[.!?][ \t])|(?<=[.!?][\"”'][ \t]))(?![^.!?\n]*(?:{memo}))"
    near = rf"{sent}[^.!?\n]*(?:\b{n}\b[^.!?\n]*(?:{tok})|(?:{tok})[^.!?\n]*\b{n}\b)"
    return tag + "|" + near


_MEMO_NEG = rf"\b(?:{MARKS})\b"
_MEMO_POS = r"\byou\s+(?:remember|recall)\w*|\byour\s+memory"


def _presence_rx(cast: list[str], foreign: list[str]) -> str:
    names = [rf"\b{re.escape(n)}\b" for n in foreign]
    own = _alt([re.escape(n) for n in cast])
    bad = rf"^{_PRE}(?!{own}\b)(?!(?:{_NOTAGS})\b)(?-i:[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)[ \t]*[*_]*[ \t]*:"
    return bad + (("|" + _foreign_rx(names, True)) if names else "")


def _chk(cid: str, t: int, typ: str, pattern: str, scope: str, role: str, w: float) -> dict:
    c = {"id": f"{cid}-t{t}", "fact": cid, "ftype": "fact", "group": "continuity", "turns": [t], "scope": scope,
         "weight": w, "type": typ, "pattern": pattern}
    if role:
        c[role] = True
    return c


# ------------------------------------------------------------ plan
_DIGIT = ["0|zero|oh", "1|one", "2|two", "3|three", "4|four", "5|five", "6|six", "7|seven", "8|eight", "9|nine"]


def _code_rx(raw: str) -> str:
    """a door code said as numerals or as words ('7139', '7 1 3 9', 'Seven-one-three-nine', 'four-three-oh-two')"""
    parts = [rf"(?:{_DIGIT[int(c)]})" for c in raw]
    return r"(?<![\w])" + r"[\s,\-\u2013.]*".join(parts) + r"(?![\w])"


def _secret(g, subj: str, used: set, kind: str | None = None) -> dict:
    for _ in range(60):
        if (g.random() < 0.5) if kind is None else kind == "code":
            raw = str(g.randint(1000, 9999))
            rx, tell, sub = _code_rx(raw), f"the {subj} code is {raw}", f"the {subj} code"
        else:
            a, n = g.choice(ADJ_P), g.choice(NOUN_P)
            raw = f"{a} {n}"
            rx, tell, sub = rf"\b{a}[\s\-_]*{n}\b", f"the pass phrase for the {subj} is '{raw}'", \
                f"the pass phrase for the {subj}"
        # no near-miss tokens: no shared word, no code with 2+ digits in the same place as an earlier one
        near = any(len(set(raw.split()) & set(u.split())) if " " in raw + u else
                   sum(x == y for x, y in zip(raw, u)) >= 2 for u in used)
        if raw in used or near or len(set(raw)) < 3:
            continue
        used.add(raw)
        return {"raw": raw, "rx": rx, "tell": tell, "sub": sub, "kind": "phrase" if " " in raw else "code"}
    raise _Fail


def _attempt(g, R: dict) -> dict:
    seq = R["seq"]
    letters = sorted(set(seq))
    places = g.sample(PLACES, len(letters))
    chg = R.get("change", False)
    n_people = 2 * len(letters) + R["trav"] + R["travleft"] + chg
    pool = FAMS + EXTRA_FAMS if chg else FAMS
    names = [f[g.randrange(3)] for f in g.sample(pool, n_people)]     # one name per family: no sound-alikes
    it = iter(names)
    subjs = g.sample(SUBJ, len(letters))
    used: set = set()
    S: dict = {}
    for k, s in enumerate(letters):
        cast = [next(it), next(it)] + ([next(it)] if chg and s == "A" else [])
        teller = g.choice(cast)
        rest_ = [c for c in cast if c != teller]
        S[s] = {"id": s, "place": places[k]["name"], "kind": places[k]["kind"], "cast": cast, "teller": teller,
                "other": rest_[0], "earshot": rest_[-1], "objs": g.sample(places[k]["objs"], 2),
                "roles": g.sample(ROLES, len(cast)), "secret": _secret(g, subjs[k], used)}
    if chg:     # the changed value: same subject, same kind, a fresh token
        old = S["A"]["secret"]
        new = _secret(g, subjs[0], used, old["kind"])
        new["tell"] = new["tell"].replace(" is ", " is now ", 1)
        S["A"]["new"] = new
    trav = next(it) if R["trav"] else None
    travl = next(it) if R["travleft"] else None
    # who is where, per visit; who knows what (person -> set of letters), where each person has been
    knows: dict = {}
    met: dict = {}
    beats: list[dict] = []
    seen: list[str] = []
    told_at: dict = {}
    for vi, (s, probes) in enumerate(zip(seq, R["probes"])):
        sc = S[s]
        first = s not in seen
        here = list(sc["cast"])
        extra = []
        if s == "A" and first:
            extra = [n for n in (trav, travl) if n]
        if trav and s == "C" and first:
            extra = [trav]
        if travl and s == "D" and first:
            extra = [travl]
        here += extra
        for n in here:
            met.setdefault(n, set()).add(s)
            knows.setdefault(n, set())
        if first:
            objs = [o[0] for o in sc["objs"]]
            roles = list(sc["roles"])
            txt = f"I step into {sc['place']}. {_cast_text(sc['cast'], roles)}."
            if s == "A" and extra:
                txt += " " + " ".join(f"{n} is here too, {TRAV_ROLE if n == trav else TRAVL_ROLE}." for n in extra)
            elif extra:
                txt += f" {extra[0]}, whom I met at {S['A']['place']}, is here as well."
            txt += f" I can see {_list(objs)}."
        else:
            txt = f"I push the door open and walk into {sc['place']} again. Only {_list(sc['cast'])} are here now."
        if beats:
            txt = "I say goodbye and head out. A little while later: " + txt
        beats.append({"kind": "arrive", "scene": s, "visit": vi, "text": txt, "here": here})
        if first and s == "A":
            n = sc["cast"][0]
            beats.append({"kind": "greet", "scene": s, "visit": vi, "npc": n, "text": GREET.format(n=n), "here": here})
            if travl:
                beats.append({"kind": "leave", "scene": s, "visit": vi, "here": here,
                              "text": f"{travl} finishes the tea, waves goodbye and leaves before anything else is said."})
                here = [n for n in here if n != travl]
        if first:
            sec = sc["secret"]
            if s == "A" and chg:
                ear, oth = sc["earshot"], sc["other"]
                txt = (f"{ear} has gone to the far end of the room and cannot hear us. {sc['teller']} leans close and "
                       f"tells me, quietly, that {sec['tell']}. {oth} and {trav}, standing right beside me, hear it too. "
                       f"{ear} hears none of it.")
                for n in (sc["teller"], oth, trav):
                    knows[n].add(s)
                beats.append({"kind": "tell", "scene": s, "visit": vi, "text": txt, "here": here})
                beats.append({"kind": "step", "scene": s, "visit": vi, "text": STEP.format(n=oth), "here": here})
                here = [n for n in here if n != oth]
                txt = (f"{sc['teller']} catches my sleeve: they changed it this morning, and {sc['new']['tell']}. "
                       f"{trav}, still beside me, hears it too. {oth} is still out in the yard and does not hear it, and "
                       f"{ear} is still at the far end.")
                hearers = [sc["teller"], trav]
                s_key = s + "+"
            elif s == "A" and R["earshot"]:
                hearers = [sc["teller"]] + ([trav] if trav else [])
                txt = (f"{sc['other']} has gone to the far end of the room and cannot hear us. {sc['teller']} leans "
                       f"close and tells me, quietly, that {sec['tell']}.")
                txt += (f" {trav}, standing right beside me, hears it too." if trav else "")
                txt += f" {sc['other']} hears none of it."
            else:
                hearers = [n for n in here]
                others = [n for n in here if n != sc["teller"]]
                txt = (f"{sc['teller']} tells me that {sec['tell']}. {_list(others)} "
                       f"{'is' if len(others) == 1 else 'are'} right beside us and "
                       f"{'hears' if len(others) == 1 else 'hear'} it too.")
            for n in hearers:
                knows[n].add(s_key if s == "A" and chg else s)
            told_at[s] = vi
            beats.append({"kind": "update" if s == "A" and chg else "tell", "scene": s, "visit": vi, "text": txt,
                          "here": here})
            seen.append(s)
            if s == "C" and R.get("retell"):
                A = S["A"]
                txt = (f"{trav} turns to {sc['other']} and quietly tells them the new {A['secret']['sub'][4:]} from "
                       f"{A['place']}: it is {A['new']['raw']} now. {sc['teller']} is busy at the far end and hears "
                       f"none of it.")
                knows[sc["other"]].add("A+")
                beats.append({"kind": "retell", "scene": s, "visit": vi, "text": txt, "here": here})
        body = []
        for kind, who, about in probes:
            b = {"kind": kind, "scene": s, "visit": vi, "here": list(here), "about": about}
            if kind in ("neg", "pos"):
                if who == "npc":
                    npc = g.choice(sc["cast"])
                elif who in ("teller", "other"):
                    npc = sc[who]
                elif who == "earshot":
                    npc = sc["earshot"]
                else:
                    npc = trav if who == "trav" else travl
                b["npc"] = npc
                b["text"] = ASK.format(n=npc, sub=S[about]["secret"]["sub"])
            elif kind == "self":
                b["text"] = SELF.format(sub=S[about]["secret"]["sub"])
            elif kind == "attrib":
                b["text"] = ATTRIB.format(sub=S[about]["secret"]["sub"])
            elif kind == "now":
                b["text"] = NOW.format(sub=S[about]["secret"]["sub"])
            else:
                b["text"] = LOOK
            body.append(b)
        look = [b for b in body if b["kind"] == "look"]
        rest = [b for b in body if b["kind"] != "look"]
        g.shuffle(rest)
        for b in look + rest:
            if b["kind"] in ("neg", "pos"):
                kn = knows[b["npc"]]
                b["value"] = "new" if b["about"] + "+" in kn else "old" if b["about"] in kn else None
                if (b["value"] is not None) != (b["kind"] == "pos"):
                    raise _Fail           # the plan must agree with what the narration made each person hear
            b["dist"] = vi - told_at[b["about"]] if b["about"] else 0
            beats.append(b)
    for b in beats:
        b["met"] = {n: sorted(met[n]) for n in b["here"]}
    turns = [b["text"] for b in beats]
    states = {(tuple(sorted(met[n])), tuple(sorted(knows[n]))) for n in met}
    return {"seq": seq, "scenes": S, "beats": beats, "turns": turns, "T": len(turns), "trav": trav, "travl": travl,
            "K": len(states), "D": max(b["dist"] for b in beats if b["kind"] in ("neg", "pos", "self", "attrib", "now"))}


def _build_checks(p: dict) -> list[dict]:
    S, out = p["scenes"], []
    for t, b in enumerate(p["beats"], 1):
        k = b["kind"]
        if k not in ("look", "neg", "pos", "self", "attrib", "now"):
            continue
        if k == "now":
            out.append(_chk("cnow", t, "require_regex", S[b["about"]]["new"]["rx"], "all", "positive", W_KNOW))
            continue
        if k == "self":
            out.append(_chk("cknow", t, "require_regex", S[b["about"]]["secret"]["rx"], "all", "positive", W_KNOW))
            continue
        if k == "attrib":
            out.append(_chk("attrib", t, "require_regex", rf"\b{re.escape(S[b['about']]['teller'])}\b", "all",
                            "positive", W_KNOW))
            continue
        sc, txt = S[b["scene"]], b["text"]
        named = lambda rx: bool(re.search(rx, txt, re.I))      # noqa: E731
        here = b["here"]
        # a person present may speak of a place they were at themselves (a traveller); nobody else's places
        ok_places = {pl for n in here for pl in b["met"][n]}
        fnames, fkeys = [], []
        for f in sorted(S):
            if f in ok_places:
                continue
            fs = S[f]
            fnames += [n for n in fs["cast"] if n not in here and not named(rf"\b{re.escape(n)}\b")]
            fkeys += [_krx(o[1]) for o in fs["objs"] if not named(_krx(o[1]))]
        for n in (p["trav"], p["travl"]):
            if n and n not in here and not named(rf"\b{re.escape(n)}\b") and not (set(p_met(p, n)) & ok_places):
                fnames.append(n)
        out.append(_chk("presence", t, "forbid_regex", _presence_rx(here, fnames), "ic", "gate", W_GATE))
        if fkeys:
            out.append(_chk("setting", t, "forbid_regex", _foreign_rx(fkeys, False), "ic", "gate", W_GATE))
        if k == "look":
            look = "".join(rf"(?=[\s\S]*\b{re.escape(n)}\b)" for n in here)
            look += "(?=[\\s\\S]*" + _alt([_krx(o[1]) for o in sc["objs"]]) + ")"
            out.append(_chk("roster", t, "require_regex", r"\A" + look, "ic", "positive", W_KNOW))
            continue
        A = S[b["about"]]
        old, new = A["secret"]["rx"], (A["new"]["rx"] if "new" in A else None)
        if k == "neg":
            tok = old if new is None else f"{old}|{new}"
            out.append(_chk("know", t, "forbid_regex", _says_rx(b["npc"], tok, _MEMO_NEG), "ic", "gate", W_KNOW))
        else:
            tok = new if b["value"] == "new" else old
            out.append(_chk("pknow", t, "require_regex", _says_rx(b["npc"], tok, _MEMO_POS), "ic", "positive", W_KNOW))
            if b["value"] == "old" and new:     # a person who left before the change still holds the old value
                out.append(_chk("stale", t, "forbid_regex", _says_rx(b["npc"], new, _MEMO_NEG), "ic", "gate", W_GATE))
        out.append(_chk("speaks", t, "require_regex", rf"^{_PRE}{re.escape(b['npc'])}[ \t]*[*_]*[ \t]*:", "ic",
                        "", W_SPEAKS))
    return out


def p_met(p: dict, n: str) -> list[str]:
    for b in reversed(p["beats"]):
        if n in b.get("met", {}):
            return b["met"][n]
    return []


def _system(passed_on: bool = False) -> str:
    return ("You are the narrator of a slow, friendly role-play. The player's character walks between places; you "
            "describe what happens and voice the people who are there.\nRules:\n"
            "1. Reply with a short narration (at most three sentences). Then, for each person who speaks, one "
            "separate line in the form `Name: what they say`. Never write a line for the player's character and never "
            "speak or act for them.\n"
            "2. The player tells you about each place, the people in it, who hears what, and what the player's "
            "character is told. Accept that as fact. Only the people and things in the place where the player's "
            "character is now exist there; you may add small unnamed background details but no new named characters. "
            "The people here never name or point to a person or thing from a place they have not been to, unless the "
            "player's words in that turn named it; the narration may recall them as the player's character's memory.\n"
            "3. The player's character remembers what they were told. A person knows only what they heard "
            "themselves, in a place where they were, within earshot; "
            + ("that includes what another person tells them; nobody else knows it.\n" if passed_on else
               "nobody else knows it unless the player's character says it.\n")
            + "4. If the player writes a line in double parentheses ((like this)) they are speaking to you out of "
            "character: answer plainly in one or two sentences, outside the story, with no tagged lines.\n")


# ------------------------------------------------------------ answers
def _correct(p: dict, t: int) -> str:
    b, S = p["beats"][t - 1], p["scenes"]
    sc, k = S[b["scene"]], b["kind"]
    n0 = b.get("npc") or sc["cast"][0]
    if k == "look":
        return f"{_list(b['here'])} are here, near {_list([o[0] for o in sc['objs']])}.\n{sc['cast'][0]}: Welcome back."
    if k == "neg":
        return f"{n0} frowns.\n{n0}: Sorry, I have no idea."
    if k == "pos":
        return f"{n0} leans in.\n{n0}: Yes. It is {_val(S, b)}."
    if k == "now":
        return f"It is {S[b['about']]['new']['raw']} now."
    if k == "self":
        return f"Your character was told {S[b['about']]['secret']['tell']}."
    if k == "attrib":
        return f"{S[b['about']]['teller']} told your character that."
    return f"{n0} nods.\n{n0}: Hello, nice to see you."


def _val(S: dict, b: dict) -> str:
    A = S[b["about"]]
    return (A["new"] if b.get("value") == "new" else A["secret"])["raw"]


def _say(p: dict, t: int, tell: bool) -> str:
    b, S = p["beats"][t - 1], p["scenes"]
    n0 = b["npc"]
    if tell:
        A = S[b["about"]]
        return f"{n0} leans in.\n{n0}: Yes. It is {(A.get('new') or A['secret'])['raw']}."
    return f"{n0} frowns.\n{n0}: Sorry, I have no idea."


# ------------------------------------------------------------ plan cache
@functools.lru_cache(maxsize=256)
def _plan_cached(level: int, key, release_seed: str, case_id: str) -> dict:
    R = LEVELS[level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, key)
    for attempt in range(300):
        g = rng(seed, f"plan{attempt}")
        try:
            p = _attempt(g, R)
        except (_Fail, IndexError, ValueError, KeyError, StopIteration):
            continue
        p.update(level=level, seed=seed, attempt=attempt, system=_system(bool(R.get("change"))))
        p["checks"] = _build_checks(p)
        p["reference"] = [_correct(p, t) for t in range(1, p["T"] + 1)]
        if grade_weighted(p["checks"], p["reference"]) == 1.0:
            return p
    raise RuntimeError(f"sceneladder: no valid draw for {case_id} R{level}")


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
                 "moves": p["seq"], "K": p["K"], "D": p["D"],
                 "prompt_tokens_est": round((len(p["system"]) + sum(len(x) for x in p["turns"])) / 4),
                 "reference_tokens_est": round(sum(len(x) for x in p["reference"]) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_plan(gen, release_seed, case_id)["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_weighted(materialized["checks"], split_turns(answer))


# ------------------------------------------------------------ mutants
MUTANT_KINDS = ("deny-all", "tell-all", "forgets-everything", "echo-the-user", "no-update")


def _mutant(p: dict, kind: str) -> str:
    out = []
    for t, b in enumerate(p["beats"], 1):
        k = b["kind"]
        good = _correct(p, t)
        if kind == "echo-the-user":
            out.append(b["text"])
        elif kind in ("deny-all", "tell-all") and k in ("neg", "pos"):
            out.append(_say(p, t, kind == "tell-all"))
        elif kind == "forgets-everything":
            if k == "look":
                out.append("You look around the room. It is quiet and warm.")
            elif k in ("neg", "pos"):
                out.append(f"{b['npc']} shrugs.\n{b['npc']}: I do not know.")
            elif k in ("self", "attrib", "now"):
                out.append("You cannot recall anything of the kind.")
            else:
                out.append("Noted.")
        elif kind == "no-update" and k in ("pos", "now"):    # everyone still holds the first value
            A = p["scenes"][b["about"]]
            raw = A["secret"]["raw"]
            out.append(f"It is {raw}." if k == "now" else f"{b['npc']} leans in.\n{b['npc']}: Yes. It is {raw}.")
        else:
            out.append(good)
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
