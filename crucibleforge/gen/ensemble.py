"""ensemble -- ENSEMBLE multi-character chat families (gen2 chat, 4.5; design build/GEN2-CHAT.md).

A seed + level produce a cast of named NPCs (each with a checkable voice: every line opens with its
own word), a scene card (system prompt) and scripted USER turns. Every key is computed from the card
and the user turns only, never from the model's replies. Grading is programmatic (session_checks);
no judge call, no word-count checks.

Variants:

``know`` (WHO-KNOWS, FANToM inside a live roleplay). NPCs keep fixed posts; the user moves between
them and tells things "in confidence" (a password, a boat, a meeting place, a buyer's name) and later
changes some of them where only some NPCs can hear. Asked in the scene, an NPC must answer from ITS
OWN belief: the current value, the stale value it last heard, or "nobody told me". Checked per turn:
only NPCs at the user's post speak, each of them speaks in voice and never says a value it never
heard or one that would be news to someone else at the post (discretion); per query: the NPC's own
belief, no other value. From L2 a final OOC turn asks a belief table (L4 adds second-order cells:
what X thinks Y believes).

``listen`` (the user's own claims). One table, three NPCs. The user drops personal facts in passing,
corrects some ("sorry, I got that wrong"), later slips (states an earlier fact differently, without
saying so; from L3 one slip reverts a corrected fact to its OLD value), and misstates the card in
subordinate clauses. Checked: someone names the right card fact in the same reply; someone catches
the slip by naming what the user said before; a closing request uses the CURRENT version of a
corrected fact and never the old one; every NPC speaks in voice every turn; no puppeting.

LEVELS (T = user turns; the last ``know`` turn from L2 is the OOC belief table):

  know    L  NPC slots updates queries ooc(1st/2nd)  T          listen  L facts canon corr slips close T
          1   3   2     1       3       0/0         7                   1   2     1     1    1     1    7
          2   4   2     2       4       4/0        10                   2   3     2     1    2     1    9
          3   4   3     3       5       6/0        12                   3   3     2     2    2     2   11
          4   5   3     4       6       6/2        14                   4   4     3     2    3     2   13

Sizes (30 seeds, release_seed "r"; chars/4) are pinned in LEVELS[..]["size_prompt"/"size_checks"]
and tests/test_gen_ensemble.py.
"""
from __future__ import annotations

import copy
import functools
import re
import sys

from ._stack_common import TURN_SEP, grade_weighted, join_turns, rng, split_turns  # noqa: F401
from .. import gen as _gen

FAMILY = "ensemble"
VARIANTS = ("know", "listen")

LEVELS = {
    "know": {
        1: {"N": 3, "K": 2, "U": 1, "Q": 3, "O": 0, "O2": 0, "T": 7, "lead": False},
        2: {"N": 4, "K": 2, "U": 2, "Q": 4, "O": 4, "O2": 0, "T": 10, "lead": False, "comp": True},
        3: {"N": 4, "K": 3, "U": 3, "Q": 5, "O": 6, "O2": 0, "T": 12, "lead": True, "comp": True},
        4: {"N": 5, "K": 3, "U": 4, "Q": 6, "O": 6, "O2": 2, "T": 14, "lead": True, "comp": True},
        # P1 round 2 knob: whispers (co-present NPCs know different things), a false "you were right here"
        # push on unknown queries, conflicting beliefs in one room allowed, no gossip propagation
        5: {"N": 4, "K": 3, "U": 3, "Q": 6, "O": 4, "O2": 0, "T": 13, "lead": True, "comp": True,
            "whisper": True, "false_presence": True, "gossip": False},
        # P1 round 3 knob: FANToM's hardest formats, strict lists. In-scene "who was with you when you were
        # last told X?" (episodic co-presence, W queries) and OOC "who has been told the current X?" (OL cells)
        6: {"N": 5, "K": 3, "U": 4, "Q": 4, "W": 3, "O": 2, "OL": 3, "O2": 0, "T": 16, "lead": True, "comp": True,
            "whisper": True, "false_presence": True, "gossip": False},
    },
    "listen": {
        1: {"F": 2, "C": 1, "R": 1, "S": 1, "X": 1, "T": 7, "revert": False},
        2: {"F": 3, "C": 2, "R": 1, "S": 2, "X": 1, "T": 9, "revert": False},
        3: {"F": 3, "C": 2, "R": 2, "S": 2, "X": 2, "T": 11, "revert": True},
        4: {"F": 4, "C": 3, "R": 2, "S": 3, "X": 2, "T": 13, "revert": True},
        # P1 round 2 knob: same events as L4, but rule 4 no longer tells the household to correct the
        # guest; the card only says they know the house cold and listen closely (LRV regime)
        5: {"F": 4, "C": 3, "R": 2, "S": 3, "X": 2, "T": 13, "revert": True, "implicit": True},
    },
}
# measured (30 seeds, release_seed "r"): prompt tokens (system + turns, chars/4), checks per item,
# median reference-answer tokens; tests/test_gen_ensemble.py pins the first two
_SIZES = {
    "know": {1: ((485, 585), (17, 17), 254), 2: ((695, 810), (26, 33), 396),
             3: ((800, 925), (35, 41), 510), 4: ((935, 1065), (46, 55), 656), 5: ((830, 975), (34, 46), 548), 6: ((995, 1115), (51, 64), 766)},
    "listen": {1: ((515, 575), (10, 10), 472), 2: ((595, 655), (14, 14), 603),
               3: ((645, 715), (17, 17), 741), 4: ((725, 800), (21, 21), 872), 5: ((665, 720), (21, 21), 877)},
}
for _v, _rows in LEVELS.items():
    for _lv, _row in _rows.items():
        _row["size_prompt"], _row["size_checks"], _row["answer_tokens"] = _SIZES[_v][_lv]

MAX_TOKENS = 6144          # per call: reply + thinking; far under the 24,576 board cap
_SUBST = r"(?=\s*(?:\S+\s+){7}\S)"           # >= 8 words: a stub guard, not a length rule (R0, P1 r1)

SCENES = [
    {"title": "Saltmarsh ferry landing", "intro": "A grey landing on a tidal river: one jetty, a row of huts and a bell that rings when the ferry is due.",
     "rooms": ["ticket hut", "jetty", "chart shed", "lamp tower", "tea stall", "net loft"]},
    {"title": "Hollin night market", "intro": "A lantern-lit market under a stone bridge, where stalls open after dark and close at the first bell.",
     "rooms": ["fish stall", "spice row", "clock tower", "well square", "bridge steps", "cloth stall"]},
    {"title": "Fennick coaching inn", "intro": "A low inn at a crossroads, warm and crowded, with a yard full of waiting carts.",
     "rooms": ["taproom", "cellar", "stable yard", "kitchen", "wash house", "tack room"]},
    {"title": "Greywater observatory", "intro": "A hilltop observatory with a round dome, a cluttered workshop and a roof terrace open to the stars.",
     "rooms": ["dome", "library", "workshop", "roof terrace", "boiler room", "gatehouse"]},
]
NAMES = ["Odo", "Marta", "Tobin", "Ilse", "Perrin", "Wick", "Nell", "Garrick", "Sable", "Hobb", "Dara", "Fenn"]
USERS = ["Wren", "Corin", "Maren", "Teo", "Lio", "Sunny"]
OPENERS = ["Well", "Look", "Aye", "Right", "Mind", "Now", "Listen", "So"]
SPEECH = [
    "mind the step, it is slick this time of day.", "we do not get many visitors in this weather.",
    "if you need anything, ask and I will see what I can find.", "that is a fair question, and I will answer it.",
    "take your time, nothing here is going anywhere.", "I have kept this place tidy for years, so wipe your boots.",
    "you will find most things where you would expect them.", "funny how quiet it gets once the crowd thins out.",
    "keep your voice down, the walls are thinner than they look.", "that is one way to go about it.",
    "the kettle will be on in a moment.", "I had a feeling the day would go like this.",
]
NARRATION = [
    "A cold draught moves through and the lamps flicker.", "Somewhere nearby a door creaks and settles.",
    "The floorboards are worn smooth by years of boots.", "A faint smell of tar and woodsmoke hangs in the air.",
    "Outside, a bell rings twice and then goes quiet.", "Dust turns slowly in a bar of pale light.",
    "A kettle ticks as it cools on a shelf.", "Rain taps at the window and stops.",
]

# ------------------------------------------------------------------ know: secret slots
SLOTS = {
    "password": {"told": "the password for the gate is {v}", "ask": "the password for the gate", "noun": "gate password",
                 "pool": ["juniper", "thistle", "saffron", "bramble", "marigold", "quince", "nutmeg", "damson"]},
    "boat": {"told": "we are taking the boat called the {v}", "ask": "which boat we are taking", "noun": "boat",
             "pool": ["Kestrel", "Dunlin", "Merrow", "Puffin", "Corrie", "Sorrel", "Wagtail", "Shearwater"]},
    "place": {"told": "we are meeting tonight at the {v}", "ask": "where we are meeting tonight", "noun": "meeting place",
              "pool": ["lime kiln", "stone cross", "toll bridge", "chapel yard", "salt store", "beacon cairn",
                       "drovers well", "gibbet oak"]},
    "buyer": {"told": "the buyer's name is {v}", "ask": "the buyer's name", "noun": "buyer's name",
              "pool": ["Ambrose", "Theodora", "Lucan", "Philippa", "Ignatius", "Rosalind", "Clementine", "Octavia"]},
}
MOVES = ["I head over to the {r}.", "I walk across to the {r}.", "I go back to the {r}.", "I make my way to the {r}."]
REVEAL = ["Keep this to yourselves: {told}.", "Between us, and no further: {told}.", "Quietly now: {told}."]
UPDATE = ["Change of plan: {told}.", "Forget what you may have heard. Now {told}.", "New orders: {told}."]
ASK = ["{X}, tell me {ask}, if you know it. If nobody told you, just say so.",
       "{X}, do you know {ask}? Only say it if you were told.",
       "{X}, remind me {ask}, as far as you know?"]
LEAD = ["{X}, you'll have heard {ask} by now, surely. Go on, what is it?"]
WHO = ["{X}, who was with you when you were last told {ask}? Just the names."]
FALSE_PRESENCE = ["{X}, you were right here when I told everyone {ask}, remember? Go on, say it."]
WHISPER = ["I lean in so only {X} can hear: {told}.", "I whisper to {X} alone: {told}."]
FILLER_K = ["I ask {Y} how the day has gone so far.", "I ask {Y} what keeps them busy here.",
            "I warm my hands and ask {Y} about the weather coming in.", "I ask {Y} who usually comes by at this hour."]


def _tag(names, lead: bool = False) -> str:
    alt = "|".join(re.escape(n) for n in names)
    return rf"^[ \t]*{TAG_LEAD if lead else ''}(?:\*\*|__)?(?:{alt})(?:\*\*|__)?[ \t]*:"


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(set(words), key=lambda w: (-len(w), w)))


def _word_alt(words) -> str:
    return rf"\b(?:{_alt(words)})\b"


def _chk(cid: str, turns: list[int], weight: float, pattern: str, scope: str = "ic", group: str = "continuity") -> dict:
    return {"id": cid, "fact": cid, "ftype": "fact", "group": group, "turns": turns, "scope": scope,
            "weight": weight, "type": "require_regex", "pattern": pattern}


def _puppet(user: str) -> str:
    return (rf"(?![\s\S]*\b{re.escape(user)}(?:'s)?(?:\s+\w+ly)?\s+(?:says|said|nods|nodded|smiles|smiled|"
            r"laughs|laughed|asks|asked|replies|replied|whispers|whispered|decides|decided|agrees|agreed|feels|"
            r"felt|thinks|thought|sighs|sighed|shrugs|shrugged|gasps|gasped|walks|walked|reaches|reached|"
            r"grabs|grabbed|takes|took|opens|opened)\b)")


_STAGE = r"(?:[*_(][^\n]*?[*_)][ \t]*)?"          # a leading *stage direction* is allowed
_Q = "['\"\u201c]?"
_TAGGED = r"^[ \t]*(?:\*\*|__)?{n}(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*"
#: an optional lead before a speaker tag: a quote, dash, bullet or '>' ('Nell: / - Odo: / > Sable:). Off by default so
#: instances built without it stay byte-identical; families opt in per call with ``lead=True`` (ATTEND L7+).
TAG_LEAD = r"(?:[-*>\u2022'\"\u2018\u201c]+[ \t]*)?"


def _tagged_rx(name: str, lead: bool = False) -> str:
    """``_TAGGED`` for one escaped name, with the optional lead when ``lead``."""
    t = _TAGGED.format(n=re.escape(name))
    return (r"^[ \t]*" + TAG_LEAD + t[len(r"^[ \t]*"):]) if lead else t


def _line(name: str, opener: str, lead: bool = False) -> str:
    """The start of a line spoken by ``name``: 'Name: ...' (any bold), or -- R0, P1 round 1: Haiku drops
    the tags but keeps the words -- an untagged line that opens with the NPC's own word plus punctuation
    (the words are unique per NPC)."""
    return (rf"(?:{_tagged_rx(name, lead)}|^[ \t]*{_STAGE}{_Q}"
            rf"(?={re.escape(opener)}[,:;!.\u2014-]))")


def _voice(name: str, opener: str, lead: bool = False) -> str:
    """No TAGGED line of ``name`` that does not open with its word."""
    return rf"(?![\s\S]*{_tagged_rx(name, lead)}(?!{_STAGE}{_Q}{re.escape(opener)}\b)\S)"


def _cast_check(t: int, user: str, absent: dict, voices: dict | None = None, lead: bool = False) -> dict:
    """One all-or-nothing check per scene turn: a real reply, no line from an absent NPC (tagged or in its
    word) and no user line, no puppeting; ``voices`` (listen): every NPC present speaks, all in voice."""
    pat = rf"\A{_SUBST}{_puppet(user)}(?![\s\S]*{_tag([user], lead)})"
    for n, w in absent.items():
        pat += rf"(?![\s\S]*{_line(n, w, lead)})"
    for n, w in (voices or {}).items():
        pat += rf"(?=[\s\S]*{_line(n, w, lead)}){_voice(n, w, lead)}"
    return _chk(f"cast-t{t}", [t], 1.0, pat, scope="ic", group="constraint")


def _ref_line(g, name: str, opener: str, text: str | None = None) -> str:
    return f"{name}: {opener}, {text or g.choice(SPEECH)}"


# ================================================================== KNOW
def _know_attempt(g, L: dict, level: int) -> dict | None:
    sc = g.choice(SCENES)
    user = g.choice(USERS)
    npcs = g.sample(NAMES, L["N"])
    openers = dict(zip(npcs, g.sample(OPENERS, L["N"])))
    rooms = g.sample(sc["rooms"], 3)
    post = {}
    order = list(npcs)
    g.shuffle(order)
    comp = order.pop() if L.get("comp") else None
    for i, n in enumerate(order):
        post[n] = rooms[i] if i < 3 else g.choice(rooms)
    if len(order) < 3:
        return None
    cst = {"with": False, "room": g.choice(rooms) if comp else None}
    if comp:
        post[comp] = None

    def present(rm):
        return [n for n in npcs if post[n] == rm or (n == comp and (cst["with"] or cst["room"] == rm))]
    slots = g.sample(sorted(SLOTS), L["K"])
    vals = {k: g.sample(SLOTS[k]["pool"], 1 + sum(1 for _ in range(L["U"]))) for k in slots}
    S = L["T"] - (1 if L["O"] else 0)
    # event list
    ev = [("reveal", k) for k in slots]
    upd = [("update", slots[j % len(slots)]) for j in range(L["U"])]
    ev += upd
    ev += [("query", None)] * L["Q"] + [("who", None)] * L.get("W", 0)
    ev += [("filler", None)] * (S - len(ev))
    if len(ev) > S:
        return None
    g.shuffle(ev)
    # repair the order: each slot's reveal comes before any update of it; the first event is a reveal
    for k in slots:
        ri = ev.index(("reveal", k))
        ui = [j for j, e in enumerate(ev) if e == ("update", k)]
        if ui and ui[0] < ri:
            ev[ri], ev[ui[0]] = ev[ui[0]], ev[ri]
    fr = next(j for j, e in enumerate(ev) if e[0] == "reveal")
    ev.insert(0, ev.pop(fr))
    room = g.choice(rooms)
    start = room
    cst0 = cst["room"]
    belief = {n: {} for n in npcs}          # n -> slot -> (value, src)
    heard = {n: set() for n in npcs}        # every value n has heard (for discretion)
    current = {}
    used = {k: 0 for k in slots}
    turns, script = [], []
    queries, history = [], []               # history: (slot, value, present-set, src)
    asked = set()
    last_told = {n: {} for n in npcs}       # n -> slot -> co-hearers at n's latest told event
    whos = []

    def w_cands(rm):
        return [(X, kk) for X in present(rm) for kk in last_told[X] if (X, kk, "who") not in asked]

    def q_cands(rm):
        hh = present(rm)
        cc = []
        for X in hh:
            if queries and queries[-1]["npc"] == X:
                continue
            for kk in current:
                if (X, kk) in asked:
                    continue
                b = belief[X].get(kk)
                if b and b[1] != "told":
                    continue
                bv = b[0] if b else None
                if L.get("gossip", True) and any(belief[Y].get(kk) and belief[Y][kk][0] != bv
                                                 for Y in hh if Y != X and bv is not None):
                    continue
                qk = "unknown" if bv is None else ("current" if bv == current[kk] else "stale")
                cc.append((X, kk, bv, qk))
        return cc

    for i, (kind, k) in enumerate(ev, 1):
        parts, move = [], False
        if comp:
            if not cst["with"] and cst["room"] == room and g.random() < 0.5:
                parts.append(f"{comp}, come with me.")
                cst["with"] = True
            elif cst["with"] and g.random() < 0.3:
                parts.append(f"{comp}, wait here.")
                cst["with"], cst["room"] = False, room
        if kind == "who":
            opts = [r for r in rooms if w_cands(r)]
            if not opts:
                return None
            nr = g.choice(opts)
            if nr != room:
                room, move = nr, True
        elif kind == "query":
            opts = [r for r in rooms if q_cands(r)]
            if not opts:
                return None
            have = {q["kind"] for q in queries}
            best = [r for r in opts if any(c[3] not in have for c in q_cands(r))] or opts
            nr = g.choice(best)
            if nr != room:
                room, move = nr, True
        elif (i > 1 and g.random() < 0.6) or (i == 1 and g.random() < 0.5):
            room = g.choice([r for r in rooms if r != room])
            move = True
        if move:
            parts.append(g.choice(MOVES).format(r=room))
        here = present(room)
        rec = {"turn": i, "room": room, "here": here, "kind": kind}
        if kind in ("reveal", "update"):
            v = vals[k][used[k]]
            used[k] += 1
            current[k] = v
            told = SLOTS[k]["told"].format(v=v)
            hearers = here
            if L.get("whisper") and len(here) > 1 and g.random() < 0.5:
                W = g.choice(here)
                hearers = [W]
                parts.append(g.choice(WHISPER).format(X=W, told=told))
                rec["whisper"] = W
            else:
                parts.append(g.choice(REVEAL if kind == "reveal" else UPDATE).format(told=told))
            for n in hearers:
                belief[n][k] = (v, "told")
                heard[n].add(v)
                last_told[n][k] = sorted(set(hearers) - {n})
            history.append((k, v, frozenset(hearers), "told"))
            rec.update(slot=k, value=v)
            if g.random() < 0.5:
                parts.append(g.choice(FILLER_K).format(Y=g.choice(here)))
        elif kind == "query":
            cands = q_cands(room)
            have = {q["kind"] for q in queries}
            pref = [c for c in cands if c[3] not in have] or cands
            X, kk, bv, qk = g.choice(pref)
            asked.add((X, kk))
            lead = L["lead"] and qk != "current" and g.random() < 0.5
            false_p = L.get("false_presence") and qk == "unknown" and g.random() < 0.7
            form = FALSE_PRESENCE if false_p else (LEAD if lead else ASK)
            parts.append(g.choice(form).format(X=X, ask=SLOTS[kk]["ask"]))
            queries.append({"turn": i, "npc": X, "slot": kk, "expect": bv, "kind": qk, "lead": lead,
                            "here": list(here)})
            if bv is not None:
                for n in here:
                    if kk not in belief[n] and L.get("gossip", True):
                        belief[n][kk] = (bv, "heard")
                    heard[n].add(bv)
                history.append((kk, bv, frozenset(here), "heard"))
            rec.update(slot=kk, npc=X, expect=bv)
        elif kind == "who":
            X, kk = g.choice(w_cands(room))
            asked.add((X, kk, "who"))
            parts.append(g.choice(WHO).format(X=X, ask=SLOTS[kk]["ask"]))
            whos.append({"turn": i, "npc": X, "slot": kk, "expect": list(last_told[X][kk])})
            rec.update(slot=kk, npc=X)
        else:
            parts.append(g.choice(FILLER_K).format(Y=g.choice(here)))
        rec["heard"] = {n: set(heard[n]) for n in npcs}
        script.append(rec)
        turns.append(" ".join(parts))
    if {"stale", "unknown", "current"} - {q["kind"] for q in queries}:
        return None
    if len(turns) != S:
        return None
    # OOC belief table
    ooc = []
    if L["O"]:
        cells = []
        for n in npcs:
            for k in slots:
                b = belief[n].get(k)
                if b and b[1] != "told":
                    continue
                bv = b[0] if b else None
                qk = "unknown" if bv is None else ("current" if bv == current[k] else "stale")
                cells.append({"kind": "first", "npc": n, "slot": k, "expect": bv, "ck": qk})
        g.shuffle(cells)
        pick = []
        for want in ("stale", "unknown", "current"):
            c = next((c for c in cells if c["ck"] == want and c not in pick), None)
            if c is None:
                return None
            pick.append(c)
        for c in cells:
            if len(pick) >= L["O"]:
                break
            if c not in pick:
                pick.append(c)
        if len(pick) < L["O"]:
            return None
        ooc = pick
        if L.get("OL"):
            lists = []
            for k in slots:
                told_now = sorted(n for n in npcs if belief[n].get(k) == (current[k], "told"))
                lists.append({"kind": "list", "slot": k, "expect": told_now})
            g.shuffle(lists)
            if len(lists) < L["OL"] or not any(len(x["expect"]) >= 2 for x in lists[:L["OL"]]):
                return None
            ooc += lists[:L["OL"]]
        if L["O2"]:
            sec = []
            for X in npcs:
                for Y in npcs:
                    if X == Y:
                        continue
                    for k in slots:
                        joint = [h for h in history if h[0] == k and X in h[2] and Y in h[2]]
                        if joint and joint[-1][3] != "told":
                            continue
                        # Y must have heard nothing later that X saw either (rule: assume only what you saw)
                        ev2 = joint[-1][1] if joint else None
                        sec.append({"kind": "second", "npc": X, "other": Y, "slot": k, "expect": ev2})
            g.shuffle(sec)
            real = [s for s in sec if s["expect"] is not None
                    and (belief[s["other"]].get(s["slot"], (None,))[0] != s["expect"] or s["expect"] != current[s["slot"]])]
            rest = [s for s in sec if s not in real]
            got = (real[:1] + rest)[:L["O2"]]
            if len(got) < L["O2"] or not real:
                return None
            ooc += got
    return {"sc": sc, "user": user, "npcs": npcs, "openers": openers, "post": post, "rooms": rooms,
            "start": start, "comp": comp, "comp_start": cst0, "slots": slots, "vals": vals, "turns": turns, "script": script,
            "queries": queries, "whos": whos, "ooc": ooc, "current": current, "L": dict(L)}


def _know_system(p: dict) -> str:
    user, sc = p["user"], p["sc"]
    rules = (
        f"You play every character in an interactive scene except {user}, who is the user's.\n"
        "Rules:\n"
        "1. Each spoken line is its own line in the form 'Name: text' (no quotation marks). Narration lines carry no name.\n"
        f"2. Characters with a post stay at it for the whole scene. A character speaks only when {user} is in the same "
        f"place, and then every character there speaks at least once in the reply. Characters elsewhere say nothing.\n"
        "3. Each character opens every line they speak with their own word (listed below).\n"
        f"4. A character knows only what was said in front of them. What {user} tells people is in confidence: nobody "
        f"repeats it in front of anyone who has not heard it, unless {user} asks them directly. Asked something they "
        "were never told, a character says plainly that nobody told them, and never guesses. Someone who missed a "
        "change still believes the old version."
        + (f" Something whispered is heard only by the one it is whispered to. Only {user}'s own words tell anyone "
           "anything: what other characters say aloud changes nobody's mind." if p["L"].get("whisper") else "")
        + "\n"
        f"5. Never write {user}'s words, actions, thoughts or choices; stop where {user} can act. Keep each reply to a "
        "short chat turn.\n"
        "6. If a message starts with OOC, step out of the scene and answer exactly as asked.")
    card = (f"SCENE CARD: {sc['title']}\n{sc['intro']}\nPosts:\n"
            + "\n".join((f"- {n} keeps the {p['post'][n]} all scene" if p["post"][n] else
                         f"- {n} has no post and starts in the {p['comp_start']}; {n} goes along with {user} after "
                         f"\"{n}, come with me\" and stays where they are after \"{n}, wait here\"")
                        + f"; every line {n} speaks starts with \"{p['openers'][n]}\"." for n in p["npcs"])
            + f"\n{user} starts in the {p['start']}.")
    return rules + "\n\n" + card


def _know_ooc(p: dict) -> dict:
    qs = []
    for c in p["ooc"]:
        ask = SLOTS[c["slot"]]["ask"]
        if c["kind"] == "first":
            qs.append(f"What does {c['npc']} believe about {ask}?")
        elif c["kind"] == "list":
            qs.append(f"Who has {p['user']} told the current {SLOTS[c['slot']]['noun']} to, directly? "
                      "List every name, or 'nobody'.")
        else:
            qs.append(f"What does {c['npc']} think {c['other']} believes about {ask}? (only from what {c['npc']} saw {c['other']} hear)")
    lists = any(c["kind"] == "list" for c in p["ooc"])
    text = ("OOC: pause the scene. Answer from what was said in front of whom, one line each, giving the word or "
            "name only, or 'unknown' if that person was never told"
            + (" (for a 'who' question: the names, or 'nobody')" if lists else "") + ":\n"
            + "\n".join(f"Q{i}. {q}" for i, q in enumerate(qs, 1))
            + "\nAnswer in exactly this form:\n" + "\n".join(f"A{i}: <answer>" for i in range(1, len(qs) + 1)))
    return {"text": text}


def _prefix(i: int) -> str:
    return rf"^[ \t]*(?:[-*]\s*)?\**A{i}\**[ \t]*[:.)]\**"


def _know_checks(p: dict) -> list[dict]:
    out = []
    user, npcs = p["user"], p["npcs"]
    allv = {k: list(p["vals"][k]) for k in p["slots"]}
    every = [v for k in p["slots"] for v in allv[k]]
    qturn = {q["turn"]: q for q in p["queries"]}
    for rec in p["script"]:
        t, here = rec["turn"], rec["here"]
        absent = [n for n in npcs if n not in here]
        out.append(_cast_check(t, user, {n: p["openers"][n] for n in absent}))
        heard = rec["heard"]
        q = qturn.get(t)
        for Y in here:
            others = [Z for Z in here if Z != Y]
            forbid = {v for v in every if v not in heard[Y]}
            forbid |= {v for v in heard[Y] if any(v not in heard[Z] for Z in others)}
            if q and q["npc"] == Y:
                forbid -= set(allv[q["slot"]])      # the query check owns this slot's values
            ln = _line(Y, p["openers"][Y])
            pat = rf"\A(?=[\s\S]*{ln}){_voice(Y, p['openers'][Y])}"
            if forbid:
                pat += rf"(?![\s\S]*{ln}[^\n]*{_word_alt(forbid)})"
            c = _chk(f"npc-t{t}-{Y}", [t], 0.5, pat, scope="ic")
            out.append(c)
    for i, q in enumerate(p["queries"], 1):
        vs = allv[q["slot"]]
        ln = _line(q["npc"], p["openers"][q["npc"]])
        if q["expect"] is None:
            pat = rf"\A(?=[\s\S]*{ln})(?![\s\S]*{ln}[^\n]*{_word_alt(vs)})"
        else:
            rest = [v for v in vs if v != q["expect"]]
            pat = rf"\A(?=[\s\S]*{ln}[^\n]*\b{re.escape(q['expect'])}\b)(?![\s\S]*{ln}[^\n]*{_word_alt(rest)})"
        out.append(_chk(f"q{i}-{q['kind']}", [q["turn"]], 3.0, pat, scope="ic"))
    for i, w in enumerate(p.get("whos", []), 1):
        ln = _line(w["npc"], p["openers"][w["npc"]])
        others = [n for n in npcs if n not in w["expect"] and n != w["npc"]]
        pat = r"\A" + "".join(rf"(?=[\s\S]*{ln}[^\n]*\b{re.escape(n)}\b)" for n in w["expect"])
        if not w["expect"]:
            pat += rf"(?=[\s\S]*{ln}[^\n]*\b(?:nobody|no one|no-one|just me|only me|alone|myself|none)\b)"
        if others:
            pat += rf"(?![\s\S]*{ln}[^\n]*{_word_alt(others)})"
        out.append(_chk(f"w{i}-who", [w["turn"]], 3.0, pat, scope="ic"))
    if p["ooc"]:
        T = len(p["turns"])
        for i, c in enumerate(p["ooc"], 1):
            vs = allv[c["slot"]]
            if c["kind"] == "list":
                others = [n for n in npcs if n not in c["expect"]]
                pat = _prefix(i) + "".join(rf"(?=[^\n]*\b{re.escape(n)}\b)" for n in c["expect"])
                if not c["expect"]:
                    pat += r"(?=[^\n]*\b(?:nobody|no one|none)\b)"
                pat += rf"(?![^\n]*{_word_alt(others)})[^\n]*\S"
                out.append(_chk(f"A{i}-list", [T], 2.0, pat, scope="all"))
                continue
            if c["expect"] is None:
                pat = rf"{_prefix(i)}(?![^\n]*{_word_alt(vs)})[^\n]*\bunknown\b"
            else:
                rest = [v for v in vs if v != c["expect"]]
                pat = rf"{_prefix(i)}(?![^\n]*(?:{_word_alt(rest)}|\bunknown\b))[^\n]*\b{re.escape(c['expect'])}\b"
            out.append(_chk(f"A{i}-{c['kind']}", [T], 2.0, pat, scope="all"))
    return out


def _know_reference(p: dict, g) -> list[str]:
    out = []
    qturn = {q["turn"]: q for q in p["queries"]}
    for rec in p["script"]:
        lines = [g.choice(NARRATION)]
        q = qturn.get(rec["turn"])
        w = next((x for x in p.get("whos", []) if x["turn"] == rec["turn"]), None)
        for Y in rec["here"]:
            if w and w["npc"] == Y:
                lines.append(_ref_line(g, Y, p["openers"][Y],
                                       (", ".join(w["expect"]) + ", and that is all.") if w["expect"]
                                       else "nobody else, just me."))
            elif q and q["npc"] == Y:
                txt = (f"last I heard, it is {q['expect']}." if q["expect"] is not None
                       else "nobody has told me that, so I cannot say.")
                lines.append(_ref_line(g, Y, p["openers"][Y], txt))
            else:
                lines.append(_ref_line(g, Y, p["openers"][Y]))
        lines.append(g.choice(NARRATION))
        out.append("\n".join(lines))
    if p["ooc"]:
        def ans(c):
            if c["kind"] == "list":
                return ", ".join(c["expect"]) or "nobody"
            return c["expect"] if c["expect"] is not None else "unknown"
        out.append("\n".join(f"A{i}: {ans(c)}" for i, c in enumerate(p["ooc"], 1)))
    return out


def _know_mutants(p: dict, ref: list[str], checks: list[dict]) -> dict:
    out = {}
    user, npcs = p["user"], p["npcs"]
    script = p["script"]
    for i, q in enumerate(p["queries"], 1):
        t = q["turn"]
        if q["kind"] == "stale":
            r = list(ref)
            r[t - 1] = r[t - 1].replace(f"it is {q['expect']}.", f"it is {p['current'][q['slot']]}.")
            out[f"stale-told-current-{i}"] = (r, [f"q{i}-stale"])
        if q["kind"] == "unknown":
            r = list(ref)
            guess = p["current"][q["slot"]]
            r[t - 1] = r[t - 1].replace("nobody has told me that, so I cannot say.", f"I would guess {guess}.")
            out[f"unknown-guesses-{i}"] = (r, [f"q{i}-unknown"])
            # a knower at the post blurts it (news to the asked NPC)
            for Y in q["here"]:
                if Y == q["npc"]:
                    continue
                rec = script[t - 1]
                v = next((v for v in rec["heard"][Y] if v in p["vals"][q["slot"]]), None)
                if v and v not in rec["heard"][q["npc"]]:
                    r = list(ref)
                    r[t - 1] = r[t - 1] + f"\n{Y}: {p['openers'][Y]}, it is {v}, as it happens."
                    out[f"blurts-to-outsider-{i}"] = (r, [f"npc-t{t}-{Y}"])
                    break
    for rec in script:
        t = rec["turn"]
        absent = [n for n in npcs if n not in rec["here"]]
        if absent and "absent-speaks" not in out:
            r = list(ref)
            r[t - 1] = r[t - 1] + f"\n{absent[0]}: {p['openers'][absent[0]]}, I can hear you from here."
            out["absent-speaks"] = (r, [f"cast-t{t}"])
        if t not in {q["turn"] for q in p["queries"]} and "present-silent" not in out and len(rec["here"]) > 1:
            Y = rec["here"][0]
            r = list(ref)
            r[t - 1] = "\n".join(x for x in r[t - 1].splitlines() if not x.startswith(f"{Y}:"))
            out["present-silent"] = (r, [f"npc-t{t}-{Y}"])
        if t not in {q["turn"] for q in p["queries"]} and "voice-broken" not in out:
            Y = rec["here"][-1]
            r = list(ref)
            r[t - 1] = r[t - 1].replace(f"{Y}: {p['openers'][Y]}, ", f"{Y}: Honestly, ", 1)
            out["voice-broken"] = (r, [f"npc-t{t}-{Y}"])
    r = list(ref)
    r[0] = r[0] + f"\n{user} nods and says it is fine."
    out["puppet"] = (r, ["cast-t1"])
    for i, w in enumerate(p.get("whos", []), 1):
        if w["expect"]:
            r = list(ref)
            r[w["turn"] - 1] = r[w["turn"] - 1].replace(", ".join(w["expect"]) + ", and that is all.",
                                                        ", ".join(w["expect"][1:] or ["nobody"]) + ", and that is all.")
            out[f"who-forgets-one-{i}"] = (r, [f"w{i}-who"])
            break
    for i, c in enumerate(p["ooc"], 1):
        if c["kind"] == "list":
            extra = next((n for n in npcs if n not in c["expect"]), None)
            if extra:
                r = list(ref)
                lines = r[-1].splitlines()
                lines[i - 1] = f"A{i}: " + ", ".join(c["expect"] + [extra])
                r[-1] = "\n".join(lines)
                out[f"list-overattributes-{i}"] = (r, [f"A{i}-list"])
                break
    if p["ooc"]:
        for i, c in enumerate(p["ooc"], 1):
            if c["kind"] == "first" and c["expect"] != p["current"][c["slot"]]:  # noqa: SIM102
                r = list(ref)
                lines = r[-1].splitlines()
                lines[i - 1] = f"A{i}: {p['current'][c['slot']]}"
                r[-1] = "\n".join(lines)
                out[f"ooc-omniscient-{i}"] = (r, [f"A{i}-first"])
                break
    return out


# ================================================================== LISTEN
NIECES = ["Ida", "Ottilie", "Bea", "Hester", "Maud", "Pearl", "Clemency", "Winifred"]
MARES = ["Bracken", "Juno", "Sorrel", "Tansy", "Hazel", "Pepper", "Willow", "Fable"]
SHIPS = ["Albatross", "Morning Star", "Halcyon", "Gull", "Petrel", "Swiftsure", "Ospray", "Dauntless"]
DOGS = ["Biscuit", "Rook", "Nutkin", "Bosun", "Pudding", "Tinker", "Smudge", "Ruffle"]
TOWNS = ["Kettering", "Ashby", "Penhallow", "Durrow", "Mallam", "Skelby", "Tarrant", "Wyke"]
FOUNDERS = ["Jem Calloway", "Agnes Thorne", "Silas Pike", "Hattie Moss", "Elias Crane", "Ruth Fairley"]
ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
TENS = {20: "twenty", 30: "thirty", 40: "forty"}
TEENS = {13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen"}


def _num_words(n: int) -> str:
    if n <= 12:
        return ONES[n]
    if n in TEENS:
        return TEENS[n]
    t, o = n - n % 10, n % 10
    return TENS[t] + (f"-{ONES[o]}" if o else "")


def _num_alt(n: int) -> str:
    w = _num_words(n)
    return rf"\b(?:{n}|{re.escape(w)}|{re.escape(w.replace('-', ' '))})\b"


_MIN = {10: ("ten past", 0), 20: ("twenty past", 0), 40: ("twenty to", 1), 50: ("ten to", 1)}
_MIN_W = {10: "ten", 20: "twenty", 40: "forty", 50: "fifty"}


def _time_text(h: int, m: int) -> str:
    return f"{h}:{m:02d}"


def _time_alt(h: int, m: int) -> str:
    rel, plus = _MIN[m]
    alts = [rf"0?{h}[:.]{m:02d}", rf"{ONES[h]}[- ]{_MIN_W[m]}", rf"{rel} {ONES[h + plus]}"]
    return r"\b(?:" + "|".join(alts) + r")\b"


FACTS = {
    "train": {"say": "my train leaves at {v} in the morning", "kind": "time",
              "slip": "Mind you, with my train at {v} I have time for one more cup.",
              "close": "I should turn in. Will someone make sure I'm up in good time for my train?"},
    "niece": {"say": "my niece {v} is the reason I'm travelling, it's her christening", "kind": "name", "pool": NIECES,
              "slip": "{v}, my niece, will love hearing about this place.",
              "close": "Before I forget, what name should I write on the christening card? I'd hate to get it wrong."},
    "mare": {"say": "my mare {v} is in your stable tonight", "kind": "name", "pool": MARES,
             "slip": "I hope {v} has settled in your stable; my mare is not fond of strangers.",
             "close": "Could someone look in on my mare before bed? Say her name to her, she likes that."},
    "ship": {"say": "my brother's ship, the {v}, docks next week", "kind": "name", "pool": SHIPS,
             "slip": "My brother will write once the {v} is in, I expect.",
             "close": "If a letter comes from my brother's ship after I've gone, which ship should you look for?"},
}
CORRECT = "Sorry, I got that wrong before: {say}, not {old}."
CANON = {
    "dog": {"card": "The house dog is called {v}.", "pool": DOGS,
            "wrong": "Your dog {w} has taken a shine to me, I think."},
    "years": {"card": "{N0} has kept this place for {v} years.",
              "wrong": "Seeing as {N0} has kept this place {w} years, I will trust {N0} on the roads."},
    "bell": {"card": "The bell over the door came from {v}.", "pool": TOWNS,
             "wrong": "That bell over the door, the one from {w}, is a fine thing."},
    "founder": {"card": "The place was founded by {v}.", "pool": FOUNDERS,
                "wrong": "Old {w}, who founded this place, would be proud of it."},
}
FILLER_L = ["What do people round here do of an evening?", "Is the road north passable at this time of year?",
            "Tell me something about this place I wouldn't guess.", "Who was the strangest guest you've ever had?",
            "Is there anything worth seeing in the morning before I go?", "How long has the weather been like this?",
            "What's the best thing on the menu, honestly?", "Do you get many travellers this late in the year?"]


def _listen_attempt(g, L: dict, level: int) -> dict | None:
    sc = g.choice(SCENES)
    user = g.choice(USERS)
    npcs = g.sample(NAMES, 3)
    openers = dict(zip(npcs, g.sample(OPENERS, 3)))
    room = g.choice(sc["rooms"])
    S = L["T"]
    facts = g.sample(sorted(FACTS), L["F"])
    if L["R"] > len([f for f in facts]) or L["X"] > L["R"]:
        return None
    canon = g.sample(sorted(CANON), L["C"])
    # values
    def draw(f, avoid=()):
        if FACTS[f]["kind"] == "time":
            while True:
                h, m = g.randint(5, 8), g.choice(sorted(_MIN))
                if (h, m) not in avoid:
                    return (h, m)
        return g.choice([x for x in FACTS[f]["pool"] if x not in avoid])

    def text(f, v):
        return _time_text(*v) if FACTS[f]["kind"] == "time" else v

    def alt(f, v):
        return _time_alt(*v) if FACTS[f]["kind"] == "time" else rf"\b{re.escape(v)}\b"

    corr = facts[:L["R"]]                 # these get corrected
    first = {f: draw(f) for f in facts}
    new = {f: draw(f, avoid=(first[f],)) for f in corr}
    closes = corr[:L["X"]]
    slips_on = list(facts[L["R"]:]) + list(corr)
    slips_on = slips_on[:L["S"]]
    if len(slips_on) < L["S"]:
        return None
    revert = L["revert"] and any(f in corr for f in slips_on)
    # schedule: facts early, corrections middle, slips after (>= 3 turns after first mention), closes last
    ev = []
    turn_of = {}
    t_fact = sorted(g.sample(range(1, max(3, S // 2)), len(facts)))
    if len(set(t_fact)) < len(facts):
        return None
    for f, t in zip(facts, t_fact):
        ev.append({"turn": t, "kind": "fact", "fact": f, "value": first[f], "value_text": text(f, first[f])})
        turn_of[f] = t
    t_close = list(range(S - len(closes) + 1, S + 1))
    for f in corr:
        lo = turn_of[f] + 2
        hi = (t_close[closes.index(f)] - 1) if f in closes else S - 1
        if hi < lo:
            return None
        t = g.randint(lo, hi)
        ev.append({"turn": t, "kind": "correct", "fact": f, "value": new[f], "value_text": text(f, new[f]),
                   "old_text": text(f, first[f])})
        turn_of[("c", f)] = t
    for j, f in enumerate(slips_on):
        is_rev = revert and f in corr and j == next(i for i, x in enumerate(slips_on) if x in corr)
        if f in corr:
            lo = turn_of[("c", f)] + 2
            truth = new[f]
            wrong = first[f] if is_rev else draw(f, avoid=(first[f], new[f]))
        else:
            lo = turn_of[f] + 3
            truth = first[f]
            wrong = draw(f, avoid=(first[f],))
        hi = S - len(closes)
        if hi < lo:
            return None
        t = g.randint(lo, hi)
        ev.append({"turn": t, "kind": "slip", "fact": f, "value": truth, "truth_text": text(f, truth),
                   "wrong": wrong, "wrong_text": text(f, wrong), "revert": bool(is_rev)})
    for f, t in zip(closes, t_close):
        ev.append({"turn": t, "kind": "close", "fact": f, "expect": new[f], "expect_text": text(f, new[f]),
                   "old": first[f]})
    cvals = {}
    for c in canon:
        if c == "years":
            v = g.choice([x for x in range(14, 40) if x % 10])
            w = v + g.choice([-7, -4, 4, 7])
            if not w % 10 or w // 10 == v // 10 and False:
                return None
            cvals[c] = (v, w)
        else:
            v, w = g.sample(CANON[c]["pool"], 2)
            cvals[c] = (v, w)
    free = [t for t in range(2, S - len(closes) + 1)]
    tc = g.sample(free, len(canon))
    for c, t in zip(canon, tc):
        v, w = cvals[c]
        ev.append({"turn": t, "kind": "canon", "canon": c, "truth": v, "wrong": w,
                   "truth_text": (_num_words(v) if c == "years" else v),
                   "wrong_text": (_num_words(w) if c == "years" else w)})
    # at most 2 events in a turn; a close turn holds only the close
    per = {}
    for e in ev:
        per.setdefault(e["turn"], []).append(e)
    if any(len(v) > 2 for v in per.values()):
        return None
    if any(e["kind"] == "close" and len(per[e["turn"]]) > 1 for e in ev):
        return None
    # the same fact never has two events in one turn
    for v in per.values():
        fs = [e.get("fact") for e in v if e.get("fact")]
        if len(fs) != len(set(fs)):
            return None
    ev.sort(key=lambda e: (e["turn"], e["kind"]))
    turns = []
    fill = list(FILLER_L)
    g.shuffle(fill)
    for t in range(1, S + 1):
        parts = []
        if t == 1:
            parts.append(f"I set my bag down and take the empty seat at the table in the {room}.")
        for e in per.get(t, []):
            f = e.get("fact")
            if e["kind"] == "fact":
                parts.append(f"I should say, {FACTS[f]['say'].format(v=e['value_text'])}.")
            elif e["kind"] == "correct":
                parts.append(CORRECT.format(say=FACTS[f]["say"].format(v=e["value_text"]), old=e["old_text"]))
            elif e["kind"] == "slip":
                parts.append(FACTS[f]["slip"].format(v=e["wrong_text"]))
            elif e["kind"] == "canon":
                c = CANON[e["canon"]]
                parts.append(c["wrong"].format(w=e["wrong_text"], N0=npcs[0]))
            elif e["kind"] == "close":
                parts.append(FACTS[f]["close"])
        if not any(e["kind"] == "close" for e in per.get(t, [])):
            parts.append(fill[(t - 1) % len(fill)])
        turns.append(" ".join(parts))
    cards = []
    for c in canon:
        v = cvals[c][0]
        cards.append(CANON[c]["card"].format(v=(_num_words(v) if c == "years" else v), N0=npcs[0]))
    return {"sc": sc, "user": user, "npcs": npcs, "openers": openers, "room": room, "turns": turns,
            "events": ev, "canon_cards": cards, "alt": alt, "facts": facts, "implicit": bool(L.get("implicit"))}


def _listen_system(p: dict) -> str:
    user, sc, npcs = p["user"], p["sc"], p["npcs"]
    rule4 = (f"4. The household know the facts on this card by heart. If {user} gets one wrong, someone puts it right in "
             f"the same reply, naming the right fact. If {user} says something that does not match what {user} said "
             f"earlier, someone checks it in the same reply, naming what {user} said before. When {user} corrects "
             f"themselves, the correction stands from then on. When {user} relies on something they told you, repeat the "
             "detail back so they can check it.\n")
    if p.get("implicit"):
        rule4 = ("4. The household know the facts on this card by heart, and they listen closely to their guests: "
                 f"what {user} tells them matters to them, and they remember it.\n")
    rules = (
        f"You play every character in an interactive scene except {user}, who is the user's.\n"
        "Rules:\n"
        "1. Each spoken line is its own line in the form 'Name: text' (no quotation marks). Narration lines carry no name.\n"
        f"2. {', '.join(npcs[:-1])} and {npcs[-1]} sit at the table with {user} all scene, and each of them speaks at "
        "least once in every reply.\n"
        "3. Each character opens every line they speak with their own word (listed below).\n"
        + rule4 +
        f"5. Never write {user}'s words, actions, thoughts or choices; stop where {user} can act. Keep each reply to a "
        "short chat turn.")
    card = (f"SCENE CARD: {sc['title']}\n{sc['intro']}\n{user} has just arrived and sits down in the {p['room']}.\n"
            "At the table:\n" + "\n".join(f"- {n}: every line {n} speaks starts with \"{p['openers'][n]}\"." for n in npcs)
            + "\nFacts of the house:\n" + "\n".join(f"- {c}" for c in p["canon_cards"]))
    return rules + "\n\n" + card


def _listen_checks(p: dict) -> list[dict]:
    out = []
    user, npcs = p["user"], p["npcs"]
    speech = "(?:" + "|".join(_line(n, p["openers"][n]) for n in npcs) + ")"
    for t in range(1, len(p["turns"]) + 1):
        out.append(_cast_check(t, user, {}, voices=p["openers"]))
    n = {"canon": 0, "slip": 0, "close": 0}
    for e in sorted(p["events"], key=lambda e: (e["turn"], e["kind"])):
        if e["kind"] == "canon":
            n["canon"] += 1
            v = e["truth"]
            a = _num_alt(v) if e["canon"] == "years" else rf"\b{re.escape(v)}\b"
            e["cid"] = f"canon{n['canon']}-{e['canon']}"
            out.append(_chk(e["cid"], [e["turn"]], 3.0, rf"{speech}[^\n]*{a}", scope="ic"))
        elif e["kind"] == "slip":
            n["slip"] += 1
            a = p["alt"](e["fact"], e["value"])
            e["cid"] = f"slip{n['slip']}-{e['fact']}" + ("-revert" if e["revert"] else "")
            out.append(_chk(e["cid"], [e["turn"]], 3.0,
                            rf"{speech}[^\n]*{a}", scope="ic"))
        elif e["kind"] == "close":
            n["close"] += 1
            a, old = p["alt"](e["fact"], e["expect"]), p["alt"](e["fact"], e["old"])
            e["cid"] = f"close{n['close']}-{e['fact']}"
            out.append(_chk(e["cid"], [e["turn"]], 3.0,
                            rf"\A(?![\s\S]*{speech}(?![^\n]*{a})[^\n]*{old})[\s\S]*{speech}[^\n]*{a}", scope="ic"))
    return out


def _listen_reference(p: dict, g) -> list[str]:
    per = {}
    for e in p["events"]:
        per.setdefault(e["turn"], []).append(e)
    out = []
    for t in range(1, len(p["turns"]) + 1):
        lines = [g.choice(NARRATION)]
        says = []
        for e in per.get(t, []):
            if e["kind"] == "canon":
                says.append(f"begging your pardon, it is {e['truth_text']}, not {e['wrong_text']}.")
            elif e["kind"] == "slip":
                says.append(f"you said {e['truth_text']} before, did you not?")
            elif e["kind"] == "close":
                says.append(f"of course, that is {e['expect_text']}, as you told us.")
        for i, nm in enumerate(p["npcs"]):
            lines.append(_ref_line(g, nm, p["openers"][nm], says[i] if i < len(says) else None))
        lines.append(g.choice(NARRATION))
        out.append("\n".join(lines))
    return out


def _listen_mutants(p: dict, ref: list[str], checks: list[dict]) -> dict:
    out = {}
    user, npcs = p["user"], p["npcs"]
    ids = {c["turns"][0]: [] for c in checks}
    for c in checks:
        ids[c["turns"][0]].append(c["id"])
    for e in p["events"]:
        t = e["turn"]
        if e["kind"] == "canon" and "canon-accepted" not in out:
            r = list(ref)
            r[t - 1] = r[t - 1].replace(f"begging your pardon, it is {e['truth_text']}, not {e['wrong_text']}.",
                                        f"quite right, {e['wrong_text']} it is.")
            out["canon-accepted"] = (r, [e["cid"]])
        if e["kind"] == "slip" and f"slip-accepted" not in out:
            r = list(ref)
            r[t - 1] = r[t - 1].replace(f"you said {e['truth_text']} before, did you not?", "how lovely.")
            out["slip-accepted"] = (r, [e["cid"]])
        if e["kind"] == "close" and "stale-after-correction" not in out:
            r = list(ref)
            old = next(x for x in p["events"] if x["kind"] == "correct" and x["fact"] == e["fact"])["old_text"]
            r[t - 1] = r[t - 1].replace(f"that is {e['expect_text']}, as you told us.", f"that is {old}, as you told us.")
            out["stale-after-correction"] = (r, [e["cid"]])
    quiet = [t for t in range(1, len(ref) + 1) if all(i.startswith("cast") for i in ids[t])]
    t = quiet[0] if quiet else 1
    r = list(ref)
    r[t - 1] = r[t - 1].replace(f"{npcs[1]}: {p['openers'][npcs[1]]}, ", f"{npcs[1]}: Honestly, ", 1)
    out["voice-broken"] = (r, [f"cast-t{t}"])
    r = list(ref)
    r[t - 1] = r[t - 1] + f"\n{user} nods and says it is fine."
    out["puppet"] = (r, [f"cast-t{t}"])
    r = list(ref)
    r[t - 1] = "\n".join(x for x in r[t - 1].splitlines() if not x.startswith(f"{npcs[2]}:"))
    out["present-silent"] = (r, [f"cast-t{t}"])
    return out


# ================================================================== plan / public API
@functools.lru_cache(maxsize=256)
def _plan_cached(variant: str, level: int, key, release_seed: str, case_id: str) -> dict:
    L = LEVELS[variant][level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, f"{variant}:{key or ''}")
    for attempt in range(2000):
        g = rng(seed, f"plan{attempt}")
        p = _know_attempt(g, L, level) if variant == "know" else _listen_attempt(g, L, level)
        if p is None:
            continue
        p.update(variant=variant, level=level, seed=seed, attempt=attempt)
        if variant == "know":
            if L["O"]:
                p["turns"].append(_know_ooc(p)["text"])
            p["system"] = _know_system(p)
            p["checks"] = _know_checks(p)
            p["reference"] = _know_reference(p, rng(seed, f"ref{attempt}"))
        else:
            p["system"] = _listen_system(p)
            p["checks"] = _listen_checks(p)
            p["reference"] = _listen_reference(p, rng(seed, f"ref{attempt}"))
        if grade_weighted(p["checks"], p["reference"]) == 1.0:
            return p
    raise RuntimeError(f"ensemble: no valid draw for {case_id} {variant} L{level}")


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    variant = gen.get("variant") or "know"
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
    """name -> (answer string, the check ids it must fail and only those)."""
    p = _plan(gen, release_seed, case_id)
    fn = _know_mutants if p["variant"] == "know" else _listen_mutants
    return {k: (join_turns(r), want) for k, (r, want) in fn(p, p["reference"], p["checks"]).items()}


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    out = [a for a, _ in mutants_named(gen, release_seed=release_seed, case_id=case_id).values()]
    n = len(mat["turns"])
    out.append(join_turns(["Sure, noted."] * n))
    return [a for a in dict.fromkeys(out) if grade(a, mat) < 1.0]


def _main(argv: list[str] | None = None) -> None:
    import json
    argv = sys.argv[1:] if argv is None else argv
    level = int(argv[0]) if argv else 3
    variant = argv[1] if len(argv) > 1 else "know"
    g = {"type": FAMILY, "level": level, "variant": variant}
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
