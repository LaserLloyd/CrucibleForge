"""PLANOPT generator (gen2 chat, 4.5; lab card EXP-20261114-20): the best day plan, re-planned as the user changes it.

PLANCHAT's lesson (EXP-20261114-13): strong models keep a plan VALID through any number of changes, because with
8 required stops there are only 1-3 feasible orders to find. PLANOPT opens the search space: the user lists 9-14
places, each with opening hours, a visit length and "want" points, and asks every turn for the plan with the
highest total of points that keeps every hard rule (hours, visit length, travel table, fixed times, deadline, must-
visit places, places ruled out). Which places to choose and in what order interact through the time windows.

Grading: ``planoptsim.plan_points`` simulates the reply's plan; the generator stores the EXACT optimum of every turn
(label DP over visited sets, ``planoptsim.solve``) in the check. Per turn: ``near`` (valid and >= 90% of the best
total) and ``best`` (valid and equal to the best total), weight 0.5 each; ``valid`` rides at weight 0 for the
record. Any plan reaching the best total passes, whatever its places or order (ties are common and all accepted).
The prompt says, in so many words, that the plan must have the highest possible total: that sentence carries both
checks. The 90% partial credit is a grading choice, stated nowhere because it only rewards.

Instance rules: each turn's best total is feasible; the previous turn's reference plan is invalid or below the new
best (so repeating fails); every cheap greedy (points-per-minute, most-points, nearest, opening order; required
places first) scores below 90% of the best on that turn.
"""
from __future__ import annotations

import copy
import functools
import sys

from .. import gen as _gen
from .. import plansim as ps
from .. import planoptsim as pos
from ._stack_common import TURN_SEP, join_turns, rng, split_turns

FAMILY = "planopt"
# revision pins: chains.revision digests only this file, so the two rule modules it reads are pinned here (tested)
_PLANSIM_SHA = "32332f6054a1ed6c"
_PLANOPTSIM_SHA = "3381b61db67324bc"
LUNCH = ps.LUNCH
hhmm = ps.hhmm
# t1 (lead 2026-11-14, rig screen): the session's FIRST turn alone, asked as a one-turn item (no history; a full
# planning problem with an exact optimum, the largest place set of the session). Same draw as the full session.
VARIANTS = ("t1",)

LEVELS = {
    1: {"places": 8, "T": 5, "req0": 1, "day": (420, 450), "kinds": ["close", "open", "pts", "ban", "deadline", "must"],
        "p2": 0.0, "near_frac": 0.9},
    2: {"places": 11, "T": 6, "req0": 1, "day": (450, 480),
        "kinds": ["close", "open", "pts", "ban", "deadline", "must", "lunch", "fixed", "retract"], "p2": 0.3,
        "near_frac": 0.9},
    3: {"places": 14, "T": 8, "req0": 1, "day": (480, 510),
        "kinds": ["close", "open", "pts", "pts", "ban", "deadline", "must", "lunch", "fixed", "slow", "retract"],
        "p2": 0.5, "near_frac": 0.9},
    # cycle 2 (symmetric-table probe: Sonnet .94 / .88 at L3): a bigger space -- 18 places, a 10-hour day
    # top cell: every strong model is within 90% on every turn (probe: Sonnet and Haiku near 8/8), so only "best"
    # scores here (near rides at weight 0 for the record)
    4: {"places": 18, "T": 8, "req0": 1, "day": (570, 600),
        "kinds": ["close", "open", "pts", "pts", "ban", "deadline", "must", "lunch", "fixed", "slow", "retract"],
        "p2": 0.5, "near_frac": 0.9, "near_w": 0.0},
}
MAX_TOKENS = 3000
USERS = ["Wren", "Corin", "Maren", "Teo", "Lio", "Sunny", "Ines", "Pell"]
NAMES = ["Bakery", "Pharmacy", "Bank", "Post Office", "Library", "Dentist", "Cafe", "Florist", "Hardware Store",
         "Market", "Barber", "Laundry", "Bookshop", "Tailor", "Optician", "Butcher", "Gallery", "Museum", "Park",
         "Gym", "Toy Shop", "Cobbler"]
ACK = ["Here is the best plan I found.", "This plan scores the most points.", "Updated below.",
       "That keeps every rule."]


class _Fail(Exception):
    pass


def _r5(x: int) -> int:
    return int(round(x / 5.0)) * 5


# ------------------------------------------------------------ world
def _world(g, L: dict) -> dict:
    names = g.sample(NAMES, L["places"])
    nodes = ["Home"] + names
    pos_ = {n: (g.randint(0, 40), g.randint(0, 40)) for n in nodes}
    pos_["Home"] = (20, 20)
    T = {a: {} for a in nodes}
    for ia, a in enumerate(nodes):          # symmetric (probe 1: models read the reverse direction of an
        for b in nodes[ia + 1:]:            # asymmetric 15x15 table; that is a lookup trap, not search)
            d = abs(pos_[a][0] - pos_[b][0]) + abs(pos_[a][1] - pos_[b][1])
            T[a][b] = T[b][a] = max(4, min(35, round(d * 0.55) + g.randint(-3, 3)))
    T = {a: {b: T[a][b] for b in nodes if b != a} for a in nodes}
    start = 540
    end = _r5(start + g.randint(*L["day"]))
    places, pts = {}, {}
    for n in names:
        d = g.choice([20, 25, 30, 30, 40, 45, 50, 60])
        k = g.random()
        if k < 0.3:                                   # all day
            o, c = g.choice([480, 510, 540]), g.choice([1020, 1050, 1080, 1140])
        elif k < 0.55:                                # morning
            o, c = g.choice([480, 540, 570, 600]), g.choice([690, 720, 750, 780])
        elif k < 0.8:                                 # afternoon
            o, c = g.choice([780, 810, 840, 870]), g.choice([960, 990, 1020, 1080])
        else:                                         # a narrow window somewhere
            o = _r5(g.randint(570, 930))
            c = o + d + g.choice([15, 25, 40, 60])
        c = max(c, o + d)
        places[n] = [o, c, d]
        pts[n] = g.choice([1, 2, 2, 3, 3, 4, 5, 5, 6, 7, 8, 9])
    req0 = g.sample(names, L["req0"])
    return {"places": places, "pts": pts, "T": T, "start": ["Home", start], "end": ["Home", end], "req0": req0,
            "names": names}


def _state(world: dict, active: list[dict]) -> dict:
    places = {k: list(v) for k, v in world["places"].items()}
    pts = dict(world["pts"])
    req = list(world["req0"])
    banned: set = set()
    fixed: dict = {}
    slow = None
    deadline = world["end"][1]
    for ev in active:
        k = ev["kind"]
        if k == "close":
            places[ev["p"]][1] = ev["t"]
        elif k == "open":
            places[ev["p"]][0] = ev["t"]
        elif k == "pts":
            pts[ev["p"]] = ev["v"]
        elif k == "ban":
            banned.add(ev["p"])
        elif k == "must":
            if ev["p"] not in req:
                req.append(ev["p"])
        elif k == "fixed":
            fixed[ev["p"]] = ev["t"]
            if ev["p"] not in req:
                req.append(ev["p"])
        elif k == "lunch":
            places[LUNCH] = [ev["lo"], ev["hi"], ev["dur"]]
            req.append(LUNCH)
        elif k == "slow":
            slow = [ev["t"], 3, 2]
        elif k == "deadline":
            deadline = ev["t"]
    req = [r for r in req if r not in banned]
    opt = [n for n in world["names"] if n not in req and n not in banned]
    return {"places": places, "pts": pts, "T": world["T"], "start": list(world["start"]),
            "end": [world["end"][0], deadline], "req": req, "opt": opt, "fixed": fixed, "slow": slow, "group": None}


def _fold(events: list[dict]) -> list[dict]:
    active: list[dict] = []
    for ev in events:
        if ev["kind"] == "retract":
            active = [a for a in active if a["id"] != ev["target"]]
        else:
            active.append(ev)
    return active


# ------------------------------------------------------------ cheap strategies (floors and instance filter)
def _greedy(st: dict, kind: str) -> list[tuple[int, int, str]]:
    """Forward construction: from the current place and time, pick the next stop by ``kind`` among stops that can
    still be done (open, fixed time reachable, home by the deadline after it); required stops (and Lunch) first."""
    places, fixed = st["places"], st["fixed"]
    loc, t = st["start"]
    rem = list(dict.fromkeys(st["req"] + st["opt"]))
    out = []
    while True:
        cands = []
        for x in rem:
            o, c, d = places[x]
            tr = 0 if x == LUNCH else ps.travel(st, loc, x, t)
            s = max(t + tr, o)
            if x in fixed:
                if s > fixed[x]:
                    continue
                s = fixed[x]
            e = s + d
            nl = loc if x == LUNCH else x
            if e > c or e + ps.travel(st, nl, st["end"][0], e) > st["end"][1]:
                continue
            p = st["pts"].get(x, 0)
            must = x in st["req"]
            if kind == "density":
                key = p / max(1, e - t)
            elif kind == "points":
                key = p
            elif kind == "near":
                key = -(s - t)
            else:  # opening
                key = -o
            cands.append(((1 if must else 0, key, -e, x), s, e, x, nl))
        if not cands:
            break
        cands.sort(reverse=True)
        _, s, e, x, nl = cands[0]
        out.append((s, e, x))
        rem.remove(x)
        t, loc = e, nl
    return out


GREEDY = ("density", "points", "near", "opening")


def _greedy_best(st: dict) -> int:
    best = 0
    for k in GREEDY:
        ok, p, _ = pos.plan_points(st, ps.render(_greedy(st, k)))
        if ok:
            best = max(best, p)
    return best


def _schedule(st: dict, order: list[str]):
    """Earliest-start schedule for an order, or None if it breaks a rule."""
    sched = ps.naive_schedule(st, order)
    ok, p, _ = pos.plan_points(st, ps.render(sched))
    return (sched, p) if ok else None


def local_search(st: dict) -> int:
    """A competent heuristic: best greedy, then insert / swap-in / drop-and-insert moves until no move improves."""
    best_order, best_p = [], -1
    for k in GREEDY:
        sched = _greedy(st, k)
        r = _schedule(st, [x for _, _, x in sched])
        if r and r[1] > best_p:
            best_order, best_p = [x for _, _, x in sched], r[1]
    if best_p < 0:
        return 0
    pool = list(dict.fromkeys(st["req"] + st["opt"]))
    improved = True
    while improved:
        improved = False
        out = [x for x in pool if x not in best_order]
        moves = []
        for x in out:
            for i in range(len(best_order) + 1):
                moves.append(best_order[:i] + [x] + best_order[i:])
                for j in range(len(best_order)):
                    if best_order[j] in st["req"]:
                        continue
                    o2 = best_order[:j] + best_order[j + 1:]
                    for i2 in range(len(o2) + 1):
                        moves.append(o2[:i2] + [x] + o2[i2:])
        for a in range(len(best_order)):
            for b in range(a + 1, len(best_order)):
                o2 = list(best_order)
                o2[a], o2[b] = o2[b], o2[a]
                moves.append(o2)
        for m in moves:
            r = _schedule(st, m)
            if r and r[1] > best_p:
                best_order, best_p, improved = m, r[1], True
                break
    return best_p


# ------------------------------------------------------------ events
def _cand(g, world: dict, active: list[dict], st: dict, P: list, nid: int, kind: str):
    at = {n: (s, e) for s, e, n in P}
    pl = st["places"]
    keys = {(a["kind"], a.get("p")) for a in active}
    inplan = [n for n in at if n != LUNCH]
    ev = {"id": nid, "kind": kind}
    if kind == "close":
        if not inplan:
            return None
        p = g.choice(inplan)
        if ("close", p) in keys:
            return None
        lo, hi = pl[p][0] + pl[p][2], at[p][1] - 5
        if hi < lo:
            return None
        ev.update(p=p, t=_r5(g.randint(max(lo, hi - 60), hi)))
    elif kind == "open":
        if not inplan:
            return None
        p = g.choice(inplan)
        if ("open", p) in keys:
            return None
        lo, hi = at[p][0] + 5, pl[p][1] - pl[p][2]
        if hi < lo:
            return None
        ev.update(p=p, t=_r5(g.randint(lo, min(hi, lo + 60))))
    elif kind == "pts":
        p = g.choice(st["opt"] + st["req"]) if st["opt"] else None
        if p is None or p == LUNCH:
            return None
        v = g.choice([1, 2, 3, 5, 7, 9, 10, 12])
        if v == st["pts"][p] or ("pts", p) in keys:
            return None
        ev.update(p=p, v=v)
    elif kind == "ban":
        cand = [n for n in inplan if n not in st["req"]]
        if not cand:
            return None
        ev["p"] = g.choice(cand)
    elif kind == "must":
        cand = [n for n in st["opt"] if n not in at]
        if not cand or len(st["req"]) >= 4:
            return None
        ev["p"] = g.choice(cand)
    elif kind == "fixed":
        if st["fixed"]:
            return None
        cand = [n for n in st["opt"] + st["req"] if n != LUNCH]
        p = g.choice(cand)
        lo, hi = pl[p][0], pl[p][1] - pl[p][2]
        if hi < lo:
            return None
        ev.update(p=p, t=_r5(g.randint(lo, hi)))
        if not lo <= ev["t"] <= hi:
            return None
    elif kind == "lunch":
        if LUNCH in st["req"]:
            return None
        lo = g.choice([690, 720, 735, 750])
        dur = g.choice([30, 40, 45])
        ev.update(lo=lo, hi=lo + dur + g.choice([20, 30, 45]), dur=dur)
    elif kind == "slow":
        if st["slow"]:
            return None
        ev["t"] = g.choice([750, 780, 810, 840])
    elif kind == "deadline":
        if any(a["kind"] == "deadline" for a in active):
            return None
        ev["t"] = _r5(st["end"][1] - g.randint(20, 60))
    elif kind == "retract":
        tg = [a for a in active if a["kind"] in ("close", "open", "pts", "ban", "fixed", "lunch", "slow", "deadline")]
        if not tg:
            return None
        ev["target"] = g.choice(tg)["id"]
    else:
        return None
    return ev


def _etext(ev: dict, world: dict, byid: dict) -> str:
    k, p = ev["kind"], ev.get("p")
    if k == "close":
        return f"The {p} now closes at {hhmm(ev['t'])} today."
    if k == "open":
        return f"The {p} will not open until {hhmm(ev['t'])} today."
    if k == "pts":
        return f"Change of heart: the {p} is now worth {ev['v']} points to me."
    if k == "ban":
        return f"Leave the {p} out today, whatever its points."
    if k == "must":
        return f"I have to go to the {p} today, so it must be in the plan (it keeps its points)."
    if k == "fixed":
        return (f"I have an appointment at the {p} at exactly {hhmm(ev['t'])}: it must be in the plan and the visit "
                f"must start at that minute.")
    if k == "lunch":
        return (f"I also need lunch between {hhmm(ev['lo'])} and {hhmm(ev['hi'])}: it takes {ev['dur']} minutes, must "
                f"start no earlier than {hhmm(ev['lo'])} and end no later than {hhmm(ev['hi'])}. Write it as "
                f"`HH:MM-HH:MM Lunch`; I eat wherever I am, so it needs no travel and the next trip starts from the "
                f"place before it. Lunch has no points.")
    if k == "slow":
        return (f"My knee is playing up: any trip that starts at or after {hhmm(ev['t'])} takes 50% longer than the "
                f"table says (rounded up to a whole minute). A trip starts when I leave, and I always leave right when "
                f"the previous visit ends (I can wait at the next place if I arrive early).")
    if k == "deadline":
        return f"I now need to be back at {world['end'][0]} by {hhmm(ev['t'])}."
    tg = byid[ev["target"]]
    tk, tp = tg["kind"], tg.get("p")
    return {
        "close": f"Forget what I said about the {tp} closing early; its normal closing time stands.",
        "open": f"Forget what I said about the {tp} opening late; its normal opening time stands.",
        "pts": f"Forget my change of heart about the {tp}: it is worth its original {world['pts'].get(tp)} points again.",
        "ban": f"On second thought the {tp} is allowed again.",
        "fixed": f"The appointment at the {tp} is cancelled: no fixed time, and it is optional again unless I said "
                 f"before that I must go there.",
        "lunch": "Forget lunch; I will eat later.",
        "slow": "My knee is fine again: every trip takes the table time again.",
        "deadline": f"Back to the original deadline: home by {hhmm(world['end'][1])}.",
    }[tk]


def _world_text(w: dict) -> str:
    lines = [f"Plan my day, please. I start at {w['start'][0]} at {hhmm(w['start'][1])} and need to be back at "
             f"{w['end'][0]} by {hhmm(w['end'][1])}. I can't fit everything in, so pick the places that give me the "
             f"most points in total.", "", "Places (opening hours, visit length, points):"]
    for n in w["names"]:
        o, c, d = w["places"][n]
        lines.append(f"- {n}: open {hhmm(o)}-{hhmm(c)}, {d} min, {w['pts'][n]} points")
    lines += ["", "Travel times in minutes (the same in both directions):"]
    for a, row in w["T"].items():
        lines.append(f"From {a}: " + ", ".join(f"{b} {m}" for b, m in row.items()))
    lines += ["", "I must go to: " + ", ".join(w["req0"]) + ". Every other place is optional. Please give me the "
              "best full plan."]
    return "\n".join(lines)


def _system(user: str) -> str:
    return (
        f"You are a day-planning assistant. {user} plans one day of errands with you in a chat and keeps changing it.\n"
        "In EVERY reply give the CURRENT full plan (all stops, not only what changed), then at most one short sentence.\n"
        "Plan format: one line per stop, in time order, `HH:MM-HH:MM Place`: 24-hour clock, two digits for hours and "
        "minutes, a hyphen between the two times, and the place name exactly as the user wrote it. Example line: "
        "`09:10-09:40 Bakery`. Write no lines for travel, starting or finishing, and nothing else on a plan line.\n"
        "The plan must keep ALL of these rules at once, with every change the user has made so far that they have not "
        "withdrawn:\n"
        "1. A visit lasts exactly the stated visit length (end minus start).\n"
        "2. A visit must start and end within the place's opening hours (a visit that ends exactly at closing time is "
        "fine).\n"
        "3. A visit may start at the earliest when the previous visit has ended plus the travel time from the previous "
        "place to this one, in that direction (starting exactly then is fine; waiting longer is allowed). The first "
        "visit follows the same rule from the start place and start time.\n"
        "4. After the last visit the trip back to the end place must be finished by the deadline (arriving exactly at "
        "it is fine).\n"
        "5. Every place the user must go to is in the plan; no place appears twice; no place outside the table and no "
        "place the user has ruled out.\n"
        "Among all plans that keep these rules, give one with the HIGHEST possible total of points (the sum of the "
        "points of the places in the plan, using the latest points the user gave). Search carefully: which places you "
        "choose and their order both matter.\n")


# ------------------------------------------------------------ attempt
def _attempt(g, L: dict) -> dict:
    for _w in range(400):
        w = _world(g, L)
        st0 = _state(w, [])
        best, P, nb = pos.solve(st0)
        if best > 0 and _greedy_best(st0) < L["near_frac"] * best:
            break
    else:
        raise _Fail
    events: list[dict] = []
    turns_ev: list[list[dict]] = [[]]
    states, refs, bests = [dict(st0, best=best)], [P], [best]
    for _t in range(2, L["T"] + 1):
        for _try in range(200):
            m = 2 if g.random() < L["p2"] else 1
            kinds = [g.choice(L["kinds"]) for _ in range(m)]
            if "retract" in kinds and m == 1:
                kinds.append(g.choice([k for k in L["kinds"] if k != "retract"]))
            cur = list(events)
            new, fail = [], False
            for kd in kinds:
                active = _fold(cur)
                st = _state(w, active)
                ev = _cand(g, w, active, st, refs[-1], len(cur) + 1, kd)
                if ev is None:
                    fail = True
                    break
                new.append(ev)
                cur.append(ev)
            if fail or len({e["kind"] for e in new}) < len(new):
                continue
            st = _state(w, _fold(cur))
            b, P, nb = pos.solve(st)
            if b <= 0:
                continue
            st["best"] = b
            ok_prev, p_prev, _ = pos.plan_points(st, ps.render(refs[-1]))
            if ok_prev and p_prev >= b:
                continue
            if _greedy_best(st) >= L["near_frac"] * b:
                continue
            events = cur
            turns_ev.append(new)
            states.append(st)
            refs.append(P)
            bests.append(b)
            break
        else:
            raise _Fail
    byid = {e["id"]: e for e in events}
    texts = [_world_text(w)]
    for evs in turns_ev[1:]:
        texts.append(" ".join(_etext(e, w, byid) for e in evs) + " Please give me the best full plan now.")
    return {"world": w, "events": events, "turns_ev": turns_ev, "states": states, "refs": refs, "bests": bests,
            "turns": texts}


def _checks(p: dict, L: dict) -> list[dict]:
    out = []
    for t, st in enumerate(p["states"], 1):
        base = {"turns": [t], "scope": "all", "type": "plan_opt", "w": st, "group": "plan", "ftype": "fact"}
        nw = L.get("near_w", 0.5)
        out.append({**base, "id": f"opt-t{t}-near", "fact": f"t{t}-near", "sub": "near", "frac": L["near_frac"],
                    "weight": nw})
        out.append({**base, "id": f"opt-t{t}-best", "fact": f"t{t}-best", "sub": "best", "weight": 1.0 - nw})
        out.append({**base, "id": f"opt-t{t}-valid", "fact": f"t{t}-valid", "sub": "valid", "weight": 0})
    return out


@functools.lru_cache(maxsize=64)
def _plan_cached(level: int, key, release_seed: str, case_id: str) -> dict:
    L = LEVELS[level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, key)
    for attempt in range(200):
        g = rng(seed, f"plan{attempt}")
        try:
            p = _attempt(g, L)
        except (_Fail, IndexError, ValueError, KeyError):
            continue
        p.update(level=level, seed=seed, attempt=attempt)
        p["user"] = g.choice(USERS)
        p["system"] = _system(p["user"])
        p["checks"] = _checks(p, L)
        gr = rng(seed, f"ref{attempt}")
        p["reference"] = [ps.render(p["refs"][t]) + "\n" + gr.choice(ACK) for t in range(L["T"])]
        return p
    raise RuntimeError(f"planopt: no valid draw for {case_id} L{level}")


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    p = copy.deepcopy(_plan_cached(int(gen.get("level", 1)), gen.get("key"), release_seed, case_id))
    if gen.get("variant") == "t1":
        for k in ("turns", "states", "refs", "bests", "reference", "turns_ev"):
            p[k] = p[k][:1]
        p["checks"] = [c for c in p["checks"] if c["turns"] == [1]]
    return p


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
                 "bests": p["bests"],
                 "prompt_tokens_est": round((len(p["system"]) + sum(len(x) for x in p["turns"])) / 4),
                 "reference_tokens_est": round(sum(len(x) for x in p["reference"]) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_plan(gen, release_seed, case_id)["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_replies(materialized["checks"], split_turns(answer))


def floor_replies(p: dict, kind: str) -> list[str]:
    """Replies of a cheap strategy: one of GREEDY | required-only | repeat-first | repeat-last."""
    out = []
    for t, st in enumerate(p["states"]):
        if kind in GREEDY:
            out.append(ps.render(_greedy(st, kind)))
        elif kind == "required-only":
            r = _schedule(st, list(st["req"]))
            out.append(ps.render(r[0]) if r else "")
        elif kind == "repeat-first":
            out.append(ps.render(p["refs"][0]))
        elif kind == "repeat-last":
            out.append(ps.render(p["refs"][t - 1]) if t else ps.render(_greedy(st, "density")))
        else:
            raise ValueError(kind)
    return out


FLOOR_KINDS = GREEDY + ("required-only", "repeat-first", "repeat-last")


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    p = _plan(gen, release_seed, case_id)
    out = [join_turns(floor_replies(p, k)) for k in FLOOR_KINDS]
    out.append(join_turns(["Sure, noted."] * len(mat["turns"])))
    return [a for a in dict.fromkeys(out) if grade(a, mat) < 1.0]


def _main(argv: list[str] | None = None) -> None:
    import json
    argv = sys.argv[1:] if argv is None else argv
    level = int(argv[0]) if argv else 3
    g = {"type": FAMILY, "level": level, "key": argv[1] if len(argv) > 1 else None}
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
