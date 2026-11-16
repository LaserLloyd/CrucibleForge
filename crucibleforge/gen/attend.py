"""ATTEND generator (gen2 chat, 4.5): inferred-rule roleplay -- the household acts on what the guest mentioned,
unasked, because the CARD gives each character a job (LRV1/LRV2's callback and card-inference checks,
which every 4.1 Panel A model failed, made deterministic).

One evening and the next morning at an inn. Three characters, always in the room, each with jobs on the card:
the door (sees every guest out with whatever they came in with), the kitchen (tonight's supper, and a pot kept
back for anyone who can't eat it), the night porter (knocks a fixed time before any train), the stationery
drawer, the shop counter, the roads, and the house's history. The guest mentions things in passing -- the
umbrella hung on the pegs, a food they can't eat, a train time (later moved), a dry pen, a child's birthday
present not yet found, the road they will walk back by while rain starts -- and later reaches the natural
moment (supper, turning in, writing, leaving) WITHOUT asking. Graded by the token the job implies, in that
reply: the umbrella, the kept-back dish, the knock time (train minus lead, current train), ink or a pencil,
the shop item or the child's name, the dry road, the true house fact after a misstatement. L1-L2 state the
attentiveness rule; from L3 it is only implied by the card (the LRV regime). 0 judge calls."""
from __future__ import annotations

import copy
import functools
import re
import sys

from .. import gen as _gen
from ._stack_common import TURN_SEP, grade_weighted, join_turns, rng, split_turns
from .ensemble import (NAMES, NARRATION, OPENERS, SHIPS, _SUBST, _TAGGED, _cast_check, _chk, _line, _time_alt,
                       _time_text)

FAMILY = "attend"
#: pin of attend_rules.py (the L7+ directive layer): chains.revision() digests only THIS file, so a change to the
#: sibling must change this line (tests/test_attend_hard.py::test_rules_pin fails until it does)
_RULES_SHA = "c952af0f2e5e305d"
#: pins of the shared modules this family builds checks from (ensemble.py tags/voices, _stack_common.py turn split), same reason
_ENSEMBLE_SHA = "b4d5f62fca26ff16"
_STACK_SHA = "d9ed8da25a93decf"
VARIANTS = ("host",)

LEVELS = {
    "host": {
        1: {"T": 7, "needs": ["coat", "diet", "wake"], "canon": 0, "hint": True},
        2: {"T": 10, "needs": ["coat", "diet", "wake", "ink", "gift"], "canon": 1, "hint": True},
        3: {"T": 10, "needs": ["coat", "diet", "wake", "ink", "gift"], "canon": 1, "hint": False},
        4: {"T": 12, "needs": ["coat", "diet", "wake", "ink", "gift", "route"], "canon": 2, "hint": False,
            "update": True, "ingredient": True},
        # knob: a decoy item on the pegs (the guest's colour must be named), needs buried mid-turn
        5: {"T": 13, "needs": ["coat", "diet", "wake", "ink", "gift", "route"], "canon": 2, "hint": False,
            "update": True, "ingredient": True, "decoy": True, "buried": True},
        # knob from the chat-hard drafts: HH4's persistent per-character voice rules (one rule each, scored
        # all-or-nothing per character per session, as LRV's VC checks) + HH3's mid-session user rule ("one line
        # each, a single sentence, no questions back") stacked on the needs, so two jobs must share one sentence
        6: {"T": 13, "needs": ["coat", "diet", "wake", "ink", "gift", "route"], "canon": 2, "hint": False,
            "update": True, "ingredient": True, "voices": True, "rule": True},
        # L7-L10 (EXP-20261114-05): the directive layer, see attend_rules.py. Per-turn checks only.
        7: {"rules": True, "T": 14, "gap": 2, "std": 3, "revoke": 1, "change": 0, "reinstate": 0, "cond": 1,
            "latch": 0, "crevoke": 0, "conflict": 0, "flip": 0, "distract": 0, "min_pos": 2, "max_pos": 7,
            "needs": [], "canon": 0, "hint": True},
        8: {"rules": True, "T": 16, "gap": 3, "std": 4, "revoke": 1, "change": 1, "reinstate": 0, "cond": 2,
            "latch": 0, "crevoke": 1, "conflict": 1, "flip": 0, "distract": 2, "exact": 1, "min_pos": 3, "max_pos": 10,
            "needs": [], "canon": 0, "hint": True},
        9: {"rules": True, "T": 20, "gap": 4, "std": 5, "revoke": 2, "change": 1, "reinstate": 0, "cond": 2,
            "latch": 1, "crevoke": 1, "conflict": 2, "flip": 0, "distract": 3, "exact": 2, "tally": 1,
            "ordinal": 0.5, "min_pos": 3, "max_pos": 12, "needs": [], "canon": 0, "hint": True},
        10: {"rules": True, "T": 26, "gap": 5, "std": 4, "revoke": 2, "change": 2, "reinstate": 1, "cond": 2,
             "latch": 2, "crevoke": 1, "conflict": 2, "flip": 1, "distract": 4, "exact": 3, "tally": 1, "par": 1,
             "ordinal": 0.7, "min_pos": 4, "max_pos": 14, "needs": [], "canon": 0, "hint": True},
        # L11: density, not length. T=22 (L10 is 26) but every turn carries more obligations: a trigger turn may
        # fire several rules at once ("share"), 3 exact-length rules, 2 tallies, odd/even speaker order, 2 latches,
        # 2 clashes both flipped, every ordinal scrap, wider declaration windows (see attend_rules.py)
        11: {"rules": True, "T": 22, "gap": 2, "std": 4, "revoke": 2, "change": 2, "reinstate": 1, "cond": 3,
             "latch": 2, "crevoke": 1, "conflict": 2, "flip": 2, "distract": 3, "exact": 2, "tally": 2, "par": 1,
             "ordinal": 1.0, "share": 0.7, "exact_ns": [8, 9], "cap_ns": [9, 10], "cap_min": 9, "nfire": 2, "xtopics": 1, "early": 12, "early_c": 12, "pair_hi": 14,
             "min_pos": 4, "max_pos": 16, "needs": [], "canon": 0, "hint": True},
        # L12 (H1 owner, 2026-11-14): INTERACTION, not density or counting. L11 without exact line lengths, one latch
        # fewer, and a last-word rule with an exception keyed on who speaks first in the same reply ("endx"), so the
        # odd/even digit rule, the first-speaker clash and the model's own choice of opener decide whether a line
        # must or must not end with the word; the exception may be rewritten mid-session (change).
        12: {"rules": True, "T": 22, "gap": 2, "std": 4, "revoke": 2, "change": 2, "reinstate": 1, "cond": 3,
             "latch": 1, "endx": 1, "crevoke": 1, "conflict": 2, "flip": 2, "distract": 3, "exact": 0, "tally": 2, "par": 1,
             "ordinal": 1.0, "share": 0.7, "cap_ns": [9, 10], "cap_min": 9, "nfire": 3, "xtopics": 1, "early": 12,
             "early_c": 12, "pair_hi": 14, "min_pos": 4, "max_pos": 16, "needs": [], "canon": 0, "hint": True},
    },
}
_SIZES = {"host": {1: ((470, 520), (10, 10), 389), 2: ((590, 655), (16, 16), 552), 3: ((560, 615), (16, 16), 554),
                   4: ((675, 730), (20, 20), 677), 5: ((705, 765), (21, 21), 737), 6: ((740, 805), (27, 36), 597),
                   7: ((500, 570), (48, 68), 540), 8: ((780, 870), (115, 135), 700),
                   9: ((1070, 1250), (215, 250), 930), 10: ((1360, 1560), (320, 350), 1300), 11: ((1420, 1620), (235, 305), 1080),
                   12: ((1300, 1700), (200, 340), 1080)}}
for _v, _rows in LEVELS.items():
    for _lv, _row in _rows.items():
        _row["size_prompt"], _row["size_checks"], _row["answer_tokens"] = _SIZES[_v][_lv]

MAX_TOKENS = 6144
USERS = ["Wren", "Corin", "Maren", "Teo", "Lio", "Sunny"]
INNS = ["the Lamb at Skelby", "the Crown at Wyke", "the Ferry House at Durrow", "the Plough at Tarrant",
        "the Bell at Mallam"]
ITEMS = ["umbrella", "walking stick", "scarf", "hat"]
COLOURS = ["green", "blue", "red", "yellow", "grey", "brown"]
MAINS = [("fish pie", "fish", ["haddock", "leeks", "cream"]), ("mushroom tart", "mushrooms", ["mushrooms", "shallots", "butter"]),
         ("pork and apple pie", "pork", ["pork", "apple", "sage"]), ("cheese and onion pasty", "cheese", ["cheese", "onion", "lard"]),
         ("egg and bacon flan", "eggs", ["eggs", "bacon", "chives"])]
ALTS = ["barley broth", "mutton stew", "lentil soup", "bean hotpot", "potato cakes", "pea and ham soup"]
LEADS = [(60, "an hour"), (90, "an hour and a half"), (120, "two hours")]
GIFTS = [("kite", "kites"), ("spinning top", "spinning tops"), ("tide table", "tide tables"), ("compass", "pocket compasses"),
         ("sketchbook", "sketchbooks"), ("harmonica", "harmonicas")]
OTHER_GOODS = ["postcards", "bootlaces", "matches", "barley sugar", "maps of the coast", "candles"]
KIDS = ["Ife", "Rosa", "Kit", "Bram", "Pia", "Otto", "Juno", "Sami"]
RELS = ["daughter", "son", "niece", "nephew"]
LOWS = ["river path", "marsh road", "ford lane", "shore track"]
HIGHS = ["chapel hill", "quarry lane", "top road", "church steps"]
NOTES = ["the times of the morning carts", "the name of the ferryman", "the way to the old mill", "what the doctor charges"]
FILLER = ["What do people round here do of an evening?", "Is it always this quiet midweek?",
          "Who usually drinks here on a weeknight?", "How long have you all worked here?",
          "What's the best thing to see in the village?", "Does the wind always come off the water like this?"]
ARRIVE = "I come in out of the evening, shake out my {c} {i} and hang it on the pegs by the door. A room for the night, please."
DIET = ["Before anyone goes to trouble, {ing} and I have never got on, I'm afraid.",
        "I should say, I can't eat {ing}, never could."]
TRAIN = "My train out is the {t} tomorrow morning, so it'll be an early night."
UPDATE = "Oh, the station boy just brought word: my train's been moved to the {t}."
PEN = "I take out my notebook and shake my pen; it has run nearly dry."
NOTE = "I open my notebook to jot down {n}."
GIFT = "My {r} {k} turns {a} on Sunday and has asked for a {g}, and I still haven't found one."
ROUTE = "Tomorrow I'll walk back to the station by the {low}, the way I came."
RAIN = "Rain starts drumming on the windows."
SUPPER = "Is there any supper going? I'm famished."
TURNIN = "Well, I'll turn in. Goodnight, all."
LEAVE = "Morning, all. I'll settle up and be on my way."
RULE = "From now on keep it short: one line each, a single sentence, and no questions back to me."
CALM = ["the fire is drawing well tonight.", "the wind has dropped a little.", "the lamps want trimming soon.",
        "the floor is wet by the door.", "the kettle is near the boil.", "the yard is quiet now.",
        "the last cart went by an hour ago.", "the shutters are rattling again."]
# HH4-style voice rules: card wording, and a regex for a VIOLATING line body (after the name tag)
VRULES = {
    "dear": ("says 'dear' in every line", r"(?![^\n]*\bdear\b)[^\n]*\S"),
    "nothat": ("never says the word 'that'", r"[^\n]*\bthat\b"),
    "short": ("never speaks more than twenty words in one line", r"(?:\S+[ \t]+){20}\S"),
    "nocontr": ("never uses a contraction (says 'do not', never 'don't')",
                r"[^\n]*\b\w+(?:n['\u2019]t|['\u2019](?:ll|re|ve|m|d))\b"),
    "noI": ("never says 'I' and talks around it", r"[^\n]*\bI\b"),
}
CANON = {
    "year": {"card": "The inn has stood since {v}.", "wrong": "Since the place has stood here since {w}, you'll have seen a few storms."},
    "bell": {"card": "The bell in the yard came off the ship {v}.", "wrong": "That bell in the yard, the one off the {w}, has it a good ring?"},
    "well": {"card": "The well in the yard is {v} feet deep.", "wrong": "I suppose that well of yours, {w} feet deep, never runs dry?"},
}


def _alt(words) -> str:
    return r"\b(?:" + "|".join(r"[\W_]+".join(re.escape(w) for w in x.split()) for x in words) + r")\b"


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_TENS = {10: "ten", 20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty",
         90: "ninety"}
_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]


def _num_alt(n: int) -> str:
    """10..99 as digits or words ("fifty-six", "fifty six")."""
    t, o = n - n % 10, n % 10
    w = _TEENS[o] if t == 10 else _TENS[t] + (f"[- ]{_ONES[o]}" if o else "")
    return rf"\b(?:{n}|{w})\b"


def _minus(h: int, m: int, mins: int) -> tuple[int, int]:
    t = h * 60 + m - mins
    return t // 60, t % 60


class _Fail(Exception):
    pass


def _attempt(g, L: dict) -> dict:
    T = L["T"]
    user = g.choice(USERS)
    npcs = g.sample(NAMES, 3)
    openers = dict(zip(npcs, g.sample(OPENERS, 3)))
    door, cook, porter = npcs
    k = {}
    k["item"], k["colour"] = g.choice(ITEMS), g.choice(COLOURS)
    k["decoy"] = g.choice([c for c in COLOURS if c != k["colour"]]) if L.get("decoy") else None
    main, main_ing, ings = g.choice(MAINS)
    k["main"], k["alt"] = main, g.choice(ALTS)
    k["avoid"] = g.choice([i for i in ings if i not in main]) if L.get("ingredient") else main_ing
    k["ings"] = ings
    k["lead"], k["lead_text"] = g.choice(LEADS)
    h = g.choice([6, 7, 8])
    m = g.choice([10, 20, 40, 50])
    k["train"] = (h, m)
    if L.get("update"):
        h2, m2 = h + g.choice([1, -1]) if h > 6 else h + 1, g.choice([x for x in (10, 20, 40, 50) if x != m])
        k["train2"] = (h2, m2)
    k["gift"], k["goods"] = g.choice(GIFTS)
    k["kid"], k["rel"], k["age"] = g.choice(KIDS), g.choice(RELS), g.choice(["six", "seven", "eight", "nine", "ten"])
    k["low"], k["high"] = g.choice(LOWS), g.choice(HIGHS)
    k["note"] = g.choice(NOTES)
    canon = g.sample(sorted(CANON), L["canon"])
    cv = {}
    for c in canon:
        if c == "year":
            v = g.randrange(1702, 1790)
            cv[c] = (str(v), str(v + g.choice([-1, 1]) * g.randrange(12, 40)))
        elif c == "bell":
            a, b = g.sample(SHIPS, 2)
            cv[c] = (a, b)
        else:
            v = g.choice([42, 56, 64, 78, 86])
            cv[c] = (str(v), str(v + g.choice([-1, 1]) * g.choice([11, 15, 23])))
    k["canon"] = cv

    # --- schedule: turn 1 arrive; T-1 turn in; T leave; supper and the note moment in between
    needs = L["needs"]
    slots = {t: [] for t in range(1, T + 1)}
    events = []
    slots[1].append(("text", ARRIVE.format(c=k["colour"], i=k["item"])))
    mid = list(range(2, T - 1))          # 2 .. T-2
    # supper moment at least 2 after the diet need
    d_need = g.choice([t for t in mid if t + 2 <= T - 2])
    supper = g.choice([t for t in mid if t >= d_need + 2])
    slots[d_need].append(("text", g.choice(DIET).format(ing=k["avoid"])))
    slots[supper].append(("text", SUPPER))
    events.append({"kind": "diet", "turn": supper, "need": d_need})
    free = [t for t in mid if t not in (d_need, supper)]
    tr = g.choice([t for t in free if t <= T - 3] or free)
    slots[tr].append(("text", TRAIN.format(t=_time_text(*k["train"]))))
    if L.get("update"):
        up = [t for t in mid if tr < t]
        if not up:
            raise _Fail
        tu = g.choice(up)
        slots[tu].append(("text", UPDATE.format(t=_time_text(*k["train2"]))))
    events.append({"kind": "wake", "turn": T - 1, "need": tu if L.get("update") else tr})
    if "ink" in needs:
        pn = [t for t in mid if t + 2 <= T - 2 and t not in (supper,)]
        if not pn:
            raise _Fail
        tp = g.choice(pn)
        tn = g.choice([t for t in mid if t >= tp + 2 and t != supper] or [None])
        if tn is None:
            raise _Fail
        slots[tp].append(("text", PEN))
        slots[tn].append(("text", NOTE.format(n=k["note"])))
        events.append({"kind": "ink", "turn": tn, "need": tp})
    if "gift" in needs:
        tg = g.choice(mid)
        slots[tg].append(("text", GIFT.format(r=k["rel"], k=k["kid"], a=k["age"], g=k["gift"])))
        events.append({"kind": "gift", "turn": T, "need": tg})
    if "route" in needs:
        tr2 = g.choice(mid)
        trn = g.choice([t for t in range(max(tr2, 2), T)])
        slots[tr2].append(("text", ROUTE.format(low=k["low"])))
        slots[trn].append(("narr", RAIN))
        events.append({"kind": "route", "turn": T, "need": tr2})
    events.append({"kind": "coat", "turn": T, "need": 1})
    for c in canon:
        tc = g.choice([t for t in mid if t not in (supper,)])
        slots[tc].append(("text", CANON[c]["wrong"].format(w=cv[c][1])))
        events.append({"kind": f"canon-{c}", "turn": tc, "need": tc})
    slots[T - 1].append(("text", TURNIN))
    slots[T].append(("text", LEAVE))
    vr, rule_turn = {}, None
    if L.get("voices"):
        vr = dict(zip(npcs, g.sample(sorted(VRULES), 3)))
    if L.get("rule"):
        cands = [t for t in range(3, min(supper, T - 1))]
        if not cands:
            raise _Fail
        rule_turn = g.choice(cands)
        slots[rule_turn].insert(0, ("text", RULE))
    if any(len([x for x in slots[t] if x[0] == "text"]) > (4 if L.get("rule") else 3) for t in slots):
        raise _Fail
    turns = []
    for t in range(1, T + 1):
        parts = [x[1] for x in slots[t] if x[0] == "narr"] + [x[1] for x in slots[t] if x[0] == "text"]
        if t not in (1, T - 1, T) and not any(x[0] == "text" and x[1] in (SUPPER,) for x in slots[t]):
            f = g.choice(FILLER)
            q = f"{g.choice(npcs)}, {f[0].lower()}{f[1:]}"
            if L.get("buried") and len(parts) >= 1:
                parts.insert(0, q)
            else:
                parts.append(q)
        turns.append(" ".join(parts))
    # a need's moment must not be in the same turn as the need (the gap is the test)
    for e in events:
        if not e["kind"].startswith("canon") and e["turn"] <= e["need"]:
            raise _Fail
    return {"user": user, "npcs": npcs, "openers": openers, "door": door, "cook": cook, "porter": porter,
            "k": k, "turns": turns, "events": events, "canon_keys": canon, "inn": g.choice(INNS),
            "goods": g.sample(OTHER_GOODS, 2), "vrules": vr, "rule_turn": rule_turn}


def _system(p: dict, L: dict) -> str:
    k, user = p["k"], p["user"]
    door, cook, porter = p["door"], p["cook"], p["porter"]
    names = ", ".join(p["npcs"][:-1]) + " and " + p["npcs"][-1]
    words = "; ".join(f"{n}: \"{w}\"" for n, w in p["openers"].items())
    goods = f"{p['goods'][0]}, {k['goods']} and {p['goods'][1]}"
    lines = [
        f"You play every character in an interactive scene except {user}, the guest, who is the user's.",
        "Rules:",
        "1. Each spoken line is its own line in the form 'Name: text' (no quotation marks). Narration lines carry no name.",
        f"2. {names} are in the common room the whole time, and each of them speaks at least once in every reply.",
        f"3. Each character opens every line they speak with their own word ({words}).",
        f"4. Never write {user}'s words, actions, thoughts or choices; stop where {user} can act. Keep each reply to a short chat turn.",
        "5. The card is canon: who knows what, and how each of them behaves.",
    ]
    if L["hint"]:
        lines.append("6. The household looks after the guest the way their jobs on the card describe, without waiting "
                     "to be asked, and remembers what the guest mentions.")
    dish = f"{k['main']} (made with {', '.join(k['ings'][:-1])} and {k['ings'][-1]})" if L.get("ingredient") else k["main"]
    cant = "something in it" if L.get("ingredient") else k["avoid"]
    card = [
        "", f"Scene card: {p['inn']}, a wet autumn evening, then the next morning. {user} has come in for one night.",
        f"- {door} keeps the door and the coat pegs, and sees every guest out with whatever they came in with."
        + (f" A {k['decoy']} {k['item']} left by a carter has hung on the pegs all week." if k["decoy"] else "")
        + f" {door} also knows the roads: the {k['low']} floods whenever it rains, and the dry way round is by the {k['high']}.",
        f"- {cook} cooks. Tonight's supper is {dish}; {cook} keeps a pot of {k['alt']} back for anyone who can't eat {cant}."
        f" {cook} also minds the little shop counter, which sells {goods}.",
        f"- {porter} is the night porter and knocks on a guest's door {k['lead_text']} before any train they have to catch."
        f" {porter} also keeps the stationery drawer.",
    ]
    for n, r in p.get("vrules", {}).items():
        card.append(f"- {n} {VRULES[r][0]}.")
    for c in p["canon_keys"]:
        card.append(f"- House history, which all three know: " + CANON[c]["card"].format(v=k["canon"][c][0]))
    return "\n".join(lines + card) + "\n"


def _keys(p: dict) -> dict:
    k = p["k"]
    tr = k.get("train2") or k["train"]
    knock = _minus(*tr, k["lead"])
    out = {"knock": knock}
    if k.get("train2"):
        out["stale"] = _minus(*k["train"], k["lead"])
    return out


def _checks(p: dict, L: dict) -> list[dict]:
    k = p["k"]
    voices = dict(p["openers"])
    out = [_cast_check(t, p["user"], {}, voices) for t in range(1, len(p["turns"]) + 1)]
    ks = _keys(p)
    for e in p["events"]:
        t, kind = e["turn"], e["kind"]
        if kind == "coat":
            item = rf"(?:{re.escape(k['item'])}|{re.escape(k['item'].split()[-1])})s?\b"   # "your stick" (R0, P1 r4)
            pat = rf"\b{k['colour']}\b[^\n]{{0,40}}\b{item}" if k["decoy"] else rf"\b{item}"
        elif kind == "diet":
            pat = _alt([k["alt"]])
        elif kind == "wake":
            cur = _time_alt(*ks["knock"])
            pat = rf"\A(?=[\s\S]*{cur})"
            if "stale" in ks:
                pat += rf"(?![\s\S]*^(?![^\n]*{cur})[^\n]*{_time_alt(*ks['stale'])})"
        elif kind == "ink":
            # R0, P1 r4: Fable met the dry pen with "take what you need from the drawer" -- any turn to the drawer,
            # the stationery or the pen counts, from the turn the pen ran dry to the note
            pat = r"\b(?:ink\w*|pencils?|nibs?|pens?|stationery|drawer)\b"
        elif kind == "gift":
            pat = _alt([k["gift"], k["goods"], k["kid"]])
        elif kind == "route":
            pat = _alt([k["high"]])
        else:
            v = k["canon"][kind.split("-", 1)[1]][0]
            # R0, P1 r4: "fifty-six feet" -- a depth may come back in words
            pat = _num_alt(int(v)) if kind == "canon-well" else rf"\b{re.escape(v)}\b"
        e["cid"] = f"{kind}-t{t}"
        if kind in ("ink", "diet", "wake"):     # acting early is attentive too: the window is need .. moment
            c = _chk(e["cid"], list(range(e["need"], t + 1)), 3.0, pat, scope="ic")
            c["per_turn"] = False
            out.append(c)
        else:
            out.append(_chk(e["cid"], [t], 3.0, pat, scope="ic"))
    T = len(p["turns"])
    for n, r in p.get("vrules", {}).items():
        out.append(_chk(f"voice-{n}", list(range(1, T + 1)), 2.0,
                        rf"\A{_SUBST}(?![\s\S]*{_TAGGED.format(n=re.escape(n))}{VRULES[r][1]})", scope="ic", group="constraint"))
    if p.get("rule_turn"):
        for t in range(p["rule_turn"], T + 1):
            pat = rf"\A{_SUBST}(?![\s\S]*\?)"
            for n, w in p["openers"].items():
                pat += rf"(?![\s\S]*{_line(n, w)}[^\n]*\n[\s\S]*{_line(n, w)})"
                pat += (rf"(?![\s\S]*{_TAGGED.format(n=re.escape(n))}\S+[ \t][^\n]*?[.!?][\"'\u201d\u2019]?[ \t]+"
                        rf"[\"'\u201c\u2018]?\w)")
            out.append(_chk(f"rule-t{t}", [t], 1.0, pat, scope="ic", group="constraint"))
    return out


def _say(e: dict, p: dict) -> tuple[str, str]:
    """(speaker, line text) that meets event e in the reference."""
    k, ks = p["k"], _keys(p)
    kind = e["kind"]
    if kind == "coat":
        return p["door"], f"here is your {k['colour']} {k['item']}, off the peg."
    if kind == "diet":
        return p["cook"], f"a bowl of {k['alt']} is kept back for you."
    if kind == "wake":
        h, m = ks["knock"]
        return p["porter"], f"a knock at {_time_text(h, m)} for your train."
    if kind == "ink":
        return p["porter"], "here is a pot of ink from the drawer."
    if kind == "gift":
        return p["cook"], f"take a {k['gift']} from the counter for {k['kid']}."
    if kind == "route":
        return p["door"], f"after the rain, go by the {k['high']}."
    c = kind.split("-", 1)[1]
    return p["npcs"][len(e["cid"]) % 3], f"begging your pardon, it is {k['canon'][c][0]}."


def _reference(p: dict, g) -> list[str]:
    per = {}
    for e in p["events"]:
        per.setdefault(e["turn"], []).append(e)
    out = []
    for t in range(1, len(p["turns"]) + 1):
        lines = [g.choice(NARRATION)]
        said = {}
        for e in per.get(t, []):
            who, text = _say(e, p)
            said.setdefault(who, []).append(text)
        for n in p["npcs"]:
            texts = said.get(n) or [g.choice(CALM)]
            if p.get("rule_turn") and t >= p["rule_turn"]:
                texts = ["; ".join(x.rstrip(".") for x in texts) + "."]
            for text in texts:
                if p.get("vrules", {}).get(n) == "dear":
                    text = text[:-1] + ", dear."
                lines.append(f"{n}: {p['openers'][n]}, {text}")
        out.append("\n".join(lines))
    return out


def _mutants(p: dict, ref: list[str]) -> dict:
    out = {}
    ks = _keys(p)
    for e in p["events"]:
        t = e["turn"]
        who, text = _say(e, p)
        r = list(ref)
        r[t - 1] = r[t - 1].replace(text.rstrip("."), "the fire is drawing well tonight")
        out[f"{e['kind']}-missed"] = (r, [e["cid"]])
        if e["kind"] == "wake" and "stale" in ks:
            r = list(ref)
            r[t - 1] = r[t - 1].replace(text.rstrip("."), f"a knock at {_time_text(*ks['stale'])} for your train")
            out["wake-stale"] = (r, [e["cid"]])
        if e["kind"] == "wake":
            r = list(ref)
            r[t - 1] = r[t - 1].replace(text.rstrip("."), f"a knock at {_time_text(*(p['k'].get('train2') or p['k']['train']))} for your train")
            out["wake-at-train-time"] = (r, [e["cid"]])
        if e["kind"] == "coat" and p["k"]["decoy"]:
            r = list(ref)
            r[t - 1] = r[t - 1].replace(text.rstrip("."), f"here is your {p['k']['decoy']} {p['k']['item']}, off the peg")
            out["coat-decoy"] = (r, [e["cid"]])
        if e["kind"] == "diet":
            r = list(ref)
            r[t - 1] = r[t - 1].replace(text.rstrip("."), f"it is {p['k']['main']} tonight, hot from the oven")
            out["diet-main-served"] = (r, [e["cid"]])
    busy = {e["turn"] for e in p["events"]}
    quiet = [t for t in range(1, len(ref) + 1) if t not in busy]
    t = quiet[0]
    n1 = p["npcs"][1]
    r = list(ref)
    r[t - 1] = r[t - 1].replace(f"{n1}: {p['openers'][n1]}, ", f"{n1}: Honestly, ", 1)
    out["voice-broken"] = (r, [f"cast-t{t}"])
    r = list(ref)
    r[t - 1] = r[t - 1] + f"\n{p['user']} nods and says it is fine."
    out["puppet"] = (r, [f"cast-t{t}"])
    r = list(ref)
    r[t - 1] = "\n".join(x for x in r[t - 1].splitlines() if not x.startswith(f"{p['npcs'][2]}:"))
    out["present-silent"] = (r, [f"cast-t{t}"])
    bad = {"dear": None, "nothat": "and that is the truth", "short": ("and the road past the mill is long and wet and "
           "the carts are late again every single morning this week"), "nocontr": "it isn't late", "noI": "I think so"}
    for n, rule in p.get("vrules", {}).items():
        lines = r_t = ref[t - 1].splitlines()
        i = next(j for j, x in enumerate(lines) if x.startswith(f"{n}:"))
        body = lines[i]
        lines = list(r_t)
        lines[i] = body.replace(", dear.", ".") if rule == "dear" else body.rstrip(".") + ", " + bad[rule] + "."
        r = list(ref)
        r[t - 1] = "\n".join(lines)
        out[f"voice-{rule}-{n}"] = (r, [f"voice-{n}"])
    if p.get("rule_turn"):
        post = [x for x in quiet if x >= p["rule_turn"]]
        if post:
            u = post[0]
            r = list(ref)
            r[u - 1] = r[u - 1].replace(f"{p['npcs'][0]}: {p['openers'][p['npcs'][0]]}, ", f"{p['npcs'][0]}: {p['openers'][p['npcs'][0]]}, will you stay long? ", 1)
            out["rule-question"] = (r, [f"rule-t{u}"])
            r = list(ref)
            r[u - 1] = r[u - 1] + f"\n{p['npcs'][2]}: {p['openers'][p['npcs'][2]]}, the yard is quiet now" + (", dear." if p["vrules"].get(p["npcs"][2]) == "dear" else ".")
            out["rule-two-lines"] = (r, [f"rule-t{u}"])
            r = list(ref)
            r[u - 1] = r[u - 1].replace(f"{p['npcs'][1]}: {p['openers'][p['npcs'][1]]}, ", f"{p['npcs'][1]}: {p['openers'][p['npcs'][1]]}, the fire is lit. And ", 1)
            out["rule-two-sentences"] = (r, [f"rule-t{u}"])
    return out


@functools.lru_cache(maxsize=256)
def _plan_cached(variant: str, level: int, key, release_seed: str, case_id: str) -> dict:
    L = LEVELS[variant][level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, f"{variant}:{key or ''}")
    if L.get("rules"):                      # L7+: the directive layer (attend_rules.py); L1-L6 below are untouched
        from . import attend_rules
        p = attend_rules.build(seed, L)
        p.update(variant=variant, level=level, seed=seed, rules_level=True)
        return p
    for attempt in range(3000):
        g = rng(seed, f"plan{attempt}")
        try:
            p = _attempt(g, L)
        except (_Fail, IndexError):
            continue
        p.update(variant=variant, level=level, seed=seed, attempt=attempt)
        p["system"] = _system(p, L)
        p["checks"] = _checks(p, L)
        p["reference"] = _reference(p, rng(seed, f"ref{attempt}"))
        if grade_weighted(p["checks"], p["reference"]) == 1.0:
            return p
    raise RuntimeError(f"attend: no valid draw for {case_id} {variant} L{level}")


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    variant = gen.get("variant") or "host"
    return copy.deepcopy(_plan_cached(variant, int(gen.get("level", 1)), gen.get("key"), release_seed, case_id))


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    p = _plan(gen, release_seed, case_id)
    return {
        "id": case_id, "category": "rpdeep", "system": p["system"], "history": [], "turns": p["turns"],
        "temperature": 0.7, "top_p": 0.95, "max_tokens": MAX_TOKENS, "user_name": p["user"],
        "checks": p["checks"], "judge_elements": [], "programmatic": True, "generator": dict(gen),
        "meta": {"family": FAMILY, "variant": p["variant"], "level": p["level"], "n_turns": len(p["turns"]),
                 "n_checks": len(p["checks"]),
                 "prompt_tokens_est": round((len(p["system"]) + sum(len(x) for x in p["turns"])) / 4),
                 "reference_tokens_est": round(sum(len(x) for x in p["reference"]) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_plan(gen, release_seed, case_id)["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_weighted(materialized["checks"], split_turns(answer))


def mutants_named(gen: dict, *, release_seed: str, case_id: str) -> dict:
    p = _plan(gen, release_seed, case_id)
    if p.get("rules_level"):
        from . import attend_rules
        return {k: (join_turns(r), want) for k, (r, want) in attend_rules.mutants(p).items()}
    return {k: (join_turns(r), want) for k, (r, want) in _mutants(p, p["reference"]).items()}


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    out = [a for a, _ in mutants_named(gen, release_seed=release_seed, case_id=case_id).values()]
    out.append(join_turns(["Sure, noted."] * len(mat["turns"])))
    return [a for a in dict.fromkeys(out) if grade(a, mat) < 1.0]


def _main(argv: list[str] | None = None) -> None:
    import json
    argv = sys.argv[1:] if argv is None else argv
    level = int(argv[0]) if argv else 3
    g = {"type": FAMILY, "level": level, "variant": "host"}
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
