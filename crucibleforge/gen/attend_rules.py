"""ATTEND L7-L10 (host variant): the DIRECTIVE layer. No FAMILY here, so gen auto-discovery skips this module;
``attend.py`` dispatches to it for the levels whose row carries ``"rules": True``.

Same inn, same three characters and the same reply format as L1-L6, but the guest now lays down HOUSE RULES as
the evening goes on, and every graded turn is "were the rules in force at this moment obeyed in THIS reply".
Every check reads ONE reply (``turns: [t]``), so each turn stays one cheap, separate per-turn element
(``element: turn``); nothing here spans turns. Difficulty levers (all seeded, all knobs in the LEVELS rows):

* ``std``      rules in force at once (word limit, no questions, one line each, who speaks first, an end word
               for a character, a speech habit for a character) -- more of them at once
* ``revoke`` / ``change`` / ``reinstate``  a rule is scrapped, rewritten, or put back mid-conversation; the old
               signature must be gone from the reply (checked for a few turns after)
* ``conflict`` two rules clash on some turns; the guest states which one wins (and at L10 flips it later)
* ``gap``      the minimum number of turns between a rule and the first turn it binds (conditional rules)
* ``cond`` / ``latch``  rules conditional on the guest's message ("whenever I mention money ...") and on EARLIER
               content ("if I ever mention the road, from your next reply on ..."); the reply is also checked
               when the condition is NOT met (no over-application)
* ``distract`` turns that quote a rule the guest does NOT impose (a sign, a carter's grumble): its signature
               must not appear

A turn's obligations are rebuilt from the schedule (``obligations``); the reference is composed from the same
obligations, and a draw is kept only if the reference passes every check."""
from __future__ import annotations

import re

from ._stack_common import rng
from .ensemble import NAMES, NARRATION, OPENERS, TAG_LEAD, _Q, _STAGE, _SUBST, _TAGGED, _puppet

USERS = ["Wren", "Corin", "Maren", "Teo", "Lio", "Sunny"]
INNS = ["the Lamb at Skelby", "the Crown at Wyke", "the Ferry House at Durrow", "the Plough at Tarrant",
        "the Bell at Mallam"]
ROLES = ["keeps the door and the coat pegs", "cooks and minds the stove", "is the night porter"]
END_WORDS = ["friend", "traveller", "stranger", "pilgrim", "neighbour", "wanderer", "squire", "drover", "mistress"]
SAY_PHRASES = ["mind the third stair", "salt and sorrel", "by candle and coal", "the hearth keeps faith",
               "north star bright", "a bell for luck", "oats before dawn", "thrice and true"]
OPEN_PHRASES = ["By lamplight", "Hear ye", "Mark the hour", "Order please", "On my word", "Hush now"]
LONG_PHRASES = ["the early coach leaves from the bottom of the old yard",
                "the cellar key hangs on the third hook by the door",
                "the ferryman crosses at first light if the tide allows"]
SHORT = ["the fire draws well", "the lamps burn low", "the kettle is warm", "the yard is quiet", "the cart is late",
         "the stairs are steep", "the ale is fresh", "the dog is asleep", "the shutters hold fast", "the beams are old"]
ASKS = ["are you warm enough", "will you stay long", "do you take milk", "is the bed warm"]
VOICES = ["dear", "nothat", "noI", "nocontr"]
VOICE_TEXT = {"dear": "says 'dear' in every line", "nothat": "never says the word 'that'",
              "noI": "never says 'I' and talks around it", "nocontr": "never uses a contraction"}
# a VIOLATING line body for a speech habit (after the name tag) -- the L6 VRULES, same wording of the habits
VIOL = {"dear": r"(?![^\n]*\bdear\b)[^\n]*\S", "nothat": r"[^\n]*\bthat\b", "noI": r"[^\n]*\bI\b",
        "nocontr": r"[^\n]*\b\w+(?:n['’]t|['’](?:ll|re|ve|m|d))\b"}
XTOPICS = {   # extra triggers, only drawn by levels that set "xtopics" (L11), so L7-L10 draws are unchanged
    "the fire": ["The fire pops and throws a spark.", "Someone has banked the fire very high."],
    "the dog": ["A wet dog shakes itself by the door.", "The dog has stolen my glove."],
}
#: every trigger names its topic outright (panel 2026-11-14: "smells good from the kitchen", "settle what I owe" and
#: "a grey cat" were read as maybe-not-a-mention by three blind readers, and Haiku skipped the kitchen one)
TOPICS = {
    "the weather": ["Rain starts drumming on the windows.", "It has turned bitterly cold outside.",
                    "The wind is rising off the water."],
    "money": ["I had better count my coins before I forget.", "I must pay the money I owe before I leave.",
              "I wonder what the room will cost."],
    "my train": ["My train goes early tomorrow.", "I still have to catch my train in the morning."],
    "food": ["The food from the kitchen smells good.", "I could eat a whole loaf.", "I am hungry as a wolf."],
    "my bag": ["I left my bag by the door.", "My bag is heavier than it looks."],
    "the cat": ["The cat winds round my ankles.", "The cat has taken the best chair."],
    "the road": ["The long road in from the ford was a trial.", "I would not walk that road after dark."],
}
FILLER = ["what do people round here do of an evening?", "is it always this quiet midweek?",
          "who usually drinks here on a weeknight?", "how long have you all worked here?",
          "what is the best thing to see in the village?", "who built the fireplace?",
          "how old is the staircase?", "where does the cellar door lead?", "is the dog friendly?",
          "does the clock keep good time?", "who sits in the corner chair?", "what is carved over the mantel?"]
NUMW = {5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}
ORD = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh",
       "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth"]
CNT = ["zero", "one", "two", "three", "four", "five", "six", "seven"]
PADS = ["tonight", "again", "now", "still", "truly", "today", "indeed", "well", "surely"]
NUMS = ["I count {n} steps up to my room.", "I counted {n} lanterns on the way in.", "There are {n} barrels in the yard."]
ARRIVE = "I come in out of the evening and take the corner seat."
W_RULE = 2.0       # an active rule check (a plain, terse reply does not pass it by itself)
PASSIVE_SHARE = 0.25   # all the checks a terse well-formed reply passes (format, caps, bans, absences) share this of a turn


class Fail(Exception):
    pass


# ------------------------------------------------------------------ rule texts
def _lc(s: str) -> str:
    return s[0].lower() + s[1:]


def _desc(r: dict) -> str:
    k = r["kind"]
    return {"cap": "the word limit", "noq": "the no-questions rule", "oneline": "the one-line rule",
            "first": "the rule about who speaks first", "end": f"{r.get('who')}'s last-word rule",
            "endx": f"{r.get('who')}'s last-word rule and its exception",
            "voice": f"the rule that {r.get('who')} {VOICE_TEXT.get(r.get('vk'), '')}", "exact": f"{r.get('who')}'s line length",
            "tally": f"the {r.get('topic')} tally", "par": "the odd-and-even rule"}.get(k) or f"the rule about {r.get('topic')}"



def _core(r: dict, winner_text: str = "") -> str:
    k, who = r["kind"], r.get("who")
    t = {
        "cap": f"No spoken line may run past {NUMW.get(r.get('n'))} words; the opening word counts, the name tag does not.",
        "exact": f"Every line {who} speaks is exactly {NUMW.get(r.get('n'))} words long; the opening word counts, the name tag does not.",
        "tally": (f"Keep a running tally of how many of my LATER messages (not this one) mention {r.get('topic')}. "
                  f"{who} must say the word 'tally' and the running number, in words, in every reply, starting with the "
                  "reply to this message, which says zero; a later message that mentions it counts at once."),
        "par": (f"Whenever my message has a number written in digits: if it is odd, {r.get('odd')} speaks first; "
                f"if it is even, {r.get('even')} speaks first."),
        "noq": "Ask me no questions at all.",
        "oneline": "One line each, a single sentence per line.",
        "first": f"{who} always speaks first, before the others.",
        "endx": (f"Every line {who} speaks must end with the word '{r.get('w')}', except in a reply where "
                 f"{r.get('other')} speaks first: in that reply none of {who}'s lines may end with it."),
        "end": f"Every line {who} speaks must end with the word '{r.get('w')}'.",
        "voice": f"{who} {VOICE_TEXT.get(r.get('vk'), '')}.",
        "say": f"Whenever I mention {r.get('topic')}, {who} must say '{r.get('w')}' in that reply.",
        "open": f"Whenever I mention {r.get('topic')}, start the reply with the words '{r.get('w')}', before any name tag.",
        "ask": f"Whenever I mention {r.get('topic')}, {who} must ask me a question in that reply.",
        "firstc": f"Whenever I mention {r.get('topic')}, {who} speaks first.",
        "longsay": f"Whenever I mention {r.get('topic')}, {who} must say all of '{r.get('w')}'.",
        "latch": (f"If I ever mention {r.get('topic')}, then from your next reply on, every line {who} speaks "
                  f"must end with the word '{r.get('w')}'."),
    }[k]
    return t + (" " + winner_text if winner_text else "")


def _winner_text(r: dict, old: dict, winner: str) -> str:
    """The precedence sentence for the clash between cond rule ``r`` and the standing rule ``old``."""
    new = winner == "new"
    k = r["kind"]
    if k == "ask":
        return ("If that clashes with the no-questions rule, the question wins, but only for "
                f"{r['who']}'s own line.") if new else "If that clashes with the no-questions rule, the no-questions rule wins."
    if k == "firstc":
        return ("If that clashes with the rule about who speaks first, this new one wins." if new else
                "If that clashes with the rule about who speaks first, the old one wins.")
    return ("If that clashes with the word limit, the saying wins, for that line only." if new else
            "If that clashes with the word limit, the word limit wins.")


def _words_ok(n: int, text: str) -> bool:
    return len(text.split()) <= n


# ------------------------------------------------------------------ planner
def _attempt(g, L: dict) -> dict:
    T, gap = L["T"], L["gap"]
    user = g.choice(USERS)
    npcs = g.sample(NAMES, 3)
    openers = dict(zip(npcs, g.sample(OPENERS, 3)))
    words, says, opens, longs = (g.sample(x, len(x)) for x in (END_WORDS, SAY_PHRASES, OPEN_PHRASES, LONG_PHRASES))
    topics = g.sample(list(TOPICS) + (list(XTOPICS) if L.get("xtopics") else []), len(TOPICS) + (len(XTOPICS) if L.get("xtopics") else 0))
    items: dict[int, list] = {t: [] for t in range(1, T + 1)}
    rules: list[dict] = []
    taken_fire: set[int] = set()
    pend: dict[int, int] = {}          # triggers already promised to a turn (dense levels only)

    def put(t, kind, text):
        if not 1 <= t <= T:
            raise Fail
        items[t].append((kind, text))
        if len(items[t]) > 3 or sum(1 for k, _ in items[t] if k == "dir") > 2:
            raise Fail

    def decl(r, t, text):
        r["decl"] = (t, sum(1 for k, _ in items[t] if k == "dir"))
        put(t, "dir", text)

    def revoke_text(r):
        if r["kind"] in ("end", "say", "open", "latch") and g.random() < L.get("ordinal", 0):
            return (f"Scrap the <<ORD:{r['id']}>> rule I laid down (counting only new rules, in the order I gave them, "
                    "not scraps or rewrites); it no longer applies.")
        # a voice rule is named by its habit, and the speaker's other rules are said to stand (F-054 on the revoke
        # form: "Scrap Odo's manner of speech" reads as wiping Odo's last-word rule too)
        keep = f" Any other rule about {r['who']} that is in force now stays in force." if r["kind"] == "voice" else ""
        return g.choice(["Scrap ", "Forget "]) + _desc(r) + "; it no longer applies." + keep

    def mk(kind, **kw):
        r = {"id": f"r{len(rules) + 1}", "kind": kind, "spans": [], "fires": [], "dead": [], **kw}
        r["lin"] = kw.get("lin") or r["id"]
        rules.append(r)
        return r

    def pick(lo, hi):
        """A turn in lo..hi that still has room for one more directive."""
        pool = [t for t in range(max(1, lo), min(T, hi) + 1)
                if sum(1 for k, _ in items[t] if k == "dir") < 2 and len(items[t]) < 3]
        if not pool:
            raise Fail
        return g.choice(pool)

    def fire_turns(lo, hi, n):
        pool = [t for t in range(lo, hi + 1) if t not in taken_fire and len(items[t]) < 3]
        if share:                       # dense levels: a turn may trigger several rules at once
            pool = [t for t in range(lo, hi + 1) if len(items[t]) + pend.get(t, 0) < 3
                    and (t not in taken_fire or g.random() < share)]
        if len(pool) < n:
            raise Fail
        ts = sorted(g.sample(pool, n))
        taken_fire.update(ts)
        for t in ts:
            pend[t] = pend.get(t, 0) + 1
        return ts

    def trigger(topic, t):
        put(t, "trig", g.choice((TOPICS | XTOPICS)[topic]))

    early = L.get("early", T // 3 + 2)
    early_c = L.get("early_c", 6)     # declaration window of the conditional / latch / tally / par / clash rules
    share = L.get("share", 0)         # chance that an already-triggering turn is offered again to another rule
    nfire = L.get("nfire", 3)         # trigger turns of a tally / par rule
    # ---- conflict pairs (their standing member is reserved out of the std pool)
    par_on = bool(L.get("par"))
    ctypes = g.sample([c for c in ("ask", "firstc", "longsay") if not (par_on and c == "firstc")], L["conflict"])
    reserved = {"ask": "noq", "firstc": "first", "longsay": "cap"}
    cands = []
    for kind in ("cap", "noq", "oneline", "first"):
        if kind == "oneline" and g.random() < 0.5:
            continue
        if kind in [reserved[c] for c in ctypes] or (kind == "first" and par_on):
            continue
        if kind == "oneline" and "ask" in ctypes:
            continue
        if kind == "noq" and "ask" in ctypes:
            continue
        cands.append((kind, None))
    for n in npcs:
        cands.append(("voice", n))
    n_end = g.randint(1, max(1, min(3 - L["latch"] - L.get("endx", 0), L["std"] - 1)))
    std_pick = [("end", n) for n in g.sample(npcs, n_end)] + g.sample(cands, L["std"] - n_end)
    if sum(1 for k, _ in std_pick if k in ("noq", "oneline")) > 1:
        raise Fail
    std = []
    for kind, who in std_pick:
        kw = {}
        if kind == "cap":
            kw["n"] = g.choice(L.get("cap_ns", [8, 9, 10]))
        elif kind == "first":
            kw["who"] = g.choice(npcs)
        elif kind == "end":
            kw.update(who=who, w=words.pop())
        elif kind == "voice":
            kw.update(who=who, vk=g.choice(VOICES))
        std.append(mk(kind, role="std", **kw))
    for i, r in enumerate(std):
        a = 1 if i == 0 else pick(1, early)
        r["a"] = a
        r["spans"] = [[a, T + 1]]
        decl(r, a, "From now on: " + _core(r))
    # ---- L12: a last-word rule with an exception keyed on who speaks first in the same reply (interacts with every
    # rule that decides the first speaker: odd/even digits, a clash that names a first speaker, a standing "first")
    for i in range(L.get("endx", 0)):
        busy = {x.get("who") for x in rules if x["kind"] in ("end", "endx")}
        free = [n for n in npcs if n not in busy]
        if not free:
            raise Fail
        who = g.choice(free)
        r = mk("endx", role="std", who=who, w=words.pop(), other=g.choice([n for n in npcs if n != who]))
        r["a"] = pick(1, 4)
        r["spans"] = [[r["a"], T + 1]]
        decl(r, r["a"], "From now on: " + _core(r))
        std.append(r)
    # ---- exact line length, a running tally, odd/even speaking order (the precise, stateful rules)
    ex_left = list(npcs)
    fx = npcs        # who may carry fixed-content rules (say / ask / longsay); dense levels keep them off exact-length lines
    for i in range(L.get("exact", 0)):
        ex_who = g.choice(ex_left)          # one character per rule (L8 draws are unchanged)
        ex_left.remove(ex_who)
        if share:
            fx = [n for n in npcs if n in ex_left] or npcs
        r = mk("exact", role="std", who=ex_who, n=g.choice(L.get("exact_ns", [7, 8, 9])))
        r["a"] = pick(1, early)
        r["spans"] = [[r["a"], T + 1]]
        decl(r, r["a"], "From now on: " + _core(r))
    extra_cond = []
    for i in range(L.get("tally", 0)):
        r = mk("tally", role="cond", topic=topics.pop(),
               who=g.choice([n for n in npcs if n not in {x["who"] for x in rules if x["kind"] == "tally"}] or npcs))
        r["a"] = pick(1, early_c)
        r["spans"] = [[r["a"], T + 1]]
        decl(r, r["a"], _core(r))
        r["fires"] = fire_turns(r["a"] + 1, T, nfire)
        extra_cond.append(r)
    for i in range(L.get("par", 0)):
        a_, b_ = g.sample(npcs, 2)
        ex = [x["other"] for x in rules if x["kind"] == "endx"]
        if ex and ex[0] not in (a_, b_):           # L12: the digits decide whether the exception applies
            b_ = ex[0] if ex[0] != a_ else b_
            if g.random() < 0.5:
                a_, b_ = b_, a_
        r = mk("par", role="cond", odd=a_, even=b_)
        r["a"] = pick(1, early_c)
        r["spans"] = [[r["a"], T + 1]]
        decl(r, r["a"], _core(r))
        r["fires"] = fire_turns(r["a"] + 1, T, nfire)
        r["nums"] = {t: g.choice(range(3, 20)) for t in r["fires"]}
        extra_cond.append(r)
    # ---- changes (a rule rewritten mid-way) and revokes (scrapped), reinstatement
    changeable = [r for r in std if r["kind"] in ("cap", "end", "first", "voice", "endx")]
    g.shuffle(changeable)
    for r in changeable[:L["change"]]:
        z = pick(r["a"] + gap, T - 2)
        r["spans"][0][1] = z
        r["changed_at"] = z
        kw = {"lin": r["lin"], "role": "std", "a": z}
        if r["kind"] == "cap":
            kw["n"] = g.choice([x for x in tuple(range(L.get("cap_min", 8), 13)) if abs(x - r["n"]) >= 2])
        elif r["kind"] == "end":
            kw.update(who=r["who"], w=words.pop())
        elif r["kind"] == "first":
            kw["who"] = g.choice([n for n in npcs if n != r["who"]])
        elif r["kind"] == "endx":
            kw.update(who=r["who"], w=r["w"], other=next(n for n in npcs if n not in (r["who"], r["other"])))
        else:
            kw.update(who=r["who"], vk=g.choice([v for v in VOICES if v != r["vk"]]))
        nr = mk(r["kind"], **kw)
        nr["spans"] = [[z, T + 1]]
        r["succ"] = nr["id"]
        if r["kind"] == "voice":
            # name the habit being replaced: "manner of speech" read as wiping every rule about the speaker
            # (F-054: all strong models dropped the speaker's last-word rule here; a model author would agree)
            put(z, "dir", f"Scratch the rule that {r['who']} {VOICE_TEXT[r['vk']]}; here is the new version. "
                + _core(nr) + f" Any other rule about {r['who']} that is in force now stays in force.")
        else:
            put(z, "dir", f"Scratch {_desc(r)}; here is the new version. " + _core(nr)
                + (" The word itself stays the same." if r["kind"] == "endx" else ""))
    rest = [r for r in std if "changed_at" not in r and r["kind"] != "endx"]     # an exception rule is never scrapped
    g.shuffle(rest)
    revoked = []
    for r in rest[:L["revoke"]]:
        z = pick(r["a"] + gap, T - 3)
        r["spans"][0][1] = z
        r["revoked_at"] = z
        revoked.append(r)
        put(z, "dir", revoke_text(r))
    for r in revoked[:L["reinstate"]]:
        zr = pick(r["revoked_at"] + 3, T - 1)
        r["spans"].append([zr, T + 1])
        put(zr, "dir", f"Put {_desc(r)} back exactly as it was before.")
    # ---- conditional rules (say / open) and a latch, all on distinct topics
    cond_rules = []
    for i in range(L["cond"]):
        kind = ["say", "open"][i % 2]
        topic = topics.pop()
        kw = {"topic": topic}
        if kind == "say":
            kw.update(who=g.choice(fx), w=says.pop())
        else:
            kw["w"] = opens.pop()
        r = mk(kind, role="cond", **kw)
        a = pick(1, early_c)
        r["a"] = a
        r["spans"] = [[a, T + 1]]
        decl(r, a, _core(r))
        r["fires"] = fire_turns(a + gap, T, g.choice([2, 3]))
        cond_rules.append(r)
    latches = []
    for i in range(L["latch"]):
        topic = topics.pop()
        busy = {x.get("who") for x in rules if x["kind"] in ("latch", "end", "endx")}
        free = [n for n in npcs if n not in busy]
        if not free:
            raise Fail
        who = g.choice(free)
        r = mk("latch", role="latch", topic=topic, who=who, w=words.pop())
        a = pick(1, early_c)
        r["a"] = a
        r["spans"] = [[a, T + 1]]
        decl(r, a, _core(r))
        r["s"] = fire_turns(a + gap, T - 3, 1)[0]
        latches.append(r)
    # ---- clashes: standing member (declared early) + conditional member (with a stated winner)
    pairs = []
    for c in ctypes:
        topic = topics.pop()
        if c == "ask":
            old = mk("noq", role="pair")
            r = mk("ask", role="pair", topic=topic, who=g.choice(fx))
        elif c == "firstc":
            old = mk("first", role="pair", who=g.choice(npcs))
            r = mk("firstc", role="pair", topic=topic, who=g.choice([n for n in npcs if n != old["who"]]))
        else:
            old = mk("cap", role="pair", n=g.choice(L.get("cap_ns", [8, 9, 10])))
            r = mk("longsay", role="pair", topic=topic, who=g.choice(fx), w=longs.pop())
        old["a"] = pick(1, early_c)
        old["spans"] = [[old["a"], T + 1]]
        decl(old, old["a"], "From now on: " + _core(old))
        r["a"] = pick(old["a"] + 1, L.get("pair_hi", 9))
        r["spans"] = [[r["a"], T + 1]]
        r["winner"] = g.choice(["new", "old"])
        r["old"] = old["id"]
        decl(r, r["a"], _core(r, _winner_text(r, old, r["winner"])))
        r["fires"] = fire_turns(r["a"] + gap, T, 3 if L["flip"] else 2)
        pairs.append(r)
    for r in pairs[:L["flip"]]:
        lo, hi = r["fires"][0], r["fires"][-1]
        zf = pick(lo + 1, hi - 1)
        new_w = "old" if r["winner"] == "new" else "new"
        r["flip_at"], r["winner2"] = zf, new_w
        old = next(x for x in rules if x["id"] == r["old"])
        put(zf, "dir", f"Change of heart about {_desc(r)}: " + _winner_text(r, old, new_w).replace("If that clashes", "when it clashes"))
    # ---- revoke a conditional rule; its trigger is mentioned once more afterwards (a dead trigger)
    pool = [r for r in cond_rules + latches]
    g.shuffle(pool)
    for r in pool[:L.get("crevoke", 0)]:
        z = g.randint(r["a"] + gap + 1, T - 3)
        if r["fires"] and z <= r["fires"][0]:
            z = r["fires"][0] + 1
        if r["kind"] == "latch" and z <= r["s"] + 1:
            z = r["s"] + 2
        if z > T - 2:
            raise Fail
        r["spans"][0][1] = z
        r["revoked_at"] = z
        put(z, "dir", revoke_text(r))
        if r["kind"] != "latch":
            d = fire_turns(z + 1, T, 1)[0]
            r["dead"] = [d]
            trigger(r["topic"], d)
    for r in cond_rules + pairs + [x for x in extra_cond if x["kind"] == "tally"]:
        for t in r["fires"]:
            trigger(r["topic"], t)
    for r in extra_cond:
        if r["kind"] == "par":
            for t in r["fires"]:
                put(t, "trig", g.choice(NUMS).format(n=r["nums"][t]))
    for r in latches:
        trigger(r["topic"], r["s"])
    # ---- distractors: a rule the guest does NOT lay down
    distract = []
    dpool = [t for t in range(3, T + 1) if t not in taken_fire]
    if len(dpool) < L["distract"]:
        raise Fail
    for i, d in enumerate(sorted(g.sample(dpool, L["distract"]))):
        kind = ["end", "open", "say"][i % 3]
        who = g.choice(npcs)
        if kind == "end":
            w = words.pop()
            text = (f"A carter by the fire grumbles that every line {who} speaks should end with '{w}'. "
                    "It is not a rule of mine, pay him no mind.")
        elif kind == "open":
            w = opens.pop()
            text = f"A sign over the hearth reads 'Begin every reply with {w}'. It is not a rule of mine."
        else:
            w = says.pop()
            text = f"The ferryman mutters that {who} ought to say '{w}'. It is not a rule of mine, ignore him."
        distract.append({"turn": d, "kind": kind, "who": who, "w": w})
        put(d, "dist", text)
    # ---- the guest's messages (ordinal references to "the Nth rule I laid down" resolve here)
    rank = {rid: i + 1 for i, (_, rid) in enumerate(sorted((r["decl"], r["id"]) for r in rules if "decl" in r))}
    for t in items:
        items[t] = [(k, re.sub(r"<<ORD:(\w+)>>", lambda m: ORD[rank[m.group(1)] - 1], x)) for k, x in items[t]]
    # L11+: the extra topics (the dog, the fire) must not be mentioned by a filler question: "is the dog friendly?"
    # under a dog tally IS a mention (H1 blind-reader pass, 2026-11-14), and the schedule did not count it
    used = {r.get("topic") for r in rules}
    fill = [f for f in FILLER if not (L.get("xtopics") and (("the dog" in used and "dog" in f)
                                                           or ("the fire" in used and "fire" in f)))]
    turns = []
    for t in range(1, T + 1):
        its = items[t]
        order = {"dir": 0, "trig": 1, "dist": 2}
        parts = [x[1] for x in sorted(its, key=lambda x: order[x[0]])]
        if t == 1:
            parts.insert(0, ARRIVE)
        parts.append(f"{g.choice(npcs)}, {g.choice(fill)}")
        turns.append(" ".join(parts))
    p = {"user": user, "npcs": npcs, "openers": openers, "inn": g.choice(INNS), "turns": turns, "rules": rules,
         "distract": distract, "T": T, "ctypes": ctypes, "shrink": bool(share)}
    p["obl"] = {t: obligations(p, t) for t in range(1, T + 1)}
    pos = {t: sum(1 for o in p["obl"][t] if not o["type"].startswith("absent")) for t in p["obl"]}
    if min(v for t, v in pos.items() if t > early + 1) < L["min_pos"] or max(pos.values()) > L["max_pos"]:
        raise Fail
    return p


# ------------------------------------------------------------------ obligations
def _active(r: dict, t: int) -> bool:
    return any(a <= t < z for a, z in r["spans"])


def obligations(p: dict, t: int) -> list[dict]:
    """What the reply to turn ``t`` must (and must not) show, from the schedule alone."""
    rules = {r["id"]: r for r in p["rules"]}
    obs: list[dict] = []
    skip: set[str] = set()
    PAIR = ("ask", "firstc", "longsay")

    def add(typ, rid, **kw):
        obs.append({"type": typ, "rid": rid, "cid": f"{typ}-{rid}-t{t}", **kw})

    for r in p["rules"]:
        if r["kind"] in PAIR and _active(r, t) and t in r["fires"]:
            skip.add(r["old"])
    for r in p["rules"]:
        k = r["kind"]
        act = _active(r, t)
        if k in PAIR:
            old = rules[r["old"]]
            if act and t in r["fires"]:
                win = r.get("winner2") if r.get("flip_at") and t >= r["flip_at"] else r["winner"]
                if k == "ask":
                    if win == "new":
                        add("ask", r["id"], who=r["who"])
                    else:
                        add("noq", old["id"])
                elif k == "firstc":
                    add("first", r["id"] if win == "new" else old["id"], who=r["who"] if win == "new" else old["who"])
                elif win == "new":
                    add("say", r["id"], who=r["who"], w=r["w"])
                    add("cap", old["id"], n=old["n"], exempt=[r["who"]])
                else:
                    add("cap", old["id"], n=old["n"], exempt=[])
            continue
        if k == "exact":
            if act:
                add("exact", r["id"], who=r["who"], n=r["n"])
            continue
        if k == "tally":
            if act:
                add("tally", r["id"], who=r["who"], k=sum(1 for x in r["fires"] if x <= t))
            continue
        if k == "par":
            if act and t in r["fires"]:
                add("first", r["id"], who=r["odd"] if r["nums"][t] % 2 else r["even"])
            continue
        if k == "endx":
            if act and r["id"] not in skip:
                add("endx", r["id"], who=r["who"], w=r["w"], other=r["other"])
            elif not act and not r.get("succ") and any(z <= t <= z + 3 for a, z in r["spans"] if z <= p["T"]):
                add("absent_end", r["id"], who=r["who"], w=r["w"])   # a scrapped rule: the word must be gone
            continue
        if k in ("cap", "noq", "first", "end", "voice", "oneline"):
            if act:
                if r["id"] in skip:
                    continue
                kw = {x: r[x] for x in ("n", "who", "w", "vk") if x in r}
                if k == "cap":
                    kw["exempt"] = []
                add(k, r["id"], **kw)
            elif k == "end" and any(z <= t <= z + 3 for a, z in r["spans"] if z <= p["T"]):
                add("absent_end", r["id"], who=r["who"], w=r["w"])   # a changed or scrapped end word must be gone
        elif k in ("say", "open"):
            if act and t in r["fires"]:
                add(k, r["id"], **{x: r[x] for x in ("who", "w") if x in r})
            elif act or t in r["dead"] or (r.get("revoked_at") and r["revoked_at"] <= t <= r["revoked_at"] + 3):
                add("absent_" + k, r["id"], **{x: r[x] for x in ("who", "w") if x in r})
        elif k == "latch":
            if act and t > r["s"]:
                add("end", r["id"], who=r["who"], w=r["w"])
            elif (r["a"] <= t <= r["s"] and (act or t >= r["a"])) or (
                    r.get("revoked_at") and r["revoked_at"] <= t <= r["revoked_at"] + 3):
                add("absent_end", r["id"], who=r["who"], w=r["w"])
    for d in p["distract"]:
        if d["turn"] <= t <= d["turn"] + 2:
            add("absent_" + d["kind"], f"d{d['turn']}", who=d["who"], w=d["w"])
    # drop an absence check that contradicts a live obligation on the same signature
    live_end = {(o["who"], o["w"]) for o in obs if o["type"] in ("end", "endx")}
    live_phr = {o["w"] for o in obs if o["type"] in ("say", "open")}
    seen, out = set(), []
    for o in obs:
        if o["type"] == "absent_end" and (o["who"], o["w"]) in live_end:
            continue
        if o["type"] in ("absent_say", "absent_open") and o["w"] in live_phr:
            continue
        if o["cid"] not in seen:
            seen.add(o["cid"])
            out.append(o)
    return out


# ------------------------------------------------------------------ checks
#: ensemble's tag start, also tolerating a leading quote / dash / bullet / '>' before the name ('Nell: or - Odo:),
#: which the plain form missed (a false pass found on session_state, 2026-11-14); L7+ only, L1-L6 keep _TAGGED
#: an opening phrase ('Hear ye') may sit on the first speaker's line before the tag ("Hear ye Tobin: ..."): the rule says
#: "start the reply with the words ..., before any name tag", and the panel's readers split on own line vs prefix
_OPEN_PRE = r"(?:(?:" + "|".join(r"[ \t]+".join(re.escape(x) for x in w.split()) for w in OPEN_PHRASES) + r")[,.!:;\u2014-]*[ \t]+)?"
_TAGGED_X = r"^[ \t]*" + _OPEN_PRE + TAG_LEAD + _TAGGED[len(r"^[ \t]*"):]


def _tagged(name: str) -> str:
    return _TAGGED_X.format(n=re.escape(name))


def _tagany(names) -> str:
    return _TAGGED_X.format(n="(?:" + "|".join(re.escape(n) for n in names) + ")")


def _phr(w: str) -> str:
    return r"\s+".join(re.escape(x) for x in w.split())


def _pre(p: dict) -> str:
    """Every rule check first needs a real in-scene reply (all three present, tagged), so an empty or
    one-word answer earns nothing from the rules that only forbid."""
    return rf"\A{_SUBST}" + "".join(rf"(?=[\s\S]*{_tagged(n)})" for n in p["npcs"])


def pattern(p: dict, o: dict) -> str:
    typ, npcs = o["type"], p["npcs"]
    pre = _pre(p)
    if typ == "cap":
        names = [n for n in npcs if n not in o.get("exempt", [])]
        return pre + rf"(?![\s\S]*{_tagany(names)}(?:\S+[ \t]+){{{o['n']}}}\S)"
    if typ == "noq":
        return pre + r"(?![\s\S]*\?)"
    if typ == "oneline":
        pat = pre
        for n in npcs:
            tg = _tagged(n)
            pat += rf"(?![\s\S]*{tg}[^\n]*\n[\s\S]*{tg})"
            pat += (rf"(?![\s\S]*{tg}\S+[ \t][^\n]*?[.!?][\"'”’]?[ \t]+"
                    rf"[\"'“‘]?\w)")
        return pat
    if typ == "first":
        return pre + rf"(?:(?!{_tagany(npcs)})[^\n]*\n)*{_tagged(o['who'])}"
    if typ == "end":
        w = re.escape(o["w"])
        return pre + rf"(?![\s\S]*{_tagged(o['who'])}(?![^\n]*\b{w}[.!?\"'”’)*_]*[ \t]*$)[^\n]*\S)"
    if typ == "voice":
        return pre + rf"(?![\s\S]*{_tagged(o['who'])}{VIOL[o['vk']]})"
    if typ == "endx":                         # the reply's own first speaker decides which half applies
        w, tg = re.escape(o["w"]), _tagged(o["who"])
        first_other = rf"(?=(?:(?!{_tagany(npcs)})[^\n]*\n)*{_tagged(o['other'])})"
        ends = rf"[^\n]*\b{w}[.!?\"'”’)*_]*[ \t]*$"
        return pre + rf"(?:{first_other}(?![\s\S]*{tg}{ends})|(?!{first_other})(?![\s\S]*{tg}(?!{ends})[^\n]*\S))"
    if typ == "exact":
        return pre + rf"(?![\s\S]*{_tagged(o['who'])}(?=\S)(?!(?:\S+[ \t]+){{{o['n'] - 1}}}\S+[ \t]*$)[^\n]*\S)"
    if typ == "tally":
        k = o["k"]
        return pre + rf"(?=[\s\S]*{_tagged(o['who'])}[^\n]*(?:\btally\b[^\n.;!?]{{0,40}}?\b(?:{k}|{CNT[k]})\b|\b(?:{k}|{CNT[k]})\b[^\n.;!?]{{0,40}}?\btally\b))"
    if typ == "say":
        return pre + rf"(?=[\s\S]*{_tagged(o['who'])}[^\n]*\b{_phr(o['w'])}\b)"
    if typ == "open":
        return pre + rf"[\s*_\"'(\[]*{_phr(o['w'])}\b"
    if typ == "ask":
        tg = _tagged(o["who"])
        return pre + rf"(?=[\s\S]*{tg}[^\n]*\?)(?![\s\S]*^(?!{tg})[^\n]*\?)"
    if typ == "absent_end":
        w = re.escape(o["w"])
        return pre + rf"(?![\s\S]*{_tagged(o['who'])}[^\n]*\b{w}[.!?\"'”’)*_]*[ \t]*$)"
    if typ == "absent_say":
        return pre + rf"(?![\s\S]*\b{_phr(o['w'])}\b)"
    if typ == "absent_open":
        return pre + rf"(?![\s*_\"'(\[]*{_phr(o['w'])}\b)"
    raise KeyError(typ)


def passive(o: dict) -> bool:
    """A rule a terse, one-sentence, question-free scene reply satisfies without reading the rules."""
    return (o["type"] in ("cap", "noq", "oneline") or o["type"].startswith("absent")
            or (o["type"] == "voice" and o["vk"] != "dear"))


def checks(p: dict) -> list[dict]:
    from .ensemble import _chk
    out = []
    for t in range(1, p["T"] + 1):
        act = [o for o in p["obl"][t] if not passive(o)]
        pas = [o for o in p["obl"][t] if passive(o)]
        wp = round(PASSIVE_SHARE / (1 - PASSIVE_SHARE) * W_RULE * len(act) / (len(pas) + 1), 4) if act else 1.0
        cast = rf"\A{_SUBST}{_puppet(p['user'])}(?![\s\S]*{_tagged(p['user'])})"
        for n in p["npcs"]:
            # voice is read through the same tag as presence (opening phrase + lead), so an off-voice line after
            # 'Hush now' cannot count the speaker present and escape the voice check (K's review of 3acb0fa)
            cast += (rf"(?=[\s\S]*{_tagged(n)})"
                     rf"(?![\s\S]*{_tagged(n)}(?!{_STAGE}{_Q}{re.escape(p['openers'][n])}\b)\S)")
        out.append(_chk(f"cast-t{t}", [t], wp, cast, scope="ic", group="constraint"))
        for o in p["obl"][t]:
            out.append(_chk(o["cid"], [t], wp if passive(o) else W_RULE, pattern(p, o), scope="ic", group="constraint"))
    return out


# ------------------------------------------------------------------ the reference reply
def compose(p: dict, t: int, g, violate: str | None = None, only: list | None = None, rot: int = 0) -> str:
    """One reply obeying every obligation of turn ``t``; ``violate`` = the cid of one obligation to break
    (used by the tests); ``only`` = use this obligation list instead (stale-state strategies)."""
    obs = p["obl"][t] if only is None else only
    npcs, op = p["npcs"], p["openers"]
    by = {o["cid"]: o for o in obs}
    bad = by.get(violate)

    def has(typ):
        return [o for o in obs if o["type"] == typ]
    order = list(npcs[rot:] + npcs[:rot])
    for o in has("first"):
        order = [o["who"]] + [n for n in npcs if n != o["who"]]
    if bad and bad["type"] == "first":
        order = [n for n in npcs if n != bad["who"]] + [bad["who"]]
    lines = []
    for o in has("open"):
        if not (bad and bad is o):
            lines.append(o["w"] + ".")
    lines.append(g.choice(NARRATION))
    for n in order:
        core = g.choice(SHORT)
        fixed = False
        punct = "."
        mid = tail = ""
        for o in has("say"):
            if o["who"] == n and not (bad and bad is o):
                core, fixed = o["w"], True
        for o in has("ask"):
            if o["who"] == n and not (bad and bad is o):
                core, punct, fixed = g.choice(ASKS), "?", True
        for o in has("tally"):
            if o["who"] == n:
                kk = o["k"] if not (bad and bad is o) else (o["k"] + 1) % 8
                mid = f", tally {CNT[kk]}"
        for o in has("voice"):
            if o["who"] == n and o["vk"] == "dear" and not (bad and bad is o):
                tail += ", dear"
        for o in has("end"):
            if o["who"] == n and not (bad and bad is o):
                tail += f", {o['w']}"
        for o in has("endx"):
            if o["who"] == n and ((order[0] != o["other"]) != bool(bad and bad is o)):
                tail += f", {o['w']}"
        if bad:
            bt = bad["type"]
            if bt == "voice" and bad["who"] == n:
                core = {"dear": core, "nothat": core + " and that is the truth", "noI": "I think " + core,
                        "nocontr": "it isn't late"}[bad["vk"]]
            if bt == "cap" and n == order[0]:
                core += " and the lamps burn low and the kettle is warm and the stairs are steep and the cart is late"
            if bt == "absent_end" and bad["who"] == n:
                tail += f", {bad['w']}"
            if bt == "absent_say" and n == order[0]:
                core = bad["w"]
        pad = ""
        for o in has("exact"):
            if o["who"] == n:
                have = len(f"{op[n]}, {core}{mid}{tail}{punct}".split())
                while p.get("shrink") and not fixed and have > o["n"] and len(core.split()) > 1:
                    core = core.rsplit(" ", 1)[0]       # dense levels: trim the filler so crowded lines still fit
                    have = len(f"{op[n]}, {core}{mid}{tail}{punct}".split())
                k_ = o["n"] - have + (1 if bad and bad is o else 0)
                pad = (" " + " ".join(PADS[:k_])) if k_ > 0 else ""
        line = f"{n}: {op[n]}, {core}{pad}{mid}{tail}{punct}"
        if bad and bad["type"] == "noq" and n == order[0]:
            line = f"{n}: {op[n]}, {core}{tail}, will you stay long?"
        if bad and bad["type"] == "oneline" and n == order[0]:
            line += f"\n{n}: {op[n]}, {g.choice(SHORT)}{tail}."
        lines.append(line)
    if bad and bad["type"] == "absent_open":
        lines.insert(0, bad["w"] + ".")
    return "\n".join(lines)


def reference(p: dict, g) -> list[str]:
    return [compose(p, t, g) for t in range(1, p["T"] + 1)]


# ------------------------------------------------------------------ system prompt
def system(p: dict) -> str:
    user, npcs = p["user"], p["npcs"]
    names = ", ".join(npcs[:-1]) + " and " + npcs[-1]
    words = "; ".join(f"{n}: \"{w}\"" for n, w in p["openers"].items())
    lines = [
        f"You play every character in an interactive scene except {user}, the guest, who is the user's.",
        "Rules:",
        "1. Each spoken line is its own line in the form 'Name: text' (no quotation marks). Narration lines carry no name.",
        f"2. {names} are in the common room the whole time, and each of them speaks at least once in every reply.",
        f"3. Each character opens every line they speak with their own word ({words}).",
        f"4. Never write {user}'s words, actions, thoughts or choices; stop where {user} can act. Keep each reply to a short chat turn.",
        f"5. {user} lays down house rules as the evening goes on. A rule binds the reply to the message that sets it "
        f"and every reply after, until {user} scraps or changes it. A rule that starts with a condition applies only "
        "to a reply whose trigger has happened. When two rules clash, the guest says which one wins. Describing a rule is not a mention of its topic.",
        "", f"Scene card: {p['inn']}, a wet autumn evening. {user} has come in for one night.",
    ]
    lines += [f"- {n} {r}." for n, r in zip(npcs, ROLES)]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ build
def build(seed: str, L: dict) -> dict:
    from ._stack_common import grade_weighted
    for attempt in range(3000):
        g = rng(seed, f"plan{attempt}")
        try:
            p = _attempt(g, L)
        except (Fail, IndexError, ValueError):
            continue
        p["attempt"] = attempt
        p["system"] = system(p)
        p["checks"] = checks(p)
        p["reference"] = reference(p, rng(seed, f"ref{attempt}"))
        if grade_weighted(p["checks"], p["reference"]) == 1.0:
            return p
    raise RuntimeError("attend rules: no valid draw")


def mutants(p: dict) -> dict:
    """{name: (replies, [cid that must fail])}: one broken obligation each (first turn that carries it)."""
    out, seen = {}, set()
    for t in range(1, p["T"] + 1):
        for o in p["obl"][t]:
            if o["type"] in seen:
                continue
            seen.add(o["type"])
            r = list(p["reference"])
            r[t - 1] = compose(p, t, rng(p["seed"], f"mut{t}{o['cid']}"), violate=o["cid"])
            out[f"{o['type']}-broken"] = (r, [o["cid"]])
    return out
