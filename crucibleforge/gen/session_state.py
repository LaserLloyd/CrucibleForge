"""session_state -- SESSION-STATE multi-turn memory / state / false-memory generator (G5).

A seed + level produce a scene card (system prompt) and 4-9 scripted USER turns in which the
user's own character changes a ledger (room, coins, pack, clock) with finished first-person
actions ("I pay Odo 4 marks for the oil flask and put it in my pack"). The last turn is an
out-of-character state check: lines A1..Ak ask for the room, the coins, the pack, the time
and for false-memory probes ("you said earlier the chart-shed key was blue, right?"). Every
scored answer depends ONLY on the scripted user turns and the card, never on what the model
wrote in its earlier replies, so the model's free text cannot change the key.

Variants (``variant`` in the generator block): ``state`` (default) = prose scene;
``multi`` = three named NPC speakers who must each speak at least L tagged lines
("Name: text") in every scene reply, with no tagged line for the user; ``multi_hard`` (4.5 Main
chat, MH3) = the ``multi`` draw graded on the hard checks only (see ``is_hard_check``).

Grading: only programmatic checks (session_checks require_regex / word_range), weighted
share: answer lines x2, format rules x1 (no puppeting of the user, reply length, and in
``multi`` tag format / speaker minimums / no user line). Format rules also demand a real reply
(>= 20 words) so an empty or stub answer scores 0.0. The answer string is the assistant
replies joined with ``TURN_SEP`` (one per user turn).

LEVELS: user turns T / ledger dimensions / false-memory probes F / distractor turns D
(T includes the final state check; A = scripted actions):

  level  T  dims                 F  D
    1    4  room, coins          1  0
    2    5  + pack               1  0
    3    6  + clock              2  0   (false claim also planted in an earlier turn)
    4    7  all four             2  1
    5    8  all four             3  2
    6    9  all four             3  3

4.5 Core levels 7-10 add CALLBACK probes and (multi) PER-SPEAKER rules. ``_simulate`` records the ledger after every
action; the final OOC turn asks, after the ledger questions, about a past moment: ``coins_at`` / ``elapsed`` (derived
numbers, weight 3), ``where_when`` / ``held_then`` / ``order`` (weight 2), ``who_got`` (weak at the frontier, off the
ladder). From L8 one false-memory claim is a FALSE CALLBACK ("you said I was in the X when I bought the Y", weight 3)
planted after the purchase and denied with the true room. Every answer comes from the scripted user turns and the card.
Speaker rules (multi): ``presence`` (speaks only while the user is in a given room), ``exit`` (gone for good once an
item is given away, 30-70% into the scene), ``names_user`` (L10), plus ONE style rule per case (opener / ends / short /
asks, the RV1/RV2 voice regexes V7/V6/V5/V3: they only floor Haiku). Presence and exit weigh 2.

  level  T  F  false-cb  D  callbacks                                   rules (multi)
    7    9  2  0         2  coins_at, elapsed                           presence, style
    8   10  2  1         2  + where_when                                presence, exit, style
    9   11  3  1         3  + held_then                                 presence, exit, style
   10   12  3  1         4  + order                                     + names_user

Measured at L7-10 (30 seeds): state prompt 550-821, multi prompt 668-987, state checks 11/13/15/16, multi checks
18/21/23/25; reference answers 563-844 (state), 477-1210 (multi). max_tokens 6144. ``mutants_named`` names the
failure modes (final-state-for-callback, speaks-out-of-room, speaks-after-exit, style-broken, accepts-false-callback).

Measured sizes (30 seeds per level, case ids X0..X29, release_seed "r"; chars/4 token estimate;
tests/test_gen_session_state.py pins these bounds). System+turns tokens / reference-answer
tokens (the reference is a lower bound: a real reply is 30-220 words per turn) / checks:

  level   state prompt  state answer  state chk   multi prompt  multi answer  multi chk
    1      339-382       200-225        5          396-440       225-385       10
    2      368-411       275-310        7          425-470       305-505       12
    3      437-487       355-390        9          495-545       390-645       14
    4      452-511       420-455        9          509-570       470-765       14
    5      504-559       490-530       10          562-617       550-890       15
    6      529-575       560-615       10          587-632       620-1010      15
"""
from __future__ import annotations

import copy
import sys
import functools
import re

from ._stack_common import TURN_SEP, cap_passive, grade_weighted, join_turns, rng, split_turns  # noqa: F401

#: pin of the shared module this family builds and grades from (turn split, weighted grade, rng): chains.revision()
#: digests only THIS file, so a change to _stack_common.py must change this line (the instance stamp moves with it;
#: tests/test_session_state_presence_20261114.py::test_stack_common_pin fails until it does). Same as attend/sceneshift.
_STACK_SHA = "d9ed8da25a93decf"

from .. import gen as _gen  # noqa: E402  (one seed derivation)

FAMILY = "session_state"
VARIANTS = ("state", "multi", "multi_hard")
# 4.5 Main (task 3c): ``multi_hard`` = the ``multi`` scene, unchanged draw for draw (same instances for the same key and
# case id), graded on the HARD checks only: the speaker rules (presence, exit), the callback answers and the false
# callback, and (since scrub pass 1, 2026-11-15) the ledger answers and the plain false-memory probes: every question
# the final turn asks is graded. The format rules that any tidy reply passes (puppeting, reply length, untagged speech,
# user line, speaker minimums, style) are not scored. The system prompt still
# states the 30-220 word range; nothing checks it (Fable wrote 221-231 words in 8 of 8 stored rows: a conformance
# artefact, CORE-TRIAGE).
HARD_VARIANT = {"multi_hard": "multi"}


#: the state answers every asked question is graded on (scrub pass 1, lead 2026-11-15: "13 questions asked, 6
#: graded"): the current place, money, pack and clock (A1-A4) and the plain false-memory probes (A10-A12), all keyed
#: from the scripted actions and the card. Proven on the stored rc3 replies of seven models before it was turned on.
LEDGER_ANSWERS = ("A-room", "A-coins", "A-time", "A-pack-has", "A-pack-no-extra")


def is_hard_check(cid: str) -> bool:
    return (cid in ("presence-in", "presence-out", "exit-before", "exit-after", "A-false-fcb") or cid.startswith("A-cb-")
            or cid in LEDGER_ANSWERS or cid.startswith("A-false-"))

LEVELS = {
    1: {"T": 4, "dims": ["room", "coins"], "F": 1, "D": 0, "plant": False},
    2: {"T": 5, "dims": ["room", "coins", "pack"], "F": 1, "D": 0, "plant": False},
    3: {"T": 6, "dims": ["room", "coins", "pack", "time"], "F": 2, "D": 0, "plant": True},
    4: {"T": 7, "dims": ["room", "coins", "pack", "time"], "F": 2, "D": 1, "plant": True},
    5: {"T": 8, "dims": ["room", "coins", "pack", "time"], "F": 3, "D": 2, "plant": True},
    6: {"T": 9, "dims": ["room", "coins", "pack", "time"], "F": 3, "D": 3, "plant": True},
    # 4.5 Core levels: callback probes (state at a past moment) and per-speaker rules (multi). The kinds are ordered
    # by the 2b frontier evidence: derived numbers (coins_at, elapsed) first, plain "which item" recall (who_got) last.
    7: {"T": 9, "dims": ["room", "coins", "pack", "time"], "F": 2, "D": 2, "plant": True, "fcb": 0,
        "C": ["coins_at", "elapsed"], "rules": ["presence", "style"]},
    8: {"T": 10, "dims": ["room", "coins", "pack", "time"], "F": 2, "D": 2, "plant": True, "fcb": 1,
        "C": ["coins_at", "elapsed", "where_when"], "rules": ["presence", "exit", "style"]},
    9: {"T": 11, "dims": ["room", "coins", "pack", "time"], "F": 3, "D": 3, "plant": True, "fcb": 1,
        "C": ["coins_at", "elapsed", "where_when", "held_then"], "rules": ["presence", "exit", "style"]},
    10: {"T": 12, "dims": ["room", "coins", "pack", "time"], "F": 3, "D": 4, "plant": True, "fcb": 1,
         "C": ["coins_at", "elapsed", "where_when", "held_then", "order"],
         "rules": ["presence", "exit", "style", "names_user"]},
}
CB_KINDS = ("coins_at", "elapsed", "where_when", "held_then", "order", "who_got")
CB_WEIGHT = {"coins_at": 3, "elapsed": 3, "where_when": 2, "held_then": 2, "order": 2, "who_got": 2}
FCB_WEIGHT = 3
RULE_WEIGHT = {"presence": 2, "exit": 2}

# median reference-answer tokens (chars/4, 30 seeds; tests/test_gen_answer_tokens.py re-measures):
# the per-variant table is exact, ``answer_tokens`` is the variant mean (what ``budget`` falls back to)
_ANSWER_TOKENS = {7: {'state': 583, 'multi': 615}, 8: {'state': 663, 'multi': 708}, 9: {'state': 739, 'multi': 727}, 10: {'state': 806, 'multi': 827},  # multi 7-10 re-measured 2026-11-14
                  1: {'state': 222, 'multi': 248}, 2: {'state': 295, 'multi': 483}, 3: {'state': 371, 'multi': 522}, 4: {'state': 442, 'multi': 494}, 5: {'state': 515, 'multi': 584}, 6: {'state': 583, 'multi': 971}}
for _lv, _row in LEVELS.items():
    _row["answer_tokens"] = round(sum(_ANSWER_TOKENS[_lv].values()) / len(_ANSWER_TOKENS[_lv]))
    # multi_hard is the multi draw, so its reference answer is the multi one (the variant mean stays state/multi)
    _row["answer_tokens_by_variant"] = {**_ANSWER_TOKENS[_lv], "multi_hard": _ANSWER_TOKENS[_lv]["multi"]}


_SUBST = r"(?=\s*(?:\S+\s+){19}\S)"          # >= 20 words in the reply
REPLY_MIN, REPLY_MAX = 30, 220

SCENES = [
    {"title": "Saltmarsh ferry landing", "cur": "marks", "key": "chart-shed key",
     "intro": "A grey landing on a tidal river: one jetty, a row of huts and a bell that rings when the ferry is due.",
     "rooms": ["ticket hut", "jetty", "waiting room", "chart shed", "lamp tower", "tea stall"],
     "roles": ["the ferryman", "the ticket clerk", "the tea seller"],
     "items": ["brass whistle", "rope coil", "tin of biscuits", "paper map", "oil flask", "wool scarf",
               "pocket compass", "tide table"]},
    {"title": "Hollin night market", "cur": "pennies", "key": "stall-cage key",
     "intro": "A lantern-lit market under a stone bridge, where stalls open after dark and close at the first bell.",
     "rooms": ["fish stall", "spice row", "clock tower", "well square", "lantern alley", "bridge steps"],
     "roles": ["the fishmonger", "the spice trader", "the clockmaker"],
     "items": ["glass bead", "bag of cloves", "tin whistle", "linen apron", "candle stub", "copper ring",
               "dried plum", "paper fan"]},
    {"title": "Fennick coaching inn", "cur": "shillings", "key": "cellar key",
     "intro": "A low inn at a crossroads, warm and crowded, with a yard full of waiting carts.",
     "rooms": ["taproom", "cellar", "stable yard", "upstairs landing", "kitchen", "garden gate"],
     "roles": ["the landlord", "the cook", "the stable hand"],
     "items": ["horn spoon", "wax seal", "bar of soap", "pewter cup", "leather strap", "sack of oats",
               "small lantern", "ball of twine"]},
    {"title": "Greywater observatory", "cur": "crowns", "key": "dome key",
     "intro": "A hilltop observatory with a round dome, a cluttered workshop and a roof terrace open to the stars.",
     "rooms": ["dome", "library", "workshop", "roof terrace", "boiler room", "gatehouse"],
     "roles": ["the astronomer", "the mechanic", "the gatekeeper"],
     "items": ["brass lens", "star chart", "oil rag", "spare fuse", "silver pencil", "wool blanket",
               "thermos flask", "set of callipers"]},
]
NAMES = ["Odo", "Marta", "Tobin", "Ilse", "Perrin", "Wick", "Nell", "Garrick", "Sable", "Hobb", "Dara", "Fenn"]
USERS = ["Wren", "Corin", "Maren", "Teo", "Lio", "Sunny"]
COLOURS = ["red", "blue", "green", "brass", "black", "white", "yellow", "grey"]
ACTIVITIES = ["sorting rope", "reading the notice board", "mending a net", "watching the water",
              "helping stack crates", "oiling a hinge"]
NARRATION = [
    "A cold draught moves through the room and the lamps flicker.", "Somewhere nearby a door creaks and settles.",
    "The floorboards are worn smooth by years of boots.", "A faint smell of tar and woodsmoke hangs in the air.",
    "Outside, a bell rings twice and then goes quiet.", "Dust turns slowly in a bar of pale light.",
    "A kettle ticks as it cools on a shelf.", "The sound of water is never far away.",
    "Someone has left a coat over the back of a chair.", "A clock on the wall runs a little slow.",
]
SPEECH = [
    "Mind the step, it is slick this time of day.", "We do not get many visitors in this weather.",
    "If you need anything, ask and I will see what I can find.", "That is a fair question, and I will answer it.",
    "Take your time, nothing here is going anywhere.", "I have kept this place tidy for years, so wipe your boots.",
    "You will find most things where you would expect them.", "Funny how quiet it gets once the crowd thins out.",
    "Keep your voice down, the walls are thinner than they look.", "Well, that is one way to go about it.",
]


def _fmt_time(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _prefix(i: int) -> str:
    return rf"^[ \t]*(?:[-*]\s*)?\**A{i}\**[ \t]*[:.)]\**"


def _chk(cid: str, turns: list[int], weight: float, **kw) -> dict:
    return {"id": cid, "fact": cid, "ftype": "fact" if weight > 1 else "format", "group": "continuity" if weight > 1 else "constraint",
            "turns": turns, "scope": kw.pop("scope", "all"), "weight": weight, "type": kw.pop("type", "require_regex"), **kw}


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in words)



# ------------------------------------------------------------------ callbacks and speaker rules (levels 7+)
OPENERS = ["Well", "Look", "Aye", "Right"]
ENDERS = ["friend", "mate", "captain"]
# lines for the styled NPC: they obey the rule they are written for (RV1/RV2 voice rules V3/V5/V6/V7)
SHORT_LINES = ["Mind the step.", "Take your time.", "Keep your voice down.", "Nothing here is going anywhere.",
               "Wipe your boots first.", "That is a fair question."]
ASK_LINES = ["Did you find what you came for?", "Is the weather turning again?", "Will you be staying long?",
             "Have you eaten yet today?", "Shall I put the kettle on?", "Do you know the way back?"]
# the RV1/RV2 speaker regexes, parametrised (V7 opener, V6/V10 ends-with, V5 seven-words, V3 only-questions)
# Each is the BAD-line pattern, read just after a speaker tag (a lookahead behind the >=20-word substance guard, so
# an empty or stub reply cannot pass a "never" rule: the same idiom as no-user-line).
STYLE_BAD = {
    "opener": lambda w: rf"(?!\W*{re.escape(w)}\b).*\S.*$",
    "ends": lambda w: rf"(?!.*\b{re.escape(w)}\W*$).*\S.*$",
    "short": lambda w: r"(?:.*?[ \t])?(?:[^\s.!?]+[ \t]+){7}[^\s.!?]+",
    "asks": lambda w: r".*?[A-Za-z0-9'\u2019)\]][.!]+(?=[\s\"\u201d\u2019]|$)",
}


#: what may stand before a speaker tag at a line start: a quote mark or a bullet (Grok s012 wrote 'Nell: and passed
#: presence-out, T's ruling 2026-11-14). ``**`` bold stays the tag's own wrapper below.
_TAG_LEAD = r"(?:[-*>\u2022'\"\u2018\u201c]+[ \t]*)?"


def _tag(name: str) -> str:
    """A speaker tag at the start of a line: ``Name:`` or ``**Name:**``, after an optional leading quote mark or
    bullet (``'Nell:``, ``- Nell:``, ``> **Nell:**``)."""
    return rf"^[ \t]*{_TAG_LEAD}(?:\*\*|__)?{re.escape(name)}(?:\*\*|__)?[ \t]*:"


def _act_phrase(a: dict) -> str | None:
    """A past-tense referring phrase that names exactly one action (item names are unique per scene)."""
    if a["k"] == "buy":
        return f"bought the {a['item']}"
    if a["k"] == "give":
        return f"gave the {a['item']} to {a['npc']}"
    return None


def _pick_callbacks(g, kinds, sim, act_turn, sc, cast, user):
    """Draw one callback per kind. Every answer comes from the scripted actions (never from model text); None when
    a kind has no valid candidate in this draw (the plan is re-drawn)."""
    acts, snaps, start, end = sim["acts"], sim["snaps"], sim["start"], sim["end"]
    n = len(acts)
    anchors = [i for i, a in enumerate(acts) if a["k"] in ("buy", "give")]
    out = []
    used = set()
    for kind in kinds:
        cands = []
        if kind == "where_when":
            for i in anchors:
                if acts[i]["k"] == "buy" and snaps[i]["room"] != end["room"] and any(a["k"] == "move" for a in acts[i + 1:]):
                    cands.append(i)
            if not cands:
                return None
            i = g.choice(cands)
            wrong = end["room"]
            out.append({"k": kind, "i": [i], "ans": snaps[i]["room"], "wrong": wrong,
                        "stem": f"Where was I when I bought the {acts[i]['item']}? (a place name)"})
        elif kind == "coins_at":
            for i in anchors:
                later = sum(1 for a in acts[i + 1:] if a["k"] in ("buy", "earn"))
                if later >= 2 and snaps[i]["coins"] != end["coins"]:
                    cands.append(i)
            if not cands:
                return None
            i = g.choice(cands)
            out.append({"k": kind, "i": [i], "ans": str(snaps[i]["coins"]), "wrong": str(end["coins"]),
                        "stem": f"How many {sc['cur']} did I have right after I {_act_phrase(acts[i])}? (a number only)"})
        elif kind == "who_got":
            gv = [i for i in anchors if acts[i]["k"] == "give"]
            if not gv:
                return None
            i = g.choice(gv)
            wrong = next(c for c in cast if c != acts[i]["npc"])
            out.append({"k": kind, "i": [i], "ans": acts[i]["npc"], "wrong": wrong,
                        "stem": f"Who did I give the {acts[i]['item']} to? (a name only)"})
        elif kind == "elapsed":
            for x in anchors:
                for y in anchors:
                    if x < y and sum(1 for a in acts[x + 1:y] if a["k"] == "wait") >= 2:
                        cands.append((x, y))
            if not cands:
                return None
            x, y = g.choice(cands)
            ans = snaps[y]["time"] - snaps[x]["time"]
            tot = end["time"] - start["time"]
            out.append({"k": kind, "i": [x, y], "ans": str(ans), "wrong": str(tot if tot != ans else ans + 5),
                        "stem": f"How many minutes passed between when I {_act_phrase(acts[x])} and when I "
                                f"{_act_phrase(acts[y])}? (a number only)"})
        elif kind == "held_then":
            for m_i, a in enumerate(acts):
                if a["k"] != "move" or a["room"] == start["room"]:
                    continue
                if sum(1 for b in acts if b["k"] == "move" and b["room"] == a["room"]) != 1:
                    continue
                for j in anchors:
                    if abs(j - m_i) <= 2 and j != m_i:
                        cands.append((m_i, j))
            if not cands:
                return None
            m_i, j = g.choice(cands)
            item = acts[j]["item"]
            ans = "yes" if item in snaps[m_i]["pack"] else "no"
            out.append({"k": kind, "i": [m_i, j], "ans": ans, "wrong": "no" if ans == "yes" else "yes",
                        "stem": f"Was the {item} in my pack when I went to the {acts[m_i]['room']}? Answer 'yes' or 'no'."})
        elif kind == "order":
            for x in anchors:
                for y in anchors:
                    if x != y and abs(act_turn[x] - act_turn[y]) >= 2:
                        cands.append((x, y))
            if not cands:
                return None
            x, y = g.choice(cands)
            first = 1 if act_turn[x] < act_turn[y] else 2
            out.append({"k": kind, "i": [x, y], "ans": str(first), "wrong": str(3 - first),
                        "stem": f"Which came first: (1) I {_act_phrase(acts[x])}, or (2) I {_act_phrase(acts[y])}? "
                                "Answer '1' or '2'."})
        else:
            raise ValueError(f"session_state: unknown callback kind {kind!r}")
    return out


def _pick_false_callback(g, sim, sc, act_turn, n_scene):
    """A claim about a past purchase; the purchase turn must leave a later turn to plant the claim in."""
    acts, snaps = sim["acts"], sim["snaps"]
    buys = [i for i, a in enumerate(acts) if a["k"] == "buy" and act_turn[i] < n_scene]
    if not buys:
        return None
    i = g.choice(buys)
    true = snaps[i]["room"]
    wrong = g.choice([r for r in sc["rooms"] if r != true])
    return {"k": "fcb", "i": i, "claim": f"you said I was in the {wrong} when I bought the {acts[i]['item']}",
            "truth": true,
            "stem": f"Did you say I was in the {wrong} when I bought the {acts[i]['item']}? "
                    "Answer 'yes' or 'no, <the room I was really in>'."}


def _pick_rules(g, cast, sim, act_turn, n_scene, kinds, sc, user, style=None):
    """Per-speaker rules for the multi variant, tied to the scripted state (room per turn, the turn of a give).
    None when the draw cannot satisfy the coupling constraints."""
    acts = sim["acts"]
    room_t, cur, ai = [], sim["start"]["room"], 0
    for t in range(1, n_scene + 1):
        while ai < len(acts) and act_turn[ai] <= t:
            if acts[ai]["k"] == "move":
                cur = acts[ai]["room"]
            ai += 1
        room_t.append(cur)
    rules = {"room_t": room_t}
    if "presence" in kinds:
        cand = [r for r in sorted(set(room_t)) if 2 <= room_t.count(r) <= n_scene - 2]
        if not cand:
            return None
        room = g.choice(cand)
        rules["presence"] = {"npc": cast[0], "room": room,
                             "in": [t for t in range(1, n_scene + 1) if room_t[t - 1] == room],
                             "out": [t for t in range(1, n_scene + 1) if room_t[t - 1] != room]}
    if "exit" in kinds:
        gv = [i for i, a in enumerate(acts) if a["k"] == "give" and 0.3 * n_scene <= act_turn[i] <= 0.7 * n_scene]
        if not gv:
            return None
        i = g.choice(gv)
        rules["exit"] = {"npc": cast[1], "item": acts[i]["item"], "t": act_turn[i]}
    if "style" in kinds:
        kind = style or g.choice(["opener", "ends", "short", "asks"])
        word = g.choice(OPENERS) if kind == "opener" else g.choice(ENDERS) if kind == "ends" else ""
        rules["style"] = {"npc": cast[2], "kind": kind, "word": word}
    if "names_user" in kinds:
        rules["names_user"] = {"npc": cast[0]}
    return rules


def _no_lines_to_the_departed(rules: dict, sim: dict, act_turn: list[int], turns: list[str],
                              partners: list[str]) -> None:
    """The exit character has no line once the user gives the exit item away, so no later scripted user line may
    address them (the same contradiction T removed for the presence character, 2026-11-14; scrub pass 1, 2026-11-15:
    001 turns 9 and 11 and 003 turn 11 still talked to the departed partner, drawn at random). Every action after
    the give that names them (a buy, a give, an earn) and every later distractor names another partner instead, a
    fixed choice (no draw), so a session where it never happens is byte-identical. In place on ``sim`` / ``turns``."""
    e = rules.get("exit")
    if not e:
        return
    gone = e["npc"]
    other = next((c for c in partners if c != gone), None)
    if other is None:
        return
    word = re.compile(rf"\b{re.escape(gone)}\b")
    acts = sim["acts"]
    gi = next(i for i, a in enumerate(acts) if a["k"] == "give" and a["item"] == e["item"])
    for i in range(gi + 1, len(acts)):
        a = acts[i]
        if a.get("npc") == gone or word.search(a["text"]):
            old_text = a["text"]
            a["text"] = word.sub(other, a["text"])
            if a.get("npc") == gone:
                a["npc"] = other
            k = act_turn[i] - 1                  # the turn that carries this action
            turns[k] = turns[k].replace(old_text, a["text"], 1)
    sim["bought"] = [(a["item"], a["price"], a["npc"]) for a in acts if a["k"] == "buy"]   # same order as drawn
    act_turns = set(act_turn)
    for k in range(e["t"], len(turns)):           # 0-based: the turns after the give's turn
        if (k + 1) not in act_turns and word.search(turns[k]):
            turns[k] = word.sub(other, turns[k])


def _speak_turns(rules: dict, npc: str, n_scene: int) -> list[int]:
    """The scene replies in which ``npc`` must speak (everyone else: every reply)."""
    ts = list(range(1, n_scene + 1))
    if rules.get("presence") and rules["presence"]["npc"] == npc:
        ts = list(rules["presence"]["in"])
    if rules.get("exit") and rules["exit"]["npc"] == npc:
        ts = [t for t in ts if t < rules["exit"]["t"]]
    return ts


def _rule_text(rules: dict, user: str) -> str:
    out = []
    if rules.get("presence"):
        p = rules["presence"]
        out.append(f"{p['npc']} works only in the {p['room']}: {p['npc']} speaks only in replies where {user} is in "
                   f"the {p['room']}, and has no lines in any other reply.")
    if rules.get("exit"):
        e = rules["exit"]
        out.append(f"When {user} gives away the {e['item']}, {e['npc']} leaves for good: from that reply on, "
                   f"{e['npc']} has no lines.")
    if rules.get("style"):
        st = rules["style"]
        n = st["npc"]
        out.append({"opener": f"Every line {n} speaks starts with the word '{st['word']}'.",
                    "ends": f"Every line {n} speaks ends with the word '{st['word']}'.",
                    "short": f"{n} speaks in short bursts: never eight or more words in a row without a . ! or ?.",
                    "asks": f"Every sentence {n} speaks is a question."}[st["kind"]])
    if rules.get("names_user"):
        n = rules["names_user"]["npc"]
        out.append(f"{n} says {user}'s name at least once in every reply where {n} speaks.")
    return " ".join(out)


def _styled_line(rules, npc, sp, g):
    """One spoken line for ``npc`` that obeys its style rule (if it has one)."""
    st = rules.get("style") if rules else None
    if st and st["npc"] == npc:
        if st["kind"] == "opener":
            return f"{st['word']}, {sp[0].lower() + sp[1:]}"
        if st["kind"] == "ends":
            return f"{sp.rstrip('.')}, {st['word']}."
        if st["kind"] == "short":
            return g.choice(SHORT_LINES)
        return g.choice(ASK_LINES)
    return sp


# ------------------------------------------------------------------ plan
def _simulate(g, sc: dict, level: int, dims: list[str], n_actions: int, user: str, cast: list[str],
              partners: list[str] | None = None) -> dict | None:
    """Draw a valid action script and the ledger it produces; None when a draw is unusable. ``partners`` = the cast
    a scripted action may name (default: the whole cast)."""
    prices = {it: g.randint(2, 9) for it in sc["items"]}
    room0 = g.choice(sc["rooms"])
    st = {"room": room0, "coins": g.randint(20, 40), "time": 60 * g.randint(8, 10) + 5 * g.randint(0, 11),
          "pack": g.sample(sc["items"], 2)}
    start = {**st, "pack": list(st["pack"])}
    need = ["move"]
    need += ["earn"] if "coins" in dims else []
    if "pack" in dims:
        need += ["buy", "give"]
    if "time" in dims:
        need += ["wait"] * (2 if level >= 7 else 1)       # callbacks (elapsed) need two waits inside one span
    if "coins" in dims and "pack" not in dims:
        need += ["spend"]
    kinds = list(need)
    pool = ["move", "wait"] if "time" in dims else ["move"]
    if "pack" in dims:
        pool += ["buy", "give"]
    pool += ["earn"]
    while len(kinds) < n_actions:
        kinds.append(g.choice(pool))
    g.shuffle(kinds)
    # a give needs an item in the pack: put a buy before the give when order would fail
    acts, bought, given = [], [], []
    npcs = partners or cast
    for k in kinds:
        if k == "move":
            r = g.choice([x for x in sc["rooms"] if x != st["room"]])
            acts.append({"k": "move", "room": r, "text": g.choice([f"I walk over to the {r}.", f"I head for the {r}.", f"I leave and go to the {r}."])})
            st["room"] = r
        elif k in ("buy", "spend"):
            cand = [x for x in sc["items"] if x not in st["pack"] and x not in given]
            if not cand:
                k = "earn"
            else:
                it = g.choice(cand)
                p = prices[it]
                if p > st["coins"]:
                    k = "earn"
                else:
                    n = g.choice(npcs)
                    st["coins"] -= p
                    st["pack"].append(it)
                    bought.append((it, p, n))
                    acts.append({"k": "buy", "item": it, "price": p, "npc": n,
                                 "text": f"I pay {n} {p} {sc['cur']} for the {it} and put it in my pack."})
                    continue
        if k == "give":
            if len(st["pack"]) <= 1:
                k = "earn"
            else:
                it = g.choice(st["pack"])
                n = g.choice(npcs)
                st["pack"].remove(it)
                given.append(it)
                acts.append({"k": "give", "item": it, "npc": n, "text": f"I give the {it} to {n}."})
                continue
        if k == "wait":
            m = 5 * g.randint(2, 11)
            if st["time"] + m > 23 * 60:
                return None
            st["time"] += m
            acts.append({"k": "wait", "m": m, "text": f"I spend {m} minutes {g.choice(ACTIVITIES)}."})
        elif k == "earn":
            n = g.randint(3, 9)
            st["coins"] += n
            acts.append({"k": "earn", "n": n, "text": f"I earn {n} {sc['cur']} helping {g.choice(npcs)} sweep up, and pocket the money."})
    # every active dimension must actually change (so a stale answer is wrong)
    if st["room"] == start["room"] or st["coins"] == start["coins"]:
        return None
    if "pack" in dims and sorted(st["pack"]) == sorted(start["pack"]):
        return None
    if "time" in dims and st["time"] == start["time"]:
        return None
    return {"start": start, "end": st, "acts": acts, "bought": bought, "given": given, "prices": prices,
            "snaps": _snapshots(start, acts)}


def _snapshots(start: dict, acts: list[dict]) -> list[dict]:
    """The ledger state AFTER every action (room, coins, clock, pack). Computed from the scripted actions alone."""
    st = {"room": start["room"], "coins": start["coins"], "time": start["time"], "pack": list(start["pack"])}
    out = []
    for a in acts:
        k = a["k"]
        if k == "move":
            st["room"] = a["room"]
        elif k == "buy":
            st["coins"] -= a["price"]
            st["pack"].append(a["item"])
        elif k == "give":
            st["pack"].remove(a["item"])
        elif k == "wait":
            st["time"] += a["m"]
        elif k == "earn":
            st["coins"] += a["n"]
        out.append({"room": st["room"], "coins": st["coins"], "time": st["time"], "pack": list(st["pack"])})
    return out


@functools.lru_cache(maxsize=256)
def _plan_cached(level: int, variant: str, key, release_seed: str, case_id: str, opts: tuple = (None, None)) -> dict:
    L = LEVELS[level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, key)
    for attempt in range(60 if level < 7 else 600):      # levels 7+ couple callbacks and rules to the draw
        g = rng(seed, f"plan{attempt}")
        sc = g.choice(SCENES)
        user = g.choice(USERS)
        cast = g.sample(NAMES, 3)
        dims = L["dims"]
        n_scene = L["T"] - 1                      # scripted turns before the final check
        n_act_turns = n_scene - L["D"]
        n_actions = n_act_turns + g.randint(0, 2)
        # the presence character (cast[0]) is a pure ABSENCE test: no scripted user action or remark names it, so
        # the user never addresses someone the rule says has no line (T's ruling 2026-11-14: 29 of 42 strong-model
        # presence-out failures answered a user line that named the absent character)
        partners = cast[1:] if variant == "multi" and "presence" in (L.get("rules") or []) else cast
        sim = _simulate(g, sc, level, dims, max(n_actions, len(dims) + 1), user, cast, partners)
        if sim is None or len(sim["acts"]) < n_act_turns:
            continue
        colour = g.choice(COLOURS)
        wrong_c = g.choice([c for c in COLOURS if c != colour])
        wrong_n = g.choice([n for n in NAMES if n not in cast])
        probes = []                                # false-memory probes, in order
        kinds = ["colour", "name"] + (["price"] if sim["bought"] and "pack" in dims else [])
        kinds += ["colour2"] if False else []
        order = [k for k in ["colour", "name", "price"] if k in kinds]
        g.shuffle(order)
        for k in order[:L["F"]]:
            if k == "colour":
                probes.append({"k": k, "claim": f"you said earlier that the {sc['key']} is {wrong_c}", "truth": colour,
                               "stem": f"Is it true you told me the {sc['key']} is {wrong_c}? Answer 'yes' or 'no, <the real colour>'."})
            elif k == "name":
                probes.append({"k": k, "claim": f"you called {sc['roles'][0]} {wrong_n}", "truth": cast[0],
                               "stem": f"Did you call {sc['roles'][0]} {wrong_n}? Answer 'yes' or 'no, <the real name>'."})
            else:
                it, p, n = g.choice(sim["bought"])
                wp = p + g.choice([2, 3, 4])
                probes.append({"k": k, "claim": f"you quoted me {wp} {sc['cur']} for the {it}", "truth": str(p),
                               "stem": f"Did you quote me {wp} {sc['cur']} for the {it}? Answer 'yes' or 'no, <the real price I paid>'."})
        if len(probes) < L["F"]:
            continue
        # scripted user turns
        acts = list(sim["acts"])
        turns: list[str] = []
        per = [1] * n_act_turns
        for _ in range(len(acts) - n_act_turns):
            per[g.randrange(n_act_turns)] += 1
        i = 0
        action_texts = []
        for c in per:
            action_texts.append(" Then ".join(a["text"] for a in acts[i:i + c]))
            i += c
        slots = list(range(n_scene))
        dist_slots = sorted(g.sample(slots, L["D"])) if L["D"] else []
        act_iter = iter(action_texts)
        distract = [f"I read the notice board: 'Next sailing leaves at {_fmt_time(g.randint(14, 22) * 60 + 5 * g.randint(0, 11))}, fare {g.randint(2, 9)} {sc['cur']}'. I do nothing about it yet.",
                    f"{g.choice(partners)} mentions saving {g.randint(40, 90)} {sc['cur']} for a new coat. I just nod and listen.",
                    f"I notice that the {g.choice(sc['rooms'])} door has been painted again. I leave it alone and keep looking around."]
        if L["D"] > len(distract):                   # level 10 only: a fourth distractor (no draw for levels 1-9)
            distract.append(f"I check the clock and tell {g.choice(partners)} I am in no hurry. I do not move.")
        g.shuffle(distract)
        for s_i in slots:
            turns.append(distract.pop() if s_i in dist_slots else next(act_iter))
        if L["plant"]:
            where = g.randrange(1, n_scene)
            turns[where] = f"Hang on, {probes[0]['claim']}, didn't you? Anyway. " + turns[where]
        cbs, rules = [], None
        if level >= 7:
            act_slots = [s_i for s_i in slots if s_i not in dist_slots]
            act_turn: list[int] = []
            for idx, c in enumerate(per):
                act_turn += [act_slots[idx] + 1] * c
            if variant == "multi":
                # the rules come first (their own seed stream, so no draw moves): the exit tells which later user
                # lines would still address the departed character (_no_lines_to_the_departed)
                rules = _pick_rules(rng(seed, f"rules{attempt}"), cast, sim, act_turn, n_scene, L["rules"], sc, user,
                                    opts[1])
                if rules is None:
                    continue
                _no_lines_to_the_departed(rules, sim, act_turn, turns, partners)
            g2 = rng(seed, f"cb{attempt}")
            cbs = _pick_callbacks(g2, list(opts[0] or L["C"]), sim, act_turn, sc, cast, user)
            if cbs is None:
                continue
            if L.get("fcb"):
                fcb = _pick_false_callback(g2, sim, sc, act_turn, n_scene)
                if fcb is None:
                    continue
                later = [t for t in range(act_turn[fcb["i"]], n_scene)]     # 0-based indices of turns after the purchase turn
                if later:
                    w = g2.choice(later)
                    turns[w] = f"Hang on, {fcb['claim']}, didn't you? Anyway. " + turns[w]
                probes = probes + [fcb]
        final = _final_turn(sc, dims, probes, user, cbs)
        turns.append(final["text"])
        plan = {"sc": sc, "user": user, "cast": cast, "colour": colour, "sim": sim, "probes": probes,
                "turns": turns, "final": final, "level": level, "variant": variant, "seed": seed,
                "attempt": attempt, "minlines": g.randint(1, 2), "cbs": cbs, "rules": rules}
        plan["system"] = _system(plan)
        plan["checks"] = _checks(plan)
        plan["reference"] = _reference(plan)
        if grade_weighted(plan["checks"], plan["reference"]) == 1.0:
            return plan
    raise RuntimeError(f"session_state: no valid draw for {case_id} level {level}")


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    variant = gen.get("variant", "state")
    # test hooks: ``callbacks`` (a list of kinds) and ``style`` (opener|ends|short|asks) override the level's draw
    cbs = tuple(gen["callbacks"]) if gen.get("callbacks") else None
    # the cached plan is shared: hand out a copy so no consumer can corrupt it
    plan = copy.deepcopy(_plan_cached(int(gen.get("level", 1)), HARD_VARIANT.get(variant, variant), gen.get("key"),
                                      release_seed, case_id, (cbs, gen.get("style"))))
    if variant in HARD_VARIANT:
        # same draw as the plain variant; only the check list narrows (the reference still scores 1.0 on it)
        plan["variant"], plan["variant_name"] = HARD_VARIANT[variant], variant
        plan["checks"] = [c for c in plan["checks"] if is_hard_check(c["id"])]
        if not plan["checks"] or grade_weighted(plan["checks"], plan["reference"]) != 1.0:
            raise RuntimeError(f"session_state: {case_id} level {plan['level']} has no hard checks")
    return plan


def _final_turn(sc: dict, dims: list[str], probes: list[dict], user: str, cbs: list[dict] | None = None) -> dict:
    qs: list[tuple[str, str]] = [("room", "Which place am I in right now?")]
    qs.append(("coins", f"How many {sc['cur']} do I have? (a number only)"))
    if "pack" in dims:
        qs.append(("pack", "What is in my pack? (items separated by commas)"))
    if "time" in dims:
        qs.append(("time", "What time does the clock show? (24-hour HH:MM)"))
    for j, c in enumerate(cbs or []):
        qs.append((f"cb{j}", c["stem"]))
    for p in probes:
        qs.append((p["k"], p["stem"]))
    lines = [f"A{i}: <answer to Q{i}>" for i in range(1, len(qs) + 1)]
    text = ("OOC: pause the scene for a quick state check. Use only the scene card and what I have told you "
            "I did; ignore anything else. Questions:\n"
            + "\n".join(f"Q{i}. {q}" for i, (_, q) in enumerate(qs, 1))
            + "\nAnswer on separate lines in exactly this form, nothing else on those lines:\n" + "\n".join(lines))
    return {"text": text, "qs": [k for k, _ in qs]}


def _system(plan: dict) -> str:
    sc, user, cast = plan["sc"], plan["user"], plan["cast"]
    sim = plan["sim"]["start"]
    rules = (f"You are the narrator and every non-player character in an interactive scene. The user plays {user}. "
             f"Never write {user}'s words, actions, thoughts or choices: narrate only the world and the other "
             f"characters, and stop where {user} can act. Each scene reply is {REPLY_MIN} to {REPLY_MAX} words. "
             "If a message starts with OOC, step out of the scene and answer exactly as asked.")
    if plan["variant"] == "multi":
        who = "Unless a character rule below says otherwise, in" if plan.get("rules") else "In"
        rules += (f" Write every spoken line as its own line in the form 'Name: text' (no quotation marks); narration "
                  f"lines carry no tag. {who} every scene reply each of {cast[0]}, {cast[1]} and {cast[2]} speaks at least "
                  f"{plan['minlines']} tagged line{'s' if plan['minlines'] > 1 else ''}. Never give {user} a tagged line.")
        if plan.get("rules"):
            rules += " Character rules: " + _rule_text(plan["rules"], user)
    card = (f"SCENE CARD: {sc['title']}\n{sc['intro']}\nPlaces: {', '.join(sc['rooms'])}.\nCurrency: {sc['cur']}.\n"
            f"Cast: " + "; ".join(f"{n} ({r})" for n, r in zip(cast, sc["roles"])) + ".\n"
            f"The {sc['key']} is {plan['colour']}.\n"
            f"START: {user} is in the {sim['room']} with {sim['coins']} {sc['cur']}; the clock reads {_fmt_time(sim['time'])}; "
            f"the pack holds: {', '.join(sim['pack'])}.")
    return rules + "\n\n" + card


def _cb_pattern(c: dict, i: int, sc: dict, cast: list[str]) -> str:
    k, ans = c["k"], c["ans"]
    if k in ("coins_at", "elapsed"):
        return rf"{_prefix(i)}[^\n\d]*\b{re.escape(ans)}\b[^\n\d]*$"
    if k == "where_when":
        others = [r for r in sc["rooms"] if r != ans]
        return rf"{_prefix(i)}(?![^\n]*\b(?:{_alt(others)})\b)[^\n]*\b{re.escape(ans)}\b"
    if k == "who_got":
        others = [n for n in cast if n != ans]
        return rf"{_prefix(i)}(?![^\n]*\b(?:{_alt(others)})\b)[^\n]*\b{re.escape(ans)}\b"
    if k == "held_then":
        return rf"{_prefix(i)}[ \t]*\**{ans}\b"
    return rf"{_prefix(i)}[ \t]*\**{ans}\b"            # order: '1' or '2'


def _checks(plan: dict) -> list[dict]:
    T = len(plan["turns"])
    mid = list(range(1, T))
    user, sc, sim, fin = plan["user"], plan["sc"], plan["sim"], plan["final"]
    out: list[dict] = []
    puppet = (rf"\A{_SUBST}(?![\s\S]*\b{re.escape(user)}(?:'s)?(?:\s+\w+ly)?\s+(?:says|said|nods|nodded|smiles|smiled|"
              r"laughs|laughed|asks|asked|replies|replied|whispers|whispered|decides|decided|agrees|agreed|feels|"
              r"felt|thinks|thought|sighs|sighed|shrugs|shrugged|gasps|gasped|walks|walked|reaches|reached|"
              r"grabs|grabbed|takes|took|opens|opened|breath|heart|voice)\b)")
    out.append(_chk("no-puppet", mid, 1, pattern=puppet))
    out.append(_chk("reply-length", mid, 1, type="word_range", min=REPLY_MIN, max=REPLY_MAX, tolerance=0.0))
    if plan["variant"] == "multi":
        R = plan.get("rules")
        ruled = {R[k]["npc"] for k in ("presence", "exit") if R and R.get(k)}
        for n in plan["cast"]:
            if n in ruled:
                continue
            out.append(_chk(f"speaks-{n}", mid, 1, scope=f"speaker:{n}", pattern=r"^\S", min_hits=plan["minlines"]))
        if R:
            sc_n = lambda n: f"speaker:{n}"        # noqa: E731
            if R.get("presence"):
                p = R["presence"]
                out.append(_chk("presence-in", p["in"], RULE_WEIGHT["presence"], scope=sc_n(p["npc"]),
                                pattern=r"^\S", min_hits=plan["minlines"]))
                out.append(_chk("presence-out", p["out"], RULE_WEIGHT["presence"],
                                pattern=rf"\A{_SUBST}(?![\s\S]*{_tag(p['npc'])})"))
            if R.get("exit"):
                e = R["exit"]
                before = [t for t in _speak_turns(R, e["npc"], len(mid))]
                after = [t for t in mid if t >= e["t"]]
                out.append(_chk("exit-before", before, RULE_WEIGHT["exit"], scope=sc_n(e["npc"]),
                                pattern=r"^\S", min_hits=plan["minlines"]))
                out.append(_chk("exit-after", after, RULE_WEIGHT["exit"],
                                pattern=rf"\A{_SUBST}(?![\s\S]*{_tag(e['npc'])})"))
            if R.get("style"):
                st = R["style"]
                out.append(_chk(f"style-{st['kind']}", mid, 1,
                                pattern=rf"\A{_SUBST}(?![\s\S]*{_tag(st['npc'])}[ \t]*(?:\*\*|__)?[ \t]*{STYLE_BAD[st['kind']](st['word'])})"))
            if R.get("names_user"):
                n = R["names_user"]["npc"]
                out.append(_chk("names-user", _speak_turns(R, n, len(mid)), 1, scope=sc_n(n),
                                pattern=rf"\b{re.escape(user)}\b"))
        out.append(_chk("no-untagged-speech", mid, 1, pattern=rf"\A{_SUBST}(?![\s\S]*^[ \t]*[\"“])"))
        out.append(_chk("no-user-line", mid, 1,
                        pattern=rf"\A{_SUBST}(?![\s\S]*{_tag(user)})"))
    fl = [T]
    qn = {k: i for i, k in enumerate(fin["qs"], 1)}
    # A1 room
    i = qn["room"]
    others = [r for r in sc["rooms"] if r != sim["end"]["room"]]
    out.append(_chk("A-room", fl, 2, pattern=rf"{_prefix(i)}(?![^\n]*\b(?:{_alt(others)})\b)[^\n]*\b{re.escape(sim['end']['room'])}\b"))
    i = qn["coins"]
    out.append(_chk("A-coins", fl, 2, pattern=rf"{_prefix(i)}[^\n\d]*\b{sim['end']['coins']}\b[^\n\d]*$"))
    if "pack" in qn:
        i = qn["pack"]
        look = "".join(rf"(?=[^\n]*\b{re.escape(it)}\b)" for it in sim["end"]["pack"])
        out.append(_chk("A-pack-has", fl, 2, pattern=rf"{_prefix(i)}{look}[^\n]*$"))
        extra = [it for it in sc["items"] if it not in sim["end"]["pack"]]
        out.append(_chk("A-pack-no-extra", fl, 2, pattern=rf"{_prefix(i)}(?![^\n]*\b(?:{_alt(extra)})\b)[^\n]*\S"))
    if "time" in qn:
        i = qn["time"]
        out.append(_chk("A-time", fl, 2, pattern=rf"{_prefix(i)}[^\n\d]*\b{_fmt_time(sim['end']['time'])}\b[^\n\d]*$"))
    for j, c in enumerate(plan.get("cbs") or []):
        i = qn[f"cb{j}"]
        out.append(_chk(f"A-cb-{c['k']}", fl, CB_WEIGHT[c["k"]], pattern=_cb_pattern(c, i, sc, plan["cast"])))
    # probes occupy the question numbers after the ledger questions, in order
    base = len(fin["qs"]) - len(plan["probes"])
    for j, p in enumerate(plan["probes"], 1):
        i = base + j
        out.append(_chk(f"A-false-{p['k']}", fl, FCB_WEIGHT if p["k"] == "fcb" else 2,
                        pattern=rf"{_prefix(i)}(?![ \t]*(?:\*\*)?yes\b)[^\n]*\b{re.escape(p['truth'])}\b"))
    # the "never do X" format rules pass for any generic reply: capped at 15% of the weight
    return cap_passive(out, {"no-puppet", "reply-length", "no-untagged-speech", "no-user-line"})


# ------------------------------------------------------------------ reference
def _reference(plan: dict) -> list[str]:
    g = rng(plan["seed"], f"ref{plan['attempt']}")
    sc, cast, sim = plan["sc"], plan["cast"], plan["sim"]
    T = len(plan["turns"])
    out = []
    for t in range(1, T):
        nar = g.sample(NARRATION, 3)
        if plan["variant"] == "multi":
            R = plan.get("rules")
            lines = [nar[0], nar[1]]
            for n in cast:
                if R and t not in _speak_turns(R, n, T - 1):
                    continue
                for _ in range(plan["minlines"]):
                    ln = _styled_line(R, n, g.choice(SPEECH), g)
                    if R and R.get("names_user") and R["names_user"]["npc"] == n:
                        ln = f"{plan['user']}, {ln[0].lower() + ln[1:]}"
                    lines.append(f"{n}: {ln}")
            lines.append(nar[2])
            out.append("\n".join(lines))
        else:
            sp = g.sample(SPEECH, 2)
            out.append(f"{nar[0]} {nar[1]} \"{sp[0]}\" says {cast[0]}. {nar[2]} \"{sp[1]}\" adds {cast[1]}, and waits.")
    fin = plan["final"]
    ans = []
    qn = fin["qs"]
    for i, k in enumerate(qn, 1):
        if k == "room":
            ans.append(f"A{i}: {sim['end']['room']}")
        elif k == "coins":
            ans.append(f"A{i}: {sim['end']['coins']}")
        elif k == "pack":
            ans.append(f"A{i}: {', '.join(sorted(sim['end']['pack']))}")
        elif k == "time":
            ans.append(f"A{i}: {_fmt_time(sim['end']['time'])}")
        elif k.startswith("cb"):
            ans.append(f"A{i}: {plan['cbs'][int(k[2:])]['ans']}")
        else:
            p = next(x for x in plan["probes"] if x["k"] == k)
            ans.append(f"A{i}: no, {p['truth']}")
    out.append("\n".join(ans))
    return out


# ------------------------------------------------------------------ public API
def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    plan = _plan(gen, release_seed, case_id)
    return {
        "id": case_id, "category": "rpdeep", "system": plan["system"], "history": [], "turns": plan["turns"],
        "temperature": 0.7, "top_p": 0.95, "max_tokens": 6144 if plan["level"] >= 7 else 1200, "user_name": plan["user"],
        "checks": plan["checks"], "judge_elements": [], "programmatic": True,
        "generator": dict(gen),
        "meta": {"family": FAMILY, "variant": plan.get("variant_name", plan["variant"]), "level": plan["level"], "n_turns": len(plan["turns"]),
                 "n_actions": len(plan["sim"]["acts"]), "n_probes": len(plan["probes"]),
                 **({"callbacks": [c["k"] for c in plan["cbs"]],
                     "rules": [k for k in ("presence", "exit", "style", "names_user") if (plan.get("rules") or {}).get(k)]}
                    if plan["level"] >= 7 else {}),
                 "n_checks": len(plan["checks"]),
                 "prompt_tokens_est": round((len(plan["system"]) + sum(len(x) for x in plan["turns"])) / 4),
                 "reference_tokens_est": round(sum(len(x) for x in plan["reference"]) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_plan(gen, release_seed, case_id)["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_weighted(materialized["checks"], split_turns(answer))


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return list(mutants_named(gen, release_seed=release_seed, case_id=case_id).values())


def mutants_named(gen: dict, *, release_seed: str, case_id: str) -> dict[str, str]:
    plan = _plan(gen, release_seed, case_id)
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    ref, user, sim = plan["reference"], plan["user"], plan["sim"]
    last = len(ref) - 1
    cands: list[list[str]] = []
    named: dict[str, list[str]] = {}

    def edit(turn: int, fn):
        r = list(ref)
        r[turn] = fn(ref[turn])
        cands.append(r)

    edit(last, lambda s: re.sub(r"(A2: )(\d+)", lambda m: f"{m.group(1)}{int(m.group(2)) + 1}", s))   # stale coins
    edit(last, lambda s: re.sub(r"(A1: ).*", rf"\g<1>{sim['start']['room']}", s))                    # start room
    edit(last, lambda s: re.sub(r"(A\d+): no, ", r"\1: yes, ", s))                                   # accepts the false memory
    edit(last, lambda s: "\n".join(x for x in s.splitlines() if not x.startswith("A2")))             # missing line
    edit(last, lambda s: re.sub(r"(A\d+: )(\d\d):(\d\d)", lambda m: f"{m.group(1)}{m.group(2)}:{(int(m.group(3)) + 5) % 60:02d}", s))
    if sim["given"]:
        edit(last, lambda s: re.sub(r"(A\d+: )([a-z][^\n]*,[^\n]*)", lambda m: m.group(1) + m.group(2) + ", " + sim["given"][0], s))
    edit(0, lambda s: s + f"\n{user} nods and says it is fine.")                                   # puppeting
    edit(0, lambda s: "Okay.")                                                                      # too short
    if plan["variant"] == "multi":
        edit(0, lambda s: "\n".join(x for x in s.splitlines() if not x.startswith(plan["cast"][0] + ":")))
        edit(0, lambda s: s + f"\n{user}: I agree.")
        edit(0, lambda s: s + '\n"Hello there," he says.')
    cands.append(["Sure, noted."] * len(ref))
    # level 7+: the callback / speaker-rule failure modes, each named (each costs its own check)
    cb_i = {c["k"]: j for j, c in enumerate(plan.get("cbs") or [])}
    qn = {k: i for i, k in enumerate(plan["final"]["qs"], 1)}

    def line_edit(turn: int, fn) -> list[str]:
        r = list(ref)
        r[turn] = fn(ref[turn])
        return r

    if plan.get("cbs"):
        def stale(s):
            for j, c in enumerate(plan["cbs"]):
                s = re.sub(rf"(?m)^(A{qn[f'cb{j}']}: ).*$", lambda m: m.group(1) + c["wrong"], s)
            return s
        named["final-state-for-callback"] = line_edit(last, stale)
        for k, j in cb_i.items():
            named[f"callback-{k}-wrong"] = line_edit(
                last, lambda s, j=j: re.sub(rf"(?m)^(A{qn[f'cb{j}']}: ).*$",
                                            lambda m: m.group(1) + plan["cbs"][j]["wrong"], s))
    R = plan.get("rules")
    if R:
        if R.get("presence"):
            p = R["presence"]
            if p["out"]:
                named["speaks-out-of-room"] = line_edit(p["out"][0] - 1, lambda s: s + f"\n{p['npc']}: I should not be here.")
        if R.get("exit"):
            e = R["exit"]
            named["speaks-after-exit"] = line_edit(e["t"] - 1, lambda s: s + f"\n{e['npc']}: One last word.")
        if R.get("style"):
            st = R["style"]
            bad = {"opener": "Anyway, mind the step, it is slick this time of day.",
                   "ends": "Mind the step, it is slick this time of day.",
                   "short": "Mind the step because it is slick this time of day and you will fall",
                   "asks": "Mind the step."}[st["kind"]]
            named["style-broken"] = line_edit(0, lambda s: re.sub(rf"(?m)^({re.escape(st['npc'])}: ).*$",
                                                                lambda m: m.group(1) + bad, s, count=1))
    fcb = [i for i, k in enumerate(plan["final"]["qs"], 1) if k == "fcb"]
    if fcb:
        named["accepts-false-callback"] = line_edit(last, lambda s: re.sub(rf"(?m)^(A{fcb[0]}: )no, ", r"\1yes, ", s))
    out: dict[str, str] = {}
    for n, c in list(named.items()) + [(f"m{i}", c) for i, c in enumerate(cands)]:
        ans = join_turns(c)
        if grade(ans, mat) < 1.0 and ans not in out.values():
            out[n] = ans
    return out


def _main(argv: list[str] | None = None) -> None:
    import json
    import sys
    argv = [FAMILY] + (sys.argv[1:] if argv is None else argv)
    level = int(argv[1]) if len(argv) > 1 else 3
    variant = argv[2] if len(argv) > 2 else "state"
    gen = {"type": FAMILY, "level": level, "variant": variant}
    m = materialize(gen, release_seed="4.5.0", case_id="SS-demo")
    print("SYSTEM:\n" + m["system"])
    for i, t in enumerate(m["turns"], 1):
        print(f"\n--- USER TURN {i} ---\n{t}")
    ref = reference(gen, release_seed="4.5.0", case_id="SS-demo")
    print("\n--- REFERENCE (last reply) ---\n" + ref.split("<<<TURN>>>")[-1])
    print("\n# meta:", json.dumps(m["meta"]))
    print("# grade(reference) =", grade(ref, m))


if __name__ == "__main__":
    _main()
