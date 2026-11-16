"""LRVX generator (4.5 Core chat candidate): LRV1 "pilots' mess" hardened and generated.

LRV1 (retired 4.1 ladder roleplay, 33 checks, 13 turns) was the closest chat item to a Core keeper (Fable 0.82, rho 0.79)
but its separation sat in judged elements and per-session voice checks. CORE-TRIAGE.md prescribed: drop the length /
passive-style checks and score voice, secret-keeping and presence / knowledge facts ALL-OR-NOTHING per character per
turn, programmatically wherever possible. This family does that, with 0 judge calls.

variants
  classic  the LRV1 case itself (card + 13 user turns from archive/4.1.0), regraded: every (character, turn) is one
           all-or-nothing voice unit, the secrets / corrections / callbacks become token checks, W1 length is gone.
  scene    seeded multi-character scene scaled by level: N characters, each with k stacked voice rules from a pool,
           secrets that open on a password phrase (and a decoy near miss), who-knows-what bookkeeping (told while
           someone was out, later stale), a liar with a tell, false beliefs, buried false user claims, callbacks.

Scoring units (each one engine check, so the engine score == generator grade):
  VOICE-<char>-t<n>  the character spoke (or, when out of the room, said nothing) and EVERY line obeyed EVERY rule
  FORM-t<n>          no user line, no puppeting, no untagged speech
  fact checks        SEC / KNOW / BEL / LIE / FC / CB, tied to one seeded token each
``weights`` (see WEIGHTS) split the score between voice units and fact checks. Units carry ``fact`` ids for per-fact
rescoring from stored transcripts (tools in tests / probe scripts)."""
from __future__ import annotations

import copy
import functools
import pathlib
import re
import sys

from .. import gen as _gen
from ._stack_common import TURN_SEP, grade_weighted, join_turns, rng, split_turns
from .attend import _num_alt
from .ensemble import _SUBST, _chk

FAMILY = "lrvx"
VARIANTS = ("scene", "classic")

# N chars, r rules per char, S secrets, K who-knows events (U of them get a later update), B false beliefs,
# lie (liar asked this many times), C buried false claims, cb callbacks, fill extra carrier turns
LEVELS = {
    "scene": {
        1: {"N": 3, "r": 2, "hard": 0, "S": 1, "K": 1, "U": 0, "B": 0, "lie": 0, "C": 1, "cb": 0, "fill": 0, "hint": True},
        2: {"N": 4, "r": 3, "hard": 0, "S": 1, "K": 2, "U": 0, "B": 1, "lie": 2, "C": 2, "cb": 0, "fill": 0, "hint": False},
        3: {"N": 5, "r": 3, "hard": 0, "S": 2, "K": 2, "U": 1, "B": 1, "lie": 2, "C": 2, "cb": 2, "fill": 0, "hint": False},
        4: {"N": 5, "r": 4, "hard": 0, "S": 2, "K": 3, "U": 1, "B": 2, "lie": 2, "C": 3, "cb": 2, "fill": 0, "hint": False},
        5: {"N": 6, "r": 4, "hard": 0, "S": 2, "K": 3, "U": 2, "B": 2, "lie": 3, "C": 3, "cb": 2, "fill": 0, "hint": False},
        # P1 round 1: L5 (4 plain rules x 6 characters) = Haiku 0.95 / Fable 0.985, so the plain LRV1 rules do not
        # separate. Round 2 knob: persistent counting / letter rules (exact word count, even word count, no repeated
        # initial letter, one banned letter) stacked on the plain ones.
        6: {"N": 5, "r": 4, "hard": 1, "S": 2, "K": 2, "U": 1, "B": 1, "lie": 2, "C": 2, "cb": 4, "fill": 0, "hint": False},
        7: {"N": 6, "r": 5, "hard": 2, "S": 2, "K": 3, "U": 1, "B": 2, "lie": 2, "C": 3, "cb": 4, "fill": 0, "hint": False, "inv": False},
        # P1 round 2: L7 = Fable 0.92 (5 voice-unit slips of 84) -> more hard rules per character, a bigger cast
        8: {"N": 6, "r": 6, "hard": 3, "S": 2, "K": 3, "U": 2, "B": 2, "lie": 3, "C": 3, "cb": 4, "fill": 0, "hint": False, "inv": False},
        # P1 round 3: with the card naming who keeps the ink / wakes guests / holds the stock (``inv`` True) Fable does all four
        # callbacks (L9 p1: 0.944 vs Haiku 0.79). From L7 the card is lean, as LRV1's was: the household is only described
        # (the attentiveness has to be inferred), which is the regime where all five 4.1 frontier models missed CB1/CB3.
        9: {"N": 7, "r": 6, "hard": 4, "S": 3, "K": 3, "U": 2, "B": 2, "lie": 3, "C": 3, "cb": 4, "fill": 0, "hint": False, "inv": False},
    },
    "classic": {1: {}},
}
for _v, _rows in LEVELS.items():
    for _lv, _row in _rows.items():
        _row.setdefault("size_prompt", (0, 10 ** 6))
        _row.setdefault("size_checks", (0, 10 ** 6))
        _row.setdefault("answer_tokens", 0)

MAX_TOKENS = 6144
GRADE_IS_ENGINE = False

#: share of the total score: voice units vs fact checks (tuned from probes, see build/LRVX.md)
WEIGHTS = {"voice": 0.5, "fact": 1.0, "form": 0.15}

USERS = [("Wren", "she", "her"), ("Maren", "she", "her"), ("Corin", "he", "his"), ("Teo", "he", "his")]
NAMES = [("Ottoline", "Brae", "she"), ("Jory", "Penhale", "he"), ("Dilys", "Harrow", "she"), ("Pip", "Marlow", "he"),
         ("Barnaby", "Quill", "he"), ("Hester", "Vosper", "she"), ("Amos", "Treloar", "he"), ("Tamsin", "Kitto", "she"),
         ("Ezra", "Pascoe", "he"), ("Nan", "Rowe", "she"), ("Silas", "Carne", "he"), ("Bryony", "Tregear", "she")]
ROLES = ["harbour-master", "pilot", "lamp-keeper", "apprentice", "steward", "net-mender", "ferryman", "cook"]
OPENERS = ["Now then", "Mind", "Well", "Look", "Right", "Listen", "So", "See here"]
CONTAINS = ["tide", "lamp", "rope", "salt", "coin", "weather", "harbour", "chart"]
FOODWORDS = ["bread", "soup", "pie", "tea", "stew", "cheese", "eggs", "loaf", "oven", "kettle", "supper", "broth", "butter",
             "flour", "salt", "pot", "pan", "cake", "ale"]
BASES = ["stove draws", "rain falls", "lamps burn", "wind drops", "night deepens", "kettle sings", "floor creaks",
         "door rattles", "fog thickens", "water laps"]
# rare nouns (a leak of one by accident is unlikely) and common ones (only asked FOR, or kept out of one mouth)
RARE = ["cistern", "belfry", "smokehouse", "coalhole", "boathouse", "chimney", "woodshed", "outhouse"]
COMMON = ["pantry", "attic", "loft", "locker", "alcove", "dresser", "bunker", "wardrobe", "larder",
          "cupboard", "stable", "laundry", "cowshed", "scullery"]
THINGS = ["the spare brass key", "the ferry money tin", "the signal lamp", "the old telescope", "the bosun's whistle",
          "the chart case", "the lantern glass", "the brass compass"]
SECRET_THINGS = ["the torn ledger page", "the unsigned repair order", "the log of the night shift", "the keeper's letter"]
PHRASES = [("salt on the sill, light in the glass", "salt on the glass, light in the sill"),
           ("the tide keeps no secrets", "the tide keeps no promises"),
           ("one bell for the living, two for the sea", "two bells for the living, one for the sea"),
           ("mend the net before the weather", "mend the weather before the net"),
           ("lamp first, then the lock", "lock first, then the lamp"),
           ("oil the hinge, spare the key", "oil the key, spare the hinge")]
TELLS = [("wipes {p} hands down {p} apron", r"\bapron\b"),
         ("straightens the salt cellar", r"\bsalt[- ]?(?:cellar|pot|shaker|dish)\b|\b(?:straighten|align|nudg|square|adjust|fidget)\w*[^.\n]{0,40}\bsalt\b"),
         ("taps the table twice with one finger", r"\b(?:tap|drum|rap)\w*\b|\bfinger\w*[^.\n]{0,60}\b(?:twice|two)\b|\b(?:twice|two)\b[^.\n]{0,40}\bfinger")]
TRAINS = [("six", "four"), ("seven", "five"), ("eight", "six")]
GIFTS = [("a printed tide table", "tide[- ]tables?", "printed tide tables"), ("a carved model boat", "model boats?", "carved model boats"),
         ("a brass pocket compass", "compass(?:es)?", "brass pocket compasses")]
KIDS = ["Ife", "Rosa", "Kit", "Bram", "Sami", "Otto", "Lark", "Juno"]
RELS = [("daughter", "she"), ("son", "he"), ("niece", "she"), ("nephew", "he")]
TOWNS = ["Port Marrow", "Skerrow", "Penhallow", "Durrow Quay", "Mallam Haven"]
PLACES = [("the Pilots' Mess", "pilot cutter"), ("the Lifeboat House", "lifeboat"), ("the Ferry Office", "ferry")]
BOATS = ["Gannet", "Kittiwake", "Shearwater", "Petrel", "Cormorant"]
SHIPS = ["Heron", "Curlew", "Dunlin", "Merlin", "Plover"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
COLOURS = ["black", "green", "blue", "red", "yellow"]
FILLER = ["{X}, how long have you known this place?", "{X}, what was the weather like that night?",
          "{X}, who else comes in here of an evening?", "{X}, what is the hardest part of the work here?",
          "{X}, how do you take your tea?", "{X}, when does the last boat come in?"]
HINT = "Each character sets right anything the visitor gets wrong about their own record."

_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}


def _numword(n: int) -> str:
    return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")


class _Fail(Exception):
    pass


# ---------------------------------------------------------------- voice rules
EXCL = [{"digits", "numwords"}, {"opener", "noyesno"}, {"exactwords", "evenwords"}, {"exactwords", "maxwords"},
        {"exactwords", "onesent"}, {"exactwords", "noquestion"}, {"twocommas", "onesent"}, {"noletter", "contains"}, {"noletter", "foodword"}]


def _rule(kind: str, g, ctx: dict) -> dict:
    """One voice rule: ``card`` (wording for the card), ``viol`` (regex matching a violating line body), and the
    parameters the reference builder needs."""
    if kind == "opener":
        w = ctx["opener"]
        return {"kind": kind, "w": w, "card": f'opens every line with the words "{w},"',
                "viol": rf"^(?!\W*{re.escape(w)}\b)\S"}
    if kind == "contains":
        w = ctx["contains"]
        return {"kind": kind, "w": w, "card": f'puts the word "{w}" in every line',
                "viol": rf"^(?!.*\b{re.escape(w)}\b)\S"}
    if kind == "foodword":
        return {"kind": kind, "w": "bread", "card": "has a food or kitchen word in every line (bread, soup, pie, tea, stew, "
                "cheese, eggs, loaf, oven, kettle, supper, broth, butter, flour, salt, pot, pan, cake or ale)",
                "viol": rf"^(?!.*\b(?:{'|'.join(FOODWORDS)})\b)\S"}
    if kind == "noyesno":
        return {"kind": kind, "card": 'never says "yes", "no", "aye", "nay", "yeah" or "nope", not even inside "no one" '
                'or "no more"', "viol": r"\b(?:yes|no|nope|aye|nay|yeah|yep)\b"}
    if kind == "digits":
        return {"kind": kind, "card": "puts at least one number written as a digit in every line", "viol": r"^(?!.*\d)\S"}
    if kind == "numwords":
        return {"kind": kind, "card": "writes every number out in words and never uses a digit", "viol": r"\d"}
    if kind == "trail":
        return {"kind": kind, "card": 'ends every line with three dots ("...") instead of a full stop',
                "viol": r"^(?!.*(?:\.\.\.|…)\W*$)\S"}
    if kind == "nocontr":
        return {"kind": kind, "card": "never uses a contraction (says \"do not\", never \"don't\"; \"it is\", never \"it's\")",
                "viol": r"\b[A-Za-z]+'(?:t|re|ll|ve|d|m)\b|\b(?:it|that|there|here|he|she|what|who|where|let|how|everyone|nobody)'s\b"}
    if kind == "maxwords":
        n = ctx["maxwords"]
        return {"kind": kind, "n": n, "card": f"never says a sentence longer than {_numword(n) if n >= 20 else _ONES[n]} words",
                "viol": rf"(?:^|(?<=[.!?…])[ \t]+)(?:[^\s.!?…]+[ \t]+){{{n}}}[^\s.!?…]+"}
    if kind == "noquestion":
        return {"kind": kind, "card": "never asks a question; states things and lets the silence ask", "viol": r"\?"}
    if kind == "noI":
        return {"kind": kind, "card": 'never says "I", "me", "my", "mine" or "myself", and talks around the speaker instead',
                "viol": r"\b(?:I|me|my|mine|myself)\b|\bI'(?:m|ve|ll|d)\b"}
    if kind == "noname":
        f, s = ctx["noname"]
        return {"kind": kind, "card": f"never says the name {f} or {s}, and never speaks of {f} by name",
                "viol": rf"\b(?:{re.escape(f)}|{re.escape(s)})\b"}
    if kind == "present":
        return {"kind": kind, "card": 'speaks only in the present tense and never uses "was", "were", "had", "did" or "been"',
                "viol": r"\b(?:was|were|had|did|been|wasn't|weren't|hadn't|didn't)\b"}
    if kind == "onesent":
        return {"kind": kind, "card": "speaks each line as one single sentence, with no full stop, question mark or "
                "exclamation mark until the end of the line", "viol": r"[.!?…]+[\"”']?[ \t]+[\"“]?[A-Za-z]"}
    if kind == "exactwords":
        n = ctx["exactwords"]
        return {"kind": kind, "n": n, "card": f"speaks in lines of exactly {_ONES[n]} words, no more and no fewer",
                "viol": rf"^(?!(?:\S+[ \t]+){{{n - 1}}}\S+[ \t]*$)\S.*$"}
    if kind == "evenwords":
        return {"kind": kind, "card": "speaks in lines with an even number of words (two, four, six, and so on)",
                "viol": r"^\S+(?:[ \t]+\S+[ \t]+\S+)*[ \t]*$"}
    if kind == "distinctinit":
        return {"kind": kind, "card": "never starts two words in the same line with the same letter",
                "viol": r"\b(\w)\w*\b[^\n]*?\b\1\w*\b"}
    if kind == "noletter":
        x = ctx["noletter"]
        return {"kind": kind, "x": x, "card": f'never uses the letter "{x}" in anything said', "viol": re.escape(x)}
    if kind == "endword3":
        return {"kind": kind, "card": "ends every line with a word of exactly three letters",
                "viol": r"^(?!.*\b\w{3}\W*$)\S"}
    if kind == "twocommas":
        return {"kind": kind, "card": "uses exactly two commas in every line, no more and no fewer",
                "viol": r"^(?![^,\n]*,[^,\n]*,[^,\n]*$)\S.*$"}
    if kind == "noexclaim":
        return {"kind": kind, "card": "never raises a voice: no exclamation marks", "viol": r"!"}
    raise KeyError(kind)


HARD = ["exactwords", "evenwords", "distinctinit", "noletter", "endword3", "twocommas"]
KINDS = ["opener", "contains", "foodword", "noyesno", "digits", "numwords", "trail", "nocontr", "maxwords", "noquestion",
         "noI", "noname", "present", "onesent", "noexclaim"]


def _choose_rules(g, n: int, k: int, taken: dict, me: int, chars: list, hard: int = 0) -> list[dict]:
    for _ in range(300):
        pool = [h for h in HARD if not (h == "noletter" and len(taken.get("noletter", ())) >= 1)]
        kinds = g.sample(pool, hard) + g.sample(KINDS, k - hard)
        if any(len(set(kinds) & e) > 1 for e in EXCL):
            continue
        if "contains" in kinds and "foodword" in kinds:
            continue
        ctx = {}
        if "opener" in kinds:
            ctx["opener"] = g.choice([o for o in OPENERS if o not in taken.get("opener", set())
                                      and not ("noletter" in kinds and "o" in o.lower())])
        if "contains" in kinds:
            ctx["contains"] = g.choice([w for w in CONTAINS if w not in taken.get("contains", set())])
        if "maxwords" in kinds:
            ctx["maxwords"] = g.choice([7, 8])
        if "exactwords" in kinds:
            ctx["exactwords"] = g.choice([7, 8])
        if "noletter" in kinds:
            ctx["noletter"] = "o"
            taken.setdefault("noletter", set()).add(me)
        if "noname" in kinds:
            others = [c for i, c in enumerate(chars) if i != me]
            o = g.choice(others)
            ctx["noname"] = (o["first"], o["sur"])
        rules = [_rule(x, g, ctx) for x in kinds]
        for r in rules:
            if r["kind"] in ("opener", "contains"):
                taken.setdefault(r["kind"], set()).add(r["w"])
        return rules
    raise _Fail


THREE = ["tar", "net", "bay", "fog", "sea", "oar", "ice", "ash", "fin", "gap", "dew", "elm", "fir", "hay", "hut", "jam",
         "key", "log", "map", "mud", "nib", "owl", "pan", "pit", "rim", "rod", "rye", "sky", "sun", "tin", "tub", "wax", "web", "yew"]
FILL = ["stove", "rain", "lamps", "wind", "night", "kettle", "floor", "door", "fog", "water", "slow", "quiet", "grey", "cold",
        "soft", "dark", "tall", "low", "calm", "long", "bright", "thin", "deep", "warm", "still", "stairs", "chair", "mist",
        "slate", "pier", "gulls", "brine", "wick", "sail", "keel", "tar", "drift", "swell", "cliff", "reef", "bell", "chain",
        "wharf", "net", "crab", "shell", "gale", "spray", "cable", "mast", "hull", "deck", "plank", "lantern", "glass", "brass",
        "iron", "wet", "dry", "mild", "harsh", "sharp", "dull", "fresh", "stale", "pale", "dim", "rich", "lean", "plain",
        "rough", "smooth", "brisk", "tidy", "damp", "hazy", "windy", "clear", "dusky", "faint", "gentle", "heavy", "light",
        "narrow", "patient", "sturdy", "steady", "wary", "weary", "yard", "zinc", "quay", "jetty", "harbor", "ember", "ashes",
        "knot", "gust", "lull", "vane", "stern", "bow", "lines", "flags", "creek", "dunes", "marsh", "heath", "fern"]


def _ref_line(c: dict, extras: list[str], g) -> str:
    """A conforming line by rejection sampling: the required words (opener, tokens, the contains/food word, a digit) plus
    random filler, tried against the character's own voice pattern."""
    rules = c["rules"]
    front = [r["w"] + "," for r in rules if r["kind"] == "opener"]
    req = list(extras) + [r["w"] for r in rules if r["kind"] in ("contains", "foodword")]
    if any(r["kind"] == "digits" for r in rules):
        req.append(str(g.randrange(2, 9)))
    exact = next((r["n"] for r in rules if r["kind"] == "exactwords"), None)
    even = any(r["kind"] == "evenwords" for r in rules)
    end3 = any(r["kind"] == "endword3" for r in rules)
    commas = any(r["kind"] == "twocommas" for r in rules)
    end = "..." if any(r["kind"] == "trail" for r in rules) else "."
    pat = re.compile(voice_pattern(rules), re.I | re.M)
    for _ in range(600):
        base = len(front) + len(req) + (1 if end3 else 0)
        k = (exact - base) if exact else g.randrange(1, 3)
        if k < 0:
            raise _Fail
        if even and not exact and (base + k) % 2:
            k += 1
        rest = req + g.sample(FILL, k)
        g.shuffle(rest)
        words = front + rest + ([g.choice(THREE)] if end3 else [])
        if commas:
            need = 2 - sum(w.count(",") for w in words)
            slots = [i for i, w in enumerate(words[:-1]) if "," not in w]
            if need < 0 or len(slots) < need:
                continue
            for i in (g.sample(slots, need) if need else []):
                words[i] += ","
        line = " ".join(words) + end
        if pat.match(line):
            return line
    raise _Fail


def voice_pattern(rules: list[dict]) -> str:
    viol = "|".join(f"(?:{r['viol']})" for r in rules)
    return rf"\A(?=\S)(?![\s\S]*?(?:{viol}))"


# ---------------------------------------------------------------- scene plan
def _attempt(g, L: dict) -> dict:
    N = L["N"]
    user, pron, poss = g.choice(USERS)
    nm = g.sample(NAMES, N)
    chars = [{"first": f, "sur": s, "g": gd, "role": ROLES[i]} for i, (f, s, gd) in enumerate(nm)]
    taken: dict = {}
    for i, c in enumerate(chars):
        c["rules"] = _choose_rules(g, N, L["r"], taken, i, chars, L.get("hard", 0))
    town = g.choice(TOWNS)
    pl, boat_kind = g.choice(PLACES)
    boat = g.choice(BOATS)
    colour = g.choice(COLOURS)
    nouns_rare, nouns_common = g.sample(RARE, len(RARE)), g.sample(COMMON, len(COMMON))
    ids = list(range(N))
    first = lambda i: chars[i]["first"]  # noqa: E731

    # ---- events -> blocks of consecutive turns
    blocks = []
    pool = ids[:]
    g.shuffle(pool)
    holders = pool[:L["S"]]
    secrets = []
    phr = g.sample(PHRASES, L["S"])
    things = g.sample(SECRET_THINGS, L["S"])
    for h, (ph, decoy), th in zip(holders, phr, things):
        secrets.append({"h": h, "phrase": ph, "decoy": decoy, "thing": th, "place": nouns_rare.pop()})
        blocks.append(("sec", len(secrets) - 1, 2))
    know = []
    for k in range(L["K"]):
        x, y, z = g.sample(ids, 3) if N >= 3 else (0, 1, 2)
        ev = {"x": x, "y": y, "z": z, "item": THINGS[k], "v1": nouns_common.pop(), "v2": None}
        if k < L["U"]:
            ev["v2"] = nouns_common.pop()
        know.append(ev)
        blocks.append(("know", k, 2))
        if ev["v2"]:
            blocks.append(("upd", k, 2))
    liar = None
    if L["lie"]:
        li = g.choice(ids)
        tl = g.choice(TELLS)
        liar = {"i": li, "tell": tl, "lie": nouns_common.pop(), "truth": nouns_rare.pop(), "n": L["lie"]}
    beliefs = []
    for b in range(L["B"]):
        a, bb = g.sample(ids, 2)
        beliefs.append({"a": a, "b": bb, "item": THINGS[len(THINGS) - 1 - b], "wrong": nouns_common.pop(), "right": nouns_common.pop()})
    for f in range(L["fill"]):
        blocks.append(("fill", f, 1))
    middle = [b for b in blocks]
    g.shuffle(middle)
    # a secret's later turns and a know/upd pair must stay contiguous and in order: they are single blocks already;
    # an update must come after its know block
    order = []
    for b in middle:
        order.append(b)
    for ev_i, ev in enumerate(know):
        if ev["v2"]:
            ki = next(i for i, b in enumerate(order) if b[:2] == ("know", ev_i))
            ui = next(i for i, b in enumerate(order) if b[:2] == ("upd", ev_i))
            if ui < ki:
                order[ki], order[ui] = order[ui], order[ki]
    T = 2 + sum(b[2] for b in order)
    turns = {t: [] for t in range(1, T + 1)}
    absent = {t: set() for t in range(1, T + 1)}
    meta = {}
    pos = 2
    asked_first = None
    for kind, idx, ln in order:
        t0 = pos
        pos += ln
        meta.setdefault(kind, []).append((idx, t0))
    # ---- text for the blocks
    U = user
    for kind, idx, t0 in [(k, i, t) for k, v in meta.items() for i, t in v]:
        if kind == "sec":
            s = secrets[idx]
            H = first(s["h"])
            ln = 2
            s["turns"] = list(range(t0, t0 + ln))
            s["trig"] = t0 + ln - 1
            asker = [i for i in ids if i != s["h"]]
            ask_all = f"\"Somebody here knows what became of {s['thing']}. I would rather hear it from whoever it is.\""
            turns[t0].append(f"{U} lays a hand flat on the table and looks round the room. {ask_all} "
                             f"{pron.capitalize()} takes {H} aside to the far end of the room, where no one else can hear, and says low: "
                             f"\"{s['decoy'].capitalize()}.\" {pron.capitalize()} waits. \"Does that mean anything to you, {H}? "
                             f"Where is {s['thing']}?\"")
            turns[s["trig"]].append(f"{U} walks {H} to the far end of the room where no one else can hear, and says low: "
                                    f"\"{s['phrase'].capitalize()}.\" {pron.capitalize()} waits a moment. \"{H}, where is {s['thing']}?\"")
        elif kind == "know":
            ev = know[idx]
            X, Y = first(ev["x"]), first(ev["y"])
            ev["t0"], ev["t1"] = t0, t0 + 1
            absent[t0].add(ev["x"])
            turns[t0].append(f"\"{X}, would you fetch the kettle from the back kitchen? Take your time.\" {U} waits until {X} has "
                             f"gone out of the room, then leans in and says quietly to {Y}: \"While {X} is out: {ev['item']} is in "
                             f"the {ev['v1']}.\"")
            turns[t0 + 1].append(f"{X} comes back into the room. \"{X}, while you were out I told {Y} something. What was it, would "
                                 f"you say?\"")
        elif kind == "upd":
            ev = know[idx]
            Y, Z = first(ev["y"]), first(ev["z"])
            ev["u0"], ev["u1"] = t0, t0 + 1
            absent[t0].add(ev["y"])
            turns[t0].append(f"\"{Y}, would you fetch a pail of water? Take your time.\" {U} waits until {Y} has gone out of the "
                             f"room, then says quietly to {Z}: \"Change of plan while {Y} is out: {ev['item']} is now in the "
                             f"{ev['v2']}.\"")
            turns[t0 + 1].append(f"{Y} comes back with the pail. \"{Y}, where is {ev['item']}?\" {pron.capitalize()} turns. "
                                 f"\"And {Z}, where would you say {ev['item']} is?\"")
    # ---- opening and close
    turns[1].append(f"The door bangs shut behind {U}, an inspector of the Board of Harbours, who shakes out a {colour} umbrella and "
                    f"props it dripping against the wall by the oilskins. \"Evening, all. {U}, from the Board, about the {boat}, "
                    f"the {boat_kind} that struck the reef four nights ago.\" " + f"\"{first(g.choice(ids))}, you first: who is who here?\"")
    turns[T].append(f"{U} closes the notebook and pulls on {poss} coat. \"It is late. Thank you, all of you. I will find the stair.\"")
    # ---- false beliefs and the liar's questions ride in carrier turns (the right characters must be in the room)
    pres0 = {t: [i for i in ids if i not in absent[t]] for t in turns}
    trig = {s_["trig"] for s_ in secrets}

    def _pick(need, used, after=1):
        c = [t for t in range(after + 1, T) if t not in trig and t not in used and all(i in pres0[t] for i in need)]
        if not c:
            raise _Fail
        t = g.choice(c)
        used.add(t)
        return t
    for b in beliefs:
        b["t"] = _pick([b["a"], b["b"]], set())
        turns[b["t"]].append(f"\"{first(b['a'])}, where is {b['item']}? And {first(b['b'])}, do you know where it is?\"")
    used_ta = set()
    for ev in know:
        if not ev["v2"]:
            ev["ta"] = _pick([ev["y"]], used_ta, after=ev["t1"])
            turns[ev["ta"]].append(f"\"{first(ev['y'])}, remind me what I told you quietly earlier.\"")
    if liar:
        used = set()
        liar["turns"] = sorted(_pick([liar["i"]], used) for _ in range(liar["n"]))
        Lr = first(liar["i"])
        asks = [f"\"{Lr}, where were you at dusk on the night of the wreck? Humour me.\"",
                f"\"{Lr}, for the record, one last time: where were you at dusk that night?\"",
                f"\"{Lr}, just so I have it straight, where were you at dusk, and who saw you?\""]
        for n, t in enumerate(liar["turns"]):
            turns[t].append(asks[n])
    # ---- cb plant: dry pen
    cb_pen = None
    if L["cb"] >= 1:
        free = [t for t in range(2, T) if not any(t in (s["trig"],) for s in secrets)]
        free = [t for t in free if t + 1 <= T - 1]
        if not free:
            raise _Fail
        cb_pen = g.choice(free)
        turns[cb_pen].append(f"{U} uncaps a pen; it has run nearly dry, so {pron} shakes it and writes harder.")
    cb_train = cb_gift = None
    if L["cb"] >= 4:
        hr, wk = g.choice(TRAINS)
        gift, gpat, gplural = g.choice(GIFTS)
        kid, (rel, _) = g.choice(KIDS), g.choice(RELS)
        age = g.choice(["eight", "nine", "ten"])
        tt, gt = g.sample([t for t in range(2, T) if t not in trig], 2)
        turns[tt].append(f"{U} glances at the clock. \"My train back is at {hr} in the morning, so I will want to be up by "
                         f"half past {wk}.\"")
        turns[gt].append(f"Almost to {'herself' if pron == 'she' else 'himself'}, {U} says: \"My {rel} {kid} is {age} and has asked me "
                         f"for {gift} for a birthday present. I have not found one anywhere.\"")
        cb_train = {"t": tt, "wk": wk, "hr": hr, "carrier": g.choice(ids)}
        cb_gift = {"t": gt, "kid": kid, "pat": gpat, "plural": gplural, "carrier": g.choice(ids), "rel": rel}
    cb_ink = g.choice(ids) if L["cb"] >= 2 else None
    # ---- buried false claims
    pres = {t: [i for i in ids if i not in absent[t]] for t in turns}
    claims = []
    kinds = g.sample(["years", "steps", "ship", "day"], min(L["C"], 4))
    free = [t for t in range(2, T) if t not in {s["trig"] for s in secrets}]
    g.shuffle(free)
    used_owner = set()
    for kd in kinds:
        if not free:
            raise _Fail
        t = free.pop()
        cand = [i for i in pres[t] if i not in used_owner]
        if not cand:
            raise _Fail
        o = g.choice(cand)
        used_owner.add(o)
        O = first(o)
        if kd == "years":
            y = g.randrange(29, 44)
            wy = y + g.choice([-1, 1]) * g.choice([6, 8, 9])
            cl = {"k": kd, "o": o, "truth_pat": _num_alt(y), "truth": _numword(y), "card": f"{O} has held the {chars[o]['role']}'s post for {_numword(y)} years",
                  "say": f"I understand you have held the post {_numword(wy)} years, {O}, so you will know better than anyone."}
        elif kd == "steps":
            s_ = g.choice([52, 58, 64, 68, 72, 77])
            ws = s_ + g.choice([-1, 1]) * g.choice([12, 16, 22])
            cl = {"k": kd, "o": o, "truth_pat": _num_alt(s_), "truth": _numword(s_), "card": f"{O} counts the steps up to the lamp room every day, and there are {_numword(s_)} of them",
                  "say": f"With the climb to the lamp room at {_numword(ws)} steps, {O}, I suppose nobody runs up it twice a night."}
        elif kd == "ship":
            sh, ws_ = g.sample(SHIPS, 2)
            cl = {"k": kd, "o": o, "truth_pat": rf"\b{sh}\b", "truth": sh, "card": f"{O}'s brother was drowned years ago aboard the {sh}",
                  "say": f"And I know you lost your brother on the {ws_}, {O}, long before my time."}
        else:
            d, wd = g.sample(DAYS, 2)
            cl = {"k": kd, "o": o, "truth_pat": rf"\b{d}\b", "truth": d, "card": f"{O} keeps the log, and the {boat} struck on {d} night",
                  "say": f"Since it all happened on {wd} night, {O}, I will take the evening from dusk."}
        cl["t"] = t
        claims.append(cl)
        turns[t].append(f"\"{cl['say']}\"")
    # a question to someone in every turn that has none yet
    for t in range(2, T):
        if not turns[t]:
            turns[t].append("\"" + g.choice(FILLER).format(X=first(g.choice(pres[t]))) + "\"")
    text = [" ".join(turns[t]) for t in range(1, T + 1)]
    return {"user": user, "pron": pron, "poss": poss, "chars": chars, "secrets": secrets, "know": know, "liar": liar,
            "beliefs": beliefs, "claims": claims, "cb_pen": cb_pen, "cb_train": cb_train, "cb_gift": cb_gift, "cb_ink": cb_ink, "T": T, "turns": text, "absent": absent,
            "town": town, "place": pl, "boat": boat, "boat_kind": boat_kind, "colour": colour,
            "umbrella": L["cb"] >= 1, "cb": L["cb"]}


def _system(p: dict, L: dict) -> str:
    u, chars = p["user"], p["chars"]
    names = ", ".join(c["first"] for c in chars[:-1]) + " and " + chars[-1]["first"]
    rules = [
        f"You are playing {len(chars)} characters at once, {names}, described in the scene card below, in an ongoing roleplay at one "
        f"table. The user plays {u}, an inspector of the Board of Harbours; {u} is the user's and no one else's.",
        "", "Rules (strict):",
        "1. Every line of speech is its own line and begins with the speaker's FIRST name in bold and a colon, exactly one of "
        + ", ".join(f"**{c['first']}:**" for c in chars) + " (never the surname). Narration goes on separate lines with no name "
        "tag, in present tense.",
        "2. Every character who is in the room speaks at least once in every turn. A character who has been sent out of the room stays "
        "out for the rest of that turn, says and does nothing, and comes back only when the user says so; they know only what was "
        "said while they were in the room. A quiet word to one person is "
        "heard by that person alone.",
        "3. Keep each character's voice and habits exactly as the card describes them, on every line and in every turn. The card is "
        "canon: who knows what, who speaks to whom, and how each of them behaves.",
        f"4. Never write {u}'s dialogue, actions, thoughts, feelings or decisions. Stop your turn at a point where {u} can act.",
        f"5. Things said and done on the page are canon too, including anything {u} has told one of the characters.",
        "6. A chat turn, not a chapter: keep each turn short. Stay inside the fiction; no notes to the user.",
    ]
    if L["hint"]:
        rules.append("7. " + HINT)
    card = ["", f"Scene card: {p['place']} at {p['town']}, a late evening. Four nights ago the {p['boat_kind']} {p['boat']} struck the reef "
            "after following the harbour light. Tonight the characters sit at one long table; a hatch leads to the back kitchen."]
    by_owner = {}
    for cl in p["claims"]:
        by_owner.setdefault(cl["o"], []).append(cl["card"])
    for i, c in enumerate(chars):
        voice = "; ".join(r["card"] for r in c["rules"])
        bits = [f"{c['first']} {c['sur']} ({c['role']}) {voice}."]
        for s in p["secrets"]:
            if s["h"] == i:
                bits.append(f"SECRET: {c['first']} hid {s['thing']} in the {s['place']}. Until someone says \"{s['phrase']}\" to "
                            f"{c['first']}, {c['first']} says nothing about {s['thing']} or where it is, and turns any question aside to "
                            f"the weather. If someone says \"{s['phrase']}\" to {c['first']}, {c['first']} tells them plainly where it is, "
                            "in their own voice. Nobody else knows.")
        lr = p["liar"]
        if lr and lr["i"] == i:
            pr = "his" if c["g"] == "he" else "her"
            tell = lr["tell"][0].format(p=pr)
            bits.append(f"{c['first']} LIES about the night of the wreck: asked where {c['first']} was at dusk, {c['first']} says "
                        f"{c['first']} was in the {lr['lie']} the whole evening and never left it (really {c['first']} was in the "
                        f"{lr['truth']}). {c['first']} tells the same lie every time, even when pressed, and every time {c['first']} "
                        f"lies {c['first']} {tell}, without knowing it.")
        for b in p["beliefs"]:
            if b["a"] == i:
                bits.append(f"{c['first']} believes {b['item']} is in the {b['wrong']}.")
            if b["b"] == i:
                bits.append(f"{c['first']} knows {b['item']} is really in the {b['right']}.")
        if p.get("cb_ink") == i and L.get("inv", True):
            bits.append(f"{c['first']} keeps a pot of ink and spare pens in the writing drawer.")
        if p.get("cb_train") and p["cb_train"]["carrier"] == i and L.get("inv", True):
            bits.append(f"{c['first']} is the night porter and wakes any guest who has an early train, with a knock at the time asked.")
        if p.get("cb_gift") and p["cb_gift"]["carrier"] == i and L.get("inv", True):
            bits.append(f"{c['first']} keeps a stack of {p['cb_gift']['plural']} in the back room.")
        for t in by_owner.get(i, []):
            bits.append(t[0].upper() + t[1:] + ".")
        card.append("- " + " ".join(bits))
    return "\n".join(rules + card) + "\n"


def _checks(p: dict, L: dict) -> list[dict]:
    chars, T, user = p["chars"], p["T"], p["user"]
    out = []
    # form per turn
    for t in range(1, T + 1):
        pat = rf"\A{_SUBST}(?![\s\S]*\b{re.escape(user)}(?:'s)?(?:\s+\w+ly)?\s+(?:says|said|nods|nodded|smiles|smiled|laughs|laughed|asks|asked|replies|replied|whispers|whispered|decides|decided|agrees|agreed|feels|felt|thinks|thought|sighs|sighed|shrugs|shrugged|gasps|gasped|walks|walked|reaches|reached|grabs|grabbed|takes|took|opens|opened)\b)(?![\s\S]*^[ \t]*(?:\*\*|__)?{re.escape(user)}(?:\*\*|__)?[ \t]*:)(?![\s\S]*^[ \t]*[\"“])"
        c = _chk(f"form-t{t}", [t], WEIGHTS["form"], pat, scope="ic", group="constraint")
        c["fact"] = f"FORM-t{t}"
        c["ftype"] = "format"
        out.append(c)
    # voice units
    for t in range(1, T + 1):
        for i, c in enumerate(chars):
            n = c["first"]
            pat = r"\A\Z" if i in p["absent"][t] else voice_pattern(c["rules"])
            ch = _chk(f"voice-{n}-t{t}", [t], WEIGHTS["voice"], pat, scope=f"speaker:{n}", group="constraint")
            ch.update(fact=f"VOICE-{n}-t{t}", ftype="voice")
            out.append(ch)
    W = WEIGHTS["fact"]

    def fact(cid, turns, pat, scope, w=1.0, any_turn=False):
        c = _chk(cid, turns, W * w, pat, scope=scope, group="continuity")
        if any_turn:
            c["per_turn"] = False
        c["fact"] = cid
        c["ftype"] = "fact"
        out.append(c)

    def has(tok):
        return rf"\b{tok}\b"

    for k, s in enumerate(p["secrets"]):
        H = chars[s["h"]]["first"]
        tok = has(s["place"])
        # never said by anyone, anywhere, before the phrase
        before = list(range(1, s["trig"]))
        fact(f"SEC{k + 1}-kept", before, rf"\A{_SUBST}(?![\s\S]*{tok})", "ic")
        fact(f"SEC{k + 1}-told", [s["trig"]], rf"\A[\s\S]*{tok}", f"speaker:{H}")
        # nobody but the holder says it at the trigger turn or after
        for i, c in enumerate(chars):
            if i == s["h"]:
                continue
            ts = [t for t in range(s["trig"], T + 1) if i not in p["absent"][t]]
            fact(f"SEC{k + 1}-not-{c['first']}", ts, rf"\A(?![\s\S]*{tok})", f"speaker:{c['first']}", w=0.25)
    for k, ev in enumerate(p["know"]):
        X, Y, Z = (chars[ev[x]]["first"] for x in "xyz")
        t1, v1, v2 = ev["t1"], has(ev["v1"]), has(ev["v2"]) if ev["v2"] else None
        fact(f"KNOW{k + 1}-out-of-earshot", [t1], rf"\A(?![\s\S]*{v1})", f"speaker:{X}")
        if not v2:
            fact(f"KNOW{k + 1}-told", [ev["ta"]], rf"\A[\s\S]*{v1}", f"speaker:{Y}")
        if v2:
            u1 = ev["u1"]
            fact(f"KNOW{k + 1}-stale", [u1], rf"\A(?=[\s\S]*{v1})(?![\s\S]*{v2})", f"speaker:{Y}")
            fact(f"KNOW{k + 1}-fresh", [u1], rf"\A(?=[\s\S]*{v2})(?![\s\S]*{v1})", f"speaker:{Z}")
    for k, b in enumerate(p["beliefs"]):
        A, B = chars[b["a"]]["first"], chars[b["b"]]["first"]
        fact(f"BEL{k + 1}-believes", [b["t"]], rf"\A(?=[\s\S]*{has(b['wrong'])})(?![\s\S]*{has(b['right'])})", f"speaker:{A}")
        fact(f"BEL{k + 1}-knows", [b["t"]], rf"\A[\s\S]*{has(b['right'])}", f"speaker:{B}")
    lr = p["liar"]
    if lr:
        Lr = chars[lr["i"]]["first"]
        for n, t in enumerate(lr["turns"], 1):
            fact(f"LIE{n}-same", [t], rf"\A(?=[\s\S]*{has(lr['lie'])})(?![\s\S]*{has(lr['truth'])})", f"speaker:{Lr}")
            fact(f"LIE{n}-tell", [t], lr["tell"][1], "ic", w=0.5)
    for k, cl in enumerate(p["claims"]):
        fact(f"FC{k + 1}-{cl['k']}", [cl["t"]], rf"\A[\s\S]*{cl['truth_pat']}", f"speaker:{chars[cl['o']]['first']}")
    if p["cb"]:
        fact("CB-umbrella", [T], r"\bumbrellas?\b", "ic", w=3.0 / W)
    if p.get("cb_train"):
        wk = p["cb_train"]["wk"]
        fact("CB-train", [T], rf"half past {wk}|\b{wk}[- ]thirty\b|\b(?:4|5|6):30\b|\b{wk}:30", "ic", w=3.0 / W)
    if p.get("cb_gift"):
        cg = p["cb_gift"]
        fact("CB-gift", [T], rf"\b{cg['kid']}\b|\b(?:{cg['pat']})\b", "ic", w=3.0 / W)
    if p["cb_pen"]:
        fact("CB-pen", [p["cb_pen"], p["cb_pen"] + 1], r"\b(?:ink|inkwell|pencil|nib|quill pens?|another pen|spare pen|new pen|fresh pen)\b", "ic", w=3.0 / W, any_turn=True)
    return out


def _extras(p: dict) -> dict:
    T = p["T"]
    extras = {t: {} for t in range(1, T + 1)}

    def add(t, i, tok):
        extras[t].setdefault(i, []).append(tok)

    for s in p["secrets"]:
        add(s["trig"], s["h"], s["place"])
    for ev in p["know"]:
        if not ev["v2"]:
            add(ev["ta"], ev["y"], ev["v1"])
        if ev["v2"]:
            add(ev["u1"], ev["y"], ev["v1"])
            add(ev["u1"], ev["z"], ev["v2"])
    for b in p["beliefs"]:
        add(b["t"], b["a"], b["wrong"])
        add(b["t"], b["b"], b["right"])
    lr = p["liar"]
    if lr:
        for t in lr["turns"]:
            add(t, lr["i"], lr["lie"])
    for cl in p["claims"]:
        add(cl["t"], cl["o"], cl["truth"])
    return extras


def _reference(p: dict, g) -> list[str]:
    chars, T = p["chars"], p["T"]
    extras = _extras(p)
    lr = p["liar"]
    out = []
    for t in range(1, T + 1):
        lines = ["The lamps hiss over the long table."]
        for i, c in enumerate(chars):
            if i in p["absent"][t]:
                continue
            lines.append(f"**{c['first']}:** " + _ref_line(c, extras[t].get(i, []), g))
        if lr and t in lr["turns"]:
            tl = lr["tell"][0].format(p="his")
            lines.append(f"{chars[lr['i']]['first']} {tl}.")
        if p["cb"] and t == T:
            lines.append(f"{chars[0]['first']} holds out the {p['colour']} umbrella from by the oilskins.")
        if p.get("cb_train") and t == T:
            lines.append(f"{chars[0]['first']} marks half past {p['cb_train']['wk']} in the knock-book.")
        if p.get("cb_gift") and t == T:
            lines.append(f"{chars[0]['first']} sets a stack for {p['cb_gift']['kid']} by the door.")
        if p["cb_pen"] and t == p["cb_pen"]:
            lines.append(f"{chars[0]['first']} sets a pot of ink by the notebook.")
        out.append("\n".join(lines))
    return out


def _mutants(p: dict, ref: list[str]) -> dict:
    """name -> (replies, check ids that must fail, and only those)."""
    out = {}
    chars = p["chars"]
    ex = _extras(p)
    T = p["T"]

    def quiet(i):
        """a turn where char i is present, has no token to say and is not the liar-tell carrier"""
        for t in range(2, T):
            if i not in p["absent"][t] and i not in ex[t]:
                return t
        return None
    # voice slip: break one rule of char 0 (or the first char with a quiet turn)
    for i, c in enumerate(chars):
        t = quiet(i)
        if t is None:
            continue
        rl = c["rules"][0]
        k = rl["kind"]
        bad = {"opener": "Honestly, the stove draws.", "contains": "stove draws.", "foodword": "stove draws.",
               "noyesno": "yes the stove draws.", "digits": "stove draws.", "numwords": "stove draws 4.",
               "trail": "stove draws.", "nocontr": "it's the stove.", "maxwords": " ".join(["stove"] * 12) + ".",
               "noquestion": "stove draws?", "noI": "I think the stove draws.", "present": "the stove was warm.",
               "onesent": "stove draws. Rain falls.", "noexclaim": "stove draws!", "exactwords": "stove draws.",
               "evenwords": "stove draws wind.", "distinctinit": "stove sail slow.", "noletter": "stove door.", "endword3": "stove draws.", "twocommas": "stove, draws."}
        if k == "noname":
            nm = re.search(r"never says the name (\w+)", rl["card"]).group(1)
            line = f"stove draws {nm}."
        else:
            line = bad[k]
        r = list(ref)
        r[t - 1] = re.sub(rf"^\*\*{c['first']}:\*\*.*$", lambda m: f"**{c['first']}:** {line}", r[t - 1], count=1, flags=re.M)
        out["voice-slip"] = (r, [f"voice-{c['first']}-t{t}"])
        # silent present character
        r = list(ref)
        r[t - 1] = re.sub(rf"^\*\*{c['first']}:\*\*.*\n?", "", r[t - 1], flags=re.M)
        out["silent"] = (r, [f"voice-{c['first']}-t{t}"])
        break
    # absent character speaks
    for ev in p["know"][:1]:
        r = list(ref)
        X = chars[ev["x"]]
        if ev["x"] not in ex[ev["t0"]]:
            r[ev["t0"] - 1] += f"\n**{X['first']}:** " + _ref_line(X, [], rng("m", "x"))
            out["absent-speaks"] = (r, [f"voice-{X['first']}-t{ev['t0']}"])
    # user puppeted
    r = list(ref)
    r[1] += f"\n{p['user']} nods and says it is fine."
    out["puppet"] = (r, ["form-t2"])
    # early leak of the first secret
    if p["secrets"]:
        s = p["secrets"][0]
        r = list(ref)
        r[0] += f"\nSomeone mutters about the {s['place']}."
        out["secret-early"] = (r, ["SEC1-kept"])
    # the told secret is not told
    if p["secrets"]:
        s = p["secrets"][0]
        r = list(ref)
        r[s["trig"] - 1] = r[s["trig"] - 1].replace(s["place"], "weather")
        out["secret-kept-back"] = (r, ["SEC1-told"])
    # the person who was out repeats what was said while out
    if p["know"]:
        ev = p["know"][0]
        X = chars[ev["x"]]
        r = list(ref)
        r[ev["t1"] - 1] = re.sub(rf"^(\*\*{X['first']}:\*\*.*?)(\.\.\.|\.)$", lambda m: m.group(1) + f" {ev['v1']}" + m.group(2), r[ev["t1"] - 1], count=1, flags=re.M)
        out["know-leak"] = (r, [f"KNOW1-out-of-earshot"])
    return out


@functools.lru_cache(maxsize=256)
def _plan_cached(level: int, key, release_seed: str, case_id: str) -> dict:
    L = LEVELS["scene"][level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, f"scene:{key or ''}")
    for attempt in range(3000):
        g = rng(seed, f"plan{attempt}")
        try:
            p = _attempt(g, L)
        except (_Fail, IndexError, ValueError):
            continue
        p.update(level=level, seed=seed, attempt=attempt, variant="scene")
        p["system"] = _system(p, L)
        p["checks"] = _checks(p, L)
        try:
            p["reference"] = _reference(p, rng(seed, f"ref{attempt}"))
        except _Fail:
            continue
        if grade_weighted(p["checks"], p["reference"]) == 1.0:
            return p
    raise RuntimeError(f"lrvx: no valid draw for {case_id} L{level}")


# ---------------------------------------------------------------- classic (LRV1 regraded)
_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CLASSIC_DIR = _ROOT / "archive" / "4.1.0" / "cases" / "rp-deep" / "LRV1-pilots-mess"
_CLASSIC_CHARS = ["Ottoline", "Jory", "Dilys", "Pip", "Barnaby"]
_C_OUT = {7: "Barnaby"}   # turn 7: Barnaby is in the scullery


def _classic_plan() -> dict:
    import yaml
    d = yaml.safe_load((_CLASSIC_DIR / "session.yaml").read_text(encoding="utf-8"))
    card = (_CLASSIC_DIR / "card.md").read_text(encoding="utf-8")
    rules = d["rules"]
    rules = re.sub(r"\n  ?6\. A chat turn, not a chapter: 180-360 words\.", "\n6. A chat turn, not a chapter. Stay inside the fiction; no notes to the user.", rules)
    rules = rules.replace("6. A chat turn, not a chapter: 180-360 words. Stay inside the fiction; no notes to the user.",
                          "6. A chat turn, not a chapter: keep each turn short. Stay inside the fiction; no notes to the user.")
    rules = rules.replace("2. All five characters speak at least once in every turn.",
                          "2. Every character who is in the room speaks at least once in every turn; a character who has left the "
                          "room (the steward, while he is out of the hall) says and does nothing until he is back.")
    system = rules.rstrip() + "\n\n" + card.strip() + "\n"
    T = len(d["turns"])
    vc = {}
    for c in d["checks"]:
        m = re.match(r"VC\d-(\w+)-voice", c["id"])
        if m:
            vc[m.group(1).capitalize()] = c["pattern"]
        # the per-rule checks carry the rest (figures in words, never says the other's name)
    extra = {}
    for c in d["checks"]:
        m = re.match(r"(?:V11|R1a|R1b|V10)-(\w+?)-", c["id"])
        if c["id"].startswith(("V11", "R1a", "R1b", "V10", "V15", "V13")) and c.get("scope", "").startswith("speaker:"):
            extra.setdefault(c["scope"].split(":")[1], []).append(c["pattern"])
    checks = []
    # LRV1's own weight mass, spread over units: the five VC checks were 2.0 each (10 in all) -> 2.0/T per (character, turn);
    # the F2 untagged-speech check was 0.5 -> 0.5/T per turn; the facts keep their 4.1 weights (callbacks CB1/3/4/5 2.0 each)
    W = {"voice": 2.0 / T, "form": 0.5 / T, "fact": 1.0}
    for t in range(1, T + 1):
        c = _chk(f"form-t{t}", [t], W["form"], rf"\A{_SUBST}(?![\s\S]*^[ \t]*[\"“])(?![\s\S]*^[ \t]*(?:\*\*|__)?Wren(?:\*\*|__)?[ \t]*:)", scope="ic", group="constraint")
        c["fact"] = f"FORM-t{t}"
        checks.append(c)
    puppet = {"id": "puppet", "fact": "puppet", "ftype": "identity", "group": "identity", "type": "no_puppeting", "names": ["Wren"],
              "weight": 0.5}
    checks.append(puppet)
    for t in range(1, T + 1):
        for n in _CLASSIC_CHARS:
            if _C_OUT.get(t) == n:
                pat = r"\A\Z"
            else:
                alts = [vc[n]] + extra.get(n, [])
                viol = "|".join(f"(?:{a})" for a in alts)
                pat = rf"\A(?=\S)(?![\s\S]*?(?:{viol}))"
            c = _chk(f"voice-{n}-t{t}", [t], W["voice"], pat, scope=f"speaker:{n}", group="constraint")
            c.update(fact=f"VOICE-{n}-t{t}", ftype="voice")
            checks.append(c)
    keep = {c["id"]: c for c in d["checks"] if c["id"] in ("X0-no-early-pip-leak", "X1-no-early-notice-14")}
    for cid, c in keep.items():
        cc = dict(c)
        cc["weight"] = W["fact"]
        cc["fact"] = cid
        checks.append(cc)

    def fact(cid, turns, pat, scope, w=1.0, any_turn=False):
        c = _chk(cid, turns, W["fact"] * w, pat, scope=scope, group="continuity")
        if any_turn:
            c["per_turn"] = False
        c["fact"] = cid
        checks.append(c)

    fact("C1-twenty-eight", [1], r"\A[\s\S]*\b(?:28|twenty[- ]eight)\b", "speaker:Ottoline", 2.0)
    fact("C2-fourteen", [2], r"\A[\s\S]*\b(?:14|fourteen)\b", "speaker:Jory", 1.0)
    fact("C3-eighty-steps", [7], r"\A[\s\S]*\b(?:80|eighty)\b", "speaker:Dilys", 0.5)
    fact("C4-heron", [10], r"\A[\s\S]*\bHeron\b", "speaker:Ottoline", 0.5)
    fact("C6-colza", [3], r"\A[\s\S]*\bcolza\b", "ic", 0.5)
    fact("C7-half-tide", [6], r"\A[\s\S]*\bhalf[- ]?tide\b", "ic", 2.0)
    fact("E1-thursday", [9], r"\A[\s\S]*\bThursday\b", "speaker:Pip", 0.25)
    fact("R1-pip-tells", [9], r"\A[\s\S]*\b(?:knock\w*|bent|bend|pin)\b", "speaker:Pip", 0.25)
    fact("R2-notice-14", [11], r"\A[\s\S]*\b(?:condemn\w*|roof)\b", "speaker:Ottoline", 0.25)
    fact("N2-pip-secret-stays", [10, 11, 12, 13], rf"\A(?![\s\S]*\b(?:bent (?:brass )?pin|knocked the (?:lamp|light))\b)", "speaker:Ottoline", 0.25)
    fact("L2-same-bread", [3, 12], r"\A[\s\S]*\b(?:bread|loaves|loaf|baking|baked|scullery)\b", "speaker:Barnaby", 0.25, any_turn=True)
    fact("CB1-umbrella", [12, 13], r"\bumbrellas?\b", "ic", 2.0, any_turn=True)
    fact("CB3-dry-pen", [5, 6], r"\b(?:ink|inkwell|pencil|nib|quill pens?|another pen|spare pen)\b", "ic", 2.0, any_turn=True)
    fact("CB4-early-train", [12, 13], r"\b(?:half past four|four[- ]thirty|4:30|train|six o'?clock|wake|knock)\b", "ic", 2.0, any_turn=True)
    fact("CB5-tide-table", [12, 13], r"\b(?:tide[- ]tables?|Ife)\b", "ic", 2.0, any_turn=True)
    return {"system": system, "turns": list(d["turns"]), "checks": checks, "user": "Wren", "T": T, "level": 1, "variant": "classic",
            "reference": _classic_reference()}


def _classic_reference() -> list[str]:
    """A hand-built answer that satisfies every classic check (a fixture for verify / tests; not a model reply)."""
    base = {"Ottoline": "The tide waits for nobody here.", "Jory": "Pilot saw the line.", "Dilys": "Mind, the stove draws well.",
            "Pip": "There are 3 chairs here...", "Barnaby": "Now then, the bread is warm and the kettle is on."}
    extra = {
        (1, "Ottoline"): "Twenty-eight years, the tide has turned that long.", (2, "Jory"): "Fourteen years. Not ten.",
        (7, "Dilys"): "Mind, eighty steps, and the knee counts them.", (9, "Pip"): "The 1 pin came out bent, a hard knock... Thursday...",
        (10, "Ottoline"): "The Heron, and the tide took it.", (11, "Ottoline"): "The bracket was condemned, the tide of that winter took the money into the roof.",
        (3, "Barnaby"): "Now then, nine loaves of bread, and the oven door stuck.", (12, "Barnaby"): "Now then, nine loaves of bread, as the oven door stuck."}
    narr = {3: "The colza lamp burns on the wall.", 5: "Pip sets a pot of ink by the pen.", 6: "The Gannet struck at half tide, says the log.",
            12: "Pip hands over the black umbrella, and a tide table for Ife, and half past four for the train."}
    out = []
    for t in range(1, 14):
        lines = [narr.get(t, "The lamps hiss over the long table.")]
        for n in _CLASSIC_CHARS:
            if _C_OUT.get(t) == n:
                continue
            lines.append(f"**{n}:** " + extra.get((t, n), base[n]))
        out.append("\n".join(lines))
    return out


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    variant = gen.get("variant") or "scene"
    if variant == "classic":
        return _classic_plan_cached()
    return copy.deepcopy(_plan_cached(int(gen.get("level", 1)), gen.get("key"), release_seed, case_id))


@functools.lru_cache(maxsize=1)
def _classic_plan_cached() -> dict:
    return _classic_plan()


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    p = _plan(gen, release_seed, case_id)
    return {
        "id": case_id, "category": "rpdeep", "system": p["system"], "history": [], "turns": p["turns"],
        "temperature": 0.8, "top_p": 0.95, "max_tokens": MAX_TOKENS, "user_name": p["user"],
        "checks": p["checks"], "judge_elements": [], "programmatic": True, "generator": dict(gen),
        "meta": {"family": FAMILY, "variant": p["variant"], "level": p["level"], "n_turns": len(p["turns"]),
                 "n_checks": len(p["checks"]),
                 "prompt_tokens_est": round((len(p["system"]) + sum(len(x) for x in p["turns"])) / 4),
                 "reference_tokens_est": round(sum(len(x) for x in (p["reference"] or [])) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    p = _plan(gen, release_seed, case_id)
    return join_turns(p["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_weighted(materialized["checks"], split_turns(answer))


def mutants_named(gen: dict, *, release_seed: str, case_id: str) -> dict:
    p = _plan(gen, release_seed, case_id)
    if p["variant"] == "classic":
        return {}
    return {k: (join_turns(r), want) for k, (r, want) in _mutants(p, p["reference"]).items()}


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    out = [a for a, _ in mutants_named(gen, release_seed=release_seed, case_id=case_id).values()]
    out.append(join_turns(["Sure, noted."] * len(mat["turns"])))
    return [a for a in dict.fromkeys(out) if grade(a, mat) < 1.0]


def unit_breakdown(mat: dict, replies: list[str]) -> dict:
    """Per kind share of weight earned: voice / form / fact (+ per fact id) for analysis scripts."""
    from .. import session_checks
    run = session_checks.run_checks({"checks": mat["checks"]}, replies)
    res = {r["id"]: r["pass"] for r in run["results"]}
    kinds = {}
    for c in mat["checks"]:
        ft = c.get("ftype", "fact")
        k = "voice" if ft == "voice" else "form" if ft in ("format", "identity") else "fact"
        a = kinds.setdefault(k, [0.0, 0.0])
        a[1] += float(c["weight"])
        a[0] += float(c["weight"]) * bool(res.get(c["id"]))
    return {"kinds": kinds, "res": res}
