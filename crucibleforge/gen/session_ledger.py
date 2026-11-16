"""session_ledger -- H2 "keeps track of state over a long session": DERIVED and CONDITIONAL state (4.5, seg H2).

The session_state family (L10) asks mostly for state the user STATED (prices said aloud, minutes said aloud); strong
models recall that at 0.90+. This family keeps the same scene shape (a narrator voicing four named characters, scripted
first-person user turns, an out-of-character state check at the end) and moves the difficulty into state the model has
to DERIVE:

* posted prices on the scene card, a purchase says only "at today's price" (the number is never in the user turn);
* a CONDITIONAL surcharge on the card: from a stated clock time on, every price is S higher (per piece), and the clock
  itself is derived from the minutes the user says they spend (moving between places takes no time, the card says so);
* a COUNTED stock (tokens / sweets ...) bought k at a time at the per-piece price and handed out k at a time;
* RETRACTIONS: a purchase handed back for exactly what was paid, and a CORRECTION of earlier minutes ("it was 35, not
  20"), which moves the clock and every elapsed span across it;
* a DEBT repaid by an amount defined by an earlier turn ("exactly what I paid for the X": two turns combined);
* callbacks about past moments (coins after an action, what an item cost, minutes between two actions, where the user
  was) and false-memory probes whose wrong value is the tempting one (the posted price where the surcharge applied);
* a mid-session OOC checkpoint (level 3+), so state is read twice with a long gap.

Every key is computed from the scripted user turns and the card, never from the model's replies. No word or sentence
count is asked or graded: the length line is "Keep each scene reply brief", with no number in it.

Speaker rules (per turn, each turn its own check so a single slip costs one element):
* presence: cast[0] works only in one place and speaks only in replies where the user is there. No scripted user line
  ever names cast[0] (K's ruling on session_state L10, 2026-11-14: users addressing the absent character made the
  rule contestable).
* exit (level 2+): cast[1] leaves for good when the user gives away a named item; no scripted line names cast[1] from
  that turn on.

Where each check's requirement is stated (the "second reader" rule):
  presence-in-tN / exit-before-tN  "In every scene reply each of A, B, C and D speaks at least one tagged line" +
                                   "unless a character rule below says otherwise" + the rule sentence.
  presence-out-tN / exit-after-tN  the rule sentence ("has no lines in any other reply" / "from that reply on, X has
                                   no lines").
  A* / B* answers                  the OOC question text, which names the answer form ("a number only", HH:MM ...).
  surcharge, clock                 card sentences "From HH:MM on ..." and "Only the minutes I say I spend move the
                                   clock; going from place to place takes no time."
  returns                          the user turn says "gives me back exactly what I paid for it".
  false-memory probes              "Answer 'yes' or 'no, <...>'".

LEVELS (T = user turns incl. the OOC checks; band = intended field placement):

  level  T   mechanics                                                         mid OOC  band
    1    6   stated prices, 2 waits, presence                                  -        low   (8B class)
    2    9   + posted prices + surcharge, counted stock, one return, exit       -        mid
    3    12  + debt repaid "as much as I paid for X", one minute correction     t6       high  (aimed above the reference)
    4    15  + second return, second correction, more callbacks                 t7       top   (pack)
"""
from __future__ import annotations

import copy
import functools
import re
import sys

from ._stack_common import TURN_SEP, grade_weighted, join_turns, rng, split_turns  # noqa: F401
from .. import gen as _gen

#: pin of the shared module this family grades with (turn split, weighted grade, rng): chains.revision() digests only
#: THIS file, so a change to _stack_common.py must change this line (tests/test_gen_session_ledger.py checks it).
_STACK_SHA = "d9ed8da25a93decf"

FAMILY = "session_ledger"
VARIANTS = ()

LEVELS = {
    1: {"T": 6, "D": 0, "posted": False, "surcharge": False, "count": False, "returns": 0, "debt": False, "corr": 0,
        "exit": False, "mid": None, "waits": 2, "buys": 2, "cbs": ["coins_at", "where_when"], "probes": ["fcb"],
        "band": "low"},
    2: {"T": 9, "D": 1, "posted": True, "surcharge": True, "count": True, "returns": 1, "debt": False, "corr": 0,
        "exit": True, "mid": None, "waits": 3, "buys": 3, "cbs": ["paid_for", "coins_at", "where_when"],
        "probes": ["price", "fcb"], "band": "mid"},
    3: {"T": 12, "D": 1, "posted": True, "surcharge": True, "count": True, "returns": 1, "debt": True, "corr": 1,
        "exit": True, "mid": 6, "waits": 4, "buys": 3, "cbs": ["paid_for", "coins_at", "elapsed", "where_when"],
        "probes": ["price", "fcb"], "band": "high"},
    4: {"T": 15, "D": 1, "posted": True, "surcharge": True, "count": True, "returns": 2, "debt": True, "corr": 2,
        "exit": True, "mid": 7, "waits": 5, "buys": 4,
        "cbs": ["paid_for", "coins_at", "elapsed", "where_when", "count_at"], "probes": ["price", "fcb"],
        "band": "top"},
    # L5 = L4 + one INTERFERENCE line in every scene turn: a number of the same kind as a real change that changes
    # nothing for the user (a declined offer, someone else's purchase, a walk that would take N minutes, an item held
    # out and kept, a declined loan). The stored L10 rows lose most where such numbers sit near the real ones.
    5: {"T": 15, "D": 1, "posted": True, "surcharge": True, "count": True, "returns": 2, "debt": True, "corr": 2,
        "exit": True, "mid": 7, "waits": 5, "buys": 4,
        "cbs": ["paid_for", "coins_at", "elapsed", "where_when", "count_at"], "probes": ["price", "fcb"],
        "band": "top", "decoys": True},
}
# rough reference-answer token sizes (chars/4) for the engine's budget fallback; tests re-measure the bounds
for _lv, _row in LEVELS.items():
    _row["answer_tokens"] = {1: 330, 2: 560, 3: 820, 4: 1050, 5: 1100}[_lv]

PRESENCE_W = 2.0        # total weight of the presence rule, split over the turns it reads
EXIT_W = 2.0
MIN_WORDS = 15          # a scene reply must have substance for a "has no line" check to pass

SCENES = [
    {"title": "Saltmarsh ferry landing", "cur": "marks", "key": "chart-shed key",
     "intro": "A grey landing on a tidal river: one jetty, a row of huts and a bell that rings when the ferry is due.",
     "rooms": ["ticket hut", "jetty", "waiting room", "chart shed", "lamp tower", "tea stall"],
     "roles": ["the ferryman", "the ticket clerk", "the tea seller", "the net mender"],
     "items": ["brass whistle", "rope coil", "tin of biscuits", "paper map", "oil flask", "wool scarf",
               "pocket compass", "tide table"],
     "stock": ("ferry token", "ferry tokens"), "pocket": "coat pocket"},
    {"title": "Hollin night market", "cur": "pennies", "key": "stall-cage key",
     "intro": "A lantern-lit market under a stone bridge, where stalls open after dark and close at the first bell.",
     "rooms": ["fish stall", "spice row", "clock tower", "well square", "lantern alley", "bridge steps"],
     "roles": ["the fishmonger", "the spice trader", "the clockmaker", "the lamp lighter"],
     "items": ["glass bead", "bag of cloves", "tin whistle", "linen apron", "candle stub", "copper ring",
               "dried plum", "paper fan"],
     "stock": ("honey sweet", "honey sweets"), "pocket": "coat pocket"},
    {"title": "Fennick coaching inn", "cur": "shillings", "key": "cellar key",
     "intro": "A low inn at a crossroads, warm and crowded, with a yard full of waiting carts.",
     "rooms": ["taproom", "cellar", "stable yard", "upstairs landing", "kitchen", "garden gate"],
     "roles": ["the landlord", "the cook", "the stable hand", "the coach driver"],
     "items": ["horn spoon", "wax seal", "bar of soap", "pewter cup", "leather strap", "sack of oats",
               "small lantern", "ball of twine"],
     "stock": ("oat cake", "oat cakes"), "pocket": "coat pocket"},
    {"title": "Greywater observatory", "cur": "crowns", "key": "dome key",
     "intro": "A hilltop observatory with a round dome, a cluttered workshop and a roof terrace open to the stars.",
     "rooms": ["dome", "library", "workshop", "roof terrace", "boiler room", "gatehouse"],
     "roles": ["the astronomer", "the mechanic", "the gatekeeper", "the librarian"],
     "items": ["brass lens", "star chart", "oil rag", "spare fuse", "silver pencil", "wool blanket",
               "thermos flask", "set of callipers"],
     "stock": ("brass token", "brass tokens"), "pocket": "jacket pocket"},
]
NAMES = ["Odo", "Marta", "Tobin", "Ilse", "Perrin", "Wick", "Nell", "Garrick", "Sable", "Hobb", "Dara", "Fenn"]
USERS = ["Wren", "Corin", "Maren", "Teo", "Lio", "Sunny"]
COLOURS = ["red", "blue", "green", "brass", "black", "white", "yellow", "grey"]
ACTIVITIES = ["sorting rope", "coiling a hawser", "mending a net", "watching the water",
              "helping stack crates", "oiling a hinge", "sweeping the floor", "untangling a line"]
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
NUMWORDS = {1: "one", 2: "two", 3: "three", 4: "four"}


def _fmt_time(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _time_pat(m: int) -> str:
    h, mm = m // 60, m % 60
    return rf"(?:0?{h}|{h:02d}):{mm:02d}"


#: what may stand before an answer label or a speaker tag at a line start: a quote mark, a bullet, a '>'
_LEAD = r"(?:[-*>•'\"‘“]+[ \t]*)?"


def _prefix(lbl: str) -> str:
    return rf"^[ \t]*{_LEAD}(?:\*\*|__)?{lbl}(?:\*\*|__)?[ \t]*[:.)](?:\*\*|__)?"


def _tag(name: str) -> str:
    return rf"^[ \t]*{_LEAD}(?:\*\*|__)?{re.escape(name)}(?:\*\*|__)?[ \t]*:"


_SUBST = rf"(?=\s*(?:\S+\s+){{{MIN_WORDS - 1}}}\S)"


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in words)


def _chk(cid: str, turns: list[int], weight: float, pattern: str, **kw) -> dict:
    return {"id": cid, "fact": cid, "ftype": "fact", "group": "continuity", "turns": turns, "scope": kw.pop("scope", "all"),
            "weight": weight, "type": "require_regex", "pattern": pattern, **kw}


# ------------------------------------------------------------------ the action script and its ledger
def _run(start: dict, acts: list[dict], cutoff: int | None, sur: int, *, surcharge=True, returns=True, corrections=True,
         debt=True) -> list[dict]:
    """The ledger AFTER every action. The switches turn one mechanic off (the named mutants: a model that ignores it).
    Times are the TRUE times (corrections applied from the start); a purchase's price uses the true clock."""
    corr = {a["w"]: a["m2"] for a in acts if a["k"] == "corr"} if corrections else {}
    st = {"room": start["room"], "coins": start["coins"], "time": start["time"], "pack": list(start["pack"]),
          "count": start["count"], "owe": 0}
    paid: dict[str, int] = {}
    out = []
    for i, a in enumerate(acts):
        k = a["k"]
        if k == "move":
            st["room"] = a["room"]
        elif k == "wait":
            st["time"] += corr.get(i, a["m"])
        elif k == "buy":
            p = a["price"] if a.get("stated") else a["posted"] + (sur if surcharge and cutoff is not None
                                                                   and st["time"] >= cutoff else 0)
            st["coins"] -= p
            st["pack"].append(a["item"])
            paid[a["item"]] = p
        elif k == "buyn":
            each = a["unit"] + (sur if surcharge and cutoff is not None and st["time"] >= cutoff else 0)
            st["coins"] -= each * a["n"]
            st["count"] += a["n"]
            paid["#stock"] = each * a["n"]
        elif k == "givn":
            st["count"] -= a["n"]
        elif k == "give":
            st["pack"].remove(a["item"])
        elif k == "earn":
            st["coins"] += a["n"]
        elif k == "ret":
            if returns:
                st["coins"] += paid[a["item"]]
                st["pack"].remove(a["item"])
        elif k == "borrow":
            if debt:
                st["coins"] += a["n"]
                st["owe"] += a["n"]
        elif k == "repay":
            amt = paid[a["item"]] if a.get("item") else a["n"]
            if debt:
                st["coins"] -= amt
                st["owe"] -= amt
        out.append({**st, "pack": list(st["pack"]), "paid": dict(paid)})
    return out


def _believed_times(start: dict, acts: list[dict]) -> list[int]:
    """The clock as the user's turns state it at each action BEFORE any correction is read (a correction then fixes
    it). Used only to refuse draws where a correction would change an already-charged surcharge."""
    t, out = start["time"], []
    for a in acts:
        if a["k"] == "wait":
            t += a["m"]
        elif a["k"] == "corr":
            t += a["m2"] - acts[a["w"]]["m"]
        out.append(t)
    return out


def _draw_actions(g, L: dict, sc: dict) -> list[str] | None:
    """The kinds in script order, with the ordering constraints a valid ledger needs."""
    kinds = ["move", "move", "earn", "give"] + ["wait"] * L["waits"] + ["buy"] * L["buys"]
    if L["count"]:
        kinds += ["buyn", "givn"]
    kinds += ["ret"] * L["returns"]
    if L["debt"]:
        kinds += ["borrow", "repay"]
    kinds += ["corr"] * L["corr"]
    if L["T"] >= 12:
        kinds += ["move"]
    g.shuffle(kinds)
    return kinds


def _simulate(g, L: dict, sc: dict, cast: list[str]) -> dict | None:
    posted = {it: g.randint(3, 9) for it in sc["items"]}
    unit = g.randint(2, 4)
    sur = g.choice([2, 3]) if L["surcharge"] else 0
    room0 = g.choice(sc["rooms"])
    start = {"room": room0, "coins": g.randint(28, 45), "time": 60 * g.randint(8, 9) + 5 * g.randint(0, 11),
             "pack": g.sample(sc["items"], 2), "count": g.randint(1, 3) if L["count"] else 0}
    kinds = _draw_actions(g, L, sc)
    acts: list[dict] = []
    pack = list(start["pack"])
    bought: list[int] = []          # indices of buys still in the pack (returnable / givable)
    waits: list[int] = []
    count = start["count"]
    have_debt = False
    room = room0
    pending = list(kinds)
    guard = 0
    while pending:
        guard += 1
        if guard > 200:
            return None
        k = pending.pop(0)
        ok = True
        if k == "move":
            room = g.choice([r for r in sc["rooms"] if r != room])
            acts.append({"k": "move", "room": room})
        elif k == "wait":
            acts.append({"k": "wait", "m": 5 * g.randint(2, 9), "act": g.choice(ACTIVITIES)})
            waits.append(len(acts) - 1)
        elif k == "buy":
            cand = [x for x in sc["items"] if x not in pack and not any(a.get("item") == x for a in acts)]
            if not cand:
                return None
            it = g.choice(cand)
            a = {"k": "buy", "item": it, "posted": posted[it]}
            if not L["posted"]:
                a.update(stated=True, price=posted[it])
            acts.append(a)
            pack.append(it)
            bought.append(len(acts) - 1)
        elif k == "buyn":
            acts.append({"k": "buyn", "n": g.randint(2, 4), "unit": unit})
            count += acts[-1]["n"]
        elif k == "givn":
            if count < 2:
                ok = False
            else:
                n = g.randint(1, min(3, count - 1))
                acts.append({"k": "givn", "n": n})
                count -= n
        elif k == "give":
            cand = [x for x in pack if not any(a["k"] == "repay" and a.get("item") == x for a in acts)]
            if len(pack) <= 1 or not cand:
                ok = False
            else:
                it = g.choice(cand)
                acts.append({"k": "give", "item": it})
                pack.remove(it)
                bought = [b for b in bought if acts[b]["item"] != it]
        elif k == "earn":
            acts.append({"k": "earn", "n": g.randint(3, 9)})
        elif k == "ret":
            # a return: a bought item still in the pack, bought at least one action earlier
            cand = [b for b in bought if acts[b]["item"] in pack and b < len(acts) - 0]
            if not cand:
                ok = False
            else:
                b = g.choice(cand)
                it = acts[b]["item"]
                acts.append({"k": "ret", "item": it, "b": b})
                pack.remove(it)
                bought.remove(b)
        elif k == "borrow":
            acts.append({"k": "borrow", "n": 5 * g.randint(2, 4)})
            have_debt = True
        elif k == "repay":
            # repay "exactly what I paid for the X": X bought earlier (two turns combined)
            cand = [i for i, a in enumerate(acts) if a["k"] == "buy"]
            if not have_debt or not cand:
                ok = False
            else:
                acts.append({"k": "repay", "item": acts[g.choice(cand)]["item"]})
        elif k == "corr":
            cand = [w for w in waits if not any(a["k"] == "corr" and a["w"] == w for a in acts)]
            if not cand:
                ok = False
            else:
                w = g.choice(cand)
                m2 = acts[w]["m"] + 5 * g.choice([-3, -2, 2, 3, 4])
                if m2 < 5:
                    m2 = acts[w]["m"] + 15
                acts.append({"k": "corr", "w": w, "m2": m2})
        if not ok:
            if len(pending) == 0:
                return None
            pending.insert(min(len(pending), g.randint(1, 3)), k)
    # the clock: true times (corrections applied) and the times the turns state before the correction is read
    true_snaps = _run(start, acts, None, 0)
    btimes = _believed_times(start, acts)
    tt = [s["time"] for s in true_snaps]
    if tt[-1] + 30 > 23 * 60:
        return None
    cutoff = None
    if L["surcharge"]:
        buys = [i for i, a in enumerate(acts) if a["k"] in ("buy", "buyn")]
        # a cutoff strictly between purchase times: some purchases before, some after, none at the cutoff; the
        # believed time (before a correction is read) must fall on the same side as the true time
        cands = []
        for c in range(tt[0] - 60, tt[-1] + 60, 5):
            if c % 15:
                continue
            after = [i for i in buys if tt[i] >= c]
            if not after or len(after) == len(buys):
                continue
            if any(tt[i] == c or btimes[i] == c or (tt[i] >= c) != (btimes[i] >= c) for i in buys):
                continue
            if not any(acts[i]["k"] == "buy" for i in after):
                continue
            cands.append(c)
        if not cands:
            return None
        cutoff = g.choice(cands)
    snaps = _run(start, acts, cutoff, sur)
    # feasibility: never short of money, a repay never exceeds the debt, debt still open at the end
    if any(s["coins"] < 0 for s in snaps) or any(s["owe"] < 0 for s in snaps):
        return None
    end = snaps[-1]
    if L["debt"] and end["owe"] <= 0:
        return None
    if end["room"] == start["room"] or end["coins"] == start["coins"] or end["time"] == start["time"]:
        return None
    # every switched-off mechanic must change something the final check reads (so each named mutant is visible)
    for flag in ("surcharge", "returns", "corrections", "debt"):
        if flag == "surcharge" and not L["surcharge"] or flag == "returns" and not L["returns"] \
                or flag == "corrections" and not L["corr"] or flag == "debt" and not L["debt"]:
            continue
        alt = _run(start, acts, cutoff, sur, **{flag: False})[-1]
        if (alt["coins"], alt["time"], sorted(alt["pack"]), alt["owe"]) == \
                (end["coins"], end["time"], sorted(end["pack"]), end["owe"]):
            return None
    return {"start": start, "acts": acts, "snaps": snaps, "cutoff": cutoff, "sur": sur, "posted": posted, "unit": unit}


# ------------------------------------------------------------------ plan
def _pack_turns(g, L: dict, acts: list[dict]) -> list[list[int]] | None:
    """Distribute the actions over the scene turns (1-2 per turn, a correction or return never shares a turn with the
    action it refers to). Returns per scene turn the action indices (a distractor turn has none)."""
    n_scene = L["T"] - 1 - (1 if L["mid"] else 0)
    n_act = n_scene - L["D"]
    if len(acts) < n_act or len(acts) > 2 * n_act:
        return None
    per = [1] * n_act
    for _ in range(len(acts) - n_act):
        cand = [i for i in range(n_act) if per[i] < 2]
        per[g.choice(cand)] += 1
    groups, i = [], 0
    for c in per:
        groups.append(list(range(i, i + c)))
        i += c
    for grp in groups:
        for j in grp:
            ref = acts[j].get("b", acts[j].get("w"))
            if ref is not None and ref in grp:
                return None
    slots = list(range(n_scene))
    dist = set(g.sample(slots, L["D"])) if L["D"] else set()
    it = iter(groups)
    return [[] if s in dist else next(it) for s in slots]


def _user_name_npc(a: dict, t: int, rules: dict, cast: list[str], g) -> str:
    """A partner for a scripted action at scene turn t: never the presence character; never the exit character from
    the exit turn on."""
    pool = cast[1:]
    if rules.get("exit") and t >= rules["exit"]["t"]:
        pool = cast[2:]
    return g.choice(pool)


def _act_text(a: dict, sc: dict, acts: list[dict]) -> str:
    cur = sc["cur"]
    k = a["k"]
    one, many = sc["stock"]
    if k == "move":
        return f"I walk over to the {a['room']}."
    if k == "wait":
        return f"I spend {a['m']} minutes {a['act']}."
    if k == "buy":
        if a.get("stated"):
            return f"I pay {a['npc']} {a['price']} {cur} for the {a['item']} and put it in my pack."
        return f"I buy the {a['item']} from {a['npc']} at today's price and put it in my pack."
    if k == "buyn":
        return f"I buy {NUMWORDS[a['n']]} {many} from {a['npc']} at today's price and put them in my {sc['pocket']}."
    if k == "givn":
        return f"I give {NUMWORDS[a['n']]} of my {many} to {a['npc']}." if a["n"] > 1 else \
            f"I give one of my {many} to {a['npc']}."
    if k == "give":
        return f"I give the {a['item']} to {a['npc']}."
    if k == "earn":
        return f"I earn {a['n']} {cur} helping {a['npc']} sweep up, and pocket the money."
    if k == "ret":
        return (f"I change my mind about the {a['item']}: I hand it back to {a['npc']}, who gives me back exactly what I "
                f"paid for it.")
    if k == "borrow":
        return f"{a['npc']} lends me {a['n']} {cur}, and I promise to pay it all back later."
    if k == "repay":
        return f"I pay {a['lender']} back part of my debt: exactly as many {cur} as I paid for the {a['item']}."
    if k == "corr":
        w = acts[a["w"]]
        return (f"I need to correct something I told you earlier: I did not spend {w['m']} minutes {w['act']}, it was "
                f"{a['m2']} minutes.")
    raise ValueError(k)


def _act_phrase(a: dict, sc: dict) -> str | None:
    if a["k"] == "buy":
        return f"bought the {a['item']}"
    if a["k"] == "give":
        return f"gave the {a['item']} to {a['npc']}"
    if a["k"] == "buyn":
        return f"bought the {sc['stock'][1]}"
    return None


def _pick_callbacks(g, kinds, sim, act_turn, sc):
    acts, snaps = sim["acts"], sim["snaps"]
    out = []
    end = snaps[-1]
    corr = {a["w"]: a["m2"] for a in acts if a["k"] == "corr"}
    money = ("buy", "buyn", "earn", "ret", "borrow", "repay")

    def turn_has(i, ks, after):
        """True when another action of kind ``ks`` shares action i's turn, after (or before) it: then "right after"
        / "when I bought" could be read as the moment or as the end of the turn (second reader, 2026-11-14)."""
        rng_ = range(i + 1, len(acts)) if after else range(0, i)
        return any(act_turn[j] == act_turn[i] and acts[j]["k"] in ks for j in rng_)
    for kind in kinds:
        anchors = [i for i, a in enumerate(acts) if _act_phrase(a, sc) and
                   sum(1 for b in acts if b["k"] == a["k"] and b.get("item") == a.get("item")) == 1]
        anchors = [i for i in anchors if not (acts[i]["k"] == "buyn" and sum(1 for b in acts if b["k"] == "buyn") > 1)]
        if kind == "paid_for":
            # a purchase charged the surcharge (the wrong-but-tempting answer is the posted price); never a returned item
            # ("what did I pay for it" vs the net 0 after the refund: blind reader 2)
            returned = {a["item"] for a in acts if a["k"] == "ret"}
            cands = [i for i in anchors if acts[i]["k"] == "buy" and not acts[i].get("stated")
                     and acts[i]["item"] not in returned and snaps[i]["paid"][acts[i]["item"]] != acts[i]["posted"]]
            if not cands:
                return None
            i = g.choice(cands)
            out.append({"k": kind, "ans": str(snaps[i]["paid"][acts[i]["item"]]), "wrong": str(acts[i]["posted"]),
                        "stem": f"How many {sc['cur']} did I pay for the {acts[i]['item']}? (a number only)"})
        elif kind == "coins_at":
            cands = [i for i in anchors if snaps[i]["coins"] != end["coins"] and not turn_has(i, money, True)
                     and sum(1 for b in acts[i + 1:] if b["k"] in ("buy", "buyn", "earn", "ret", "borrow", "repay")) >= 2]
            if not cands:
                return None
            i = g.choice(cands)
            out.append({"k": kind, "ans": str(snaps[i]["coins"]), "wrong": str(end["coins"]),
                        "stem": f"How many {sc['cur']} did I have right after I {_act_phrase(acts[i], sc)}? (a number only)"})
        elif kind == "where_when":
            cands = [i for i in anchors if acts[i]["k"] == "buy" and snaps[i]["room"] != end["room"]]
            if not cands:
                return None
            i = g.choice(cands)
            out.append({"k": kind, "ans": snaps[i]["room"], "wrong": end["room"],
                        "stem": f"Where was I when I bought the {acts[i]['item']}? (a place name)"})
        elif kind == "elapsed":
            # a span that contains a corrected wait when the level has one (derived from the correction)
            cands = []
            for x in anchors:
                for y in anchors:
                    if x < y and sum(1 for a in acts[x + 1:y] if a["k"] == "wait") >= 2 \
                            and not turn_has(x, ("wait", "corr"), True) and not turn_has(y, ("wait", "corr"), False):
                        if corr and not any(w in corr for w in range(x + 1, y)):
                            continue
                        cands.append((x, y))
            if not cands:
                return None
            x, y = g.choice(cands)
            ans = snaps[y]["time"] - snaps[x]["time"]
            stated = sum(acts[j]["m"] for j in range(x + 1, y) if acts[j]["k"] == "wait")
            out.append({"k": kind, "ans": str(ans), "wrong": str(stated if stated != ans else ans + 5),
                        "stem": f"How many minutes passed between when I {_act_phrase(acts[x], sc)} and when I "
                                f"{_act_phrase(acts[y], sc)}? (a number only)"})
        elif kind == "count_at":
            cands = [i for i in anchors if acts[i]["k"] == "buy" and snaps[i]["count"] != end["count"]
                     and not turn_has(i, ("buyn", "givn"), True)]
            if not cands:
                return None
            i = g.choice(cands)
            out.append({"k": kind, "ans": str(snaps[i]["count"]), "wrong": str(end["count"]),
                        "stem": f"How many {sc['stock'][1]} did I have right after I {_act_phrase(acts[i], sc)}? "
                                "(a number only)"})
        else:
            raise ValueError(kind)
    if len({c["stem"] for c in out}) != len(out):
        return None
    return out


def _pick_probes(g, kinds, sim, act_turn, n_scene, sc, cbs):
    acts, snaps = sim["acts"], sim["snaps"]
    out = []
    asked = {c["stem"].split("? ")[0] for c in cbs}
    for kind in kinds:
        buys = [i for i, a in enumerate(acts) if a["k"] == "buy" and act_turn[i] < n_scene]
        if kind == "price":
            returned = {a["item"] for a in acts if a["k"] == "ret"}
            cands = [i for i in buys if not acts[i].get("stated") and snaps[i]["paid"][acts[i]["item"]] != acts[i]["posted"]
                     and acts[i]["item"] not in returned and not any(acts[i]["item"] in s for s in asked)]
            if not cands:
                return None
            i = g.choice(cands)
            p = snaps[i]["paid"][acts[i]["item"]]
            out.append({"k": "price", "i": i, "claim": f"the {acts[i]['item']} cost me {acts[i]['posted']} {sc['cur']}, didn't it",
                        "truth": str(p),
                        "stem": f"Did the {acts[i]['item']} cost me {acts[i]['posted']} {sc['cur']}? "
                                f"Answer 'yes' or 'no, <the real price in {sc['cur']}>'."})
        elif kind == "fcb":
            cands = [i for i in buys if not any(o.get("i") == i for o in out)]
            if not cands:
                return None
            i = g.choice(cands)
            true = snaps[i]["room"]
            wrong = g.choice([r for r in sc["rooms"] if r != true])
            out.append({"k": "fcb", "i": i, "claim": f"I was in the {wrong} when I bought the {acts[i]['item']}, wasn't I",
                        "truth": true,
                        "stem": f"Was I in the {wrong} when I bought the {acts[i]['item']}? "
                                "Answer 'yes' or 'no, <the place I was really in>'."})
    return out


def _pick_rules(g, L, cast, sim, act_turn, n_scene, room_t, room_s):
    """``room_t`` / ``room_s``: the user's place at the end / start of each scene turn. A turn in which the user enters
    or leaves the presence place is graded neither way (the character may or may not speak: both readings are
    defensible, second reader 2026-11-14)."""
    rules = {}
    cand = [r for r in sorted(set(room_t)) if 2 <= room_t.count(r) <= n_scene - 2]
    if not cand:
        return None
    room = g.choice(cand)
    trans = [t for t in range(1, n_scene + 1) if room_s[t - 1] != room_t[t - 1] and room in (room_s[t - 1], room_t[t - 1])]
    rules["presence"] = {"npc": cast[0], "room": room, "transit": trans,
                         "in": [t for t in range(1, n_scene + 1) if room_t[t - 1] == room and t not in trans],
                         "out": [t for t in range(1, n_scene + 1) if room_t[t - 1] != room and t not in trans]}
    if not rules["presence"]["in"] or not rules["presence"]["out"]:
        return None
    if L["exit"]:
        acts = sim["acts"]
        gv = [i for i, a in enumerate(acts) if a["k"] == "give" and 0.3 * n_scene <= act_turn[i] <= 0.7 * n_scene]
        if not gv:
            return None
        i = g.choice(gv)
        rules["exit"] = {"npc": cast[1], "item": acts[i]["item"], "t": act_turn[i]}
    return rules


@functools.lru_cache(maxsize=256)
def _plan_cached(level: int, key, release_seed: str, case_id: str) -> dict:
    L = LEVELS[level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, key)
    for attempt in range(2000):
        g = rng(seed, f"plan{attempt}")
        sc = g.choice(SCENES)
        user = g.choice(USERS)
        cast = g.sample(NAMES, 4)
        sim = _simulate(g, L, sc, cast)
        if sim is None:
            continue
        acts = sim["acts"]
        groups = _pack_turns(g, L, acts)
        if groups is None:
            continue
        n_scene = len(groups)
        act_turn = [0] * len(acts)
        for t, grp in enumerate(groups, 1):
            for j in grp:
                act_turn[j] = t
        # room per scene turn (the room the user is in once the turn's actions are done)
        room_t, room_s = [], []
        for t in range(1, n_scene + 1):
            cur = sim["start"]["room"] if t == 1 else room_t[-1]
            room_s.append(cur)
            for j in groups[t - 1]:
                if acts[j]["k"] == "move":
                    cur = acts[j]["room"]
            room_t.append(cur)
        rules = _pick_rules(rng(seed, f"rules{attempt}"), L, cast, sim, act_turn, n_scene, room_t, room_s)
        if rules is None:
            continue
        # name the partners (after the exit turn is known); the lender is fixed at the borrow
        g3 = rng(seed, f"names{attempt}")
        lender = None
        for j, a in enumerate(acts):
            if a["k"] in ("buy", "buyn", "givn", "give", "earn", "ret", "borrow"):
                if a["k"] == "ret":
                    a["npc"] = acts[a["b"]]["npc"]
                    if rules.get("exit") and act_turn[j] >= rules["exit"]["t"] and a["npc"] == cast[1]:
                        a["npc"] = None
                else:
                    a["npc"] = _user_name_npc(a, act_turn[j], rules, cast, g3)
                if a["k"] == "borrow":
                    lender = a["npc"]
            if a["k"] == "repay":
                a["lender"] = lender
                if rules.get("exit") and act_turn[j] >= rules["exit"]["t"] and lender == cast[1]:
                    a["lender"] = None
        if any(a.get("npc", "x") is None or a.get("lender", "x") is None for a in acts):
            continue
        # the exit give must not be TO the leaving character
        if rules.get("exit") and any(a["k"] == "give" and a["item"] == rules["exit"]["item"] and a["npc"] == cast[1]
                                     for a in acts):
            continue
        g2 = rng(seed, f"cb{attempt}")
        cbs = _pick_callbacks(g2, L["cbs"], sim, act_turn, sc)
        if cbs is None:
            continue
        probes = _pick_probes(g2, L["probes"], sim, act_turn, n_scene, sc, cbs)
        if probes is None:
            continue
        # scripted user turns
        colour = g.choice(COLOURS)
        scene_turns = []
        dist_pool = [f"I glance at the notice board: 'Closing time today: {_fmt_time(g.randint(14, 22) * 60 + 5 * g.randint(0, 11))}'. I do nothing about it yet.",
                     f"I notice that the {g.choice(sc['rooms'])} door has been painted again. I leave it alone and keep looking around.",
                     f"I stand still for a moment and look around. I do not move."]
        g.shuffle(dist_pool)
        for t, grp in enumerate(groups, 1):
            scene_turns.append(" Then ".join(_act_text(acts[j], sc, acts) for j in grp) if grp else dist_pool.pop())
        decoys = []
        if L.get("decoys"):
            decoys = _decoys(rng(seed, f"decoy{attempt}"), sim, groups, act_turn, rules, cast, sc)
            for d in decoys:
                scene_turns[d["t"] - 1] += " " + d["text"]
        # plant each false claim in a scene turn after the moment it is about (never in the moment's own turn)
        for p in probes:
            later = [t for t in range(act_turn[p["i"]] + 1, n_scene + 1)]
            if later:
                w = g2.choice(later)
                scene_turns[w - 1] = f"Hang on, {p['claim']}? Anyway. " + scene_turns[w - 1]
        turns = list(scene_turns)
        mid = None
        if L["mid"]:
            # the mid OOC check sits after scene turn mid-1: it asks the state right then
            k = L["mid"] - 1
            snap_t = max([j for j in range(len(acts)) if act_turn[j] <= k], default=None)
            if snap_t is None:
                continue
            s = sim["snaps"][snap_t]
            # the clock as told so far: a correction announced after the check is not known yet (blind reader 1); a
            # correction never moves a purchase across the surcharge time, so coins / stock / debt are unaffected
            told = _believed_times(sim["start"], acts[:snap_t + 1])[-1]
            mid = {"after": k, "coins": s["coins"], "time": told, "count": s["count"], "owe": s["owe"]}
            turns.insert(k, _mid_text(sc, L, mid))
        final = _final_turn(sc, L, cbs, probes)
        turns.append(final["text"])
        plan = {"sc": sc, "user": user, "cast": cast, "colour": colour, "sim": sim, "probes": probes, "cbs": cbs,
                "decoys": decoys,
                "rules": rules, "turns": turns, "final": final, "mid": mid, "level": level, "seed": seed,
                "attempt": attempt, "groups": groups, "act_turn": act_turn, "L": L}
        plan["scene_turn_ids"] = _scene_turn_ids(plan)
        plan["system"] = _system(plan)
        plan["checks"] = _checks(plan)
        plan["reference"] = _reference(plan)
        if grade_weighted(plan["checks"], plan["reference"]) == 1.0:
            return plan
    raise RuntimeError(f"session_ledger: no valid draw for {case_id} level {level}")


THINGS = ["pot of glue", "bag of nails", "loaf of bread", "jar of honey", "box of matches", "skein of wool"]


def _decoys(g, sim, groups, act_turn, rules, cast, sc) -> list[dict]:
    """One interference line per scene turn. Each names a number or item of the same kind as a real change but, as
    the line itself says, changes nothing for the user. ``fx`` = what a model that counted it would get wrong (the
    ``counts-interference`` mutant)."""
    acts, snaps, cur = sim["acts"], sim["snaps"], sc["cur"]
    out = []
    kinds = ["offer", "npc_pay", "talk_wait", "hold", "lend"]
    for t in range(1, len(groups) + 1):
        before = [j for j in range(len(acts)) if act_turn[j] <= t]
        st = snaps[before[-1]] if before else {**sim["start"], "owe": 0}
        pool = cast[2:] if rules.get("exit") and t >= rules["exit"]["t"] else cast[1:]
        npc = g.choice(pool)
        # no loan offer in a turn with a real loan or repayment, or from the lender (it would read as a contradiction)
        debt_turn = any(acts[j]["k"] in ("borrow", "repay") for j in range(len(acts)) if act_turn[j] == t)
        lender = next((a.get("npc") for a in acts if a["k"] == "borrow"), None)
        k = g.choice([x for x in kinds if (x != "hold" or st["pack"]) and (x != "lend" or not debt_turn)])
        if k == "lend" and npc == lender:
            npc = g.choice([c for c in pool if c != lender] or pool)
        if k == "offer":
            touched = set(sim["start"]["pack"]) | {a.get("item") for a in acts}
            it = g.choice([x for x in sc["items"] if x not in touched] or [x for x in sc["items"] if x not in st["pack"]])
            # the price asked is the item's real price at that moment (posted, plus the surcharge once it applies)
            p = sim["posted"][it] + (sim["sur"] if sim["cutoff"] is not None and st["time"] >= sim["cutoff"] else 0)
            out.append({"t": t, "k": k, "fx": {"coins": -p}, "text": f"{npc} offers to sell me the {it} for {p} {cur}; I say no and keep my money."})
        elif k == "npc_pay":
            other = g.choice([c for c in pool if c != npc] or cast[2:])
            p = g.randint(3, 12)
            out.append({"t": t, "k": k, "fx": {"coins": -p}, "text": f"I watch {npc} pay {other} {p} {cur} for a {g.choice(THINGS)}; it is none of my business."})
        elif k == "talk_wait":
            m = 5 * g.randint(3, 12)
            out.append({"t": t, "k": k, "fx": {"time": m}, "text": f"{npc} says the walk to the {g.choice(sc['rooms'])} usually takes {m} minutes. I stay where I am for now."})
        elif k == "hold":
            it = g.choice(st["pack"])
            out.append({"t": t, "k": k, "fx": {"drop": it}, "text": f"I hold out the {it} to {npc}, then think better of it and put it back in my pack."})
        else:
            n = 5 * g.randint(2, 4)
            out.append({"t": t, "k": k, "fx": {"coins": n, "owe": n}, "text": f"{npc} offers to lend me {n} {cur}; I thank them and say no."})
    return out


def _scene_turn_ids(plan: dict) -> list[int]:
    """1-based conversation turn number of each scene turn (the mid OOC turn shifts the later ones)."""
    n_scene = len(plan["groups"])
    out = []
    for s in range(1, n_scene + 1):
        out.append(s + (1 if plan["mid"] and s > plan["mid"]["after"] else 0))
    return out


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    return copy.deepcopy(_plan_cached(int(gen.get("level", 1)), gen.get("key"), release_seed, case_id))


def _mid_text(sc: dict, L: dict, mid: dict) -> str:
    qs = [f"How many {sc['cur']} do I have right now? (a number only)", "What time does the clock show? (24-hour HH:MM)"]
    if L["count"]:
        qs.append(f"How many {sc['stock'][1]} do I have? (a number only)")
    if L["debt"]:
        qs.append(f"How many {sc['cur']} do I owe right now? (a number only; 0 if none)")
    return ("OOC: a quick state check before we go on. Use only the scene card and what I have told you I did. "
            "Questions:\n" + "\n".join(f"Q{i}. {q}" for i, q in enumerate(qs, 1))
            + "\nAnswer on separate lines in exactly this form, nothing else on those lines:\n"
            + "\n".join(f"B{i}: <answer to Q{i}>" for i in range(1, len(qs) + 1))
            + "\nAnswer only these lines; the scene goes on with my next message.")


def _final_turn(sc: dict, L: dict, cbs: list[dict], probes: list[dict]) -> dict:
    qs = [("room", "Which place am I in right now?"), ("coins", f"How many {sc['cur']} do I have? (a number only)"),
          ("pack", "What is in my pack? (item names separated by commas)"),
          ("time", "What time does the clock show? (24-hour HH:MM)")]
    if L["count"]:
        qs.append(("count", f"How many {sc['stock'][1]} do I have? (a number only)"))
    if L["debt"]:
        qs.append(("owe", f"How many {sc['cur']} do I still owe? (a number only)"))
    qs += [(f"cb{j}", c["stem"]) for j, c in enumerate(cbs)]
    qs += [(f"p{j}", p["stem"]) for j, p in enumerate(probes)]
    text = ("OOC: pause the scene for a state check. Use only the scene card and what I have told you I did; ignore "
            "anything else. Questions:\n" + "\n".join(f"Q{i}. {q}" for i, (_, q) in enumerate(qs, 1))
            + "\nAnswer on separate lines in exactly this form, nothing else on those lines:\n"
            + "\n".join(f"A{i}: <answer to Q{i}>" for i in range(1, len(qs) + 1)))
    return {"text": text, "qs": [k for k, _ in qs]}


def _rule_text(rules: dict, user: str) -> str:
    out = []
    p = rules["presence"]
    out.append(f"{p['npc']} works only in the {p['room']}: {p['npc']} speaks only in replies where {user} is in the "
               f"{p['room']}, and has no lines in any other reply.")
    if rules.get("exit"):
        e = rules["exit"]
        out.append(f"When {user} gives away the {e['item']}, {e['npc']} leaves for good: from that reply on, "
                   f"{e['npc']} has no lines.")
    return " ".join(out)


def _system(plan: dict) -> str:
    sc, user, cast, sim, L = plan["sc"], plan["user"], plan["cast"], plan["sim"], plan["L"]
    st = sim["start"]
    rules = (f"You are the narrator and every non-player character in an interactive scene. The user plays {user}. "
             f"Never write {user}'s words, actions, thoughts or choices: narrate only the world and the other "
             f"characters, and stop where {user} can act. Keep each scene reply brief: a few sentences of narration "
             f"and the characters' lines. If a message starts with OOC, step out of the scene and answer exactly as "
             f"asked. Write every spoken line as its own line in the form 'Name: text' (no quotation marks); "
             f"narration lines carry no tag. Unless a character rule below says otherwise, in every scene reply each "
             f"of {', '.join(cast[:-1])} and {cast[-1]} speaks at least one tagged line. Never give {user} a tagged "
             f"line. Character rules: " + _rule_text(plan["rules"], user))
    card = [f"SCENE CARD: {sc['title']}", sc["intro"], f"Places: {', '.join(sc['rooms'])}.", f"Currency: {sc['cur']}.",
            "Cast: " + ", ".join(cast) + ".",
            f"The {sc['key']} is {plan['colour']}."]
    if L["posted"]:
        items = [a["item"] for a in sim["acts"] if a["k"] == "buy"]
        shown = sorted(set(items) | set(plan_rng_extra(plan)))
        card.append("Posted prices: " + "; ".join(f"{it} {sim['posted'][it]} {sc['cur']}" for it in shown)
                    + (f"; {sc['stock'][1]} {sim['unit']} {sc['cur']} each" if L["count"] else "") + ".")
    if L["surcharge"]:
        card.append(f"From {_fmt_time(sim['cutoff'])} on, everything costs {sim['sur']} {sc['cur']} more than its posted "
                    f"price (for each piece).")
    card.append(f"Only the minutes {user} says they spend move the clock; going from place to place takes no time. "
                f"{user} stays in one place until {user} says they walk somewhere else; the cast come and go, and "
                f"{user} deals with them wherever {user} is.")
    card.append(f"START: {user} is in the {st['room']} with {st['coins']} {sc['cur']}; the clock reads "
                f"{_fmt_time(st['time'])}; the pack holds: {', '.join(st['pack'])}"
                + (f"; {user} has {st['count']} {sc['stock'][1] if st['count'] != 1 else sc['stock'][0]} in the "
                   f"{sc['pocket']}" if L["count"] else "") + ".")
    return rules + "\n\n" + "\n".join(card)


def plan_rng_extra(plan: dict) -> list[str]:
    """Two posted prices for items never bought (the board lists more than the script uses)."""
    g = rng(plan["seed"], f"board{plan['attempt']}")
    bought = {a["item"] for a in plan["sim"]["acts"] if a["k"] == "buy"} | set(plan["sim"]["start"]["pack"])
    rest = [it for it in plan["sc"]["items"] if it not in bought]
    return g.sample(rest, min(2, len(rest)))


def _num_pat(lbl: str, n: int | str) -> str:
    return rf"{_prefix(lbl)}[^\n\d]*(?<![\d.])\b{re.escape(str(n))}\b(?![.,]?\d)[^\n\d]*$"


def _checks(plan: dict) -> list[dict]:
    sc, sim, fin, R, L = plan["sc"], plan["sim"], plan["final"], plan["rules"], plan["L"]
    T = len(plan["turns"])
    ids = plan["scene_turn_ids"]
    out: list[dict] = []
    # speaker rules, one check per turn read
    p = R["presence"]
    ins = [ids[t - 1] for t in p["in"]]
    outs = [ids[t - 1] for t in p["out"]]
    n_p = len(ins) + len(outs)
    for t in ins:
        out.append(_chk(f"presence-in-t{t}", [t], PRESENCE_W / n_p, r"^\S", scope=f"speaker:{p['npc']}", min_hits=1))
    for t in outs:
        out.append(_chk(f"presence-out-t{t}", [t], PRESENCE_W / n_p, rf"\A{_SUBST}(?![\s\S]*{_tag(p['npc'])})"))
    if R.get("exit"):
        e = R["exit"]
        before = [ids[t - 1] for t in range(1, e["t"])]
        # the exit turn itself is graded neither way (a goodbye line or silence: both defensible)
        after = [ids[t - 1] for t in range(e["t"] + 1, len(ids) + 1)]
        n_e = len(before) + len(after)
        for t in before:
            out.append(_chk(f"exit-before-t{t}", [t], EXIT_W / n_e, r"^\S", scope=f"speaker:{e['npc']}", min_hits=1))
        for t in after:
            out.append(_chk(f"exit-after-t{t}", [t], EXIT_W / n_e, rf"\A{_SUBST}(?![\s\S]*{_tag(e['npc'])})"))
    # mid checkpoint
    if plan["mid"]:
        m, mt = plan["mid"], plan["mid"]["after"] + 1
        out.append(_chk("B-coins", [mt], 1, _num_pat("B1", m["coins"])))
        out.append(_chk("B-time", [mt], 1, rf"{_prefix('B2')}[^\n\d]*\b{_time_pat(m['time'])}\b[^\n\d]*$"))
        i = 3
        if L["count"]:
            out.append(_chk("B-count", [mt], 1, _num_pat(f"B{i}", m["count"])))
            i += 1
        if L["debt"]:
            pat = _num_pat(f"B{i}", m["owe"]) if m["owe"] else \
                rf"{_prefix(f'B{i}')}[^\n\d]*(?:\b0\b|\bnothing\b|\bnone\b)[^\n\d]*$"
            out.append(_chk("B-owe", [mt], 1, pat))
    fl = [T]
    end = sim["snaps"][-1]
    qn = {k: i for i, k in enumerate(fin["qs"], 1)}
    others = [r for r in sc["rooms"] if r != end["room"]]
    out.append(_chk("A-room", fl, 1, rf"{_prefix(f'A{qn['room']}')}(?![^\n]*\b(?:{_alt(others)})\b)[^\n]*\b{re.escape(end['room'])}\b"))
    out.append(_chk("A-coins", fl, 1, _num_pat(f"A{qn['coins']}", end["coins"])))
    look = "".join(rf"(?=[^\n]*\b{re.escape(it)}\b)" for it in end["pack"])
    extra = [it for it in sc["items"] if it not in end["pack"]]
    out.append(_chk("A-pack", fl, 1, rf"{_prefix(f'A{qn['pack']}')}{look}(?![^\n]*\b(?:{_alt(extra)})\b)[^\n]*\S"))
    out.append(_chk("A-time", fl, 1, rf"{_prefix(f'A{qn['time']}')}[^\n\d]*\b{_time_pat(end['time'])}\b[^\n\d]*$"))
    if "count" in qn:
        out.append(_chk("A-count", fl, 1, _num_pat(f"A{qn['count']}", end["count"])))
    if "owe" in qn:
        out.append(_chk("A-owe", fl, 1, _num_pat(f"A{qn['owe']}", end["owe"])))
    for j, c in enumerate(plan["cbs"]):
        lbl = f"A{qn[f'cb{j}']}"
        if c["k"] == "where_when":
            oth = [r for r in sc["rooms"] if r != c["ans"]]
            pat = rf"{_prefix(lbl)}(?![^\n]*\b(?:{_alt(oth)})\b)[^\n]*\b{re.escape(c['ans'])}\b"
        else:
            pat = _num_pat(lbl, c["ans"])
        out.append(_chk(f"A-cb-{c['k']}", fl, 1, pat))
    for j, pr in enumerate(plan["probes"]):
        lbl = f"A{qn[f'p{j}']}"
        out.append(_chk(f"A-false-{pr['k']}", fl, 1,
                        rf"{_prefix(lbl)}(?![ \t]*(?:\*\*)?yes\b)[^\n]*\b{re.escape(pr['truth'])}\b"))
    return out


# ------------------------------------------------------------------ reference
def _speaks(plan: dict, npc: str, s: int) -> bool:
    R = plan["rules"]
    if R["presence"]["npc"] == npc and s not in R["presence"]["in"]:
        return False
    if R.get("exit") and R["exit"]["npc"] == npc and s >= R["exit"]["t"]:
        return False
    return True


def _answers(plan: dict, snaps: list[dict] | None = None) -> str:
    sim, fin = plan["sim"], plan["final"]
    end = (snaps or sim["snaps"])[-1]
    out = []
    for i, k in enumerate(fin["qs"], 1):
        if k == "room":
            v = end["room"]
        elif k == "coins":
            v = end["coins"]
        elif k == "pack":
            v = ", ".join(sorted(end["pack"]))
        elif k == "time":
            v = _fmt_time(end["time"])
        elif k == "count":
            v = end["count"]
        elif k == "owe":
            v = end["owe"]
        elif k.startswith("cb"):
            v = plan["cbs"][int(k[2:])]["ans"]
        else:
            v = f"no, {plan['probes'][int(k[1:])]['truth']}"
        out.append(f"A{i}: {v}")
    return "\n".join(out)


def _mid_answers(plan: dict) -> str:
    m, L = plan["mid"], plan["L"]
    vals = [m["coins"], _fmt_time(m["time"])] + ([m["count"]] if L["count"] else []) + ([m["owe"]] if L["debt"] else [])
    return "\n".join(f"B{i}: {v}" for i, v in enumerate(vals, 1))


def _reference(plan: dict) -> list[str]:
    g = rng(plan["seed"], f"ref{plan['attempt']}")
    out = []
    n_scene = len(plan["groups"])
    for s in range(1, n_scene + 1):
        if plan["mid"] and s == plan["mid"]["after"] + 1:
            out.append(_mid_answers(plan))
        nar = g.sample(NARRATION, 3)
        lines = [nar[0], nar[1]]
        for n in plan["cast"]:
            if _speaks(plan, n, s):
                lines.append(f"{n}: {g.choice(SPEECH)}")
        lines.append(nar[2])
        out.append("\n".join(lines))
    out.append(_answers(plan))
    return out


# ------------------------------------------------------------------ public API
def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    plan = _plan(gen, release_seed, case_id)
    return {
        "id": case_id, "category": "rpdeep", "system": plan["system"], "history": [], "turns": plan["turns"],
        "temperature": 0.7, "top_p": 0.95, "max_tokens": 16384, "user_name": plan["user"],
        "checks": plan["checks"], "judge_elements": [], "programmatic": True, "generator": dict(gen),
        "meta": {"family": FAMILY, "level": plan["level"], "band": plan["L"]["band"], "n_turns": len(plan["turns"]),
                 "n_actions": len(plan["sim"]["acts"]), "callbacks": [c["k"] for c in plan["cbs"]],
                 "probes": [p["k"] for p in plan["probes"]], "n_checks": len(plan["checks"]),
                 "prompt_tokens_est": round((len(plan["system"]) + sum(len(x) for x in plan["turns"])) / 4),
                 "reference_tokens_est": round(sum(len(x) for x in plan["reference"]) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_plan(gen, release_seed, case_id)["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_weighted(materialized["checks"], split_turns(answer))


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return list(dict.fromkeys(mutants_named(gen, release_seed=release_seed, case_id=case_id).values()))


def mutants_named(gen: dict, *, release_seed: str, case_id: str) -> dict[str, str]:
    """Named bad strategies, each a full answer; only those that score < 1.0 are returned (tests demand the
    mechanic-specific ones exist where the level has the mechanic)."""
    plan = _plan(gen, release_seed, case_id)
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    ref, sim, L = plan["reference"], plan["sim"], plan["L"]
    last = len(ref) - 1
    named: dict[str, list[str]] = {}

    def final_with(snaps):
        r = list(ref)
        r[last] = _answers(plan, snaps)
        return r

    for flag, on in (("surcharge", L["surcharge"]), ("returns", L["returns"]), ("corrections", L["corr"]),
                     ("debt", L["debt"])):
        if on:
            named[f"ignores-{flag}"] = final_with(_run(sim["start"], sim["acts"], sim["cutoff"], sim["sur"],
                                                       **{flag: False}))
    named["start-state"] = final_with([{**sim["start"], "owe": 0}])
    if plan.get("decoys"):
        e = copy.deepcopy(sim["snaps"][-1])
        for d in plan["decoys"]:
            e["coins"] += d["fx"].get("coins", 0)
            e["time"] += d["fx"].get("time", 0)
            e["owe"] += d["fx"].get("owe", 0)
            if d["fx"].get("drop") in e["pack"]:
                e["pack"].remove(d["fx"]["drop"])
        named["counts-interference"] = final_with([e])
    qn = {k: i for i, k in enumerate(plan["final"]["qs"], 1)}

    def sub(s, lbl, v):
        return re.sub(rf"(?m)^({lbl}: ).*$", lambda m: m.group(1) + v, s)

    r = list(ref)
    for j, c in enumerate(plan["cbs"]):
        r[last] = sub(r[last], f"A{qn[f'cb{j}']}", c["wrong"])
    named["final-state-for-callback"] = r
    r = list(ref)
    for j, p in enumerate(plan["probes"]):
        r[last] = sub(r[last], f"A{qn[f'p{j}']}", "yes")
    named["accepts-false-memory"] = r
    R, ids = plan["rules"], plan["scene_turn_ids"]
    if R["presence"]["out"]:
        t = ids[R["presence"]["out"][0] - 1] - 1
        r = list(ref)
        r[t] = r[t] + f"\n{R['presence']['npc']}: I should not be here."
        named["speaks-out-of-room"] = r
    r = list(ref)
    for s in range(1, len(ids) + 1):
        r[ids[s - 1] - 1] = "\n".join(x for x in r[ids[s - 1] - 1].splitlines() if not x.startswith(R["presence"]["npc"] + ":"))
    named["never-voices-presence-npc"] = r
    if R.get("exit"):
        t = ids[R["exit"]["t"]] - 1          # the scene turn after the exit turn (the exit turn itself is not graded)
        r = list(ref)
        r[t] = r[t] + f"\n{R['exit']['npc']}: One last word."
        named["speaks-after-exit"] = r
    named["do-nothing"] = ["Sure, noted."] * len(ref)
    named["silent-scene"] = [x if i == last or (plan["mid"] and i == plan["mid"]["after"]) else "" for i, x in
                             enumerate(ref)]
    out: dict[str, str] = {}
    for n, c in named.items():
        ans = join_turns(c)
        if grade(ans, mat) < 1.0:
            out[n] = ans
    return out


def _main(argv: list[str] | None = None) -> None:
    import json
    argv = sys.argv[1:] if argv is None else argv
    level = int(argv[0]) if argv else 3
    gen = {"type": FAMILY, "level": level}
    m = materialize(gen, release_seed="4.5.0", case_id="SL-demo")
    print("SYSTEM:\n" + m["system"])
    for i, t in enumerate(m["turns"], 1):
        print(f"\n--- USER TURN {i} ---\n{t}")
    ref = reference(gen, release_seed="4.5.0", case_id="SL-demo")
    print("\n--- REFERENCE (last reply) ---\n" + ref.split(TURN_SEP)[-1])
    print("\n# meta:", json.dumps(m["meta"]))
    print("# grade(reference) =", grade(ref, m))
    for n, a in mutants_named(gen, release_seed="4.5.0", case_id="SL-demo").items():
        print(f"# mutant {n}: {grade(a, m):.3f}")


if __name__ == "__main__":
    _main()
