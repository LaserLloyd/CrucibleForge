"""PLANCHAT generator (gen2 chat, 4.5; lab card EXP-20261114-13): keep a day plan VALID while the user keeps changing it.

A user plans a day of errands with an assistant over T <= 16 turns. Turn 1 states a seeded world: 5-8 places with
opening hours and visit lengths, an (asymmetric) travel-time table, a start place and time, and an end place with a
deadline, plus the places on today's list. Every turn the user changes something (a place closes early or opens
late, an appointment at an exact time, skip / add a place, lunch in a window, a slowdown after 13:00, an earlier
deadline, a sister who joins and wants three stops back to back, and RETRACTIONS of earlier changes) and the
assistant must reply with the CURRENT full plan, one line `HH:MM-HH:MM Place` per stop.

Grading is a simulation (``plansim.evaluate``, check type ``plan_sim``), one per-turn element (`element: turn`),
independent of what the model wrote earlier. Sub-checks (named, `element: check` uses them with scoring "share"):
parse, stops (every listed place exactly once), hours (a visit starts and ends inside opening hours; ending exactly
at closing time is fine), duration, travel (next start >= previous end + table time, in that direction, slowed
after the slowdown), fixed (exact start), deadline, excluded (no place that is not on the list), group (back to back).

Scoring (generator key ``scoring``): "all" (default) = a turn is worth 1 only when EVERY sub-check passes; the
sub-checks ride along at weight 0 so the failing one is on record. "share" = the weighted share of the sub-checks.
All-or-nothing is the default because a plan with one broken constraint is an unusable plan, and because a share
lets "repeat last turn's plan" collect parse/stops/hours credit for free (measured in the tests: the share floor is
far above the all-or-nothing floor).

The generator guarantees a feasible plan at every turn (search = the reference), keeps at most K feasible orders
per turn (K by level; most turns have 1-3), and requires that the previous turn's reference plan is INVALID after the
change (so "repeat last turn" fails). Levers (LEVELS): places, turns, event kinds, K, two changes per turn,
retractions. Reply budget: plan lines plus <= 1 sentence (~150-250 tokens); a hosted probe re-sends the history, so a
top-level session costs about 60-120k input tokens in total."""
from __future__ import annotations

import copy
import functools
import sys

from .. import gen as _gen
from .. import plansim as ps
from ._stack_common import TURN_SEP, join_turns, rng, split_turns

FAMILY = "planchat"
LUNCH = ps.LUNCH
hhmm = ps.hhmm

# kinds: close (closes early), open (opens late), fixed, skip, add, lunch, slow, deadline, group, retract
LEVELS = {
    1: {"places": 5, "req0": 3, "max_req": 4, "T": 6, "K": 8, "kinds": ["close", "skip", "add", "open"], "p2": 0.0,
        "so": 25, "sc": 25},
    2: {"places": 6, "req0": 4, "max_req": 5, "T": 8, "K": 6, "kinds": ["close", "open", "fixed", "skip", "add"],
        "p2": 0.1, "so": 20, "sc": 20},
    3: {"places": 6, "req0": 4, "max_req": 5, "T": 10, "K": 5,
        "kinds": ["close", "open", "fixed", "skip", "add", "lunch", "deadline"], "p2": 0.2, "so": 15, "sc": 15},
    4: {"places": 7, "req0": 4, "max_req": 6, "T": 12, "K": 4,
        "kinds": ["close", "open", "fixed", "skip", "add", "lunch", "deadline", "slow", "retract"], "p2": 0.3,
        "so": 15, "sc": 15},
    5: {"places": 7, "req0": 5, "max_req": 6, "T": 14, "K": 3,
        "kinds": ["close", "open", "fixed", "skip", "add", "lunch", "deadline", "slow", "retract", "group"],
        "p2": 0.4, "so": 12, "sc": 12},
    6: {"places": 8, "req0": 5, "max_req": 7, "T": 16, "K": 3,
        "kinds": ["close", "open", "fixed", "skip", "add", "lunch", "deadline", "slow", "retract", "group"],
        "p2": 0.5, "so": 10, "sc": 10},
    # the one harder tweak (probe 1: Haiku 16/16 at L6): tighter windows, K 2, two or three changes per turn,
    # more retractions / groups / slowdowns (weighted by repeating them in the kinds list)
    7: {"places": 8, "req0": 6, "max_req": 7, "T": 16, "K": 2,
        "kinds": ["close", "open", "fixed", "skip", "add", "lunch", "deadline", "slow", "retract", "retract", "group",
                  "group", "fixed"], "p2": 0.8, "p3": 0.35, "so": 6, "sc": 6},
}
# level -> ((prompt tokens est min, max over 30 seeds), (checks min, max), reference tokens): measured, pinned by tests
_SIZES = {1: ((665, 730), (6, 6), 144), 2: ((756, 867), (8, 8), 226), 3: ((804, 958), (10, 10), 297),
          4: ((935, 1248), (12, 12), 386), 7: ((1314, 1752), (16, 16), 596), 5: ((1008, 1358), (14, 14), 458), 6: ((1156, 1476), (16, 16), 574)}
for _lv, _row in LEVELS.items():
    _row["size_prompt"], _row["size_checks"], _row["answer_tokens"] = _SIZES[_lv]

MAX_TOKENS = 3000
USERS = ["Wren", "Corin", "Maren", "Teo", "Lio", "Sunny", "Ines", "Pell"]
NAMES = ["Bakery", "Pharmacy", "Bank", "Post Office", "Library", "Dentist", "Cafe", "Florist", "Hardware Store",
         "Market", "Barber", "Laundry", "Bookshop", "Tailor", "Optician", "Butcher"]
ACK = ["Here is the updated plan.", "This plan fits everything.", "Updated below.", "That works with all your changes."]


class _Fail(Exception):
    pass


def _r5(x: int) -> int:
    return int(round(x / 5.0)) * 5


# ------------------------------------------------------------ state from events
def _state(world: dict, active: list[dict]) -> dict:
    places = {k: list(v) for k, v in world["places"].items()}
    req = list(world["req0"])
    fixed: dict = {}
    group, slow = None, None
    deadline = world["end"][1]
    for ev in active:
        k = ev["kind"]
        if k == "close":
            places[ev["p"]][1] = ev["t"]
        elif k == "open":
            places[ev["p"]][0] = ev["t"]
        elif k == "fixed":
            fixed[ev["p"]] = ev["t"]
        elif k == "skip":
            if ev["p"] in req:
                req.remove(ev["p"])
        elif k == "add":
            if ev["p"] not in req:
                req.append(ev["p"])
        elif k == "lunch":
            places[LUNCH] = [ev["lo"], ev["hi"], ev["dur"]]
            req.append(LUNCH)
        elif k == "slow":
            slow = [ev["t"], 3, 2]
        elif k == "deadline":
            deadline = ev["t"]
        elif k == "group":
            fixed[ev["g"][0]] = ev["t"]
            group = list(ev["g"])
    return {"places": places, "T": world["T"], "start": list(world["start"]), "end": [world["end"][0], deadline],
            "req": req, "fixed": fixed, "group": group, "slow": slow}


def _fold(world: dict, events: list[dict], upto: int) -> list[dict]:
    """Active events after the first ``upto`` events (retractions remove their target)."""
    active: list[dict] = []
    for ev in events[:upto]:
        if ev["kind"] == "retract":
            active = [a for a in active if a["id"] != ev["target"]]
        else:
            active.append(ev)
    return active


# ------------------------------------------------------------ world
def _greedy_order(st: dict, kind: str) -> list[str]:
    req = list(st["req"])
    if kind == "greedy-open":
        return sorted(req, key=lambda x: (st["places"][x][0], x))
    if kind == "greedy-close":
        return sorted(req, key=lambda x: (st["places"][x][1], x))
    order, loc, tm, rem = [], st["start"][0], st["start"][1], req[:]
    while rem:
        x = min(rem, key=lambda y: (ps.travel(st, loc, y, tm) + max(0, st["places"][y][0] - tm), y))
        rem.remove(x)
        order.append(x)
        tm = ps.naive_schedule(st, order)[-1][1]
        if x != LUNCH:
            loc = x
    return order


def _greedy_beats(st: dict) -> bool:
    """True when a cheap greedy order, scheduled competently, is already a valid plan (instance rejected)."""
    return any(ps.evaluate(st, ps.render(ps.naive_schedule(st, _greedy_order(st, k))))["valid"][0]
               for k in ("greedy-open", "greedy-close", "greedy-near"))


def _world(g, L: dict) -> dict:
    names = g.sample(NAMES, L["places"])
    end_home = g.random() < 0.6
    nodes = ["Home"] + ([] if end_home else ["Station"]) + names
    pos = {n: (g.randint(0, 30), g.randint(0, 30)) for n in nodes}
    T = {}
    for a in nodes:
        T[a] = {}
        for b in nodes:
            if a != b:
                d = abs(pos[a][0] - pos[b][0]) + abs(pos[a][1] - pos[b][1])
                T[a][b] = max(3, min(28, round(d * 0.5) + g.randint(-3, 3)))
    dur = {n: g.choice([20, 25, 30, 30, 35, 40, 45, 60]) for n in names}
    start = ["Home", g.choice([510, 540, 570])]
    end = ["Home" if end_home else "Station", 0]
    order = names[:]
    g.shuffle(order)
    t, loc, places = start[1], "Home", {}
    for n in order:
        s = t + T[loc][n] + g.randint(0, 8)
        e = s + dur[n]
        o_ = max(450, _r5(s - g.randint(0, L["so"])))
        c_ = min(1200, _r5(e + g.randint(0, L["sc"])))
        r_ = g.random()
        if r_ < 0.3:          # opens early: the order is not the opening order
            o_ = max(450, _r5(o_ - g.randint(60, 150)))
        elif r_ < 0.55:       # closes late
            c_ = min(1200, _r5(c_ + g.randint(60, 150)))
        places[n] = [o_, c_, dur[n]]
        # keep the window able to hold the visit
        places[n][0] = min(places[n][0], places[n][1] - dur[n])
        t, loc = e, n
    end[1] = _r5(t + T[loc][end[0]] + g.randint(5, 25) + 4)
    req0 = g.sample(names, L["req0"])
    return {"places": places, "T": T, "start": start, "end": end, "req0": req0, "names": names, "order": order}


# ------------------------------------------------------------ events
def _solve(state: dict, K: int, g) -> tuple[int, list | None]:
    return ps.solve(state, cap=K + 1, rng=g)


def _cands(g, L: dict, world: dict, active: list[dict], state: dict, P: list, nid: int, kind: str):
    """One candidate event of ``kind`` (or None). P = the previous reference plan [(s, e, name)]."""
    at = {n: (s, e) for s, e, n in P}
    keys = {(a["kind"], a.get("p")) for a in active}
    pl = state["places"]
    req = state["req"]
    real = [r for r in req if r != LUNCH]
    ev = {"id": nid, "kind": kind}
    if kind == "close":
        p = g.choice(real)
        if ("close", p) in keys or p not in at:
            return None
        lo = pl[p][0] + pl[p][2]
        hi = at[p][1] - 5
        if hi < lo:
            return None
        ev.update(p=p, t=_r5(g.randint(max(lo, hi - 40), hi)))
        if not lo <= ev["t"] <= hi:
            return None
    elif kind == "open":
        p = g.choice(real)
        if ("open", p) in keys or p not in at:
            return None
        lo = at[p][0] + 5
        hi = pl[p][1] - pl[p][2]
        if hi < lo:
            return None
        ev.update(p=p, t=_r5(g.randint(lo, min(hi, lo + 40))))
        if not lo <= ev["t"] <= hi:
            return None
    elif kind == "fixed":
        p = g.choice(real)
        if ("fixed", p) in keys or p in state["fixed"]:
            return None
        lo, hi = pl[p][0], pl[p][1] - pl[p][2]
        if hi < lo:
            return None
        ev.update(p=p, t=_r5(g.randint(lo, hi)))
        if ev["t"] == at.get(p, (0, 0))[0] or not lo <= ev["t"] <= hi:
            return None
    elif kind == "skip":
        cand = [r for r in real if r not in state["fixed"] and r not in (state["group"] or [])]
        if len(real) <= 3 or not cand:
            return None
        ev["p"] = g.choice(cand)
    elif kind == "add":
        cand = [n for n in world["names"] if n not in req]
        if len(real) >= L["max_req"] or not cand:
            return None
        ev["p"] = g.choice(cand)
    elif kind == "lunch":
        if LUNCH in req:
            return None
        lo = g.choice([690, 720, 735, 750])
        dur = g.choice([30, 35, 40, 45])
        ev.update(lo=lo, hi=lo + dur + g.choice([30, 40, 50]), dur=dur)
    elif kind == "slow":
        if state["slow"] or any(a["kind"] == "slow" for a in active):
            return None
        ev["t"] = g.choice([750, 780, 810, 840])
    elif kind == "deadline":
        if any(a["kind"] == "deadline" for a in active):
            return None
        cur = state["end"][1]
        ev["t"] = _r5(cur - g.randint(10, 40))
    elif kind == "group":
        if state["group"] or len(real) < 3:
            return None
        seq = [x for x in P if x[2] != LUNCH]
        i = g.randrange(0, max(1, len(seq) - 2))
        tri = seq[i:i + 3]
        if len(tri) < 3 or tri[0][2] in state["fixed"] or any(x[2] not in req for x in tri):
            return None
        a = tri[0][2]
        rest = [tri[1][2], tri[2][2]]
        if g.random() < 0.5:
            rest.reverse()
        t = _r5(tri[0][0] + g.choice([-20, -10, 5, 10, 20, 30]))
        if not pl[a][0] <= t <= pl[a][1] - pl[a][2]:
            return None
        ev.update(g=[a] + rest, t=t)
    elif kind == "retract":
        tg = [a for a in active if a["kind"] in ("close", "open", "fixed", "lunch", "slow", "deadline", "group")]
        if not tg:
            return None
        ev["target"] = g.choice(tg)["id"]
    else:
        return None
    return ev


def _etext(ev: dict, world: dict, byid: dict) -> str:
    k = ev["kind"]
    p = ev.get("p")
    if k == "close":
        return f"The {p} now closes at {hhmm(ev['t'])} today."
    if k == "open":
        return f"The {p} will not open until {hhmm(ev['t'])} today."
    if k == "fixed":
        return f"I must be at the {p} at exactly {hhmm(ev['t'])}: my visit there has to start at that minute."
    if k == "skip":
        return f"Skip the {p} today."
    if k == "add":
        return f"Please add the {p} to my list."
    if k == "lunch":
        return (f"I also need lunch somewhere between {hhmm(ev['lo'])} and {hhmm(ev['hi'])}: it takes {ev['dur']} "
                f"minutes, must start no earlier than {hhmm(ev['lo'])} and end no later than {hhmm(ev['hi'])}. Write it "
                f"as `HH:MM-HH:MM Lunch`; I eat wherever I am, so it needs no travel and the next trip starts from "
                f"the place before it.")
    if k == "slow":
        return (f"My knee is playing up: any trip that starts at or after {hhmm(ev['t'])} takes 50% longer than the "
                f"table says (rounded up to a whole minute).")
    if k == "deadline":
        return f"I now need to be back at {world['end'][0]} by {hhmm(ev['t'])}."
    if k == "group":
        a, b, c = ev["g"]
        return (f"My sister joins me at the {a} at {hhmm(ev['t'])} (my visit there starts at that minute) and comes "
                f"along to the {b} and the {c} too, so do those three stops back to back with the {a} first, the other "
                f"two in either order, and nothing in between.")
    tg = byid[ev["target"]]
    tk = tg["kind"]
    if tk == "close":
        return f"Forget what I said about the {tg['p']} closing early; its normal closing time stands."
    if tk == "open":
        return f"Forget what I said about the {tg['p']} opening late; its normal opening time stands."
    if tk == "fixed":
        return f"Cancel the fixed time at the {tg['p']}; I can go whenever suits."
    if tk == "lunch":
        return "Forget lunch; I will eat later."
    if tk == "slow":
        return "My knee is fine again: every trip takes the table time again."
    if tk == "deadline":
        return f"Back to the original plan: be at {world['end'][0]} by {hhmm(world['end'][1])}."
    return (f"My sister cannot come after all, so drop the meeting at the {tg['g'][0]} and the back-to-back "
            f"requirement.")


def _world_text(w: dict, user: str) -> str:
    lines = [f"Plan my day, please. I start at {w['start'][0]} at {hhmm(w['start'][1])} and need to be at "
             f"{w['end'][0]} by {hhmm(w['end'][1])}.", "", "Places (opening hours, visit length):"]
    for n in w["names"]:
        o, c, d = w["places"][n]
        lines.append(f"- {n}: open {hhmm(o)}-{hhmm(c)}, {d} min")
    lines += ["", "Travel times in minutes (from -> to; they are not the same in both directions):"]
    for a, row in w["T"].items():
        lines.append(f"From {a}: " + ", ".join(f"{b} {m}" for b, m in row.items()))
    lines += ["", "Today I need to visit: " + ", ".join(w["req0"]) + ". The other places in the table are only for "
              "later, if I ask. Please give me the full plan."]
    return "\n".join(lines)


# ------------------------------------------------------------ attempt
def _attempt(g, L: dict, scoring: str) -> dict:
    """Bounded search: up to 6 restarts of the whole draw before the attempt counts as failed."""
    for _ in range(6):
        try:
            return _attempt1(g, L, scoring)
        except _Fail:
            continue
    raise _Fail


def _attempt1(g, L: dict, scoring: str) -> dict:
    for _w in range(1500):            # the world is drawn until turn 1 is feasible, tight and not greedy-solvable
        w = _world(g, L)
        st0 = _state(w, [])
        n, P = _solve(st0, L["K"], g)
        if 1 <= n <= L["K"] and not _greedy_beats(st0):
            break
    else:
        raise _Fail
    events: list[dict] = []
    turns_ev: list[list[dict]] = [[]]
    states, refs = [], []
    states.append(st0)
    refs.append(P)
    for t in range(2, L["T"] + 1):
        ok = False
        for _try in range(600):
            m = 3 if L.get("p3") and g.random() < L["p3"] else 2 if g.random() < L["p2"] else 1
            kinds = [g.choice(L["kinds"]) for _ in range(m)]
            kinds = [g.choice(L["kinds"]) if k in ("skip", "add") and g.random() < 0.5 else k for k in kinds]
            if "retract" in kinds and m == 1:
                kinds.append(g.choice([k for k in L["kinds"] if k != "retract"]))
            new = []
            active = _fold(w, events, len(events))
            cur_events = list(events)
            fail = False
            for kd in kinds:
                st = _state(w, active)
                ev = _cands(g, L, w, active, st, refs[-1], len(cur_events) + 1, kd)
                if ev is None:
                    fail = True
                    break
                new.append(ev)
                cur_events.append(ev)
                active = _fold(w, cur_events, len(cur_events))
            if fail:
                continue
            st = _state(w, active)
            if len({e["kind"] for e in new}) < len(new) and any(e["kind"] != "skip" and e["kind"] != "add" for e in new):
                continue
            n, P = _solve(st, L["K"], g)
            if not 1 <= n <= L["K"] or _greedy_beats(st):
                continue
            # the previous reference plan must be invalid now
            old = ps.render(refs[-1])
            if ps.evaluate(st, old)["valid"][0]:
                continue
            events.extend(new)
            turns_ev.append(new)
            states.append(st)
            refs.append(P)
            ok = True
            break
        if not ok:
            raise _Fail
    byid = {e["id"]: e for e in events}
    texts = [_world_text(w, "")]
    for evs in turns_ev[1:]:
        texts.append(" ".join(_etext(e, w, byid) for e in evs) + " Please give me the full updated plan.")
    return {"world": w, "events": events, "turns_ev": turns_ev, "states": states, "refs": refs, "turns": texts}


def _system(user: str, w: dict) -> str:
    return (
        f"You are a day-planning assistant. {user} plans one day of errands with you in a chat and keeps changing it.\n"
        "In EVERY reply give the CURRENT full plan (all stops, not only what changed), then at most one short sentence.\n"
        "Plan format: one line per stop, in time order, `HH:MM-HH:MM Place`: 24-hour clock, two digits for hours and "
        "minutes, a hyphen between the two times, and the place name exactly as the user wrote it. Example line: "
        "`09:10-09:40 Bakery`. Write no lines for travel, starting or finishing, and nothing else on a plan line.\n"
        "The plan must satisfy ALL of the following at once, with every change the user has made so far that they have "
        "not withdrawn:\n"
        "1. A visit lasts exactly the stated visit length (end minus start).\n"
        "2. A visit must start and end within the place's opening hours: you may arrive at closing time minus visit "
        "length at the latest (a visit that ends exactly at closing time is fine).\n"
        "3. A visit may start at the earliest when the previous visit has ended plus the travel time from the previous "
        "place to this one, in that direction (starting exactly then is fine; waiting longer is allowed). The first "
        "visit follows the same rule from the start place and start time.\n"
        "4. After the last visit the trip to the end place must be finished by the deadline (arriving exactly at it is "
        "fine).\n"
        "5. Visit every place on the user's current list exactly once, and no place that is not on it.\n")


def _checks(p: dict, scoring: str) -> list[dict]:
    out = []
    for t, st in enumerate(p["states"], 1):
        base = {"fact": f"t{t}", "ftype": "fact", "group": "plan", "turns": [t], "scope": "all", "type": "plan_sim",
                "w": st}
        if scoring == "all":
            out.append({**base, "id": f"plan-t{t}", "sub": "valid", "weight": 1.0})
            for s in ps.SUBS:
                if s == "fixed" and not st["fixed"] or s == "group" and not st["group"]:
                    continue
                out.append({"id": f"plan-t{t}-{s}", "turns": [t], "type": "plan_sim", "sub": s, "w": st,
                            "group": "plan", "scope": "all", "weight": 0})
        else:
            for s in ps.SUBS:
                if s == "fixed" and not st["fixed"] or s == "group" and not st["group"]:
                    continue
                out.append({**base, "id": f"plan-t{t}-{s}", "fact": f"t{t}-{s}", "sub": s, "weight": 1.0})
    return out


def _reply(p: dict, t: int, g) -> str:
    return ps.render(p["refs"][t - 1]) + "\n" + g.choice(ACK)


@functools.lru_cache(maxsize=256)
def _plan_cached(level: int, key, scoring: str, release_seed: str, case_id: str) -> dict:
    L = LEVELS[level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, key)
    for attempt in range(300):
        g = rng(seed, f"plan{attempt}")
        try:
            p = _attempt(g, L, scoring)
        except (_Fail, IndexError, ValueError, KeyError):
            continue
        p.update(level=level, seed=seed, attempt=attempt, scoring=scoring)
        p["user"] = g.choice(USERS)
        p["system"] = _system(p["user"], p["world"])
        p["checks"] = _checks(p, scoring)
        gr = rng(seed, f"ref{attempt}")
        p["reference"] = [_reply(p, t, gr) for t in range(1, L["T"] + 1)]
        return p
    raise RuntimeError(f"planchat: no valid draw for {case_id} L{level}")


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    sc = gen.get("scoring", "all")
    if sc not in ("all", "share"):
        raise _gen.GeneratorError(f"planchat: scoring must be all|share, not {sc!r}")
    return copy.deepcopy(_plan_cached(int(gen.get("level", 1)), gen.get("key"), sc, release_seed, case_id))


def grade_replies(checks: list[dict], replies: list[str]) -> float:
    from ._stack_common import grade_weighted
    return grade_weighted(checks, replies)


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    p = _plan(gen, release_seed, case_id)
    nchk = sum(1 for c in p["checks"] if c.get("weight"))
    return {
        "id": case_id, "category": "rpdeep", "system": p["system"], "history": [], "turns": p["turns"],
        "temperature": 0.7, "top_p": 0.95, "max_tokens": MAX_TOKENS, "user_name": p["user"],
        "checks": p["checks"], "judge_elements": [], "programmatic": True, "generator": dict(gen),
        "meta": {"family": FAMILY, "level": p["level"], "n_turns": len(p["turns"]), "n_checks": nchk,
                 "prompt_tokens_est": round((len(p["system"]) + sum(len(x) for x in p["turns"])) / 4),
                 "reference_tokens_est": round(sum(len(x) for x in p["reference"]) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_plan(gen, release_seed, case_id)["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_replies(materialized["checks"], split_turns(answer))


# ------------------------------------------------------------ floors (cheap strategies, used by tests and mutants)
def floor_replies(p: dict, kind: str, g=None) -> list[str]:
    """Replies of a cheap strategy: random-order | greedy-open | greedy-near | repeat-last | repeat-first."""
    out = []
    for t, st in enumerate(p["states"], 1):
        req = list(st["req"])
        if kind == "random-order":
            g.shuffle(req)
            order = req
        elif kind == "greedy-open":
            order = _greedy_order(st, kind)
        elif kind in ("greedy-near", "greedy-close"):
            order = _greedy_order(st, kind)
        elif kind == "repeat-last":
            out.append(out[-1] if out else "")
            if t > 1:
                out[-1] = ps.render(p["refs"][t - 2])
            continue
        elif kind == "repeat-first":
            out.append(ps.render(p["refs"][0]))
            continue
        else:
            raise ValueError(kind)
        out.append(ps.render(ps.naive_schedule(st, order)))
    return out


FLOOR_KINDS = ("random-order", "greedy-open", "greedy-close", "greedy-near", "repeat-last", "repeat-first")


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    p = _plan(gen, release_seed, case_id)
    out = [join_turns(floor_replies(p, k, rng(p["seed"], "mut" + k))) for k in FLOOR_KINDS]
    out.append(join_turns(["Sure, noted."] * len(mat["turns"])))
    return [a for a in dict.fromkeys(out) if grade(a, mat) < 1.0]


def _main(argv: list[str] | None = None) -> None:
    import json
    argv = sys.argv[1:] if argv is None else argv
    level = int(argv[0]) if argv else 6
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
