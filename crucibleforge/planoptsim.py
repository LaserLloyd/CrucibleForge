"""planoptsim -- the grader and exact solver behind the PLANOPT family (lab card EXP-20261114-20).

PLANOPT is PLANCHAT with a search space: the user lists many places, each with "want" points, and asks for the plan
with the most points that keeps every hard rule. A state is plain JSON (it rides inside every ``plan_opt`` check):

    places  {name: [open, close, dur]}   minutes since midnight ("Lunch" = [window lo, window hi, dur], no travel)
    pts     {name: points}               every place (required ones count too; Lunch has none)
    T, start, end, fixed, slow           as in plansim
    req     [names]                      must be in the plan (exactly once)
    opt     [names]                      may be in the plan (at most once); anything else is not allowed
    best    int                          the exact optimum (sum of points), computed by ``solve`` at generation time

Validity reuses ``plansim.evaluate`` (hours, duration, travel, fixed, deadline, each stop once, nothing unknown) on
a copy of the state whose required list is the reply's own allowed stops plus the required ones.
"""
from __future__ import annotations

from . import plansim as ps

LUNCH = ps.LUNCH


def last_block(text: str) -> str:
    """The reply's LAST plan. Plan lines (a time range) are read in order; a new block starts only when a plan
    line's start time is not later than the previous plan line's start (time goes back = a new draft). Any other
    line (a note under a stop, "wait, let me fix that") is ignored, so notes between stops never split a plan,
    and a later-time duplicate stays in its block and is still caught by the parser."""
    blocks, cur, prev = [], [], None
    for raw in (text or "").splitlines():
        m = ps._RANGE.match(ps._LEAD.sub("", raw.strip()))
        if not m:
            continue
        start = int(m.group(1)) * 60 + int(m.group(2))
        if cur and prev is not None and start <= prev:
            blocks.append(cur)
            cur = []
        cur.append(raw)
        prev = start
    if cur:
        blocks.append(cur)
    return "\n".join(blocks[-1]) if blocks else (text or "")


def plan_points(state: dict, text: str) -> tuple[bool, int, str]:
    """(valid, points, why) of the reply's last plan. Points count each allowed place in it once (required too)."""
    text = last_block(text)
    lines, why = ps.parse_plan(text, state)
    if lines is None:
        return False, 0, why
    names = [n for _, _, n in lines]
    allowed = set(state["req"]) | set(state["opt"])
    chosen = list(state["req"]) + [n for n in dict.fromkeys(names) if n in state["opt"] and n not in state["req"]]
    st2 = dict(state, req=chosen, group=None)
    r = ps.evaluate(st2, text)
    bad = [f"{k}: {v[1]}" for k, v in r.items() if k != "valid" and not v[0]]
    extra = sorted({n for n in names if n not in allowed})
    if extra and not any(b.startswith("excluded") for b in bad):
        bad.append(f"excluded: {extra}")
    pts = sum(state["pts"].get(n, 0) for n in dict.fromkeys(names) if n in allowed)
    return (not bad), pts, "; ".join(bad[:3])


def check_opt(turn_texts, chk) -> tuple[bool, str]:
    """session_checks entry (type ``plan_opt``). ``sub``: valid | near (valid and >= frac * best) | best."""
    _, text = turn_texts[0]
    st = chk["w"]
    ok, pts, why = plan_points(st, text)
    sub = chk.get("sub", "best")
    if not ok:
        return False, f"invalid ({why}); {pts} pts of best {st['best']}"
    if sub == "valid":
        return True, f"valid, {pts} of {st['best']}"
    if sub == "near":
        need = chk.get("frac", 0.9) * st["best"]
        return pts >= need - 1e-9, f"{pts} pts vs best {st['best']} (need {need:g})"
    return pts >= st["best"], f"{pts} pts vs best {st['best']}"


# ------------------------------------------------------------------ exact solver
def solve(state: dict, want_witness: bool = True):
    """Exact optimum by label DP over (visited set, last real place) -> earliest finish (optimal: travel never
    shrinks when you leave later, and points depend only on the set). -> (best, schedule|None, n_best_sets)."""
    nodes = list(dict.fromkeys(list(state["req"]) + list(state["opt"])))
    idx = {n: i for i, n in enumerate(nodes)}
    places, fixed, pts = state["places"], state["fixed"], state["pts"]
    reqmask = 0
    for r in state["req"]:
        reqmask |= 1 << idx[r]
    pv = [int(pts.get(n, 0)) for n in nodes]
    end_node, deadline = state["end"]
    st_loc, st_t = state["start"]
    labels = {(0, st_loc): st_t}
    parent = {(0, st_loc): None}
    frontier = [(0, st_loc)]
    best, best_keys = -1, []
    best_sets: set = set()

    def mpts(m):
        s, i = 0, 0
        while m:
            if m & 1:
                s += pv[i]
            m >>= 1
            i += 1
        return s

    while frontier:
        nxt = []
        for key in frontier:
            m, loc = key
            t = labels[key]
            if (m & reqmask) == reqmask and t + ps.travel(state, loc, end_node, t) <= deadline:
                v = mpts(m)
                if v > best:
                    best, best_keys, best_sets = v, [key], {m}
                elif v == best:
                    best_keys.append(key)
                    best_sets.add(m)
            for j, x in enumerate(nodes):
                b = 1 << j
                if m & b:
                    continue
                o, c, d = places[x]
                s = max(t if x == LUNCH else t + ps.travel(state, loc, x, t), o)
                if x in fixed:
                    if s > fixed[x]:
                        continue
                    s = fixed[x]
                e = s + d
                if e > c:
                    continue
                nl = loc if x == LUNCH else x
                k2 = (m | b, nl)
                if k2 not in labels:
                    labels[k2] = e
                    parent[k2] = (key, s, e, x)
                    nxt.append(k2)
                elif e < labels[k2]:
                    labels[k2] = e
                    parent[k2] = (key, s, e, x)
        frontier = nxt
    if best < 0:
        return -1, None, 0
    sched = None
    if want_witness:
        sched, k = [], best_keys[0]
        while parent[k] is not None:
            pk, s, e, x = parent[k]
            sched.append((s, e, x))
            k = pk
        sched.reverse()
    return best, sched, len(best_sets)
